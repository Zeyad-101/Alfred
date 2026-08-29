"""Task operations: complete/uncomplete, due-date, and bucketed listing."""
from __future__ import annotations

import sqlite3
from datetime import date

from core.db import now as _now
from core.memory import Memory, _memories_from_rows


# Sentinel error class for callers that want to distinguish "you called
# this on the wrong type of memory" from a successful no-op.
class NotATaskError(Exception):
    """Raised when a task operation is called on a non-task memory."""


def _ensure_task(conn: sqlite3.Connection, memory_id: int) -> bool:
    """Return True iff ``memory_id`` is a live, non-deleted task.

    Centralizes the type+deleted check so every task operation enforces
    the same precondition. Returning a bool (rather than raising) lets
    callers that prefer silent no-ops to use it as a guard.
    """
    row = conn.execute(
        "SELECT type, is_deleted FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    if row is None:
        return False
    return row["type"] == "task" and not bool(row["is_deleted"])


def complete_task(conn: sqlite3.Connection, memory_id: int) -> None:
    """Mark ``memory_id`` complete by stamping ``task_details.completed_at``.

    No-op (no error, no row change) if the memory is gone, soft-deleted,
    or not a task -- the editor's "Mark Complete" button should never
    surface an error dialog for these cases.
    """
    if not _ensure_task(conn, memory_id):
        return
    conn.execute(
        "INSERT INTO task_details(memory_id, due_date, completed_at) "
        "VALUES (?, NULL, ?) "
        "ON CONFLICT(memory_id) DO UPDATE SET completed_at = excluded.completed_at",
        (memory_id, _now()),
    )
    conn.commit()


def uncomplete_task(conn: sqlite3.Connection, memory_id: int) -> None:
    """Clear the completion stamp. Same no-op-on-mismatch rule as ``complete_task``."""
    if not _ensure_task(conn, memory_id):
        return
    # A task with no task_details row yet is already effectively
    # uncomplete; nothing to do. Otherwise flip the stamp.
    row = conn.execute(
        "SELECT 1 FROM task_details WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    if row is None:
        return
    conn.execute(
        "UPDATE task_details SET completed_at = NULL WHERE memory_id = ?",
        (memory_id,),
    )
    conn.commit()


def set_due_date(
    conn: sqlite3.Connection, memory_id: int, due_date: str | None
) -> None:
    """Set or clear a task's due date. No-op on non-tasks.

    Passing ``None`` clears the due date (the row stays -- it's the
    task's home, complete-with-no-due-date and pending-with-no-due-date
    are both valid states).
    """
    if not _ensure_task(conn, memory_id):
        return
    conn.execute(
        "INSERT INTO task_details(memory_id, due_date, completed_at) "
        "VALUES (?, ?, NULL) "
        "ON CONFLICT(memory_id) DO UPDATE SET due_date = excluded.due_date",
        (memory_id, due_date),
    )
    conn.commit()


# ---------- bucketed listing ----------


# Three user-facing buckets, plus two internal ones.
#
# ``today`` and ``upcoming`` between them cover every incomplete task
# exactly once -- overdue work is folded into Today (it is what you have
# to deal with now, not a category of its own) and undated work sits in
# Upcoming (it is real, but it isn't due). That partition is what lets
# callers wanting "all open tasks" simply add the two.
#
# ``due_on`` is the exact-date query the old ``today`` used to be. It is
# not a display bucket: the assistant needs it to answer "what's due
# tomorrow" without dragging today's and every overdue task along.
_VALID_BUCKETS = ("today", "upcoming", "completed", "due_on", "all")


def list_tasks(
    conn: sqlite3.Connection,
    bucket: str,
    reference_date: date | None = None,
) -> list[tuple[Memory, str | None, str | None]]:
    """Return (memory, due_date, completed_at) tuples for the given bucket.

    ``reference_date`` defaults to today (local) and exists so tests can
    freeze "what is today" without monkey-patching the system clock. All
    bucketing compares against this calendar date, not a datetime -- a
    task due "today" stays in Today all day regardless of time.
    """
    if bucket not in _VALID_BUCKETS:
        raise ValueError(
            f"list_tasks: bucket must be one of {_VALID_BUCKETS}, got {bucket!r}"
        )
    today = (reference_date or date.today()).isoformat()

    # Every bucket filters on type='task' + not deleted at minimum.
    # Each bucket adds the relevant completion / due-date predicate.
    base = (
        "FROM memories m "
        "LEFT JOIN task_details td ON td.memory_id = m.id "
        "WHERE m.type = 'task' AND m.is_deleted = 0"
    )

    if bucket == "today":
        # Due today *or* earlier. Overdue tasks are not a separate
        # category -- they are today's problem -- so they sort to the top
        # of Today by their own due date. Undated tasks are excluded:
        # they have no due date to be late against.
        where = " AND td.completed_at IS NULL AND " \
                "td.due_date IS NOT NULL AND td.due_date <= ?"
        order = "ORDER BY td.due_date ASC, m.updated_at DESC"
        params: tuple[object, ...] = (today,)
    elif bucket == "upcoming":
        # Everything else that is still open: dated in the future, or
        # never dated at all. Undated tasks sort last -- a task with a
        # date is more actionable than one without.
        where = " AND td.completed_at IS NULL AND " \
                "(td.due_date IS NULL OR td.due_date > ?)"
        order = "ORDER BY (td.due_date IS NULL), td.due_date ASC, m.updated_at DESC"
        params = (today,)
    elif bucket == "due_on":
        # Exact-date match against ``reference_date``. Used for "what's
        # due tomorrow", where the caller passes tomorrow as the
        # reference and wants that day alone.
        where = " AND td.completed_at IS NULL AND td.due_date = ?"
        order = "ORDER BY td.due_date ASC, m.updated_at DESC"
        params = (today,)
    elif bucket == "completed":
        where = " AND td.completed_at IS NOT NULL"
        # Newest completion first; tiebreak on updated_at for stability.
        order = "ORDER BY td.completed_at DESC, m.updated_at DESC"
        params = ()
    else:  # 'all'
        where = ""
        # Not-completed first (by due_date, undated last), then completed
        # newest-first. Keeps the most actionable tasks near the top.
        order = (
            "ORDER BY (td.completed_at IS NOT NULL), "
            "(td.due_date IS NULL), td.due_date ASC, "
            "td.completed_at DESC, m.updated_at DESC"
        )
        params = ()

    rows = conn.execute(
        f"SELECT m.*, td.due_date AS _due, td.completed_at AS _completed {base}{where} {order}",
        params,
    ).fetchall()

    # Hydrate tags via the existing memory path, then strip the JOIN-only
    # columns and zip the due/completed values alongside.
    memories = _memories_from_rows(conn, rows)
    return [
        (m, row["_due"], row["_completed"])
        for m, row in zip(memories, rows, strict=True)
    ]
