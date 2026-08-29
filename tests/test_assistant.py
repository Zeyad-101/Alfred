"""Tests for the lightweight intent-classification assistant
(``core.assistant``).

Covers the three public entry points:

* :func:`classify_input` — the capture-vs-question rule
* :func:`extract_query_terms` — stopword stripping + fallback
* :func:`ask` — the top-level dispatcher used by the popup

The classify/extract tests are pure-function (no DB), so they
just exercise the rules. The ``ask`` tests build on the
``conn`` fixture from ``conftest.py`` so a real SQLite
database is in scope, and the question-path test inserts a
known memory so the FTS5 MATCH has something to find.
"""
from __future__ import annotations

import pytest

from core.assistant import (
    INTENT_CAPTURE,
    INTENT_QUESTION,
    MAX_QUESTION_RESULTS,
    ask,
    classify_input,
    extract_query_terms,
)
from core.memory import create_memory
from core.inbox import list_inbox


# ---------- classify_input ----------


class TestClassifyInput:
    """The rule table for the capture-vs-question decision."""

    def test_remember_that_is_capture(self):
        assert classify_input("remember that I need to buy milk") == INTENT_CAPTURE

    def test_remember_without_that_is_capture(self):
        assert classify_input("remember this for the meeting") == INTENT_CAPTURE

    def test_remember_exact_word_is_capture(self):
        # Degenerate case: user typed "remember" alone and hit
        # Enter. Still a capture, not a question.
        assert classify_input("remember") == INTENT_CAPTURE

    def test_remember_with_trailing_question_mark_is_capture(self):
        # "remember" prefix wins over the trailing "?". The
        # brief is explicit about this.
        assert classify_input("remember what I said about the meeting?") == INTENT_CAPTURE

    def test_what_is_my_name_is_question(self):
        assert classify_input("what's my name?") == INTENT_QUESTION

    def test_who_am_i_is_question(self):
        assert classify_input("who am i") == INTENT_QUESTION

    def test_where_did_i_put_it_is_question(self):
        assert classify_input("where did I put it") == INTENT_QUESTION

    def test_how_do_i_is_question(self):
        assert classify_input("how do I reset the database") == INTENT_QUESTION

    def test_bare_question_mark_is_question(self):
        # Even without an interrogative opener, a trailing
        # "?" is enough to classify as a question.
        assert classify_input("really?") == INTENT_QUESTION

    def test_plain_statement_defaults_to_capture(self):
        # The brief is explicit: an unprompted plain statement
        # is still worth noting.
        assert classify_input("I need to renew the passport") == INTENT_CAPTURE

    def test_casing_is_ignored(self):
        # "REMEMBER" should classify the same as "remember".
        assert classify_input("REMEMBER THAT Buy milk") == INTENT_CAPTURE
        assert classify_input("WHAT IS my name?") == INTENT_QUESTION

    def test_leading_whitespace_is_stripped(self):
        # The popup's submit handler trims the text, but
        # classify should also handle it defensively.
        assert classify_input("   what is my name?") == INTENT_QUESTION
        assert classify_input("   remember that this is a note") == INTENT_CAPTURE

    def test_empty_input_is_capture(self):
        # Defensive default; the popup rejects empty input
        # before calling classify, but if it's called, it
        # shouldn't crash or return "question".
        assert classify_input("") == INTENT_CAPTURE
        assert classify_input("   ") == INTENT_CAPTURE


# ---------- extract_query_terms ----------


class TestExtractQueryTerms:
    """The stopword-stripping + fallback behavior."""

    def test_strips_known_stopwords(self):
        # "what", "is", "my" are all in the stopword set.
        assert extract_query_terms("what is my name") == "name"

    def test_keeps_significant_words(self):
        assert (
            extract_query_terms("where did I put the keys")
            == "put keys"
        )

    def test_handles_apostrophe_s(self):
        # "what's" -> "what s" after punctuation translate;
        # both "what" and "s" are stopwords, so only "name"
        # survives.
        assert extract_query_terms("what's my name?") == "name"

    def test_question_mark_does_not_appear_in_output(self):
        result = extract_query_terms("what is my name?")
        assert "?" not in result
        assert "?" not in result.strip()

    def test_collapses_runs_of_whitespace(self):
        # Multiple spaces and tabs/punctuation gaps all
        # collapse to single spaces.
        assert (
            extract_query_terms("what   is    my   name?")
            == "name"
        )

    def test_falls_back_when_all_stopwords(self):
        # "what?" has only stopwords + a trailing "?".
        # Without the fallback, the result would be empty
        # and FTS5 would raise.
        result = extract_query_terms("what?")
        assert result == "what"
        assert result != ""

    def test_falls_back_when_only_auxiliaries(self):
        # "is the" has only stopwords. Fallback returns the
        # raw tokens rather than "".
        result = extract_query_terms("is the")
        assert result != ""
        # The fallback keeps "is" and "the" verbatim.
        assert "is" in result
        assert "the" in result

    def test_empty_input_returns_empty(self):
        # The fallback doesn't apply to a fully empty
        # string — there's nothing to fall back to.
        assert extract_query_terms("") == ""

    def test_no_stopwords_present_passes_through(self):
        # A question with zero stopwords is returned as-is
        # (modulo lowercasing and punctuation).
        assert (
            extract_query_terms("passport renewal")
            == "passport renewal"
        )

    def test_casing_is_normalised(self):
        # Output is always lower-case so it matches
        # whatever FTS5 normalisation the table uses.
        assert (
            extract_query_terms("What Is My NAME?")
            == "name"
        )


# ---------- ask ----------


class TestAsk:
    """The top-level dispatcher used by the capture popup."""

    def test_capture_path_creates_inbox_item(self, conn):
        result = ask(conn, "remember that I need to buy milk")
        assert result["intent"] == INTENT_CAPTURE
        assert "memory_id" in result
        # The id is a real inbox row.
        assert isinstance(result["memory_id"], int)
        items = list_inbox(conn)
        assert len(items) == 1
        assert items[0].id == result["memory_id"]
        # Existing capture behavior: the full text is filed
        # verbatim, including the "remember that" prefix. The
        # brief is explicit that the capture path is
        # unchanged from the pre-Phase-17 behavior.
        assert items[0].content == "remember that I need to buy milk"

    def test_capture_path_does_not_run_search(self, conn):
        # Capture path must NOT touch the search results
        # (the question path is the one that fills
        # ``results``). A buggy classifier would put both
        # keys in the dict; the brief asks for exactly
        # one shape per intent.
        result = ask(conn, "remember something")
        assert "results" not in result
        assert "query" not in result

    def test_question_path_with_match(self, conn):
        # Seed a memory the search can find. FTS5 matches
        # on the title (memories_fts indexes title+content)
        # — see core/db.py for the schema.
        create_memory(
            conn,
            title="My name is Zeyad",
            content="Self-introduction note.",
        )
        result = ask(conn, "what's my name?")
        assert result["intent"] == INTENT_QUESTION
        assert result["query"] == "name"
        assert len(result["results"]) >= 1
        # The first result is the seeded memory.
        top = result["results"][0]
        assert "Zeyad" in top.title

    def test_question_path_with_no_match_returns_empty_list(self, conn):
        # Empty DB, no matches, no error. The brief
        # explicitly forbids crashing on zero hits.
        result = ask(conn, "what's my favorite color?")
        assert result["intent"] == INTENT_QUESTION
        assert result["results"] == []
        assert result["query"]  # some terms survived

    def test_question_path_caps_at_max_results(self, conn):
        # Seed more than MAX_QUESTION_RESULTS matching
        # memories and confirm the cap is enforced.
        for i in range(MAX_QUESTION_RESULTS + 3):
            create_memory(
                conn,
                title=f"Book note number {i}",
                content="Reading list.",
            )
        result = ask(conn, "what book notes are there?")
        assert result["intent"] == INTENT_QUESTION
        assert len(result["results"]) <= MAX_QUESTION_RESULTS

    def test_question_path_does_not_create_inbox_item(self, conn):
        # The question path runs search, not create. A
        # buggy dispatcher would also file the question
        # as a note.
        before = len(list_inbox(conn))
        ask(conn, "what's the weather?")
        after = len(list_inbox(conn))
        assert before == after, (
            "question path leaked an inbox item"
        )

    def test_question_path_excludes_deleted(self, conn):
        # Insert a memory, then soft-delete it; the question
        # path must NOT return it. The brief is explicit
        # that question results are "non-deleted only".
        from core.memory import delete_memory

        new_id = create_memory(
            conn,
            title="Secret note about test",
            content="",
        )
        delete_memory(conn, new_id)
        result = ask(conn, "what's the secret note?")
        assert result["intent"] == INTENT_QUESTION
        # The deleted row is filtered out.
        assert all(r.id != new_id for r in result["results"])

    def test_ask_uses_classify_via_rules(self, conn, monkeypatch):
        # If ``classify_input`` is broken, ``ask`` would be
        # broken. We don't mock it (the whole point of the
        # integration test is to exercise the real
        # rule-table), but we do assert that the two are
        # consistent across a few representative inputs.
        cases = [
            ("remember that something", INTENT_CAPTURE),
            ("what time is it?", INTENT_QUESTION),
            ("just a plain note", INTENT_CAPTURE),
        ]
        for text, expected_intent in cases:
            result = ask(conn, text)
            assert result["intent"] == expected_intent, (
                f"ask() disagreed with classify_input() for {text!r}"
            )

    @pytest.mark.parametrize("weird", ["", "   ", None])
    def test_ask_handles_empty_input_gracefully(self, conn, weird):
        # Empty / whitespace input: classify returns
        # capture, the dispatcher creates an inbox item
        # with an empty-ish body. The popup rejects empty
        # input before calling ask, but the dispatcher
        # itself should not raise.
        if weird is None:
            # ``ask`` is typed as taking ``str``; passing
            # None would be a contract violation. Skip the
            # None case at the parametrize level rather
            # than letting it raise an unrelated error.
            pytest.skip("ask expects str, not None")
        result = ask(conn, weird)
        assert result["intent"] == INTENT_CAPTURE
        assert "memory_id" in result
