"""Tests for task operations and bucketed listing.

Bucket tests use a frozen ``reference_date`` so they don't depend on the
real wall-clock date — what "today" means in 2027 should be the same
test it is in 2024.
"""
from __future__ import annotations

from datetime import date


# Frozen "today" used by every bucket assertion below. Arbitrary but
# stable: chosen to be mid-2026 so we can test past/today/future cleanly.
FROZEN_TODAY = date(2026, 6, 15)


def _as_task(conn, title: str, content: str = "", type: str = "inbox"):
    """Helper: create a memory and convert it to a task, return its id.

    Skips the inbox flow (which only knows how to set ``type='inbox'``)
    and produces a task directly. Use when the test isn't about the
    inbox->task promotion.
    """
    from core.memory import create_memory

    return create_memory(conn, title, content, type=type)


# ---------- complete / uncomplete ----------


def test_complete_then_uncomplete_roundtrip(conn):
    from core.tasks import complete_task, uncomplete_task
    from core.inbox import convert_to_task

    mid = _as_task(conn, "Roundtrip")
    convert_to_task(conn, mid)
    complete_task(conn, mid)
    row = conn.execute(
        "SELECT completed_at FROM task_details WHERE memory_id = ?", (mid,)
    ).fetchone()
    assert row["completed_at"] is not None

    uncomplete_task(conn, mid)
    row = conn.execute(
        "SELECT completed_at FROM task_details WHERE memory_id = ?", (mid,)
    ).fetchone()
    assert row["completed_at"] is None


def test_complete_task_noop_on_non_task(conn):
    from core.tasks import complete_task
    from core.memory import create_memory

    mid = create_memory(conn, "Note", "x", type="note")
    complete_task(conn, mid)  # must not raise
    rows = conn.execute(
        "SELECT * FROM task_details WHERE memory_id = ?", (mid,)
    ).fetchall()
    assert rows == []


def test_uncomplete_task_with_no_details_row_is_noop(conn):
    """A task that was never given a due/completion is already uncomplete;
    uncomplete must not crash and must not create an empty row."""
    from core.tasks import uncomplete_task
    from core.memory import create_memory

    mid = create_memory(conn, "Untouched task", "x", type="task")
    uncomplete_task(conn, mid)
    rows = conn.execute(
        "SELECT * FROM task_details WHERE memory_id = ?", (mid,)
    ).fetchall()
    assert rows == []


def test_uncomplete_task_noop_on_non_task(conn):
    from core.tasks import uncomplete_task
    from core.memory import create_memory

    mid = create_memory(conn, "Note", "x", type="note")
    # Give it a task_details row anyway (would be unusual but possible).
    conn.execute(
        "INSERT INTO task_details(memory_id, due_date, completed_at) "
        "VALUES (?, NULL, '2026-01-01T00:00:00+00:00')",
        (mid,),
    )
    conn.commit()
    uncomplete_task(conn, mid)
    # Row untouched: uncomplete is gated on _ensure_task, not on the
    # presence of a task_details row.
    row = conn.execute(
        "SELECT completed_at FROM task_details WHERE memory_id = ?", (mid,)
    ).fetchone()
    assert row["completed_at"] is not None


def test_complete_after_set_due_preserves_due(conn):
    """Setting completed_at must not clobber the due_date on the same row."""
    from core.tasks import complete_task, set_due_date
    from core.inbox import convert_to_task

    mid = _as_task(conn, "Preserve")
    convert_to_task(conn, mid, due_date="2026-09-01")
    set_due_date(conn, mid, "2026-09-10")
    complete_task(conn, mid)
    row = conn.execute(
        "SELECT due_date, completed_at FROM task_details WHERE memory_id = ?",
        (mid,),
    ).fetchone()
    assert row["due_date"] == "2026-09-10"
    assert row["completed_at"] is not None


# ---------- set_due_date ----------


def test_set_due_date_creates_row_when_missing(conn):
    from core.tasks import set_due_date
    from core.memory import create_memory

    mid = create_memory(conn, "Task with no details yet", "x", type="task")
    set_due_date(conn, mid, "2026-09-01")
    row = conn.execute(
        "SELECT due_date FROM task_details WHERE memory_id = ?", (mid,)
    ).fetchone()
    assert row["due_date"] == "2026-09-01"


def test_set_due_date_updates_existing_row(conn):
    from core.tasks import set_due_date
    from core.inbox import convert_to_task

    mid = _as_task(conn, "Update me")
    convert_to_task(conn, mid, due_date="2026-09-01")
    set_due_date(conn, mid, "2026-10-15")
    row = conn.execute(
        "SELECT due_date FROM task_details WHERE memory_id = ?", (mid,)
    ).fetchone()
    assert row["due_date"] == "2026-10-15"


def test_set_due_date_to_none_clears(conn):
    from core.tasks import set_due_date
    from core.inbox import convert_to_task

    mid = _as_task(conn, "Clear me")
    convert_to_task(conn, mid, due_date="2026-09-01")
    set_due_date(conn, mid, None)
    row = conn.execute(
        "SELECT due_date FROM task_details WHERE memory_id = ?", (mid,)
    ).fetchone()
    assert row["due_date"] is None


def test_set_due_date_noop_on_non_task(conn):
    from core.tasks import set_due_date
    from core.memory import create_memory

    mid = create_memory(conn, "Note", "x", type="note")
    set_due_date(conn, mid, "2026-09-01")
    rows = conn.execute(
        "SELECT * FROM task_details WHERE memory_id = ?", (mid,)
    ).fetchall()
    assert rows == []


# ---------- bucket logic ----------


def _populate(conn):
    """Seed an inbox item, a today task, an upcoming task, an overdue
    task, an undated task, and a completed task. Returns a dict of
    names -> memory ids so tests can assert specific members.
    """
    from core.inbox import create_inbox_item
    from core.tasks import complete_task

    # The non-task rows must NOT appear in any task bucket.
    create_inbox_item(conn, "Should not appear")
    from core.memory import create_memory
    note = create_memory(conn, "Plain note", "x", type="note")

    # Tasks for each bucket. Due dates are in ISO YYYY-MM-DD.
    today_task = create_memory(conn, "Today task", "x", type="task")
    conn.execute(
        "INSERT INTO task_details(memory_id, due_date, completed_at) "
        "VALUES (?, ?, NULL)",
        (today_task, "2026-06-15"),
    )

    upcoming_task = create_memory(conn, "Upcoming task", "x", type="task")
    conn.execute(
        "INSERT INTO task_details(memory_id, due_date, completed_at) "
        "VALUES (?, ?, NULL)",
        (upcoming_task, "2026-06-20"),
    )

    overdue_task = create_memory(conn, "Overdue task", "x", type="task")
    conn.execute(
        "INSERT INTO task_details(memory_id, due_date, completed_at) "
        "VALUES (?, ?, NULL)",
        (overdue_task, "2026-06-10"),
    )

    undated_task = create_memory(conn, "Undated task", "x", type="task")
    # Note: no task_details row at all — equivalent to due_date=NULL.

    completed_task = create_memory(conn, "Completed task", "x", type="task")
    conn.execute(
        "INSERT INTO task_details(memory_id, due_date, completed_at) "
        "VALUES (?, ?, '2026-06-14T12:00:00+00:00')",
        (completed_task, "2026-06-14"),
    )
    conn.commit()
    complete_task(conn, completed_task)  # exercise the API path too

    return {
        "note": note,
        "today": today_task,
        "upcoming": upcoming_task,
        "overdue": overdue_task,
        "undated": undated_task,
        "completed": completed_task,
    }


def test_bucket_today(conn):
    from core.tasks import list_tasks

    ids = _populate(conn)
    rows = list_tasks(conn, "today", reference_date=FROZEN_TODAY)
    mem_ids = [m.id for m, _d, _c in rows]
    # Overdue work is today's problem, not a category of its own, so it
    # sits in Today ahead of what is actually due today (due_date ASC).
    assert mem_ids == [ids["overdue"], ids["today"]]


def test_bucket_upcoming(conn):
    from core.tasks import list_tasks

    ids = _populate(conn)
    rows = list_tasks(conn, "upcoming", reference_date=FROZEN_TODAY)
    mem_ids = [m.id for m, _d, _c in rows]
    # Undated work is real but not due, so it belongs here, sorted last.
    assert mem_ids == [ids["upcoming"], ids["undated"]]


def test_today_and_upcoming_partition_every_open_task(conn):
    """The two open buckets cover each incomplete task exactly once.

    This is the property callers rely on to mean "all open tasks" by
    adding the two lists: no task is missing from both, and none is in
    both, so a sum cannot double-count.
    """
    from core.tasks import list_tasks

    ids = _populate(conn)
    today = {
        m.id for m, _d, _c in list_tasks(conn, "today", reference_date=FROZEN_TODAY)
    }
    upcoming = {
        m.id
        for m, _d, _c in list_tasks(conn, "upcoming", reference_date=FROZEN_TODAY)
    }
    assert today & upcoming == set()
    assert today | upcoming == {
        ids["today"],
        ids["upcoming"],
        ids["overdue"],
        ids["undated"],
    }


def test_bucket_due_on_matches_that_date_alone(conn):
    """``due_on`` is the exact-date query Today used to be.

    The assistant needs it for "what is due tomorrow" — pointing the
    ``today`` bucket at tomorrow would drag today's and every overdue
    task along with it.
    """
    from core.tasks import list_tasks

    ids = _populate(conn)
    rows = list_tasks(conn, "due_on", reference_date=FROZEN_TODAY)
    assert [m.id for m, _d, _c in rows] == [ids["today"]]
    later = list_tasks(conn, "due_on", reference_date=date(2026, 6, 20))
    assert [m.id for m, _d, _c in later] == [ids["upcoming"]]


def test_bucket_completed(conn):
    from core.tasks import list_tasks

    ids = _populate(conn)
    rows = list_tasks(conn, "completed", reference_date=FROZEN_TODAY)
    mem_ids = [m.id for m, _d, _c in rows]
    assert mem_ids == [ids["completed"]]
    # And the due_date / completed_at come back populated.
    mem, due, completed = rows[0]
    assert mem.id == ids["completed"]
    assert due == "2026-06-14"
    assert completed is not None


def test_bucket_all_includes_every_task(conn):
    from core.tasks import list_tasks

    ids = _populate(conn)
    rows = list_tasks(conn, "all", reference_date=FROZEN_TODAY)
    mem_ids = {m.id for m, _d, _c in rows}
    # All 5 tasks, none of the non-tasks.
    assert mem_ids == {
        ids["today"],
        ids["upcoming"],
        ids["overdue"],
        ids["undated"],
        ids["completed"],
    }


def test_buckets_exclude_non_tasks_and_deleted(conn):
    from core.memory import create_memory, delete_memory
    from core.tasks import list_tasks

    # A non-task and a deleted task must not appear in any bucket.
    create_memory(conn, "Note", "x", type="note")
    gone = create_memory(conn, "To delete", "x", type="task")
    conn.execute(
        "INSERT INTO task_details(memory_id, due_date, completed_at) "
        "VALUES (?, '2026-06-15', NULL)",
        (gone,),
    )
    conn.commit()
    delete_memory(conn, gone)

    for bucket in ("today", "upcoming", "due_on", "completed", "all"):
        rows = list_tasks(conn, bucket, reference_date=FROZEN_TODAY)
        mem_ids = {m.id for m, _d, _c in rows}
        assert gone not in mem_ids, f"deleted task leaked into {bucket!r}"


def test_bucket_orders_today_and_upcoming_by_due_date(conn):
    """Within a bucket, rows are ordered by due_date ASC."""
    from core.memory import create_memory
    from core.tasks import list_tasks

    a = create_memory(conn, "A later", "x", type="task")
    b = create_memory(conn, "B earlier", "x", type="task")
    c = create_memory(conn, "C middle", "x", type="task")
    for mid, due in ((a, "2026-06-25"), (b, "2026-06-17"), (c, "2026-06-20")):
        conn.execute(
            "INSERT INTO task_details(memory_id, due_date, completed_at) "
            "VALUES (?, ?, NULL)",
            (mid, due),
        )
    conn.commit()

    rows = list_tasks(conn, "upcoming", reference_date=FROZEN_TODAY)
    assert [m.id for m, _d, _c in rows] == [b, c, a]


def test_bucket_upcoming_orders_undated_last(conn):
    """Within Upcoming, dated rows come before undated ones (NULLs last)."""
    from core.memory import create_memory
    from core.tasks import list_tasks

    undated = create_memory(conn, "Undated", "x", type="task")
    earlier = create_memory(conn, "Earlier", "x", type="task")
    later = create_memory(conn, "Later", "x", type="task")
    # Both dates are in the future relative to FROZEN_TODAY so they land
    # in Upcoming rather than being folded into Today as overdue.
    for mid, due in ((earlier, "2026-06-17"), (later, "2026-06-21")):
        conn.execute(
            "INSERT INTO task_details(memory_id, due_date, completed_at) "
            "VALUES (?, ?, NULL)",
            (mid, due),
        )
    conn.commit()
    # undated intentionally has no task_details row.

    rows = list_tasks(conn, "upcoming", reference_date=FROZEN_TODAY)
    mem_ids = [m.id for m, _d, _c in rows]
    assert mem_ids == [earlier, later, undated]


def test_bucket_completed_orders_by_completed_at_desc(conn):
    """Newest completion is first."""
    from core.memory import create_memory
    from core.tasks import list_tasks

    older = create_memory(conn, "Older done", "x", type="task")
    newer = create_memory(conn, "Newer done", "x", type="task")
    conn.execute(
        "INSERT INTO task_details(memory_id, due_date, completed_at) "
        "VALUES (?, NULL, '2026-06-01T10:00:00+00:00')",
        (older,),
    )
    conn.execute(
        "INSERT INTO task_details(memory_id, due_date, completed_at) "
        "VALUES (?, NULL, '2026-06-14T10:00:00+00:00')",
        (newer,),
    )
    conn.commit()

    rows = list_tasks(conn, "completed", reference_date=FROZEN_TODAY)
    assert [m.id for m, _d, _c in rows] == [newer, older]


def test_list_tasks_invalid_bucket_raises(conn):
    from core.tasks import list_tasks

    try:
        list_tasks(conn, "yesterday", reference_date=FROZEN_TODAY)
    except ValueError:
        return
    raise AssertionError("expected ValueError for invalid bucket")


def test_list_tasks_default_uses_today_when_no_reference(conn):
    """reference_date=None falls back to date.today() — just verify it
    doesn't crash and the result respects the is_deleted=0 filter."""
    from core.memory import create_memory
    from core.tasks import list_tasks

    mid = create_memory(conn, "Sometime today", "x", type="task")
    conn.execute(
        "INSERT INTO task_details(memory_id, due_date, completed_at) "
        "VALUES (?, ?, NULL)",
        (mid, date.today().isoformat()),
    )
    conn.commit()
    rows = list_tasks(conn, "today")  # no reference_date -> uses date.today()
    assert any(m.id == mid for m, _d, _c in rows)
