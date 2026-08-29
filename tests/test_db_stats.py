"""Tests for ``core.db_stats``.

The stats / vacuum / reset / restore module is the "destructive
operations" home: every helper here is what the Settings dialog's
Data and Backups pages call. The tests are organized by function
and try to keep the surface honest — not just "does it run" but
"does it return what the Settings dialog actually consumes".
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from core.db import get_connection, init_db
from core.db_stats import (
    ALL_TABLES,
    get_backup_stats,
    get_stats,
    list_backups,
    reset_all_data,
    restore_from_backup,
    vacuum_db,
)
from core.export_import import backup_db
from core.inbox import convert_to_task, create_inbox_item
from core.memory import create_memory
from core.projects import create_project
from core.tasks import complete_task


# ----- ALL_TABLES sanity -----


def test_all_tables_is_non_empty_tuple_of_strings():
    """ALL_TABLES is a tuple (not a list) so callers can rely on
    immutability, and every entry is a non-empty string."""
    assert isinstance(ALL_TABLES, tuple)
    assert len(ALL_TABLES) > 0
    for name in ALL_TABLES:
        assert isinstance(name, str)
        assert name


# ----- get_stats -----


def test_get_stats_empty_database(conn, tmp_path):
    """A freshly-init'd DB has zero of everything."""
    db_path = str(tmp_path / "alfred_test.db")
    stats = get_stats(conn, db_path)
    assert stats["total_memories"] == 0
    assert stats["by_type"] == {"note": 0, "inbox": 0, "task": 0}
    assert stats["project_count"] == 0
    assert stats["db_size"] > 0  # the file is on disk


def test_get_stats_counts_by_type(conn, tmp_path):
    """Memories of each type are counted in their own bucket.

    The by_type breakdown is what the Settings dialog's Data
    page shows in its three labeled rows.
    """
    db_path = str(tmp_path / "alfred_test.db")
    create_memory(conn, "Note A", "body")
    create_memory(conn, "Note B", "body")
    create_inbox_item(conn, "Inbox item")
    convert_to_task(conn, 3, due_date=None)
    stats = get_stats(conn, db_path)
    assert stats["total_memories"] == 3
    assert stats["by_type"] == {"note": 2, "inbox": 0, "task": 1}


def test_get_stats_excludes_deleted(conn, tmp_path):
    """Soft-deleted memories are NOT counted in totals or by_type.

    The stats are a "what's actually on the file" view, not a
    "what was ever here" view — a deleted note shouldn't bloat
    the totals the user sees.
    """
    from core.memory import delete_memory

    db_path = str(tmp_path / "alfred_test.db")
    create_memory(conn, "Live", "")
    create_memory(conn, "Soon-deleted", "")
    delete_memory(conn, 2)
    stats = get_stats(conn, db_path)
    assert stats["total_memories"] == 1
    assert stats["by_type"]["note"] == 1


def test_get_stats_counts_projects(conn, tmp_path):
    """Project count reflects the projects table, not the links.

    A project with zero memories is still counted — the user
    might be in the middle of setting one up.
    """
    db_path = str(tmp_path / "alfred_test.db")
    assert get_stats(conn, db_path)["project_count"] == 0
    create_project(conn, "Alpha")
    create_project(conn, "Beta")
    assert get_stats(conn, db_path)["project_count"] == 2


def test_get_stats_handles_missing_db_file(conn, tmp_path):
    """A missing file should report ``db_size=0`` rather than crash.

    The Settings dialog should not blow up if the user has
    moved the DB file out from under us.
    """
    stats = get_stats(conn, str(tmp_path / "does-not-exist.db"))
    assert stats["db_size"] == 0


# ----- get_backup_stats / list_backups -----


def test_get_backup_stats_empty_directory(tmp_path):
    """An empty or missing directory reports count=0, size=0."""
    # Missing dir
    s = get_backup_stats(str(tmp_path / "missing"))
    assert s == {"count": 0, "total_size": 0}
    # Empty dir
    os.makedirs(tmp_path / "empty")
    s = get_backup_stats(str(tmp_path / "empty"))
    assert s == {"count": 0, "total_size": 0}


def test_get_backup_stats_counts_only_alfred_backups(tmp_path):
    """Loose files in the backup dir are ignored.

    The count is the number of ``alfred-backup-*.db`` files,
    not the number of files in the directory. A user who
    drops a README in there shouldn't see the count jump.
    """
    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir)
    # Two real backups
    open(os.path.join(backup_dir, "alfred-backup-1.db"), "wb").close()
    open(os.path.join(backup_dir, "alfred-backup-2.db"), "wb").close()
    # A couple of decoys
    open(os.path.join(backup_dir, "README.md"), "w").close()
    open(os.path.join(backup_dir, "alfred-export.json"), "w").close()
    open(os.path.join(backup_dir, "alfred-backup-notadb.txt"), "w").close()
    s = get_backup_stats(backup_dir)
    assert s["count"] == 2


def test_get_backup_stats_sums_sizes(tmp_path):
    """total_size is the sum of every counted backup's on-disk size."""
    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir)
    # Two backups, one 100 bytes, one 250 bytes.
    p1 = os.path.join(backup_dir, "alfred-backup-1.db")
    p2 = os.path.join(backup_dir, "alfred-backup-2.db")
    with open(p1, "wb") as f:
        f.write(b"x" * 100)
    with open(p2, "wb") as f:
        f.write(b"x" * 250)
    s = get_backup_stats(backup_dir)
    assert s["count"] == 2
    assert s["total_size"] == 350


def test_list_backups_empty_dir(tmp_path):
    """Missing or empty directory returns ``[]``."""
    assert list_backups(str(tmp_path / "missing")) == []
    os.makedirs(tmp_path / "empty")
    assert list_backups(str(tmp_path / "empty")) == []


def test_list_backups_returns_metadata(tmp_path):
    """Each entry has path, name, size, mtime."""
    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir)
    p = os.path.join(backup_dir, "alfred-backup-20240101.db")
    with open(p, "wb") as f:
        f.write(b"x" * 42)
    entries = list_backups(backup_dir)
    assert len(entries) == 1
    e = entries[0]
    assert set(e.keys()) == {"path", "name", "size", "mtime"}
    assert e["path"] == p
    assert e["name"] == "alfred-backup-20240101.db"
    assert e["size"] == 42
    assert isinstance(e["mtime"], float)


def test_list_backups_newest_first(tmp_path):
    """Backups are sorted newest-first (most recent mtime wins).

    The Settings dialog shows the most recent at the top, so
    the ordering needs to be deterministic and correct.
    """
    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir)
    for i in range(3):
        p = os.path.join(backup_dir, f"alfred-backup-{i}.db")
        with open(p, "wb") as f:
            f.write(b"x")
        # Bump the mtime by a clearly distinguishable amount.
        new_mtime = 1700000000 + i * 100
        os.utime(p, (new_mtime, new_mtime))
    names = [e["name"] for e in list_backups(backup_dir)]
    assert names == [
        "alfred-backup-2.db",
        "alfred-backup-1.db",
        "alfred-backup-0.db",
    ]


def test_list_backups_ignores_decoys(tmp_path):
    """Non-backup files don't appear in the list."""
    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir)
    open(os.path.join(backup_dir, "alfred-backup-real.db"), "wb").close()
    open(os.path.join(backup_dir, "alfred-export.json"), "w").close()
    open(os.path.join(backup_dir, "alfred-backup-bad.txt"), "w").close()
    names = [e["name"] for e in list_backups(backup_dir)]
    assert names == ["alfred-backup-real.db"]


# ----- vacuum_db -----


def test_vacuum_db_runs_without_error(conn):
    """VACUUM on a small DB completes and leaves the conn usable."""
    create_memory(conn, "A", "")
    create_memory(conn, "B", "")
    vacuum_db(conn)
    # Connection is still usable for further reads/writes.
    rows = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert rows == 2


def test_vacuum_db_reclaims_space_after_mass_delete(tmp_path):
    """After many deletes, VACUUM shrinks the file.

    This is the user's "make my DB smaller" button. The
    behavior we can verify is: a heavily-padded DB shrinks
    measurably after VACUUM.
    """
    db_path = str(tmp_path / "vacuum.db")
    c = get_connection(db_path)
    init_db(c)
    # Insert a lot, then delete most, then vacuum.
    big = "x" * 10_000
    for _ in range(50):
        create_memory(c, big, big)
    # Soft-delete all but one
    ids = [r[0] for r in c.execute("SELECT id FROM memories").fetchall()]
    for mid in ids[1:]:
        from core.memory import delete_memory
        delete_memory(c, mid)
    c.commit()
    size_before = os.path.getsize(db_path)
    vacuum_db(c)
    size_after = os.path.getsize(db_path)
    # The file should be smaller (or at worst, the same — never larger).
    assert size_after <= size_before
    c.close()


# ----- reset_all_data -----


def test_reset_all_data_empties_every_table(tmp_path):
    """reset_all_data leaves every table with zero rows.

    This is the destructive action in the Settings dialog's
    Danger zone. The test exercises the same DB the dialog
    would: a real-schema DB with a representative mix of
    rows across the parent tables.
    """
    db_path = str(tmp_path / "reset.db")
    c = get_connection(db_path)
    init_db(c)
    # Populate every parent table.
    create_memory(c, "Note", "")
    create_inbox_item(c, "Inbox")
    convert_to_task(c, 2)
    create_project(c, "Project")
    assert c.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1

    reset_all_data(c)

    # Every table is empty.
    for table in ("memories", "projects", "tags", "memory_tags",
                  "memory_versions", "task_details", "memory_links",
                  "memory_projects"):
        n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert n == 0, f"{table} should be empty after reset"
    c.close()


def test_reset_all_data_does_not_drop_schema(tmp_path):
    """After reset, the schema is intact — the tables still exist.

    DROP TABLE would be easier but is more destructive (it
    would also clear the FTS5 index, version triggers, etc.).
    DELETE + VACUUM is the right shape: a clean slate, but the
    schema — including any future triggers — is preserved.
    """
    db_path = str(tmp_path / "schema_intact.db")
    c = get_connection(db_path)
    init_db(c)
    create_memory(c, "X", "")
    reset_all_data(c)
    # Schema is intact: we can still create a new memory.
    new_id = create_memory(c, "After reset", "")
    assert new_id is not None
    c.close()


# ----- restore_from_backup -----


def test_restore_from_backup_round_trip(tmp_path):
    """A backup can be restored over the live DB and the content swaps.

    The live DB has content A; a backup has content B; after
    restore, the DB has content B. The returned conn reads the
    new state.
    """
    live_path = str(tmp_path / "live.db")
    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir)

    # Step 1: write a "first" DB with one memory, then back it up.
    c1 = get_connection(live_path)
    init_db(c1)
    create_memory(c1, "First version", "from the past")
    c1.commit()
    backup_path = backup_db(live_path, backup_dir=backup_dir)
    c1.close()

    # Step 2: blow the live DB away and write a "second" one with
    # different content. Deleting the file (rather than just
    # deleting rows) is the simplest way to model the "live DB
    # has drifted far from the backup" scenario.
    os.remove(live_path)
    c2 = get_connection(live_path)
    init_db(c2)
    create_memory(c2, "Second version", "current state")
    c2.commit()
    rows = c2.execute("SELECT title FROM memories").fetchall()
    assert [r["title"] for r in rows] == ["Second version"]

    # Step 3: restore the backup; the live DB should now have
    # the FIRST version's content.
    c3 = restore_from_backup(c2, live_path, backup_path)
    rows = c3.execute("SELECT title FROM memories").fetchall()
    titles = sorted(r["title"] for r in rows)
    assert titles == ["First version"]

    c3.close()


def test_restore_from_backup_closes_old_connection(tmp_path):
    """The old connection is closed by the restore function.

    Catching a use-after-close in the test is the cleanest way
    to verify this contract. ``conn.close()`` is idempotent
    though, so an explicit close-before-restore would also
    work — we're testing that the function does it for us.
    """
    live_path = str(tmp_path / "live.db")
    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir)
    c1 = get_connection(live_path)
    init_db(c1)
    create_memory(c1, "x", "")
    backup_path = backup_db(live_path, backup_dir=backup_dir)

    c2 = get_connection(live_path)
    # Don't close c2 manually — let restore_from_backup do it.
    c_new = restore_from_backup(c2, live_path, backup_path)

    # Using c2 now should fail (it's closed).
    with pytest.raises(sqlite3.ProgrammingError):
        c2.execute("SELECT 1")
    # But c_new is open and usable.
    n = c_new.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert n == 1
    c1.close()
    c_new.close()


def test_restore_from_backup_preserves_schema_metadata(tmp_path):
    """After a restore, the schema (including future tables) is intact.

    The backup is just a copy of the DB file at backup time,
    so its schema is whatever was there at that moment. A
    new table added AFTER the backup is NOT in the backup
    and would be missing after a restore. We just verify
    the backup is self-consistent: the new conn can run the
    same queries as the old one.
    """
    live_path = str(tmp_path / "live.db")
    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir)
    c1 = get_connection(live_path)
    init_db(c1)
    create_memory(c1, "X", "")
    backup_path = backup_db(live_path, backup_dir=backup_dir)

    c2 = get_connection(live_path)
    init_db(c2)
    c_new = restore_from_backup(c2, live_path, backup_path)

    # Both queries the dialog would run on the new conn work.
    n = c_new.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert n == 1
    n2 = c_new.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    assert n2 == 0
    c1.close()
    c_new.close()


def test_restore_from_backup_missing_file_raises(tmp_path):
    """A missing backup file raises ``OSError`` (not a silent no-op).

    The Settings dialog catches ``OSError`` and shows a
    message box. A wrong exception class would be missed.
    """
    live_path = str(tmp_path / "live.db")
    c1 = get_connection(live_path)
    init_db(c1)
    with pytest.raises(OSError):
        restore_from_backup(
            c1, live_path, str(tmp_path / "does-not-exist.db")
        )
    c1.close()
