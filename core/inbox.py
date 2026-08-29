"""Inbox workflow: frictionless single-field capture, organize or convert."""
from __future__ import annotations

import sqlite3

from core.db import now as _now
from core.memory import Memory, _memories_from_rows, create_memory, get_memory


_INBOX_TITLE_MAX = 50


def derive_title(content: str) -> str:
    """Build a title from the first non-empty line of ``content``.

    Title length is capped at ``_INBOX_TITLE_MAX`` characters so a runaway
    paste doesn't blow out the list-row width. Truncation is plain (no
    ellipsis) -- a precise prefix is more useful for scanning than a
    decorative one.
    """
    first_line = content.split("\n", 1)[0].strip()
    if len(first_line) > _INBOX_TITLE_MAX:
        return first_line[:_INBOX_TITLE_MAX]
    return first_line


def create_inbox_item(conn: sqlite3.Connection, content: str) -> int:
    """Capture ``content`` as a single-field inbox entry.

    The title is derived from the first line of ``content`` (truncated to
    ``_INBOX_TITLE_MAX`` chars if needed). The caller passes only the body
    because the whole point of an inbox is frictionless capture -- a
    separate title prompt would defeat that.
    """
    title = derive_title(content)
    return create_memory(conn, title=title, content=content, type="inbox")


def list_inbox(conn: sqlite3.Connection) -> list[Memory]:
    """Return all live inbox items, oldest first.

    Ordering by ``created_at`` ASC surfaces the backlog at the top of the
    list -- the user opens the inbox to deal with what's been sitting
    there, not to see the most recent capture.
    """
    rows = conn.execute(
        "SELECT * FROM memories "
        "WHERE type = 'inbox' AND is_deleted = 0 "
        "ORDER BY created_at ASC"
    ).fetchall()
    return _memories_from_rows(conn, rows)


def organize_to_note(conn: sqlite3.Connection, memory_id: int) -> None:
    """Promote an inbox item to a regular note.

    The title is left as-is unless it's empty/whitespace, in which case
    it's derived from the first line of content (same rule as
    ``create_inbox_item``). An empty-title row would otherwise render
    blank in the list and be unsearchable, so this backfill is a
    safety net for callers that bypassed ``create_inbox_item``.
    """
    memory = get_memory(conn, memory_id)
    if memory is None:
        return

    if not memory.title.strip():
        new_title = derive_title(memory.content)
    else:
        new_title = memory.title

    conn.execute(
        "UPDATE memories SET type = 'note', title = ?, updated_at = ? WHERE id = ?",
        (new_title, _now(), memory_id),
    )
    conn.commit()


def convert_to_task(
    conn: sqlite3.Connection, memory_id: int, due_date: str | None = None
) -> None:
    """Promote an inbox item to a task.

    The ``task_details`` row is upserted (created if missing, updated if
    present), so calling this twice -- e.g. after a due-date change
    through the editor -- is safe and idempotent. ``due_date`` is a
    string in whatever format the caller chose (we store it as TEXT and
    compare with string ordering, so callers should use ISO ``YYYY-MM-DD``
    for correct "today / upcoming" bucketing).
    """
    # Flip the type first; if the memory doesn't exist, the UPDATE is a
    # no-op and we don't need to do anything to task_details either.
    cur = conn.execute(
        "UPDATE memories SET type = 'task', updated_at = ? WHERE id = ?",
        (_now(), memory_id),
    )
    if cur.rowcount == 0:
        return

    # Upsert: SQLite's ON CONFLICT(memory_id) targets the PRIMARY KEY
    # on task_details, so a re-convert (or a re-call of this function)
    # updates the existing row in place.
    conn.execute(
        "INSERT INTO task_details(memory_id, due_date, completed_at) "
        "VALUES (?, ?, NULL) "
        "ON CONFLICT(memory_id) DO UPDATE SET due_date = excluded.due_date",
        (memory_id, due_date),
    )
    conn.commit()
