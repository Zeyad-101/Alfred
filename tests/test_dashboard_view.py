"""Smoke tests for the dashboard landing view
(``ui.dashboard_view``).

The dashboard is a presentation layer over the existing
``core`` queries — these tests build a known fixture DB
state, construct the view, and confirm the rendered counts
and the due-today list reflect that state. We don't
test paint (that would require a real display) or click
handlers (those are covered by the main-window integration
tests via ``_on_dashboard_memory_opened``).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from core.memory import create_memory
from core.projects import create_project
from core.tasks import (
    complete_task,
    set_due_date,
)
from core.db import now as _now
from core.db import get_connection, init_db
from ui.dashboard_view import (
    DASHBOARD_GREETING_AFTERNOON,
    DASHBOARD_GREETING_EVENING,
    DASHBOARD_GREETING_MORNING,
    DASHBOARD_GREETING_NIGHT,
    DashboardView,
    _greeting_for,
)


# ---------- helpers ----------


def _make_task(conn, title: str, due_date: str | None = None):
    """Create a task with the given due date.

    Wraps the multi-step create + convert flow in one
    helper so the test bodies stay focused on the
    dashboard's rendering rather than the DB plumbing.
    """
    new_id = create_memory(
        conn,
        title=title,
        content="",
    )
    # Convert to task (creates the task_details row).
    conn.execute(
        "UPDATE memories SET type = 'task' WHERE id = ?",
        (new_id,),
    )
    conn.execute(
        "INSERT INTO task_details(memory_id, due_date, completed_at) "
        "VALUES (?, ?, NULL)",
        (new_id, due_date),
    )
    conn.commit()
    return new_id


# ---------- greeting routing ----------


class TestGreetingFor:
    """The time-of-day branch table is small but worth pinning."""

    def test_morning_5am_to_12pm(self):
        assert _greeting_for(datetime(2026, 8, 28, 7, 0)) == DASHBOARD_GREETING_MORNING
        # 11:59 still morning.
        assert _greeting_for(datetime(2026, 8, 28, 11, 59)) == DASHBOARD_GREETING_MORNING

    def test_afternoon_12pm_to_6pm(self):
        assert _greeting_for(datetime(2026, 8, 28, 12, 0)) == DASHBOARD_GREETING_AFTERNOON
        assert _greeting_for(datetime(2026, 8, 28, 15, 30)) == DASHBOARD_GREETING_AFTERNOON
        # 17:59 still afternoon.
        assert _greeting_for(datetime(2026, 8, 28, 17, 59)) == DASHBOARD_GREETING_AFTERNOON

    def test_evening_6pm_to_10pm(self):
        assert _greeting_for(datetime(2026, 8, 28, 18, 0)) == DASHBOARD_GREETING_EVENING
        assert _greeting_for(datetime(2026, 8, 28, 21, 30)) == DASHBOARD_GREETING_EVENING

    def test_late_night_after_10pm(self):
        assert _greeting_for(datetime(2026, 8, 28, 22, 0)) == DASHBOARD_GREETING_NIGHT
        assert _greeting_for(datetime(2026, 8, 28, 23, 59)) == DASHBOARD_GREETING_NIGHT

    def test_late_night_before_5am(self):
        # The "after midnight" branch shares the late-night
        # greeting with the 22:00-23:59 window.
        assert _greeting_for(datetime(2026, 8, 28, 0, 30)) == DASHBOARD_GREETING_NIGHT
        assert _greeting_for(datetime(2026, 8, 28, 4, 59)) == DASHBOARD_GREETING_NIGHT

    def test_returns_string_not_none(self):
        # Defensive: every hour in the day must produce a
        # non-None string. The branch table is exhaustive
        # by construction (lo < hi for every entry), but
        # a regression that drops an entry would silently
        # start returning None.
        for hour in range(24):
            text = _greeting_for(datetime(2026, 8, 28, hour, 0))
            assert isinstance(text, str) and text, (
                f"no greeting for hour={hour}"
            )


# ---------- construction / render smoke ----------


def test_dashboard_constructs_empty(qapp, conn):
    """An empty DB still produces a valid dashboard.

    The stats cards show 0, the due-today list is hidden and
    its empty-state label is shown. The view should
    construct without error regardless of fixture data.
    """
    view = DashboardView(conn)
    try:
        # Greeting is some non-empty string (we don't pin
        # which one — the test machine's clock could be
        # anything).
        assert view.greeting_text()
        # No memories, no projects, no tasks: every stat
        # is zero.
        assert view._memories_card.value_label.text() == "0"
        assert view._tasks_card.value_label.text() == "0"
        assert view._projects_card.value_label.text() == "0"
        # With no data, the lists are empty and the
        # empty-state labels exist. ``isHidden`` is True
        # (we explicitly hide them in the construction
        # path when there ARE items) — but the cleaner
        # assertion is on the count + the fact that
        # the empty label widget itself is present and
        # not explicitly hidden. ``isVisible`` is
        # gated on the widget being ``show()``n
        # upstream, which doesn't happen in a headless
        # test, so we test the count + ``isHiddenTo``
        # on the list (it's shown when there's data,
        # hidden when there isn't — we use the
        # underlying counter as the source of truth).
        assert view.tasks_list.count() == 0
        # The empty-state label exists as a widget; the
        # data-driven ``show``/``hide`` flips it in
        # response to refresh() but the underlying
        # ``setText`` always sets the fallback copy.
        assert view.tasks_empty.text()
    finally:
        view.deleteLater()


def test_dashboard_stats_reflect_fixture_data(qapp, conn):
    """Insert known fixture data, then build the dashboard.

    Counts and list contents must match.
    """
    # 3 plain notes (1 deleted, so 2 visible to the dashboard).
    create_memory(conn, title="Note 1", content="")
    create_memory(conn, title="Note 2", content="")
    m3 = create_memory(conn, title="Note 3 (deleted)", content="")
    conn.execute(
        "UPDATE memories SET is_deleted = 1 WHERE id = ?", (m3,)
    )
    conn.commit()
    # 1 project.
    create_project(conn, name="My Project")
    # Tasks (these ALSO count as memories — list_memories
    # returns them — so the total memory count includes
    # every task created below as well).
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    # 1 undated task — open, so it lands in Upcoming.
    _make_task(conn, "Undated task", due_date=None)
    # 1 task due today — open, lands in Today.
    _make_task(conn, "Today task", due_date=today)
    # 1 task due yesterday — overdue, folded into Today.
    _make_task(conn, "Overdue task", due_date=yesterday)
    # 1 completed task.
    completed_id = _make_task(conn, "Done task", due_date=today)
    complete_task(conn, completed_id)

    view = DashboardView(conn)
    try:
        # Total live memories = 2 notes + 4 tasks = 6
        # (the deleted one is filtered out, the 1
        # completed task is still a memory, just in the
        # "completed" task bucket).
        assert view._memories_card.value_label.text() == "6"
        # Open tasks = Today + Upcoming, which between them
        # are every incomplete task: undated + due today +
        # overdue = 3. Only the completed one is excluded.
        assert view._tasks_card.value_label.text() == "3"
        # Projects: 1.
        assert view._projects_card.value_label.text() == "1"
    finally:
        view.deleteLater()


def test_dashboard_tasks_today_lists_only_today(qapp, conn):
    """The due-today list only shows tasks whose due_date
    matches today. Tasks due yesterday, tomorrow, or with
    no due date are not in this list."""
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    _make_task(conn, "Today A", due_date=today)
    _make_task(conn, "Today B", due_date=today)
    _make_task(conn, "Tomorrow", due_date=tomorrow)
    _make_task(conn, "Yesterday", due_date=yesterday)
    _make_task(conn, "Undated", due_date=None)

    view = DashboardView(conn)
    try:
        # The today list shows only the two tasks whose
        # due_date is today.
        assert view.tasks_list.count() == 2
        titles = {
            view.tasks_list.item(i).text()
            for i in range(view.tasks_list.count())
        }
        assert titles == {"Today A", "Today B"}
    finally:
        view.deleteLater()


def test_dashboard_click_emits_memory_opened(qapp, conn):
    """Clicking a due-today row emits ``memory_opened``
    with the underlying memory id. The main window's
    ``_on_dashboard_memory_opened`` slot is the consumer
    — it routes through ``_on_item_selected`` to load
    the memory in the editor. We don't need to bring
    up the whole main window for this test; we just
    confirm the view's signal contract.
    """
    new_id = _make_task(conn, "Clickable", due_date=date.today().isoformat())
    view = DashboardView(conn)
    try:
        received: list[int] = []
        view.memory_opened.connect(received.append)
        # The first (and only) row in the due-today list is
        # our seeded task. Triggering ``itemClicked``
        # directly is the documented way to drive a
        # QListWidget from a unit test — synthesizing a
        # mouse event would require mapping to item
        # rect coordinates, which adds nothing over a
        # direct emit.
        assert view.tasks_list.count() == 1
        view.tasks_list.itemClicked.emit(
            view.tasks_list.item(0)
        )
        assert received == [new_id]
    finally:
        view.deleteLater()


def test_dashboard_refresh_picks_up_new_data(qapp, conn):
    """After a task is added, calling ``refresh()`` updates the
    stats cards and the due-today list without a full rebuild.
    """
    view = DashboardView(conn)
    try:
        # Empty: stats are 0, the list is empty.
        assert view._memories_card.value_label.text() == "0"
        assert view.tasks_list.count() == 0
        # Add a task due today and refresh.
        _make_task(conn, "After refresh", due_date=date.today().isoformat())
        view.refresh()
        assert view._memories_card.value_label.text() == "1"
        assert view._tasks_card.value_label.text() == "1"
        assert view.tasks_list.count() == 1
        assert view.tasks_list.item(0).text() == "After refresh"
    finally:
        view.deleteLater()


def test_dashboard_handles_db_query_failure_gracefully(qapp, conn, monkeypatch):
    """If a core query raises, the dashboard shows zeros
    rather than crashing. The main window's status bar
    carries the underlying error; the dashboard's job
    is to stay on its feet.
    """
    from core import memory as core_memory
    from core import projects as core_projects
    from core import tasks as core_tasks

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(core_memory, "list_memories", _boom)
    monkeypatch.setattr(core_projects, "list_projects", _boom)
    monkeypatch.setattr(core_tasks, "list_tasks", _boom)

    view = DashboardView(conn)
    try:
        # Stats are zero, the list is empty, view is alive.
        assert view._memories_card.value_label.text() == "0"
        assert view._tasks_card.value_label.text() == "0"
        assert view._projects_card.value_label.text() == "0"
        assert view.tasks_list.count() == 0
    finally:
        view.deleteLater()
