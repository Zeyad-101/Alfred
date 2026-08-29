"""Dashboard -- Alfred's default landing view.

A read-only landing page that orients the user when Alfred
opens: a time-of-day greeting, a quick-stats row, and the
tasks that need attention today. Every piece is driven by an
existing ``core`` query -- this view is a presentation layer,
not a data layer.

The view emits ``memory_opened`` when the user clicks a
due-today row; the main window listens for that signal and
routes it through the same ``_on_item_selected`` path the
sidebar's other views use to load a memory into the editor.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.memory import Memory, list_memories
from core.projects import list_projects
from core.tasks import list_tasks
from ui.strings import (
    DASHBOARD_GREETING_AFTERNOON,
    DASHBOARD_GREETING_EVENING,
    DASHBOARD_GREETING_MORNING,
    DASHBOARD_GREETING_NIGHT,
    DASHBOARD_OPEN_TOOLTIP,
    DASHBOARD_STAT_MEMORIES,
    DASHBOARD_STAT_PROJECTS,
    DASHBOARD_STAT_TASKS,
    DASHBOARD_STATS_HEADER,
    DASHBOARD_SUBHEAD,
    DASHBOARD_TASKS_TODAY_EMPTY,
    DASHBOARD_TASKS_TODAY_HEADER,
)


# Time-of-day boundaries for the greeting. The boundaries
# are intentionally generous: nobody needs "Good morning"
# to be precisely 12:00:00 sharp. Morning runs through
# late breakfast; afternoon through to dinner; evening
# for a few hours; anything later falls into the
# "late night" branch which the strings file has a
# distinct, slightly warmer phrasing for.
_GREETING_HOURS = (
    (5, 12, DASHBOARD_GREETING_MORNING),
    (12, 18, DASHBOARD_GREETING_AFTERNOON),
    (18, 22, DASHBOARD_GREETING_EVENING),
    (22, 24, DASHBOARD_GREETING_NIGHT),
    (0, 5, DASHBOARD_GREETING_NIGHT),
)


def _greeting_for(now: datetime) -> str:
    """Pick a greeting string based on the local hour.

    Pure function over ``now.hour`` so tests can pin it
    to any time of day without monkey-patching the
    system clock.
    """
    hour = now.hour
    for lo, hi, text in _GREETING_HOURS:
        if lo <= hour < hi:
            return text
    # Defensive fallback (the tuple above covers every
    # hour 0..23, so we should never land here).
    return DASHBOARD_GREETING_EVENING


class _StatCard(QFrame):
    """A single labeled big-number stat for the stats row.

    The quick-stats row is three small cards (memories,
    tasks, projects), each showing a number and a label. The card is a QFrame
    with the theme's ``objectName="card"`` so it picks
    up the rounded filled-panel style.

    The card is read-only and emits no signals; it's a
    display surface, not an action target.
    """

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # The theme's card style keys off this objectName.
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        # The big number -- the actual count. Empty until
        # ``set_value`` is called. Size and color come from
        # the theme's "statValue" step, not from a font
        # tweak here: this used to set 18pt inline, which
        # meant the one number on the dashboard that is
        # supposed to be the loudest thing in its card was
        # sized in a different file from everything it has
        # to sit in scale with.
        self.value_label = QLabel("0")
        self.value_label.setObjectName("statValue")
        self.value_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self.value_label)

        # The "Memories" / "Open tasks" / "Projects" label
        # below the number, one step down and quieter so
        # the eye reads the number first.
        self.caption_label = QLabel(label)
        self.caption_label.setObjectName("statLabel")
        layout.addWidget(self.caption_label)

    def set_value(self, value: int) -> None:
        """Update the big number. ``str(int)`` rather than
        ``f"{value}"`` to guard against a float sneaking
        in from a misbehaving query."""
        self.value_label.setText(str(int(value)))


class _TaskList(QListWidget):
    """The due-today list. Each row carries the source
    :class:`Memory` as UserRole data so the parent's
    ``itemClicked`` slot can pull it out without having
    to query the DB again.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Reuse the standard list styling (rounded
        # background, accent on selection) -- no special
        # objectName means the theme's generic QListWidget
        # selectors apply.
        # The theme gives every QListWidget a filled, bordered
        # panel. Inside the dashboard's card that would be a box
        # in a box, so this one opts out by objectName (see the
        # matching rule in ui/theme.py) and drops its frame.
        self.setObjectName("dashboardTaskList")
        self.setFrameShape(QListWidget.NoFrame)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QListWidget.SingleSelection)
        # Tab focus would interrupt the user's keyboard
        # flow if they hit Tab inside the dashboard;
        # everything reachable via the sidebar / list /
        # editor doesn't need the dashboard's lists in
        # the focus chain.
        self.setFocusPolicy(Qt.NoFocus)


class DashboardView(QWidget):
    """Alfred's default landing view.

    Signals
    -------
    ``memory_opened(int)``
        Emitted with a memory id when the user clicks a
        row in the due-today list.
        The main window routes this through its existing
        ``_on_item_selected`` so the editor loads the
        memory and the view switches to All Memories
        (or stays in Dashboard -- the existing
        ``_on_item_selected`` already handles the
        editor-load; the main window also handles a
        view-switch if requested by the caller).

    Construction
    ------------
    The view takes a ``sqlite3.Connection`` and runs its
    queries eagerly in :meth:`refresh`. It is a read-only
    display -- no mutation -- so the view has no DB-write
    path of its own.
    """

    memory_opened = Signal(int)

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.conn = conn
        # Cached greeting so ``refresh`` can re-evaluate
        # it cheaply (the list queries are the slow part).
        self._greeting: str = _greeting_for(datetime.now())
        self._build_ui()
        # Populate eagerly so a headless / offscreen
        # construction (used by tests) sees the real
        # numbers without having to call refresh()
        # explicitly.
        self.refresh()

    # ----- public API -----

    def refresh(self) -> None:
        """Re-query the data and repopulate the cards and lists.

        Called by the main window after any DB-mutating
        action (create / save / delete / complete-task)
        so the dashboard always reflects the live state.
        Cheap enough to call on every change; the three
        queries are simple index lookups.
        """
        self._refresh_greeting()
        self._refresh_stats()
        self._refresh_tasks_today()

    def greeting_text(self) -> str:
        """The greeting currently in the header label.

        Useful for tests that want to assert the
        time-of-day routing without having to scrape
        the QLabel.
        """
        return self.greeting_label.text()

    # ----- UI construction -----

    def _build_ui(self) -> None:
        # The dashboard is a single column: header, stats
        # row, then the due-today card. Stretch is on the
        # card so it fills the available vertical space;
        # the header and stats row stay at the top.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(16)

        # Header: greeting + subhead. The greeting is the
        # big welcome; the subhead is the same dry
        # invitation the rest of the app uses when the
        # DB is empty.
        header_box = QVBoxLayout()
        header_box.setSpacing(2)
        self.greeting_label = QLabel(self._greeting)
        # The display step -- the top of the type scale, and the
        # only widget in the app that uses it. Was an inline 20pt.
        self.greeting_label.setObjectName("displayLabel")
        header_box.addWidget(self.greeting_label)
        subhead = QLabel(DASHBOARD_SUBHEAD)
        subhead.setObjectName("subheadLabel")
        header_box.addWidget(subhead)
        outer.addLayout(header_box)

        # Stats row: three equally-sized cards. The
        # outer container is just a horizontal layout --
        # no need for a dedicated QFrame wrapper since
        # the cards themselves carry the rounded
        # background via the theme's card style.
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        # A muted section label sits above the row so
        # the eye groups the three cards together.
        stats_header = QLabel(DASHBOARD_STATS_HEADER)
        stats_header.setObjectName("sectionHeader")
        outer.addWidget(stats_header)
        self._memories_card = _StatCard(DASHBOARD_STAT_MEMORIES)
        self._tasks_card = _StatCard(DASHBOARD_STAT_TASKS)
        self._projects_card = _StatCard(DASHBOARD_STAT_PROJECTS)
        stats_row.addWidget(self._memories_card)
        stats_row.addWidget(self._tasks_card)
        stats_row.addWidget(self._projects_card)
        outer.addLayout(stats_row)

        # Due today, as one card rather than a bare header
        # over a bordered list. The header and the list used
        # to read as two unrelated widgets stacked by
        # accident; putting them inside a single rounded
        # panel -- and stripping the list's own frame so
        # there is no box drawn inside a box -- makes the
        # section match the stat cards above it. The card
        # takes the remaining height on its own now that
        # Recent activity is gone.
        self.tasks_card = QFrame()
        self.tasks_card.setObjectName("card")
        tasks_box = QVBoxLayout(self.tasks_card)
        # Tight margins: the card's own stylesheet padding
        # already provides the breathing room, so layout
        # margins on top of it would double-space the
        # contents away from the border.
        tasks_box.setContentsMargins(4, 2, 4, 2)
        tasks_box.setSpacing(8)
        tasks_header = QLabel(DASHBOARD_TASKS_TODAY_HEADER)
        tasks_header.setObjectName("sectionHeader")
        tasks_box.addWidget(tasks_header)
        self.tasks_list = _TaskList()
        self.tasks_list.itemClicked.connect(self._on_item_clicked)
        tasks_box.addWidget(self.tasks_list, 1)
        self.tasks_empty = QLabel(DASHBOARD_TASKS_TODAY_EMPTY)
        self.tasks_empty.setObjectName("muted")
        self.tasks_empty.hide()
        tasks_box.addWidget(self.tasks_empty)

        outer.addWidget(self.tasks_card, 1)

    # ----- data refresh -----

    def _refresh_greeting(self) -> None:
        # Cheap: just a datetime.now() and a table lookup.
        # We re-evaluate on every refresh in case the
        # app has been open across a greeting boundary
        # (e.g. open at 11pm, still running at 1am).
        self._greeting = _greeting_for(datetime.now())
        self.greeting_label.setText(self._greeting)

    def _refresh_stats(self) -> None:
        # Total memories: list_memories excludes deleted
        # by default -- the card is labelled "total
        # memories" and the natural reading is "live
        # memories", which matches what every other
        # memory count in the app shows.
        try:
            all_memories = list_memories(self.conn)
        except Exception:
            # Defensive: a transient DB error should not
            # blank the dashboard. Show zero rather than
            # crash; the main window's status bar will
            # already be carrying the underlying error
            # if it's anything the user needs to know
            # about.
            all_memories = []
        self._memories_card.set_value(len(all_memories))

        # Open task count: Today + Upcoming, which under the
        # three-bucket rules is every incomplete task exactly
        # once (overdue counts in Today, undated in Upcoming).
        # That makes the card's number match what the Tasks
        # view shows above its Completed section.
        try:
            open_tasks = list_tasks(self.conn, "today") + list_tasks(
                self.conn, "upcoming"
            )
        except Exception:
            open_tasks = []
        self._tasks_card.set_value(len(open_tasks))

        # Project count is the size of the project list,
        # not a sum across projects.
        try:
            projects = list_projects(self.conn)
        except Exception:
            projects = []
        self._projects_card.set_value(len(projects))

    def _refresh_tasks_today(self) -> None:
        # ``due_on``, not ``today``: the card is headed "Due today" and
        # means it literally. The Tasks view's Today bucket deliberately
        # folds overdue work in (it is what has to be dealt with now),
        # but a glance card that silently answered a different question
        # than its own heading would be worse than one that is narrow.
        try:
            tasks = list_tasks(self.conn, "due_on")
        except Exception:
            tasks = []
        self.tasks_list.clear()
        if not tasks:
            self.tasks_list.hide()
            self.tasks_empty.show()
            return
        self.tasks_empty.hide()
        self.tasks_list.show()
        for mem, due, completed in tasks:
            self.tasks_list.addItem(self._format_memory_row(mem, mem))

    def _format_memory_row(self, mem: Memory, _payload: Memory) -> QListWidgetItem:
        """Compose a single-row display string for a memory.

        Title only -- the dashboard is a glance view, and
        the full snippet lives in the editor after the
        user clicks. Storing the full :class:`Memory` as
        UserRole data means the click handler doesn't
        need a DB round-trip to open it.
        """
        title = (mem.title or "").strip() or "(untitled)"
        item = QListWidgetItem(title)
        item.setData(Qt.UserRole, mem)
        item.setToolTip(DASHBOARD_OPEN_TOOLTIP)
        return item

    # ----- click handling -----

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """A row in either list was clicked: forward it.

        The dashboard doesn't know what the editor
        looks like or how to switch views; it just
        emits the memory id and lets the main window
        route the click through the existing
        ``_on_item_selected`` slot. The id is
        ``int()``-coerced so the signal payload is a
        plain Python int rather than a Qt variant.
        """
        mem = item.data(Qt.UserRole)
        if mem is None:
            return
        self.memory_opened.emit(int(mem.id))
