"""Tests for trash listing and bulk permanent deletion."""
from __future__ import annotations


# ---------- list_trash ----------


def test_list_trash_empty_when_nothing_deleted(conn):
    from core.memory import create_memory, list_trash

    create_memory(conn, "A", "x")
    create_memory(conn, "B", "y")
    assert list_trash(conn) == []


def test_list_trash_excludes_live_memories(conn):
    from core.memory import create_memory, delete_memory, list_trash

    keep = create_memory(conn, "Keep me", "x")
    gone = create_memory(conn, "Delete me", "y")
    delete_memory(conn, gone)

    items = list_trash(conn)
    assert [m.id for m in items] == [gone]
    assert all(m.id != keep for m in items)


def test_list_trash_includes_all_types(conn):
    """Trash is a single holding area regardless of type — notes,
    inbox items, and tasks all show up together."""
    from core.inbox import convert_to_task, create_inbox_item
    from core.memory import create_memory, delete_memory, list_trash

    note = create_memory(conn, "Note", "x", type="note")
    inbox = create_inbox_item(conn, "Inbox")
    task = create_inbox_item(conn, "Task src")
    convert_to_task(conn, task, due_date="2026-09-01")

    for mid in (note, inbox, task):
        delete_memory(conn, mid)

    items = list_trash(conn)
    types = {m.type for m in items}
    assert types == {"note", "inbox", "task"}
    assert len(items) == 3


def test_list_trash_orders_newest_first(conn):
    """Most recently deleted item appears first.

    The 'deletion order' here is driven by wall-clock timing: each
    ``delete_memory`` stamps ``deleted_at`` with the current time, so
    we space the calls out enough that the timestamps differ at
    second-level precision. SQLite's TEXT comparison of the ISO
    strings works correctly as long as the wall-clock values are
    strictly increasing.
    """
    import time
    from core.memory import create_memory, delete_memory, list_trash

    a = create_memory(conn, "First deleted", "x")
    b = create_memory(conn, "Second deleted", "y")
    c = create_memory(conn, "Third deleted", "z")
    delete_memory(conn, a)
    time.sleep(0.01)
    delete_memory(conn, b)
    time.sleep(0.01)
    delete_memory(conn, c)

    items = list_trash(conn)
    assert [m.id for m in items] == [c, b, a]


def test_list_trash_pulls_tags_along(conn):
    """Trashed memories still report their tags — the preview uses them."""
    from core.memory import add_tags, create_memory, delete_memory, list_trash

    mid = create_memory(conn, "Tagged", "x", tags=["a", "b"])
    delete_memory(conn, mid)
    [m] = list_trash(conn)
    assert m.tags == ["a", "b"]


def test_list_trash_does_not_swallow_restored_items(conn):
    from core.memory import create_memory, delete_memory, list_trash, restore_memory

    mid = create_memory(conn, "Bounce", "x")
    delete_memory(conn, mid)
    assert len(list_trash(conn)) == 1
    restore_memory(conn, mid)
    assert list_trash(conn) == []


# ---------- empty_trash ----------


def test_empty_trash_returns_count_of_deleted_rows(conn):
    from core.memory import create_memory, delete_memory, empty_trash

    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "y")
    c = create_memory(conn, "C", "z")
    delete_memory(conn, a)
    delete_memory(conn, b)
    delete_memory(conn, c)

    n = empty_trash(conn)
    assert n == 3


def test_empty_trash_returns_zero_when_empty(conn):
    from core.memory import empty_trash

    assert empty_trash(conn) == 0


def test_empty_trash_leaves_live_memories_alone(conn):
    from core.memory import create_memory, delete_memory, empty_trash, list_memories

    keep = create_memory(conn, "Keep", "x")
    gone = create_memory(conn, "Go", "y")
    delete_memory(conn, gone)
    empty_trash(conn)

    survivors = list_memories(conn, include_deleted=True)
    assert [m.id for m in survivors] == [keep]


def test_empty_trash_cascades_to_memory_tags(conn):
    """Permanent delete of a memory with tags removes the memory_tags rows."""
    from core.memory import create_memory, delete_memory, empty_trash

    mid = create_memory(conn, "Tagged", "x", tags=["alpha", "beta"])
    delete_memory(conn, mid)
    empty_trash(conn)

    # The memory_tags rows must be gone — they would otherwise dangle
    # pointing at a memory_id that no longer exists.
    rows = conn.execute(
        "SELECT * FROM memory_tags WHERE memory_id = ?", (mid,)
    ).fetchall()
    assert rows == []


def test_empty_trash_cascades_to_memory_versions(conn):
    """Permanent delete of a memory with versions removes its history rows."""
    from core.memory import (
        create_memory,
        delete_memory,
        empty_trash,
        update_memory,
    )

    mid = create_memory(conn, "Versioned", "v1 content")
    update_memory(conn, mid, content="v2 content")
    delete_memory(conn, mid)
    empty_trash(conn)

    rows = conn.execute(
        "SELECT * FROM memory_versions WHERE memory_id = ?", (mid,)
    ).fetchall()
    assert rows == []


def test_empty_trash_cascades_to_task_details(conn):
    """Permanent delete of a task removes its task_details row."""
    from core.inbox import convert_to_task, create_inbox_item
    from core.memory import delete_memory, empty_trash

    mid = create_inbox_item(conn, "Task to be purged")
    convert_to_task(conn, mid, due_date="2026-09-01")
    delete_memory(conn, mid)
    empty_trash(conn)

    rows = conn.execute(
        "SELECT * FROM task_details WHERE memory_id = ?", (mid,)
    ).fetchall()
    assert rows == []


def test_empty_trash_cascades_for_mixed_children(conn):
    """A trashed memory with tags, versions, AND task_details leaves no orphans."""
    from core.inbox import convert_to_task, create_inbox_item
    from core.memory import (
        create_memory,
        delete_memory,
        empty_trash,
        update_memory,
    )

    # A regular note with tags + versions.
    note = create_memory(conn, "Note", "n1", tags=["x"])
    update_memory(conn, note, content="n2")

    # A task with a due date. convert_to_task returns None — track
    # the id via the inbox item it was built from.
    task = create_inbox_item(conn, "Task")
    convert_to_task(conn, task, due_date="2026-09-01")

    # A plain inbox item (no children, but still must be cleaned).
    inbox = create_inbox_item(conn, "Inbox")

    for mid in (note, task, inbox):
        delete_memory(conn, mid)

    n = empty_trash(conn)
    assert n == 3

    # No orphaned rows in any child table.
    for table in ("memory_tags", "memory_versions", "task_details"):
        rows = conn.execute(
            f"SELECT memory_id FROM {table} "  # noqa: S608 — table is a fixed literal
            f"WHERE memory_id IN (?, ?, ?)",
            (note, task, inbox),
        ).fetchall()
        assert rows == [], f"orphan rows in {table}: {rows}"


def test_empty_trash_is_idempotent(conn):
    """Calling twice in a row should not raise and should report 0 the second time."""
    from core.memory import create_memory, delete_memory, empty_trash

    mid = create_memory(conn, "Gone", "x")
    delete_memory(conn, mid)
    assert empty_trash(conn) == 1
    # Second call: nothing to delete.
    assert empty_trash(conn) == 0
