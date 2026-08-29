"""Database statistics and destructive operations for Alfred.

This module is the home for the queries the Settings dialog uses
on its "Data" page: counts, sizes, vacuum, reset, and the
restore-from-backup swap. None of these are high-frequency
operations -- the stats are refreshed on demand, the destructive
ones are gated behind the danger-zone UI -- so the readability
of plain SQL wins over micro-optimization.
"""
from __future__ import annotations

import os
import shutil
import sqlite3

from core.db import get_connection


# Every table the schema creates. Used by :func:`reset_all_data`
# to decide which tables to clear. The order here doesn't matter
# (the FK cascades handle the parent->child relationships); we
# just need a complete list so a new table added in a future
# schema version is only missed if it's not in this tuple.
ALL_TABLES: tuple[str, ...] = (
    "memory_links",
    "memory_projects",
    "task_details",
    "memory_versions",
    "memory_tags",
    "memories",
    "tags",
    "projects",
)


def get_stats(conn: sqlite3.Connection, db_path: str) -> dict:
    """Return a dict of summary stats for the current DB state.

    Output keys::

        {
          "total_memories": <int>,     # non-deleted
          "by_type": {                 # counts per type, non-deleted
            "note": <int>,
            "inbox": <int>,
            "task": <int>,
          },
          "project_count": <int>,
          "db_size": <int>,            # bytes
        }

    ``db_size`` is the on-disk file size from ``os.path.getsize``;
    it includes whatever SQLite has flushed to disk, not the
    in-memory working set. A small WAL delta may not be reflected
    here immediately after a write -- that's fine, this is a
    rough "is my DB getting big?" indicator, not a precise
    accounting tool.
    """
    total = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE is_deleted = 0"
    ).fetchone()[0]

    by_type: dict[str, int] = {}
    for type_name in ("note", "inbox", "task"):
        n = conn.execute(
            "SELECT COUNT(*) FROM memories "
            "WHERE type = ? AND is_deleted = 0",
            (type_name,),
        ).fetchone()[0]
        by_type[type_name] = n

    project_count = conn.execute(
        "SELECT COUNT(*) FROM projects"
    ).fetchone()[0]

    try:
        db_size = os.path.getsize(db_path)
    except OSError:
        # If the file vanished or is unreadable, report 0 rather
        # than crashing the dialog.
        db_size = 0

    return {
        "total_memories": total,
        "by_type": by_type,
        "project_count": project_count,
        "db_size": db_size,
    }


def get_backup_stats(backup_dir: str) -> dict:
    """Return ``{"count": <int>, "total_size": <int>}`` for ``backup_dir``.

    Missing or empty directory is reported as ``count=0``,
    ``total_size=0`` -- the Settings UI's Backups page shows
    zeros rather than a "folder not found" error, since the
    folder is created on demand the first time the user takes
    a backup. Only the ``alfred-backup-*.db`` files are counted;
    loose files in the same folder (e.g. an old test artifact)
    are ignored so the count is meaningful.
    """
    count = 0
    total_size = 0
    if os.path.isdir(backup_dir):
        for name in os.listdir(backup_dir):
            if not name.startswith("alfred-backup-") or not name.endswith(
                ".db"
            ):
                continue
            path = os.path.join(backup_dir, name)
            if not os.path.isfile(path):
                continue
            count += 1
            try:
                total_size += os.path.getsize(path)
            except OSError:
                pass
    return {"count": count, "total_size": total_size}


def list_backups(backup_dir: str) -> list[dict]:
    """Return one dict per ``alfred-backup-*.db`` file in ``backup_dir``.

    Each dict has ``{"path", "name", "size", "mtime"}`` keys,
    newest-first. Missing or empty directory returns ``[]``.
    Used by the Settings dialog's Backups page to populate the
    list with the actual files on disk.
    """
    if not os.path.isdir(backup_dir):
        return []
    out: list[dict] = []
    for name in os.listdir(backup_dir):
        if not name.startswith("alfred-backup-") or not name.endswith(".db"):
            continue
        path = os.path.join(backup_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        out.append(
            {
                "path": path,
                "name": name,
                "size": st.st_size,
                "mtime": st.st_mtime,
            }
        )
    # Newest first -- the user almost always wants the most recent
    # backup at the top of the list.
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


def vacuum_db(conn: sqlite3.Connection) -> None:
    """Run ``VACUUM`` on the connection, then commit.

    VACUUM rebuilds the database file, packing it back to its
    minimum size after large deletes (e.g. after ``reset_all_data``
    or after emptying the trash on a long-lived DB). It can take
    a moment on big files; the Settings dialog confirms with the
    user before invoking it.

    VACUUM implicitly commits the current transaction, but we
    commit again afterwards for clarity (and so a subsequent
    caller can rely on the connection being in a clean state).
    """
    conn.execute("VACUUM")
    conn.commit()


def reset_all_data(conn: sqlite3.Connection) -> None:
    """Delete every row from every table, then VACUUM.

    We DELETE from every table in the schema. The FK
    ``ON DELETE CASCADE`` clauses on the child tables would
    handle the cleanup if we only deleted from the parents
    (``memories``, ``tags``, ``projects``), but deleting from
    every table explicitly is robust against a future table
    being added without a parent-side cascade, and the cost is
    negligible for a personal-scale DB.

    Order: child tables first, then parents -- this matters when
    ``foreign_keys = ON`` because DELETE on a parent row with a
    still-present child would fail under strict FK. Reversing
    the order is safe in either case (cascade or no cascade),
    so the safer order is to clear children first.

    Followed by ``VACUUM`` to reclaim the freed space -- without
    it, the file stays at its old size even though every row is
    gone, which would mislead the user looking at the Data
    page's "DB size" stat.
    """
    children = (
        "memory_links",
        "memory_projects",
        "task_details",
        "memory_versions",
        "memory_tags",
    )
    parents = ("memories", "tags", "projects")
    for table in children:
        conn.execute(f"DELETE FROM {table}")
    for table in parents:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.execute("VACUUM")
    conn.commit()


def restore_from_backup(
    conn: sqlite3.Connection, db_path: str, backup_file: str
) -> sqlite3.Connection:
    """Replace ``db_path`` with ``backup_file`` and return a fresh conn.

    The passed connection is closed -- it would be unsafe to keep
    it open pointing at a database file that's about to be
    overwritten underneath it. The function then copies the
    backup file over the live DB path (using ``shutil.copy2`` to
    preserve metadata) and opens a new connection to the now-
    restored file. The returned connection is what the caller
    should swap in for the old one.

    The caller's responsibilities:
        1. ``conn.close()`` is called here; the caller's reference
           is now invalid.
        2. The returned connection should replace the caller's
           stored reference.
        3. The caller should re-``init_db``-free the new connection
           (the schema is already there because it was in the
           backup).
        4. The caller should refresh any UI that's reading from
           the old connection.
    """
    # Close the live connection before clobbering the file
    # underneath it. sqlite3 close is a no-op if the connection
    # is already closed, so a defensive close in the caller is
    # safe.
    try:
        conn.close()
    except Exception:
        pass
    shutil.copy2(backup_file, db_path)
    return get_connection(db_path)
