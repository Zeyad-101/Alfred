"""Tests for FTS5 search behavior and the FTS sync triggers."""
from __future__ import annotations


def test_exact_match(conn):
    from core.memory import create_memory
    from core.search import search

    create_memory(conn, "Python tip", "Use list comprehensions")
    results = search(conn, "comprehensions")
    assert len(results) == 1
    assert "comprehensions" in results[0].content


def test_partial_word(conn):
    """Searching for a single word that is part of a longer document works."""
    from core.memory import create_memory
    from core.search import search

    create_memory(conn, "Languages", "Python is a programming language")
    results = search(conn, "programming")
    assert len(results) == 1


def test_no_results(conn):
    from core.memory import create_memory
    from core.search import search

    create_memory(conn, "Note", "Hello world")
    assert search(conn, "kangaroo") == []


def test_empty_query_returns_all(conn):
    from core.memory import create_memory
    from core.search import search

    create_memory(conn, "A", "alpha")
    create_memory(conn, "B", "beta")
    create_memory(conn, "C", "gamma")
    results = search(conn, "")
    assert {m.title for m in results} == {"A", "B", "C"}


def test_whitespace_only_query_returns_all(conn):
    from core.memory import create_memory
    from core.search import search

    create_memory(conn, "Only", "x")
    assert len(search(conn, "   ")) == 1


def test_special_chars_do_not_crash(conn):
    """FTS5-special characters in raw input must not raise."""
    from core.memory import create_memory
    from core.search import search

    create_memory(conn, "Note", "Some content here for testing")
    queries = [
        '"hello"',          # unmatched quote
        "*stuff*",          # wildcards
        "hello*world",      # wildcard mid-token
        "(group)",          # grouping
        "hello:world",      # column filter syntax
        "^boost",           # boost marker
        "-negate",          # negation
        "must+have",        # must-include
        "MAX98357A's amp",  # user example from the spec
    ]
    for q in queries:
        results = search(conn, q)  # must not raise
        assert isinstance(results, list)


def test_fts_trigger_sync_on_update(conn):
    """Editing content must immediately change what search() can find.

    This proves the AFTER UPDATE trigger on ``memories`` is keeping
    ``memories_fts`` in sync — without it, the old term would still
    be findable and the new one would not.
    """
    from core.memory import create_memory, update_memory
    from core.search import search

    mid = create_memory(conn, "Fruit", "I like apples and bananas")
    assert len(search(conn, "apples")) == 1
    assert len(search(conn, "bananas")) == 1

    update_memory(conn, mid, content="Now I prefer cherries and dates")
    assert search(conn, "apples") == []
    assert search(conn, "bananas") == []
    assert len(search(conn, "cherries")) == 1
    assert len(search(conn, "dates")) == 1


def test_soft_deleted_excluded_from_search_by_default(conn):
    from core.memory import create_memory, delete_memory
    from core.search import search

    mid = create_memory(conn, "Secret", "Hidden treasure map")
    assert len(search(conn, "treasure")) == 1
    delete_memory(conn, mid)
    assert search(conn, "treasure") == []


def test_soft_deleted_included_in_search_when_requested(conn):
    from core.memory import create_memory, delete_memory
    from core.search import search

    mid = create_memory(conn, "Secret", "Hidden treasure map")
    delete_memory(conn, mid)
    assert len(search(conn, "treasure", include_deleted=True)) == 1


def test_search_includes_tags_in_returned_memory(conn):
    """search() returns Memory objects with their tags populated.

    Note: FTS5 only indexes title+content, so we search for a word that is
    actually in the indexed text — not for the tag name itself.
    """
    from core.memory import create_memory
    from core.search import search

    create_memory(conn, "T", "findable content", tags=["alpha", "beta"])
    results = search(conn, "findable")
    assert len(results) == 1
    assert results[0].tags == ["alpha", "beta"]


def test_last_token_prefix_match(conn):
    """The LAST token gets a prefix wildcard so mid-typing search works.

    The motivating case: a memory contains the single token
    ``Max98357A`` (FTS5's default unicode61 tokenizer doesn't split
    letters from digits, so this is one token, not two). Typing just
    ``Max`` should still find it, because ``Max`` is the token the user
    is actively typing.
    """
    from core.memory import create_memory
    from core.search import search

    create_memory(conn, "Amp notes", "Hookup for Max98357A amplifier chip")
    results = search(conn, "Max")
    assert len(results) == 1
    assert "Max98357A" in results[0].content


def test_prefix_only_applies_to_last_token(conn):
    """Earlier tokens stay exact-match; only the LAST gets a prefix.

    A query of ``"esp32 Max"`` must require both an exact ``esp32``
    token AND a ``Max`` prefix somewhere in the document. A memory
    that contains ``esp32`` but no ``Max``-prefixed token must NOT
    match — otherwise we'd be silently doing prefix matching on
    non-last tokens too, which would be wrong (and surprising — the
    user already "finished typing" those words).
    """
    from core.memory import create_memory
    from core.search import search

    create_memory(conn, "Both", "esp32 driver for Max98357A amplifier")
    create_memory(conn, "Only esp32", "esp32 dev board notes, no amp yet")

    results = search(conn, "esp32 Max")
    assert len(results) == 1
    assert results[0].title == "Both"


def test_unsafe_last_token_falls_back_to_quoted(conn):
    """If the last token has non-alnum characters, it is quoted (no prefix).

    We can't safely combine a phrase delimiter with a ``*`` wildcard in
    FTS5, so an unsafe last token simply gets the old behavior: a
    literal-phrase match, no prefix expansion. The query must still not
    crash.
    """
    from core.memory import create_memory
    from core.search import search

    create_memory(conn, "Note", "Some content here for testing")
    # All of these have non-alnum in the last token, so they all become
    # quoted. They should return a list (possibly empty) without error.
    for q in ["foo bar-baz", "foo bar.baz", "foo bar/baz", "foo bar baz'"]:
        results = search(conn, q)
        assert isinstance(results, list)
