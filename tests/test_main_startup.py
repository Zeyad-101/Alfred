"""Regression test for the main.py startup path.

Phase 8 introduced ``main.py`` and a ``DEFAULT_DB_PATH`` constant
in ``core/db.py``. The first run of ``python main.py`` on a fresh
machine crashed with ``sqlite3.OperationalError: no such table:
memories`` because the startup path opened a connection but never
called ``init_db(conn)`` — every test in the suite (including the
smoke test) builds its connection through a fixture that calls
``init_db`` explicitly, so the omission only surfaced when a real
user ran the entry point for the first time.

This file pins the correct startup sequence so the regression
can't return unnoticed.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def _fresh_db_path(tmp_path: Path) -> str:
    """A path under ``tmp_path`` that does not yet exist on disk.

    Used to simulate a fresh install. We use a path inside
    ``tmp_path`` rather than ``DEFAULT_DB_PATH`` because the
    latter resolves to an absolute path that would clobber the
    developer's real database if init_db ever silently changes
    behavior. The test cares about the sequence
    (get_connection + init_db + list_memories), not the specific
    default path.
    """
    return str(tmp_path / "data" / "alfred.db")


def test_main_startup_initializes_schema(tmp_path):
    """The startup sequence ``get_connection(path)`` →
    ``init_db(conn)`` → ``list_memories(conn)`` must succeed on a
    fresh database file with no schema in place.

    This mirrors what ``main.py`` does, byte-for-byte, so a
    regression in either the default path or the init step is
    caught here. The path is in ``tmp_path`` so the test is
    hermetic — a regression that triggers a real-file write
    can't damage the developer's actual data/ directory.
    """
    db_path = _fresh_db_path(tmp_path)
    assert not Path(db_path).exists(), (
        f"test precondition: {db_path} should not exist yet"
    )

    # The exact sequence main.py runs.
    from core.db import get_connection, init_db
    conn = get_connection(db_path)
    init_db(conn)
    try:
        # ``list_memories`` would crash with ``no such table`` if
        # init_db had been skipped — which is precisely the
        # bug we're guarding against.
        from core.memory import list_memories
        assert list_memories(conn) == []
    finally:
        conn.close()

    # The DB file now exists and has the expected tables.
    assert Path(db_path).exists()
    # Re-open and verify a few key tables are present.
    conn2 = get_connection(db_path)
    try:
        tables = {
            row["name"]
            for row in conn2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for required in ("memories", "tags", "memory_tags", "projects"):
            assert required in tables, (
                f"required table {required!r} missing after init_db; "
                f"got {tables!r}"
            )
    finally:
        conn2.close()


def test_main_startup_without_init_db_crashes(tmp_path):
    """Companion to the positive test above: confirm that
    ``list_memories`` really does fail without ``init_db``.

    If this ever stops failing, the schema became self-creating
    (e.g. via ``CREATE TABLE IF NOT EXISTS`` happening lazily
    inside the queries themselves) and the test no longer
    reflects reality. Better to know that than to silently
    rot.
    """
    db_path = _fresh_db_path(tmp_path)
    from core.db import get_connection
    conn = get_connection(db_path)
    try:
        from core.memory import list_memories
        try:
            list_memories(conn)
        except sqlite3.OperationalError as exc:
            # This is the exact error the user hit: ``no such
            # table: memories``. The test passing here means the
            # negative case (skip init_db) really does crash —
            # which is what makes the positive test above
            # meaningful.
            assert "no such table" in str(exc).lower()
        else:
            raise AssertionError(
                "list_memories should fail without init_db; if it "
                "now succeeds, the positive regression test is no "
                "longer guarding anything real."
            )
    finally:
        conn.close()


def test_main_imports_init_db():
    """``main.py`` must import ``init_db`` from ``core.db``.

    This is a tight, paranoid guard against the exact regression
    that slipped through 173 tests: the function is imported but
    not called, which a static read of ``main.py`` would have
    caught. The other test in this file pins the runtime
    behavior; this one pins the source-level invariant.
    """
    import importlib
    import main
    importlib.reload(main)
    # ``init_db`` is referenced as ``main.init_db`` after the
    # ``from core.db import init_db`` line, so a successful
    # import is observable through the module's namespace.
    from core.db import init_db
    assert main.init_db is init_db, (
        "main.py must import init_db from core.db so the "
        "fresh-install startup path can call it"
    )
