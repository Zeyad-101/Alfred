"""Lightweight assistant-style intent classification and routing.

Alfred's quick-capture popup (and the floating desktop
companion) are the only entry points most users will touch, so
"remember that..." and "what's my name?" both have to work from
the same one-line input -- no two-mode toggle, no separate
Ask/Note buttons.

This module is the rule-based intent layer behind that single
input. It is intentionally NOT a real LLM / NLP pipeline:

* :func:`classify_input` decides whether the user's text is a
  *capture* (note for the file) or a *question* (something to
  search Alfred's existing memory for).
* :func:`extract_query_terms` strips a small stopword list from
  a question, producing FTS5-friendly terms that the existing
  ``core.search.search`` can match against the memories table.
* :func:`ask` is the top-level dispatcher used by the popup --
  it classifies, then either files a new inbox item or runs
  a search and packages the results for display.

The rules are deliberately tight and the stopword list is
deliberately small. Both are documented in-line so a future
tweak is a one-line change rather than a hunt through
regex archaeology.
"""
from __future__ import annotations

import re
import sqlite3
import string
from typing import Literal, Optional

from core.inbox import create_inbox_item
from core.memory import Memory, _row_to_memory, get_tags
from core.search import search


# The two intent types the popup cares about. Re-exported as
# module-level constants so callers (tests, the popup) can
# refer to them by name without restating the string literal.
INTENT_CAPTURE = "capture"
INTENT_QUESTION = "question"

# Default cap on the number of question results we return.
# Three is enough to confirm a hit and scan alternatives
# without overwhelming a small popup.
MAX_QUESTION_RESULTS = 3

# Words that signal a question when they start a sentence.
# Lower-cased here; the matcher lower-cases the input before
# checking, so the case in the user's text doesn't matter.
_QUESTION_STARTERS = frozenset({
    "what",
    "who",
    "when",
    "where",
    "why",
    "how",
    "is",
    "are",
    "am",
    "do",
    "does",
    "did",
    "can",
    "could",
    "will",
    "would",
    "should",
})

# Stopwords stripped from a question before searching. This is
# intentionally NOT a general English stopword list -- it is
# exactly the words Alfred's own question shapes use, plus a few
# trivial additions ("'s", "?", and basic punctuation) that
# never carry search weight. The set is small on purpose:
# every word we drop is a word we DON'T get to match against
# the memories, and over-stripping hurts recall.
_QUERY_STOPWORDS = frozenset({
    "what",
    "who",
    "when",
    "where",
    "why",
    "how",
    "is",
    "are",
    "am",
    "do",
    "does",
    "did",
    "my",
    "your",
    "the",
    "a",
    "an",
    "can",
    "could",
    "will",
    "would",
    "should",
    "s",
    "i",
})

# A precompiled translation table of every ASCII punctuation
# character to a single space. Using ``str.translate`` is
# dramatically faster than a chain of ``str.replace`` calls
# and is exactly the right tool for the job -- punctuation
# becomes word boundaries, which is what we want for token
# splitting. Built once at import time.
_PUNCT_TABLE = str.maketrans(
    {ch: " " for ch in string.punctuation}
)

# Precompiled tokenizer. Two-or-more whitespace characters
# collapse to a single space, so we can split on plain ``" "``
# without losing any tokens.
_WHITESPACE_RE = re.compile(r"\s+")


def classify_input(text: str) -> Literal["capture", "question"]:
    """Decide whether ``text`` is a capture or a question.

    Rules (in order):

    1. The stripped, lower-cased text starts with
       ``"remember"`` (covers both ``"remember that ..."`` and
       ``"remember ..."``) -> ``"capture"``.
    2. The text ends with ``"?"`` OR the first whitespace-
       separated token is in :data:`_QUESTION_STARTERS` ->
       ``"question"``.
    3. Otherwise -> ``"capture"``. An unprompted plain
       statement is still worth noting; this matches the
       pre-existing "type something, hit Enter, get an
       inbox row" behavior of the capture box.

    The function is pure: it only reads ``text`` and never
    touches the database, which makes it cheap to unit-test
    and safe to call before opening any connection.
    """
    if not text:
        # An empty submission is rejected earlier in the
        # popup, but classify defensively anyway so the
        # dispatch is never asked to act on a vacuous input.
        return INTENT_CAPTURE

    stripped = text.strip()
    if not stripped:
        return INTENT_CAPTURE

    lowered = stripped.lower()

    # Rule 1: explicit "remember" prefix wins, and it wins
    # regardless of whether the rest of the
    # string happens to look like a question ("remember
    # what I said about the meeting?" is still a capture,
    # not a search).
    if lowered.startswith("remember that") or lowered.startswith("remember "):
        return INTENT_CAPTURE
    # Exact "remember" (no trailing space) -- a degenerate
    # case but still a capture, not a question.
    if lowered == "remember":
        return INTENT_CAPTURE

    # Rule 2: question-mark terminator, or an interrogative
    # opener. The opener check looks at the first token, so
    # a question like "where did I put the keys?" matches
    # via "where" rather than via the trailing "?".
    if lowered.endswith("?"):
        return INTENT_QUESTION
    first_token = lowered.split(" ", 1)[0]
    if first_token in _QUESTION_STARTERS:
        return INTENT_QUESTION

    # Rule 3: default to capture. An unprompted plain
    # statement should still produce a note, which is what
    # the capture box did before it learned to answer
    # questions at all.
    return INTENT_CAPTURE


def extract_query_terms(question: str) -> str:
    """Reduce a question to the words worth searching for.

    Steps:

    1. Lower-case and translate ASCII punctuation to spaces
       (``"what's my name?"`` -> ``"what s my name  "``).
    2. Collapse runs of whitespace, then split into tokens.
    3. Drop tokens in :data:`_QUERY_STOPWORDS`; keep
       everything else verbatim.
    4. Re-join the survivors with single spaces.

    If the stopword filter leaves nothing (e.g. the user
    typed just ``"what?"``), we fall back to the original
    text minus punctuation. That fallback is what keeps
    search from being called with an empty string -- FTS5
    raises on an empty MATCH expression, and the user
    clearly meant *something*.

    The result is ready to feed straight into
    :func:`core.search.search`; ``search`` will quote each
    token for FTS5 safety regardless.
    """
    if not question:
        return ""

    lowered = question.lower()
    de_puncted = lowered.translate(_PUNCT_TABLE)
    tokens = _WHITESPACE_RE.sub(" ", de_puncted).split(" ")
    # Filter: drop empties (the translate pass leaves a few
    # when punctuation is adjacent to whitespace) and
    # stopwords. We compare against the lower-cased set so
    # casing is irrelevant.
    kept = [t for t in tokens if t and t not in _QUERY_STOPWORDS]

    if kept:
        return " ".join(kept)

    # Fallback: no significant terms survived. Re-run the
    # de-punctuation but skip the stopword filter, so a
    # question like "what?" becomes "what" (something to
    # search on) rather than "" (an FTS5 error).
    fallback = [t for t in tokens if t]
    return " ".join(fallback)


def ask(conn: sqlite3.Connection, text: str) -> dict:
    """Classify ``text`` and dispatch to capture or search.

    Returns a dict with two top-level shapes:

    * Capture path::

        {"intent": "capture", "memory_id": <int>}

      ``memory_id`` is the row id of the new inbox item
      (delegated to :func:`core.inbox.create_inbox_item`).

    * Question path::

        {"intent": "question",
         "query": <cleaned terms>,
         "results": list[Memory]}

      ``query`` is the cleaned search string (what
      :func:`extract_query_terms` produced), and ``results``
      is a list of up to :data:`MAX_QUESTION_RESULTS` live,
      non-deleted memories matching the FTS5 query.

    The popup's "open that memory in the editor" path
    receives the whole dict and branches on ``intent`` --
    a single return value keeps the call site flat and
    easy to wire from a Qt signal.

    Errors from the underlying ``create_inbox_item`` /
    ``search`` are allowed to propagate; the popup catches
    them and falls back to a graceful dismiss rather than
    crashing.
    """
    intent = classify_input(text)

    if intent == INTENT_CAPTURE:
        new_id = create_inbox_item(conn, text)
        return {"intent": INTENT_CAPTURE, "memory_id": new_id}

    # Question path: clean the question, then search. An
    # empty clean string (the user's question reduced to
    # nothing) still goes to ``search``, which itself
    # short-circuits empty queries to ``list_memories`` --
    # that "show me everything" fallback is reasonable for
    # a question with no recoverable terms.
    query = extract_query_terms(text)
    results = search(conn, query, include_deleted=False)
    # Cap the result set so a wide search doesn't push the
    # popup into a 30-line scroll view. Three is the chosen
    # limit; the constant is named so a future change is a
    # one-line edit.
    capped = results[:MAX_QUESTION_RESULTS]
    return {
        "intent": INTENT_QUESTION,
        "query": query,
        "results": capped,
    }


# ---------------------------------------------------------------------------
# Single-fact and single-entry lookups (butler interaction layer)
# ---------------------------------------------------------------------------
# These are the DB half of the curated question shapes in
# ``ui.assistant_controller``. They live here, in core/, so the UI layer
# never writes SQL of its own; they are plain LIKE + regex lookups, with
# no fuzzy or semantic matching anywhere.

#: Matches a stored self-description such as "my name is Bruce" or
#: "My birthday: 19 February". Deliberately anchored on the literal
#: word "my" plus the field name the user asked about -- this is a
#: pattern match against text the user themselves filed, not inference.
_PROFILE_VALUE_TEMPLATE = (
    r"\bmy\s+{field}\s*(?:is|are|was|were|=|:|-)\s*(.+?)\s*(?:[.;\n]|$)"
)


def find_profile_fact(conn: sqlite3.Connection, field: str) -> Optional[str]:
    """Return the value the user filed for "my <field> is ...", if any.

    Scans stored entries (newest first) for the literal phrasing and
    returns the first captured value. ``None`` means nothing on file --
    the caller turns that into a polite "I don't have that on file".
    """
    name = re.sub(r"\s+", " ", (field or "")).strip()
    if not name:
        return None

    pattern = re.compile(
        _PROFILE_VALUE_TEMPLATE.format(field=re.escape(name)), re.IGNORECASE
    )
    rows = conn.execute(
        "SELECT title, content FROM memories "
        "WHERE is_deleted = 0 AND (content LIKE ? OR title LIKE ?) "
        "ORDER BY updated_at DESC",
        (f"%my {name}%", f"%my {name}%"),
    ).fetchall()

    for row in rows:
        haystack = f"{row['title'] or ''}\n{row['content'] or ''}"
        match = pattern.search(haystack)
        if match:
            value = match.group(1).strip().strip("\"'")
            if value:
                return value
    return None


def find_entry_by_title_or_text(
    conn: sqlite3.Connection, fragment: str
) -> Optional[Memory]:
    """Find the single entry a "when did I create X" question refers to.

    Title matches win over body matches, and shorter titles win over
    longer ones, so "the cave note" resolves to "Cave" rather than
    "Cave renovation budget" when both exist. Substring matching only.
    """
    needle = (fragment or "").strip()
    if not needle:
        return None

    like = f"%{needle}%"
    row = conn.execute(
        "SELECT * FROM memories WHERE is_deleted = 0 AND title LIKE ? "
        "ORDER BY LENGTH(title), updated_at DESC LIMIT 1",
        (like,),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM memories WHERE is_deleted = 0 AND content LIKE ? "
            "ORDER BY updated_at DESC LIMIT 1",
            (like,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_memory(row, get_tags(conn, row["id"]))
