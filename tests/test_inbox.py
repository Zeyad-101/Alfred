"""Tests for inbox capture, list, and conversion operations."""
from __future__ import annotations


# ---------- create_inbox_item ----------


def test_create_inbox_item_uses_short_first_line_as_title(conn):
    from core.inbox import create_inbox_item
    from core.memory import get_memory

    mid = create_inbox_item(conn, "Buy milk")
    m = get_memory(conn, mid)
    assert m.type == "inbox"
    assert m.title == "Buy milk"
    assert m.content == "Buy milk"


def test_create_inbox_item_uses_first_line_only(conn):
    """Title comes from the first line; subsequent lines live in content only."""
    from core.inbox import create_inbox_item
    from core.memory import get_memory

    mid = create_inbox_item(conn, "First line\nSecond line\nThird")
    m = get_memory(conn, mid)
    assert m.title == "First line"
    assert m.content == "First line\nSecond line\nThird"


def test_create_inbox_item_truncates_long_title(conn):
    """A first line longer than 50 chars gets truncated (no ellipsis)."""
    from core.inbox import create_inbox_item
    from core.memory import get_memory

    long_line = "x" * 200
    mid = create_inbox_item(conn, long_line)
    m = get_memory(conn, mid)
    assert m.title == "x" * 50
    assert m.content == long_line  # content is the full original


def test_create_inbox_item_strips_leading_whitespace_in_title(conn):
    """Leading/trailing whitespace on the first line doesn't end up in the title."""
    from core.inbox import create_inbox_item
    from core.memory import get_memory

    mid = create_inbox_item(conn, "   spaced out   \nbody")
    m = get_memory(conn, mid)
    assert m.title == "spaced out"


# ---------- list_inbox ----------


def test_list_inbox_excludes_other_types(conn):
    from core.inbox import create_inbox_item, list_inbox
    from core.memory import create_memory

    create_inbox_item(conn, "A")
    create_memory(conn, "Note", "x", type="note")
    create_memory(conn, "Task title", "x", type="task")
    items = list_inbox(conn)
    assert len(items) == 1
    assert items[0].title == "A"
    assert items[0].type == "inbox"


def test_list_inbox_orders_oldest_first(conn):
    from core.inbox import create_inbox_item, list_inbox

    create_inbox_item(conn, "first")
    create_inbox_item(conn, "second")
    create_inbox_item(conn, "third")
    items = list_inbox(conn)
    assert [m.title for m in items] == ["first", "second", "third"]


def test_list_inbox_excludes_soft_deleted(conn):
    from core.inbox import create_inbox_item, list_inbox
    from core.memory import delete_memory

    a = create_inbox_item(conn, "keep me")
    b = create_inbox_item(conn, "delete me")
    delete_memory(conn, b)
    items = list_inbox(conn)
    assert [m.id for m in items] == [a]


def test_list_inbox_empty_when_no_inbox_items(conn):
    from core.inbox import list_inbox
    from core.memory import create_memory

    create_memory(conn, "Note", "x")
    assert list_inbox(conn) == []


# ---------- organize_to_note ----------


def test_organize_to_note_changes_type(conn):
    from core.inbox import create_inbox_item, organize_to_note
    from core.memory import get_memory, list_memories

    mid = create_inbox_item(conn, "Some body content")
    organize_to_note(conn, mid)
    m = get_memory(conn, mid)
    assert m.type == "note"
    # The now-note should appear in the regular list and disappear from
    # the inbox listing.
    assert any(x.id == mid for x in list_memories(conn))
    from core.inbox import list_inbox
    assert list_inbox(conn) == []


def test_organize_to_note_preserves_existing_title(conn):
    from core.inbox import create_inbox_item, organize_to_note
    from core.memory import get_memory

    # create_inbox_item already set a title from the first line; organize
    # must not overwrite it.
    mid = create_inbox_item(conn, "My title\nWith body")
    organize_to_note(conn, mid)
    assert get_memory(conn, mid).title == "My title"


def test_organize_to_note_derives_title_when_empty(conn):
    """A title that is empty/whitespace gets derived from content's first line."""
    from core.inbox import organize_to_note
    from core.memory import create_memory, get_memory

    # Bypass create_inbox_item so we can set an empty title directly —
    # create_inbox_item would never produce one.
    mid = create_memory(conn, "", "Real first line\nMore", type="inbox")
    organize_to_note(conn, mid)
    assert get_memory(conn, mid).title == "Real first line"
    assert get_memory(conn, mid).type == "note"


def test_organize_to_note_derives_title_when_whitespace(conn):
    from core.inbox import organize_to_note
    from core.memory import create_memory, get_memory

    mid = create_memory(conn, "   ", "Derived title", type="inbox")
    organize_to_note(conn, mid)
    assert get_memory(conn, mid).title == "Derived title"


def test_organize_to_note_unknown_id_is_noop(conn):
    from core.inbox import organize_to_note

    # No exception, no effect.
    organize_to_note(conn, 99999)


# ---------- convert_to_task ----------


def test_convert_to_task_changes_type_and_creates_details(conn):
    from core.inbox import convert_to_task, create_inbox_item
    from core.memory import get_memory

    mid = create_inbox_item(conn, "Follow up on invoice")
    convert_to_task(conn, mid, due_date="2026-09-01")
    m = get_memory(conn, mid)
    assert m.type == "task"

    row = conn.execute(
        "SELECT due_date, completed_at FROM task_details WHERE memory_id = ?",
        (mid,),
    ).fetchone()
    assert row["due_date"] == "2026-09-01"
    assert row["completed_at"] is None


def test_convert_to_task_no_due_date(conn):
    from core.inbox import convert_to_task, create_inbox_item

    mid = create_inbox_item(conn, "Quick task")
    convert_to_task(conn, mid)  # no due_date
    row = conn.execute(
        "SELECT due_date, completed_at FROM task_details WHERE memory_id = ?",
        (mid,),
    ).fetchone()
    assert row["due_date"] is None
    assert row["completed_at"] is None


def test_convert_to_task_idempotent_when_called_twice(conn):
    """A second call (e.g. setting a different due date) updates the existing row,
    not a duplicate — and the task_details PK is on memory_id so a second
    INSERT would be a constraint violation without the upsert."""
    from core.inbox import convert_to_task, create_inbox_item

    mid = create_inbox_item(conn, "Idempotent task")
    convert_to_task(conn, mid, due_date="2026-09-01")
    convert_to_task(conn, mid, due_date="2026-09-15")  # must not raise

    rows = conn.execute(
        "SELECT due_date FROM task_details WHERE memory_id = ?", (mid,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["due_date"] == "2026-09-15"


def test_convert_to_task_unknown_id_is_noop(conn):
    from core.inbox import convert_to_task

    # No row updated, no task_details row created, no exception.
    convert_to_task(conn, 99999, due_date="2026-09-01")
    rows = conn.execute(
        "SELECT * FROM task_details WHERE memory_id = 99999"
    ).fetchall()
    assert rows == []
