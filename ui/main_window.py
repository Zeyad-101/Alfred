"""Main window -- Alfred shell with sidebar-driven view switching.

Wires together the sidebar, the per-view middle pane, and the editor
panel. The widget subclasses (``EditorPanel``, ``MemoryListWidget``,
``TasksTreeWidget``, ``Sidebar``) do not touch the data layer directly;
this class is the only place that calls ``core`` functions.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from core.db import DEFAULT_DB_PATH, get_connection  # noqa: F401  (re-exported for tests)
from core.export_import import backup_db, export_to_file, import_from_file
from core.settings import DEFAULT_SETTINGS, load_settings, save_settings
from core.inbox import (
    convert_to_task,
    create_inbox_item,
    list_inbox,
    organize_to_note,
)
from core.links import list_linked_memories
from core.memory import (
    Memory,
    create_memory,
    delete_memory,
    empty_trash,
    get_memory,
    list_memories,
    list_trash,
    list_versions,
    permanently_delete_memory,
    restore_memory,
    set_tags,
    toggle_pin,
    update_memory,
)
from core.projects import (
    Project,
    create_project,
    delete_project,
    get_memory_project,
    list_projects,
    rename_project,
    set_memory_project,
)
from core.search import search
from core.tasks import (
    complete_task,
    list_tasks,
    set_due_date,
    uncomplete_task,
)
from ui.app_icon import app_icon
from ui.editor_panel import EditorPanel
from ui.history_dialog import HistoryDialog
from ui.icons import standard_icon
from ui.memory_list import MemoryListWidget
from ui.assistant_controller import ACTION_SETTINGS
from ui.capture_popup import CapturePopup
from ui.dashboard_view import DashboardView
from ui.hotkey import HotkeyController
from ui.projects_view import ProjectsView
from ui.settings_dialog import SettingsDialog
from ui.sidebar import (
    VIEW_ALL_MEMORIES,
    VIEW_DASHBOARD,
    VIEW_INBOX,
    VIEW_PROJECTS,
    VIEW_TASKS,
    VIEW_TRASH,
    Sidebar,
)
from ui.trash_view import TrashView
from ui.strings import (
    APP_TITLE,
    BACKUP_DONE,
    CAPTURE_CONFIRMATION,
    DELETED,
    DELETE_CONFIRM_BODY,
    DELETE_CONFIRM_TITLE,
    DUE_DATE_CLEARED,
    DUE_DATE_SET,
    EMPTY_STATE,
    EXPORT_DEFAULT_FILENAME_PREFIX,
    EXPORT_DIALOG_FILTER,
    EXPORT_DIALOG_TITLE,
    EXPORT_DONE,
    IMPORT_CONFIRM_BODY,
    IMPORT_CONFIRM_TITLE,
    IMPORT_DIALOG_FILTER,
    IMPORT_DIALOG_TITLE,
    IMPORT_DONE,
    INBOX_CONVERTED,
    INBOX_EMPTY,
    INBOX_ORGANIZED,
    MENU_BACKUP,
    MENU_EXPORT,
    MENU_FILE,
    MENU_IMPORT,
    NEW_INBOX,
    NEW_MEMORY,
    PERMANENTLY_DELETED,
    PROJECT_CREATED,
    PROJECT_DELETED,
    PROJECT_RENAMED,
    PROJECTS_EMPTY,
    REMINDER_BALLOON_BODY,
    REMINDER_BALLOON_TITLE,
    RESTORED,
    SAVED_NEW,
    SAVED_UPDATED,
    SEARCH_NO_RESULTS,
    SEARCH_PLACEHOLDER,
    SETTINGS_HOTKEY_UNAVAILABLE,
    SETTINGS_RESTART_REQUIRED_BODY,
    SETTINGS_RESTART_REQUIRED_TITLE,
    TASK_COMPLETED,
    TASKS_EMPTY,
    TASK_UNCOMPLETED,
    TRASH_EMPTIED,
    TRASH_EMPTY_STATE,
    TRAY_EXIT,
    TRAY_MINIMIZE_NOTIFICATION_BODY,
    TRAY_MINIMIZE_NOTIFICATION_TITLE,
    TRAY_OPEN,
    TRAY_OPEN_INBOX,
    TRAY_PINNED,
    TRAY_QUICK_CAPTURE,
    TRAY_SEARCH,
    TRAY_SETTINGS,
    TRAY_TOOLTIP,
    VERSION_RESTORED,
)
from ui.tasks_view import TasksTreeWidget

SEARCH_DEBOUNCE_MS = 250
STATUS_TIMEOUT_MS = 3000

# Buckets shown in the Tasks view, in display order. Centralized so the
# tree population and the "all tasks" query can share one definition.
# Three, not four: overdue tasks belong in Today and undated ones in
# Upcoming, so "Pending" had nothing left of its own to hold.
_TASK_BUCKETS: tuple[str, ...] = ("today", "upcoming", "completed")

# The subset that is still open. Today + Upcoming is every incomplete
# task exactly once, which is what the reminder balloon wants to count --
# summing every bucket in ``_TASK_BUCKETS`` would have folded Completed
# into a count of open work.
_OPEN_TASK_BUCKETS: tuple[str, ...] = ("today", "upcoming")

# View id -> page index in ``middle_stack``, in the order ``_build_ui``
# adds the pages. One table rather than a hardcoded number at each
# call site: the several places that switch views by hand had all
# drifted off by one when Dashboard was inserted as page 0, silently
# landing the user on the wrong page.
_VIEW_PAGE_INDEX: dict[str, int] = {
    VIEW_DASHBOARD: 0,
    VIEW_ALL_MEMORIES: 1,
    VIEW_INBOX: 2,
    VIEW_TASKS: 3,
    VIEW_TRASH: 4,
    VIEW_PROJECTS: 5,
}


def _load_task_details(
    conn: sqlite3.Connection, memory_id: int
) -> tuple[str | None, str | None] | None:
    """Return (due_date, completed_at) for a task, or None if not a task.

    The editor uses this to populate the task row's initial state; the
    return value flows straight into ``EditorPanel.set_task_state``.
    """
    row = conn.execute(
        "SELECT type FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    if row is None or row["type"] != "task":
        return None
    details = conn.execute(
        "SELECT due_date, completed_at FROM task_details WHERE memory_id = ?",
        (memory_id,),
    ).fetchone()
    if details is None:
        return (None, None)
    return (details["due_date"], details["completed_at"])


class MainWindow(QMainWindow):
    def __init__(
        self,
        conn: sqlite3.Connection,
        settings: dict | None = None,
        hotkey_controller: HotkeyController | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.conn = conn
        # Settings: a working dict owned by the main window. Saved
        # back to disk by _on_settings_saved; read on demand by the
        # File-menu Backup path and the reminder timer.
        self.settings: dict = dict(settings) if settings is not None else dict(DEFAULT_SETTINGS)
        # The hotkey controller is the same one main.py built and
        # used for the initial register() call. We hold a reference
        # so a hotkey change in the Settings dialog can call
        # ``register(new_hotkey)`` to swap at runtime.
        self._hotkey_controller = hotkey_controller
        # The dark theme is applied unconditionally at startup
        # (in main.py) so children inherit it automatically. No
        # per-instance theme call here -- Alfred is dark-mode only.
        self.setWindowTitle(APP_TITLE)
        # Belt and braces alongside ``app.setWindowIcon`` in
        # ``main.py``: a window built without the application-wide
        # icon having been set (a test, a probe, an embedded use)
        # still wears the right one.
        self.setWindowIcon(app_icon())
        self.resize(1000, 650)
        self.setMinimumSize(720, 480)

        # The view the sidebar is currently showing. Drives both the
        # stacked-widget page index and the list/tree refresh strategy.
        # The default landing view is the Dashboard, not
        # All Memories.
        self._current_view: str = VIEW_DASHBOARD

        # The quick-capture popup is created once and re-shown. It is
        # parented to the main window so its lifetime is tied to the
        # application's, but it is centered on the cursor's screen,
        # not the main window's (see CapturePopup._center_on_current_screen).
        # The popup's ``memory_opened`` signal (the
        # question-answer path) is wired into the existing
        # memory-load slot, so clicking a result row loads it in
        # the editor and navigates to All Memories.
        # ``settings_provider`` hands the popup's assistant controller a
        # live read of the settings dict (for the topic-keyword rules).
        # A lambda rather than the dict itself because
        # ``_on_settings_saved`` rebinds ``self.settings`` wholesale.
        self.capture_popup = CapturePopup(
            self.conn, parent=self, settings_provider=lambda: self.settings
        )
        self.capture_popup.captured.connect(self._on_capture_popup_captured)
        self.capture_popup.memory_opened.connect(self._on_capture_memory_opened)
        # Butler interaction layer: the popup can now ask the main
        # window to switch views ("open my tasks") or to surface a
        # specific project ("tell me everything about Alfred").
        self.capture_popup.view_requested.connect(
            self._on_capture_view_requested
        )
        self.capture_popup.project_shown.connect(
            self._on_capture_project_shown
        )
        # A project write, which is deliberately *not* a request to be
        # taken anywhere -- see :meth:`_on_capture_project_changed`.
        self.capture_popup.project_changed.connect(
            self._on_capture_project_changed
        )

        # Whether we've already shown the user the one-time
        # "still here in the tray" notification on close. We don't
        # persist this -- a session flag is enough to keep the
        # notification from firing every time the user re-hides
        # the window mid-session.
        self._tray_minimize_notified: bool = False

        # Tray icon: only available if the system actually supports
        # it (e.g. Linux without a StatusNotifierWatcher may not).
        # We build it eagerly so the menu is ready before the user
        # first hides the window.
        self._tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = self._build_tray()

        # Periodic task reminders. A QTimer fires on the configured
        # interval; on each tick we count open tasks and show a
        # tray balloon if the count changed since last time (so a
        # task being completed or added is what triggers a fresh
        # reminder, not just the passage of time).
        self._reminder_timer: QTimer = QTimer(self)
        self._reminder_timer.timeout.connect(self._on_reminder_tick)
        self._last_reminder_count: int | None = None
        self._apply_reminder_state()

        self._build_ui()
        self._wire_signals()
        self._refresh_list()  # initial population

        # On first run, surface the empty state in the status bar so
        # the user knows the app isn't broken.
        if self._current_list_widget().count() == 0:
            self.statusBar().showMessage(EMPTY_STATE, STATUS_TIMEOUT_MS * 2)

    # ----- UI construction -----

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # Top row: + Inbox and New entry buttons. The butler
        # mascot lives in a floating always-on-top desktop
        # companion (see ui/desktop_companion.py) rather than
        # here, so the header is just the toolbar row. The
        # buttons carry standard icons so the toolbar reads as
        # deliberate chrome rather than a row of text labels.
        top_row = QHBoxLayout()
        self.new_inbox_btn = QPushButton(standard_icon("new_inbox"), NEW_INBOX)
        self.new_inbox_btn.setDefault(False)
        self.new_inbox_btn.setAutoDefault(False)
        top_row.addWidget(self.new_inbox_btn)
        self.new_btn = QPushButton(standard_icon("new"), NEW_MEMORY)
        self.new_btn.setDefault(False)
        self.new_btn.setAutoDefault(False)
        top_row.addWidget(self.new_btn)
        top_row.addStretch()
        outer.addLayout(top_row)

        # Splitter: sidebar | middle (per-view) | editor
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # Sidebar
        self.sidebar = Sidebar()
        # The sidebar's first row is Dashboard;
        # selecting it here keeps the sidebar / middle
        # pane / _current_view in sync on first paint,
        # rather than relying on the user's first click to
        # line them up.
        self.sidebar.select_view(VIEW_DASHBOARD)
        splitter.addWidget(self.sidebar)

        # Middle: a stacked widget so the "list" region can morph
        # between three views without rebuilding the layout.
        # Page 0 is the Dashboard (the default landing
        # view); the other views follow it.
        self.middle_stack = QStackedWidget()

        # Page 0: Dashboard (read-only landing view)
        self.dashboard_view = DashboardView(self.conn)
        self.middle_stack.addWidget(self.dashboard_view)

        # Page 1: All Memories (search + list)
        all_view = QWidget()
        all_layout = QVBoxLayout(all_view)
        all_layout.setContentsMargins(0, 0, 0, 0)
        all_layout.setSpacing(4)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(SEARCH_PLACEHOLDER)
        self.search_input.setClearButtonEnabled(True)
        all_layout.addWidget(self.search_input)
        self.all_list = MemoryListWidget()
        all_layout.addWidget(self.all_list, 1)
        self.middle_stack.addWidget(all_view)

        # Page 2: Inbox (list only, no search)
        self.inbox_list = MemoryListWidget()
        self.middle_stack.addWidget(self.inbox_list)

        # Page 3: Tasks (tree with four buckets)
        self.tasks_tree = TasksTreeWidget()
        self.middle_stack.addWidget(self.tasks_tree)

        # Page 4: Trash (list + read-only preview)
        self.trash_view = TrashView(self.conn)
        self.middle_stack.addWidget(self.trash_view)

        # Page 5: Projects (project list | project's memories)
        self.projects_view = ProjectsView(self.conn)
        self.middle_stack.addWidget(self.projects_view)

        splitter.addWidget(self.middle_stack)

        # Editor
        self.editor = EditorPanel()
        splitter.addWidget(self.editor)

        splitter.setStretchFactor(0, 0)  # sidebar
        splitter.setStretchFactor(1, 1)  # middle
        splitter.setStretchFactor(2, 2)  # editor
        splitter.setSizes([160, 400, 640])
        outer.addWidget(splitter, 1)

        self.statusBar().showMessage("")

        # The menu bar carries the File menu (Export / Import / Backup
        # Now). These actions are intentionally discoverable, not
        # buried: the export/import surface is meant to be easy to
        # find. ``menuBar()`` creates and shows a
        # QMenuBar on first call, so no separate construction needed.
        self._build_menu()

    def _wire_signals(self) -> None:
        # Toolbar
        self.new_btn.clicked.connect(self._on_new_clicked)
        self.new_inbox_btn.clicked.connect(self._on_new_inbox_clicked)

        # Sidebar
        self.sidebar.view_selected.connect(self._on_view_changed)
        # The Settings row is an action, not a view: the sidebar has
        # already put the highlight back on the current view by the time
        # this fires, so the same dialog the toolbar and the tray open is
        # all there is to do.
        self.sidebar.settings_requested.connect(self._on_settings_clicked)

        # Dashboard -> main window. Clicking a recent-activity
        # or due-today row in the dashboard is the same
        # gesture as clicking a row in any other list view:
        # load the memory into the editor and (for the
        # dashboard) switch to All Memories so the user
        # lands in the full list with their selection
        # preserved.
        self.dashboard_view.memory_opened.connect(self._on_dashboard_memory_opened)

        # Search debounce (only active in All Memories view)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self.search_timer.timeout.connect(self._run_search)
        self.search_input.textChanged.connect(lambda _: self.search_timer.start())

        # List <-> editor (both list widgets + the tree)
        self.all_list.item_selected.connect(self._on_item_selected)
        self.inbox_list.item_selected.connect(self._on_item_selected)
        self.tasks_tree.item_selected.connect(self._on_item_selected)

        # Trash view -> main window
        self.trash_view.empty_trash_requested.connect(self._on_trash_empty)
        self.trash_view.restore_requested.connect(self._on_trash_restore)
        self.trash_view.delete_permanent_requested.connect(self._on_trash_permanent_delete)

        # Projects view -> main window
        self.projects_view.new_project_requested.connect(self._on_project_new)
        self.projects_view.rename_project_requested.connect(self._on_project_rename)
        self.projects_view.delete_project_requested.connect(self._on_project_delete)
        self.projects_view.memory_selected.connect(self._on_item_selected)

        # Editor -> main window
        self.editor.save_clicked.connect(self._on_editor_save)
        self.editor.delete_clicked.connect(self._on_editor_delete)
        self.editor.pin_toggled.connect(self._on_editor_pin_toggled)
        self.editor.history_clicked.connect(self._on_editor_history)
        self.editor.organize_clicked.connect(self._on_editor_organize)
        self.editor.convert_to_task_clicked.connect(self._on_editor_convert_to_task)
        self.editor.task_complete_toggled.connect(self._on_editor_task_complete)
        self.editor.task_due_date_changed.connect(self._on_editor_task_due_date)
        self.editor.project_changed.connect(self._on_editor_project_changed)
        self.editor.link_added.connect(self._on_editor_link_added)
        self.editor.link_removed.connect(self._on_editor_link_removed)
        self.editor.related_memory_clicked.connect(self._on_item_selected)

    def _build_menu(self) -> None:
        """Populate the menu bar with the File menu.

        The menu bar was created by ``QMainWindow.menuBar()`` (called
        implicitly inside ``_build_ui``); we just hang a File menu
        and its three actions off it. A ``&`` in a label marks the
        mnemonic -- the user can open the menu with Alt+F on Windows.
        """
        menubar = self.menuBar()
        file_menu = menubar.addMenu(MENU_FILE)

        export_action = QAction(MENU_EXPORT, self)
        export_action.triggered.connect(self._on_export_all)
        file_menu.addAction(export_action)

        import_action = QAction(MENU_IMPORT, self)
        import_action.triggered.connect(self._on_import)
        file_menu.addAction(import_action)

        file_menu.addSeparator()

        backup_action = QAction(MENU_BACKUP, self)
        backup_action.triggered.connect(self._on_backup_now)
        file_menu.addAction(backup_action)

    # ----- list refresh -----

    def _current_list_widget(self):
        """The list/tree/trash widget that backs the current view.

        Used by the startup empty-state check to decide whether to
        surface the "Nothing on file yet" message. Returns whichever
        widget represents the current view (a MemoryListWidget for
        All Memories / Inbox, the TasksTreeWidget for Tasks, the
        TrashView for Trash, or the ProjectsView for Projects).
        The Dashboard has no "list" widget of its own -- it
        composes the same data into a richer landing view --
        so the empty-state check on Dashboard returns the
        All Memories list as a proxy (the status-bar
        "Nothing on file" message is more useful there
        than the dashboard's own empty card).
        """
        if self._current_view == VIEW_DASHBOARD:
            return self.all_list
        if self._current_view == VIEW_INBOX:
            return self.inbox_list
        if self._current_view == VIEW_TASKS:
            return self.tasks_tree
        if self._current_view == VIEW_TRASH:
            return self.trash_view
        if self._current_view == VIEW_PROJECTS:
            return self.projects_view
        return self.all_list

    def _refresh_list(self) -> None:
        """Rebuild the visible list/tree for the current view.

        Each view uses a different population strategy. Selection
        preservation is per-view: the previously selected id is
        re-selected in whatever widget backs the current view, if
        it's still there.
        """
        if self._current_view == VIEW_DASHBOARD:
            # Dashboard has no "list" -- it composes its own
            # stats + recent + due-today cards. Just trigger
            # its own refresh; the underlying queries are
            # already idempotent.
            self.dashboard_view.refresh()
            return
        if self._current_view == VIEW_ALL_MEMORIES:
            self._refresh_all_memories()
        elif self._current_view == VIEW_INBOX:
            self._refresh_inbox()
        elif self._current_view == VIEW_TASKS:
            self._refresh_tasks()
        elif self._current_view == VIEW_TRASH:
            self._refresh_trash()
        elif self._current_view == VIEW_PROJECTS:
            self._refresh_projects()
        else:
            # Defensive: unknown view id, fall back to all_memories.
            self._current_view = VIEW_ALL_MEMORIES
            self._refresh_all_memories()

    def _refresh_all_memories(self) -> None:
        previous_id = self.all_list.current_memory_id()
        query = self.search_input.text().strip()
        if query:
            memories = search(self.conn, query)
        else:
            memories = list_memories(self.conn)

        memory_ids = {m.id for m in memories}
        self.all_list.set_memories(memories)

        if not memory_ids:
            self.editor.set_memory(None)
            if query:
                self.statusBar().showMessage(SEARCH_NO_RESULTS, STATUS_TIMEOUT_MS)
        elif previous_id is not None and previous_id in memory_ids:
            self.all_list.select_memory(previous_id)
        elif previous_id is not None:
            self.editor.set_memory(None)

    def _refresh_inbox(self) -> None:
        previous_id = self.inbox_list.current_memory_id()
        items = list_inbox(self.conn)
        memory_ids = {m.id for m in items}
        self.inbox_list.set_memories(items)
        if not memory_ids:
            self.editor.set_memory(None)
            self.statusBar().showMessage(INBOX_EMPTY, STATUS_TIMEOUT_MS)
        elif previous_id is not None and previous_id in memory_ids:
            self.inbox_list.select_memory(previous_id)
        elif previous_id is not None:
            self.editor.set_memory(None)

    def _refresh_tasks(self) -> None:
        previous_id = self.tasks_tree.current_memory_id()
        tasks_by_bucket: dict[str, list[tuple[Memory, str | None, str | None]]] = {
            b: list_tasks(self.conn, b) for b in _TASK_BUCKETS
        }
        self.tasks_tree.set_tasks(tasks_by_bucket)

        if not self.tasks_tree.has_any_tasks():
            self.editor.set_memory(None)
            self.statusBar().showMessage(TASKS_EMPTY, STATUS_TIMEOUT_MS)
        elif previous_id is not None and self.tasks_tree.select_memory(previous_id):
            # select_memory() re-selects and emits item_selected; the
            # editor reload is handled by that.
            pass
        else:
            # Previous selection vanished (e.g. task was completed) --
            # editor should not still be showing it.
            self.editor.set_memory(None)

    def _refresh_trash(self) -> None:
        """Populate the trash list. Editor is irrelevant in this view."""
        previous_id = self.trash_view.current_memory_id()
        items = list_trash(self.conn)
        self.trash_view.set_trash(items, previous_id=previous_id)
        # Clear the editor so nothing stale lingers from a prior view.
        # The editor is also hidden when this view is active; clearing
        # is belt-and-suspenders for when the user comes back.
        self.editor.set_memory(None)
        if not items:
            self.statusBar().showMessage(TRASH_EMPTY_STATE, STATUS_TIMEOUT_MS)

    def _refresh_projects(self) -> None:
        """Populate the projects view.

        The editor stays visible in the projects view (unlike Trash,
        which hides it): picking a project on the right shows real
        memories, and the user may well want to edit one. We re-select
        the previously-selected project if it's still in the list.
        The editor is NOT cleared here -- refreshing the project list
        shouldn't drop a memory the user is actively editing.
        """
        previous_id = self.projects_view.current_project_id()
        projects = list_projects(self.conn)
        self.projects_view.set_projects(projects, previous_id=previous_id)
        if not projects:
            self.statusBar().showMessage(PROJECTS_EMPTY, STATUS_TIMEOUT_MS)
        # Always rebuild the editor's project combo so newly-created
        # projects are selectable from the dropdown on a saved memory.
        self._refresh_editor_projects()

    def _refresh_editor_projects(self) -> None:
        """Reload the editor's project combo with the latest project list.

        Called after any project CRUD so the dropdown stays in sync.
        Preserves the current selection if the project still exists.
        """
        projects = list_projects(self.conn)
        self.editor.set_projects([(p.id, p.name) for p in projects])

    def _refresh_editor_related(self, memory_id: int | None = None) -> None:
        """Re-query and re-render the editor's Related chip list.

        Defaults to the currently-loaded memory; callers that know
        which memory they just touched can pass it explicitly. The
        result is a no-op when no memory is loaded.
        """
        target = memory_id if memory_id is not None else self.editor.current_id
        if target is None:
            return
        related = list_linked_memories(self.conn, target)
        self.editor.set_related(related)

    # ----- slots -----

    def _on_view_changed(self, view_id: str) -> None:
        if view_id == self._current_view:
            return
        self._current_view = view_id

        # Switch the middle pane.
        page = _VIEW_PAGE_INDEX.get(view_id)
        if page is not None:
            self.middle_stack.setCurrentIndex(page)
        # In trash mode the editor is irrelevant -- the trash view
        # shows its own read-only preview and per-item actions. Hide
        # it so the user isn't looking at a blank form for the item
        # the trash view is already previewing. Projects mode KEEPS
        # the editor visible: the project's right pane shows the
        # project's memories, and clicking one should open the
        # editor just like in All Memories. Dashboard mode ALSO
        # keeps the editor visible -- clicking a due-today row in
        # the dashboard lands the user with that task already
        # loaded, ready to edit.
        self.editor.setVisible(view_id not in (VIEW_TRASH,))

        # When the user manually switches views, stop any in-flight
        # search debounce -- the input from the previous view is no
        # longer relevant.
        self.search_timer.stop()

        self._refresh_list()

    def _on_new_clicked(self) -> None:
        # Switch back to All Memories first; "New" doesn't make sense
        # in Inbox or Tasks.
        if self._current_view != VIEW_ALL_MEMORIES:
            self._current_view = VIEW_ALL_MEMORIES
            self.middle_stack.setCurrentIndex(_VIEW_PAGE_INDEX[VIEW_ALL_MEMORIES])
            self.sidebar.select_view(VIEW_ALL_MEMORIES)
        # Fresh slate: clear search so the full list is visible,
        # clear list selection, clear editor, focus the title.
        self.search_timer.stop()
        self.search_input.clear()
        self._run_search()  # immediate, don't wait for the debounce
        self.all_list.clearSelection()
        self.all_list.setCurrentItem(None)
        self.editor.set_memory(None)
        self.editor.focus_title()

    def _on_new_inbox_clicked(self) -> None:
        """Open the quick-capture popup.

        The "+ Inbox" toolbar button is now a secondary path to the
        same popup the global hotkey and tray menu summon. The
        popup itself does the DB write and emits ``captured``; the
        main window just needs to listen and refresh afterwards.
        """
        self._show_quick_capture()

    def _show_quick_capture(self) -> None:
        """Show the quick-capture popup.

        Centralized so the toolbar button, the tray menu, and the
        global hotkey all summon the same widget through the same
        reset-and-show path.
        """
        self.capture_popup.open_capture()

    def _on_capture_popup_captured(self, memory_id: int) -> None:
        """The popup just wrote an inbox item.

        Show the butler's confirmation in the status bar (the
        popup itself shows a brief inline confirmation, but the
        status bar is where the user will look for persistent
        feedback) and refresh the current view so the new item
        appears if the user happens to be in Inbox or All Memories.
        """
        self.statusBar().showMessage(CAPTURE_CONFIRMATION, STATUS_TIMEOUT_MS)
        self._refresh_list()

    def _on_capture_memory_opened(self, memory_id: int) -> None:
        """The popup's answer panel: user clicked a result row.

        The ask-or-remember path: the user asked
        a question, the popup listed up to three matches,
        and the user picked one. We load that memory into
        the editor and switch to All Memories so the user
        lands in the full list with their selection
        preserved -- same gesture as a click in any other
        list view.
        """
        # Switch to All Memories first (without re-emitting
        # view_selected, since ``select_view`` doesn't
        # emit -- it just re-selects, and the subsequent
        # _refresh_list call below will repopulate the
        # list with the freshly-loaded selection).
        self._current_view = VIEW_ALL_MEMORIES
        self.middle_stack.setCurrentIndex(_VIEW_PAGE_INDEX[VIEW_ALL_MEMORIES])
        self.sidebar.select_view(VIEW_ALL_MEMORIES)
        # Reuse the existing per-row click path so the
        # editor / list-selection / project-related
        # loading all happen the same way as a regular
        # All-Memories click. ``_refresh_list`` first so
        # the list is populated before we ask it to
        # select by id.
        self._refresh_list()
        self._on_item_selected(memory_id)

    def _on_capture_view_requested(self, view_id: str) -> None:
        """The popup resolved an ACTION ("open my tasks").

        The main window may be trayed when the user talks to the
        companion, so restore it before switching -- otherwise the
        view changes behind a hidden window and nothing appears to
        happen. ``select_view`` doesn't emit, so we call
        ``_on_view_changed`` explicitly to do the actual pane swap
        and refresh.
        """
        self._show_main_window()
        if view_id == ACTION_SETTINGS:
            self._on_settings_clicked()
            return
        self.sidebar.select_view(view_id)
        self._on_view_changed(view_id)

    def _on_capture_project_shown(self, project_id: int) -> None:
        """The popup answered a project question and wants it on screen.

        Switch to Projects, then re-select the project the butler
        just talked about. ``set_projects(..., previous_id=...)`` is
        the existing re-selection hook, so the row's memories load
        exactly as they would after a manual click.
        """
        self._show_main_window()
        self.sidebar.select_view(VIEW_PROJECTS)
        self._on_view_changed(VIEW_PROJECTS)
        self.projects_view.set_projects(
            list_projects(self.conn), previous_id=int(project_id)
        )

    def _on_capture_project_changed(self, project_id: int) -> None:
        """The popup wrote to a project without asking to show it.

        Starting a project, or filing an entry under one, is a thing the
        user said in passing -- very possibly over the top of another
        application -- so the window is left exactly where they had it.
        No :meth:`_show_main_window`, no view switch: the contrast with
        :meth:`_on_capture_project_shown` is the whole point of the two
        signals being separate.

        ``_refresh_list`` is the existing per-view dispatcher, so
        whatever is on screen (the Projects list, the dashboard's
        counts, All Memories) picks the write up, and a window that is
        hidden or on some other view has nothing to do -- it rebuilds
        from the database when the user next looks at it.
        """
        self._refresh_list()

    def _on_dashboard_memory_opened(self, memory_id: int) -> None:
        """A row in the dashboard was clicked.

        Same routing as :meth:`_on_capture_memory_opened`:
        load the memory into the editor and switch to All
        Memories. The dashboard itself is read-only, so
        any click is implicitly a "show me this entry"
        gesture -- landing the user in the full list with
        the entry already selected is the most useful
        place to drop them.
        """
        self._on_capture_memory_opened(memory_id)

    def _on_hotkey_pressed(self) -> None:
        """Global Ctrl+Space handler -- connected via the hotkey bridge.

        If the popup is already visible, the press is a no-op
        that just refocuses it (a hard toggle would clobber
        whatever the user is mid-typing). Otherwise we open the
        capture popup. The main window itself stays as it was:
        the popup is frameless and centered on the cursor's
        screen, so it appears wherever the user is, regardless
        of whether Alfred is the active window.
        """
        if self.capture_popup.isVisible():
            # Already open -- just bring it forward and focus the
            # input. Don't clear the field; the user may be typing.
            self.capture_popup.raise_()
            self.capture_popup.activateWindow()
            self.capture_popup.input.setFocus()
        else:
            self.capture_popup.open_capture()

    def _run_search(self) -> None:
        self._refresh_list()

    def _on_item_selected(self, memory_id: int) -> None:
        m = get_memory(self.conn, memory_id)
        if m is None:
            # Memory was deleted under us; treat as "nothing selected".
            self.editor.set_memory(None)
            return
        # Sync the project combo before set_memory -- set_memory enables
        # the combo based on whether a memory is loaded, and the
        # subsequent set_memory_project will set the actual selection.
        # The combo is populated from list_projects (cheap) so the
        # user can change project assignments from this memory.
        self._refresh_editor_projects()
        self.editor.set_memory(m)
        # Reflect the loaded memory's current project in the combo.
        project = get_memory_project(self.conn, memory_id)
        self.editor.set_memory_project(project.id if project else None)
        # Re-evaluate the History button: a memory with at least one
        # version snapshot enables it; an unedited memory does not.
        self._update_history_button(memory_id)
        # If the loaded memory is a task, hydrate the task-specific
        # editor widgets (complete toggle label, due-date picker).
        task_state = _load_task_details(self.conn, memory_id)
        if task_state is not None:
            due_date, completed_at = task_state
            self.editor.set_task_state(due_date, completed_at)
        # If the memory is NOT a task, set_memory already hid the task
        # row and reset the cached state.
        # Populate the Related section with the loaded memory's
        # current links. set_memory already showed the container;
        # set_related renders the chip list (or the empty state).
        related = list_linked_memories(self.conn, memory_id)
        self.editor.set_related(related)

    def _on_editor_save(self, data: dict) -> None:
        title = data["title"]
        mid = data["memory_id"]
        desired_pin = data["is_pinned"]
        # Project id captured BEFORE save: while the editor was open
        # for an unsaved row the combo was disabled, so this value is
        # always either the memory's prior project (for an edit) or
        # None (for a create). For an edit the user may have changed
        # it via the dropdown; for a create they couldn't, so this is
        # always None on create.
        desired_project_id = self._editor_current_project_id()

        if mid is None:
            new_id = create_memory(
                self.conn,
                title,
                data["content"],
                tags=data["tags"],
            )
            if desired_pin:
                toggle_pin(self.conn, new_id)
            if desired_project_id is not None:
                # We can only assign after the memory row exists. For
                # unsaved rows the combo is disabled, so the only way
                # to reach this branch is to be editing a previously
                # saved memory that just got the project combo changed
                # -- and in that case this save flow wasn't a create.
                set_memory_project(self.conn, new_id, desired_project_id)
            self.statusBar().showMessage(SAVED_NEW, STATUS_TIMEOUT_MS)
            saved_id = new_id
        else:
            update_memory(self.conn, mid, title=title, content=data["content"])
            set_tags(self.conn, mid, data["tags"])
            current = get_memory(self.conn, mid)
            if current is not None and current.is_pinned != desired_pin:
                toggle_pin(self.conn, mid)
            # Project change is applied via project_changed while
            # editing, so by the time we reach save the DB is already
            # in sync. Still, defensively re-apply in case a save
            # without a prior project_changed happens (e.g. legacy
            # caller). ``set_memory_project`` is idempotent.
            set_memory_project(self.conn, mid, desired_project_id)
            self.statusBar().showMessage(SAVED_UPDATED, STATUS_TIMEOUT_MS)
            saved_id = mid

        # _refresh_list re-selects the saved id (via memory_ids match),
        # which re-emits item_selected and reloads the editor with
        # authoritative data (including the new updated_at).
        self._refresh_list()
        self._reselect_after_refresh(saved_id)
        # Saving an edit always creates a version snapshot, so the
        # history button is now definitely enabled for this row.
        self._update_history_button(saved_id)

    def _on_editor_delete(self, memory_id: int) -> None:
        reply = QMessageBox.question(
            self,
            DELETE_CONFIRM_TITLE,
            DELETE_CONFIRM_BODY,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        delete_memory(self.conn, memory_id)
        self.statusBar().showMessage(DELETED, STATUS_TIMEOUT_MS)
        # If a memory is loaded and one of its Related entries is
        # about to be soft-deleted, the link stays (CASCADE only
        # fires on hard delete) but the linked memory is no longer
        # list_linked_memories-visible (it filters is_deleted = 0).
        # Refresh the chip list so the user sees this immediately.
        if (
            self.editor.current_id is not None
            and self.editor.current_id != memory_id
        ):
            self._refresh_editor_related(self.editor.current_id)
        self._refresh_list()
        # _refresh_list already cleared the editor because the deleted
        # id is no longer in the active set.

    def _on_editor_pin_toggled(self, memory_id: int) -> None:
        toggle_pin(self.conn, memory_id)
        self._refresh_list()
        self._reselect_after_refresh(memory_id)

    def _on_editor_history(self, memory_id: int) -> None:
        dialog = HistoryDialog(self.conn, memory_id, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        # The dialog accepted -- either a restore happened, or the user
        # closed it after viewing. Reload the memory in the editor
        # either way; the version count won't have shrunk, so the
        # history button stays enabled.
        m = get_memory(self.conn, memory_id)
        if m is not None:
            self.editor.set_memory(m)
        self._refresh_list()
        self._reselect_after_refresh(memory_id)
        self._update_history_button(memory_id)
        self.statusBar().showMessage(VERSION_RESTORED, STATUS_TIMEOUT_MS)

    # ----- inbox action slots -----

    def _on_editor_organize(self, memory_id: int) -> None:
        organize_to_note(self.conn, memory_id)
        self.statusBar().showMessage(INBOX_ORGANIZED, STATUS_TIMEOUT_MS)
        self._refresh_list()
        # The item is now a note -- it may have left the inbox list. Try
        # to keep it selected; if it vanished, the editor will clear.
        self._reselect_after_refresh(memory_id)

    def _on_editor_convert_to_task(
        self, memory_id: int, due_date: str | None
    ) -> None:
        convert_to_task(self.conn, memory_id, due_date=due_date)
        self.statusBar().showMessage(INBOX_CONVERTED, STATUS_TIMEOUT_MS)
        self._refresh_list()
        # The item is now a task -- may have left the inbox list. If
        # the user is currently in the Inbox view, switching to the
        # Tasks view would be presumptuous; just refresh and let the
        # selection fall where it may.
        self._reselect_after_refresh(memory_id)

    # ----- task action slots -----

    def _on_editor_task_complete(self, memory_id: int) -> None:
        task_state = _load_task_details(self.conn, memory_id)
        if task_state is None:
            return
        _, completed_at = task_state
        if completed_at:
            uncomplete_task(self.conn, memory_id)
            new_state = _load_task_details(self.conn, memory_id)
            self.statusBar().showMessage(TASK_UNCOMPLETED, STATUS_TIMEOUT_MS)
        else:
            complete_task(self.conn, memory_id)
            new_state = _load_task_details(self.conn, memory_id)
            self.statusBar().showMessage(TASK_COMPLETED, STATUS_TIMEOUT_MS)
        if new_state is not None:
            self.editor.set_task_state(*new_state)
        self._refresh_list()
        self._reselect_after_refresh(memory_id)

    def _on_editor_task_due_date(
        self, memory_id: int, due_date: str | None
    ) -> None:
        set_due_date(self.conn, memory_id, due_date)
        if due_date is None:
            self.statusBar().showMessage(DUE_DATE_CLEARED, STATUS_TIMEOUT_MS)
        else:
            self.statusBar().showMessage(DUE_DATE_SET, STATUS_TIMEOUT_MS)
        new_state = _load_task_details(self.conn, memory_id)
        if new_state is not None:
            self.editor.set_task_state(*new_state)
        self._refresh_list()
        self._reselect_after_refresh(memory_id)

    # ----- trash slots -----

    def _on_trash_restore(self, memory_id: int) -> None:
        """Un-soft-delete ``memory_id`` and refresh the trash list.

        We don't auto-navigate to the restored item's natural view --
        the intent is "the next visit to that view shows it" -- but
        we do refresh the trash so the item disappears from this list
        immediately.
        """
        restore_memory(self.conn, memory_id)
        self.statusBar().showMessage(RESTORED, STATUS_TIMEOUT_MS)
        self._refresh_list()

    def _on_trash_permanent_delete(self, memory_id: int) -> None:
        """Hard-delete ``memory_id`` (cascade cleans tags/versions/task_details)."""
        permanently_delete_memory(self.conn, memory_id)
        self.statusBar().showMessage(PERMANENTLY_DELETED, STATUS_TIMEOUT_MS)
        # If a memory is loaded in the editor and the just-deleted
        # memory was in its Related list, the cascade removed the
        # link row but the editor's chip list still shows the stale
        # row until the next refresh. The next list refresh below
        # does NOT re-render the editor's chip list, so handle it
        # explicitly.
        if (
            self.editor.current_id is not None
            and self.editor.current_id != memory_id
        ):
            self._refresh_editor_related(self.editor.current_id)
        self._refresh_list()

    def _on_trash_empty(self) -> None:
        """Permanently remove every trashed memory. Cascade via ON DELETE CASCADE."""
        n = empty_trash(self.conn)
        self.statusBar().showMessage(TRASH_EMPTIED, STATUS_TIMEOUT_MS)
        # If a memory is loaded and any of its links pointed to a
        # now-permanently-deleted trashed memory, the cascade
        # already removed those link rows. Refresh the editor's
        # chip list so the user doesn't see a ghost entry.
        if self.editor.current_id is not None:
            self._refresh_editor_related(self.editor.current_id)
        self._refresh_list()

    # ----- project slots -----

    def _on_project_new(self, name: str, description: str) -> None:
        """Create a project. Stays on the Projects view so the user
        can immediately see the new entry in the list and start
        adding memories to it.
        """
        create_project(self.conn, name, description=description)
        self.statusBar().showMessage(PROJECT_CREATED, STATUS_TIMEOUT_MS)
        # The new project may also affect the editor's combo (e.g. if
        # the user is editing a memory while doing this), so refresh
        # the editor's options too.
        self._refresh_list()
        self._refresh_editor_projects()

    def _on_project_rename(
        self, project_id: int, name: str, description: str
    ) -> None:
        rename_project(
            self.conn, project_id, name=name, description=description
        )
        self.statusBar().showMessage(PROJECT_RENAMED, STATUS_TIMEOUT_MS)
        self._refresh_list()
        self._refresh_editor_projects()

    def _on_project_delete(self, project_id: int) -> None:
        """Hard-delete the project. Memories stay, they just lose
        their project link. The editor's combo is refreshed so a
        currently-loaded memory no longer shows the deleted project.
        """
        delete_project(self.conn, project_id)
        self.statusBar().showMessage(PROJECT_DELETED, STATUS_TIMEOUT_MS)
        self._refresh_list()
        self._refresh_editor_projects()
        # If a memory is currently loaded, its project may have just
        # been deleted -- reflect that in the combo.
        if self.editor.current_id is not None:
            project = get_memory_project(self.conn, self.editor.current_id)
            self.editor.set_memory_project(project.id if project else None)

    def _on_editor_project_changed(
        self, memory_id: int, project_id: int | None
    ) -> None:
        """User picked a different project for the loaded memory.

        Apply immediately (not deferred to save) so the assignment is
        durable if the user navigates away or closes the window
        without saving. The save flow also defensively re-applies
        the combo's current value, so even a stale signal on
        load won't corrupt state.
        """
        set_memory_project(self.conn, memory_id, project_id)
        # Project membership changes affect the project's memory count
        # in the sidebar and in the projects view; refresh whichever
        # view needs it.
        if self._current_view == VIEW_PROJECTS:
            self._refresh_projects()
        else:
            # Off-view: just bump the editor's combo data (no need to
            # re-render the projects view if it's hidden).
            self._refresh_editor_projects()

    # ----- related (memory links) slots -----

    def _on_editor_link_added(self, memory_id: int, other_id: int) -> None:
        """User picked a memory in the Add Related dialog.

        Apply immediately (same as project_changed -- the relationship
        is durable on its own, no need to wait for the save flow).
        Re-querying and re-populating the chip list is what makes the
        new link visible right away.
        """
        from core.links import link_memories

        try:
            link_memories(self.conn, memory_id, other_id)
        except ValueError:
            # A self-link would have been caught at the picker level
            # (the loaded memory is excluded from candidates), so
            # reaching here means someone wired the dialog wrong.
            # Fail silently rather than crash the app; the picker
            # can be re-opened.
            return
        self._refresh_editor_related(memory_id)

    def _on_editor_link_removed(self, memory_id: int, other_id: int) -> None:
        """User clicked the (x) on a chip.

        No confirmation: removing a link is low-risk (re-addable at
        any time) and a confirm dialog on every unlink would be
        friction the butler stays out of.
        """
        from core.links import unlink_memories

        unlink_memories(self.conn, memory_id, other_id)
        self._refresh_editor_related(memory_id)

    # ----- export / import / backup slots -----

    def _on_export_all(self) -> None:
        """File -> Export all... handler.

        Prompts for a destination (default filename is
        ``alfred-export-YYYY-MM-DD.json`` in the user's home), writes
        the JSON, and shows a status-bar confirmation with the count.
        Any I/O error becomes a butler-tone message box. Export is a
        non-destructive action, so the worst-case
        UX is "the file didn't write; here's why".
        """
        default_name = (
            EXPORT_DEFAULT_FILENAME_PREFIX + date.today().isoformat() + ".json"
        )
        # getSaveFileName returns (path, filter). An empty path means
        # the user cancelled -- silently bail without a status message.
        path, _ = QFileDialog.getSaveFileName(
            self,
            EXPORT_DIALOG_TITLE,
            default_name,
            EXPORT_DIALOG_FILTER,
        )
        if not path:
            return
        try:
            export_to_file(self.conn, path)
        except OSError as exc:
            QMessageBox.warning(
                self,
                EXPORT_DIALOG_TITLE,
                f"Could not write to {path}: {exc}",
            )
            return
        # Status-bar confirmation with the count. We get the count
        # from the export we just wrote -- reading the file back would
        # be silly, and we don't want to add a ``count_memories``
        # helper just for this. A rough-and-ready alternative:
        # recount via the existing list_memories API.
        from core.memory import list_memories as _list_memories

        count = len(_list_memories(self.conn))
        self.statusBar().showMessage(
            EXPORT_DONE.format(count=count, path=path), STATUS_TIMEOUT_MS
        )

    def _on_import(self) -> None:
        """File -> Import... handler.

        Prompts for a file, asks the user to confirm (the wording
        deliberately makes the additive, non-merging nature of the
        import unmistakable), and then
        runs the import. On success, refreshes the current view so
        the newly-arrived entries show up. On failure, the import
        rolls back and we surface the error in a message box.
        """
        path, _ = QFileDialog.getOpenFileName(
            self,
            IMPORT_DIALOG_TITLE,
            "",
            IMPORT_DIALOG_FILTER,
        )
        if not path:
            return
        # Peek at the file to get the entry + link counts for the
        # confirmation dialog. We do this via export_all's read
        # helper indirectly: re-use import_from_file's read step.
        # Easiest: read the file, count memories and links, show the
        # confirm, then call import_from_file (which re-reads and
        # re-validates). Slightly wasteful but the file is small.
        try:
            from core.export_import import _read_and_validate_export
            preview = _read_and_validate_export(path)
        except ValueError as exc:
            QMessageBox.warning(self, IMPORT_DIALOG_TITLE, str(exc))
            return
        n_memories = len(preview.get("memories", []))
        n_links = len(preview.get("links", []))
        body = IMPORT_CONFIRM_BODY.format(
            count=n_memories, links=n_links
        )
        reply = QMessageBox.question(
            self,
            IMPORT_CONFIRM_TITLE,
            body,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            summary = import_from_file(self.conn, path)
        except ValueError as exc:
            QMessageBox.warning(self, IMPORT_DIALOG_TITLE, str(exc))
            return
        # The current view may not show the newly-imported entries
        # (e.g. importing memories while the user is in Tasks), but
        # a refresh is cheap and harmless -- and the projects view
        # specifically needs it so any newly-created project shows
        # up. If the user is editing a memory, we re-derive the
        # editor's project combo to reflect any new projects.
        self._refresh_list()
        self._refresh_editor_projects()
        self.statusBar().showMessage(
            IMPORT_DONE.format(
                count=summary["memories_imported"],
                links=summary["links_imported"],
                projects=summary["projects_created"],
            ),
            STATUS_TIMEOUT_MS,
        )

    def _on_backup_now(self) -> None:
        """File -> Backup now handler.

        Commits any pending writes, copies the DB file into
        ``backups/`` with a timestamped name, and shows the path
        in the status bar. The commit-before-copy is a deliberate
        risk mitigation: copying a SQLite file with uncommitted
        WAL data is unsafe, and a single commit() flushes the
        pending state. A full WAL checkpoint would be more
        correct, but this simplification is deliberate.
        """
        # Flush pending writes before copying. The high-level helpers
        # all commit themselves; this is for whatever might still be
        # in the implicit transaction (e.g. from a recent read+write
        # interleaving). It is cheap and harmless if there's nothing
        # pending.
        try:
            self.conn.commit()
        except Exception:
            pass
        # The destination comes from settings when the Backups page
        # has set one; otherwise ``backup_db`` falls back to
        # ``core.paths.default_backup_dir`` -- ``data/backups`` in
        # dev mode, the per-user app-data folder when packaged. The
        # status bar shows the full path either way.
        try:
            backup_path = backup_db(
                DEFAULT_DB_PATH, backup_dir=self.settings.get("backup_dir")
            )
        except OSError as exc:
            QMessageBox.warning(
                self,
                MENU_BACKUP,
                f"Could not write the backup: {exc}",
            )
            return
        self.statusBar().showMessage(
            BACKUP_DONE.format(path=backup_path), STATUS_TIMEOUT_MS
        )

    # ----- settings slots -----

    def _on_settings_clicked(self) -> None:
        """Open the Settings dialog. Wires its signals to local slots.

        The dialog is created fresh on every open so Cancel really
        does cancel (no leftover state from a prior session of the
        same dialog). The signals are connected to methods on this
        window; the dialog itself never touches global state.
        """
        dialog = SettingsDialog(
            self.settings,
            self.conn,
            DEFAULT_DB_PATH,
            parent=self,
        )
        dialog.settings_saved.connect(self._on_settings_saved)
        dialog.backup_restored.connect(self._on_backup_restored)
        dialog.exec()

    def _on_settings_saved(self, new_settings: dict) -> None:
        """Apply the new settings to runtime state and persist to disk.

        Order matters here:
        1. Update ``self.settings`` so other slots (e.g. the next
           File -> Backup now) see the new values.
        2. Apply each side effect -- hotkey, storage
           location, reminder state. The hotkey re-register may
           fail (both libraries refuse the new combo); in that
           case the previous hotkey stays active and the status
           bar surfaces the failure. The settings file is
           updated either way (the user may have changed
           something else successfully and the hotkey failure
           is a runtime concern, not a settings concern).
        3. Persist the new dict to ``data/settings.json``. A
           write failure here is non-fatal -- the in-memory
           settings are already in effect -- but we surface it
           in the status bar so the user knows their change
           won't survive a restart.
        """
        old_settings = self.settings
        self.settings = dict(new_settings)

        # Hotkey: re-register if it changed. The controller is
        # what owns the underlying library hook; we just call
        # register() with the new value.
        new_hk = self.settings.get("hotkey", DEFAULT_SETTINGS["hotkey"])
        old_hk = old_settings.get("hotkey", DEFAULT_SETTINGS["hotkey"])
        if new_hk != old_hk and self._hotkey_controller is not None:
            def _on_unavailable() -> None:
                self.statusBar().showMessage(
                    SETTINGS_HOTKEY_UNAVAILABLE, STATUS_TIMEOUT_MS * 2
                )
            self._hotkey_controller.register(
                new_hk, on_unavailable=_on_unavailable
            )

        # Storage path: a real change requires a restart. The
        # dialog already shows the notice; we also surface it
        # in the status bar / a one-time popup so it's
        # unambiguous.
        if (
            self.settings.get("db_path")
            != old_settings.get("db_path")
        ):
            QMessageBox.information(
                self,
                SETTINGS_RESTART_REQUIRED_TITLE,
                SETTINGS_RESTART_REQUIRED_BODY,
            )

        # Reminder: start/stop the timer to match the new state.
        self._apply_reminder_state()

        # Persist. The Settings dialog's ``Save`` button is the
        # only path to this slot, so a write failure is the
        # user's cue that their changes are session-scoped.
        try:
            save_settings(self.settings)
        except OSError as exc:
            self.statusBar().showMessage(
                f"Could not save settings: {exc}",
                STATUS_TIMEOUT_MS * 2,
            )

    def _on_backup_restored(self, new_conn: sqlite3.Connection) -> None:
        """The Settings dialog restored a backup; swap our conn and refresh.

        The dialog already closed its old conn (via
        ``restore_from_backup``) and emitted a fresh one. The
        main window now points all its reads at the new conn and
        refreshes every visible view so the restored state is
        immediately visible.
        """
        self.conn = new_conn
        # The capture popup is bound to the old conn; rebind it
        # so future captures write to the new DB. (We could also
        # drop and re-create the popup, but the only field that
        # holds a conn is the one we set; an attribute update is
        # cheaper and easier to reason about.)
        try:
            self.capture_popup.conn = new_conn
            # The assistant controller holds its own reference (and
            # derives the worker's connection path from it), so it
            # has to follow the popup onto the new database.
            self.capture_popup.controller.conn = new_conn
            self.capture_popup.controller.reset_context()
        except Exception:
            pass
        # The trash, projects, and editor views each hold their
        # own conn reference; refresh them so the new DB's
        # contents show up.
        self._refresh_list()
        self._refresh_editor_projects()
        if self.editor.current_id is not None:
            m = get_memory(self.conn, self.editor.current_id)
            if m is not None:
                self.editor.set_memory(m)
        # The reminder counter is a view of the DB; reset it so
        # the next tick counts the new state.
        self._last_reminder_count = None

    def _apply_reminder_state(self) -> None:
        """Start or stop the reminder timer to match current settings.

        Idempotent: calling twice with the same settings is a
        no-op. The interval is converted from minutes
        (settings, human-tunable) to milliseconds (Qt, native
        unit) at the boundary.
        """
        if not self.settings.get("reminders_enabled", True):
            self._reminder_timer.stop()
            return
        interval_min = int(
            self.settings.get(
                "reminder_interval_minutes",
                DEFAULT_SETTINGS["reminder_interval_minutes"],
            )
        )
        # QTimer.setInterval is in ms; clamp to a sane floor so a
        # user typing "1" gets reminders every minute rather than
        # every millisecond.
        ms = max(1, interval_min) * 60 * 1000
        self._reminder_timer.setInterval(ms)
        self._reminder_timer.start()
        # Reset the counter so the next tick fires a balloon
        # regardless of what was last shown.
        self._last_reminder_count = None

    def _on_reminder_tick(self) -> None:
        """Periodic reminder tick: count open tasks, show a balloon.

        The balloon only fires when the count CHANGES from the
        last tick -- so the user isn't pinged every hour for the
        same N tasks. The first tick after a settings change
        resets the count to ``None`` and always fires.
        """
        n = self._count_open_tasks()
        if self._last_reminder_count == n:
            return
        self._last_reminder_count = n
        if not self.settings.get("notifications_enabled", True):
            return
        if self._tray is None or not self._tray.isVisible():
            return
        self._tray.showMessage(
            REMINDER_BALLOON_TITLE,
            REMINDER_BALLOON_BODY.format(count=n),
            QSystemTrayIcon.Information,
            3000,
        )

    def _count_open_tasks(self) -> int:
        """Count non-completed tasks (across the open buckets).

        Used by the reminder timer. ``list_tasks`` over Today and
        Upcoming is the natural way to get this; the cost is one
        query per bucket, which on a personal-scale DB is
        negligible. If the DB is closed (e.g. mid-restore), the
        count is 0 -- a reminder of 0 is benign and ignored.
        """
        try:
            total = 0
            for bucket in _OPEN_TASK_BUCKETS:
                total += len(list_tasks(self.conn, bucket))
            return total
        except Exception:
            return 0

    # ----- helpers -----

    def _reselect_after_refresh(self, memory_id: int) -> None:
        """Re-select ``memory_id`` in whichever widget backs the current view."""
        if self._current_view == VIEW_ALL_MEMORIES:
            self.all_list.select_memory(memory_id)
        elif self._current_view == VIEW_INBOX:
            self.inbox_list.select_memory(memory_id)
        elif self._current_view == VIEW_TASKS:
            self.tasks_tree.select_memory(memory_id)
        elif self._current_view == VIEW_TRASH:
            # Best-effort: if the memory is still in trash, re-select.
            # If it isn't (e.g. just restored), no-op; the list shows
            # the new top-of-trash row instead.
            self.trash_view.set_trash(list_trash(self.conn), previous_id=memory_id)
        elif self._current_view == VIEW_PROJECTS:
            # Re-selecting a memory inside the projects view is awkward:
            # the user is conceptually editing a memory (not a project),
            # and the project list is the thing that needs a re-render
            # for memory count changes. So refresh the projects view
            # and let the right-pane re-derive its selection from the
            # currently-selected project. If a specific memory matters,
            # the caller can reselect it via select_memory.
            self.projects_view.set_projects(
                list_projects(self.conn),
                previous_id=self.projects_view.current_project_id(),
            )

    def _editor_current_project_id(self) -> int | None:
        """Read the project combo's current selection as an ``int | None``.

        Centralized so the save flow, which captures the value before
        writing, doesn't have to know the editor's internals.
        """
        # The combo's userData on the placeholder is ``None``; on a
        # real project it's the project id as an int. The editor
        # already exposes ``_project_selected_id`` as a private helper;
        # we re-derive it here rather than expose a new public method
        # to keep the editor's API surface small.
        from ui.editor_panel import EditorPanel  # noqa: F401  (import kept for type)
        return self.editor._project_selected_id()

    def _update_history_button(self, memory_id: int | None) -> None:
        if memory_id is None:
            self.editor.set_history_available(False)
            return
        has_versions = bool(list_versions(self.conn, memory_id))
        self.editor.set_history_available(has_versions)

    # ----- tray / window lifecycle -----

    def _build_tray(self) -> QSystemTrayIcon:
        """Create the system tray icon and its context menu.

        Returns a configured ``QSystemTrayIcon`` but does not show
        it yet -- the user might be on a system where the tray
        exists but shouldn't pop a balloon at startup. Showing is
        a one-liner the caller can opt into if desired.
        """
        tray = QSystemTrayIcon(self._build_tray_icon(), self)
        tray.setToolTip(TRAY_TOOLTIP)
        menu = self._build_tray_menu()
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        return tray

    def _build_tray_icon(self) -> QIcon:
        """Return the application icon for the tray.

        This used to paint its own 64x64 rounded square in
        ``#1f3a5f`` with a white "A" -- a blue that appears nowhere
        else in Alfred, next to an ``.exe`` wearing the mascot and a
        window wearing Qt's default. Three icons for one app.

        Now it hands back :func:`ui.app_icon.app_icon`, which
        carries eight sizes: the tray gets a bitmap drawn for the
        size it actually paints at instead of one 64px square
        resampled down to 16. The method stays so ``_build_tray``
        is untouched, and so there is still one obvious place to
        special-case the tray if it ever needs different art from
        the window.
        """
        return app_icon()

    def _build_tray_menu(self) -> QMenu:
        """Build the right-click context menu.

        The Settings entry opens the SettingsDialog. The dialog
        itself owns its UI state; this slot just instantiates it
        and wires its signals to the appropriate slots.
        """
        from PySide6.QtGui import QGuiApplication  # noqa: F401  (kept local; rarely imported elsewhere)

        menu = QMenu(self)

        open_action = QAction(TRAY_OPEN, menu)
        open_action.triggered.connect(self._show_main_window)
        menu.addAction(open_action)

        capture_action = QAction(TRAY_QUICK_CAPTURE, menu)
        capture_action.triggered.connect(self._show_quick_capture)
        menu.addAction(capture_action)

        menu.addSeparator()

        search_action = QAction(TRAY_SEARCH, menu)
        search_action.triggered.connect(self._focus_search)
        menu.addAction(search_action)

        inbox_action = QAction(TRAY_OPEN_INBOX, menu)
        inbox_action.triggered.connect(self._open_inbox_view)
        menu.addAction(inbox_action)

        pinned_action = QAction(TRAY_PINNED, menu)
        pinned_action.triggered.connect(self._open_pinned)
        menu.addAction(pinned_action)

        menu.addSeparator()

        settings_action = QAction(TRAY_SETTINGS, menu)
        settings_action.triggered.connect(self._on_settings_clicked)
        menu.addAction(settings_action)

        menu.addSeparator()

        exit_action = QAction(TRAY_EXIT, menu)
        exit_action.triggered.connect(self._exit_application)
        menu.addAction(exit_action)

        return menu

    def _on_tray_activated(self, reason) -> None:
        """Tray click/double-click handler.

        A double-click (the conventional Windows tray activation
        gesture) shows the main window. Single-clicks are ignored
        so the user can still use the right-click menu without
        accidentally toggling the window.
        """
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_main_window()

    def _show_main_window(self) -> None:
        """Restore the main window from a hidden/trayed state.

        Called by the tray's "Open Alfred" menu item and the
        tray-icon double-click. Also useful as a public hook for
        ``main.py`` to wire the hotkey to.
        """
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def open_dashboard(self) -> None:
        """Restore the window *and* land on the Dashboard.

        What a double-click on the desktop companion is wired to.
        ``_show_main_window`` alone only un-hides the window, so it
        reopened on whatever view happened to be current when the user
        last closed it -- asking Alfred to come out should put you on
        the landing view, not back in the middle of Trash.

        Kept separate from ``_show_main_window`` on purpose: the tray's
        "Open Alfred" is a plain restore and should not throw away the
        view the user was working in.
        """
        self._show_main_window()
        self.sidebar.select_view(VIEW_DASHBOARD)
        # ``select_view`` re-selects the row, and the resulting
        # currentItemChanged does emit ``view_selected`` -> this same
        # slot; calling it directly is what makes the switch happen
        # when Dashboard's row was *already* current but the stack had
        # been left on another page. ``_on_view_changed`` early-returns
        # if the view is genuinely unchanged, so the double call is
        # harmless either way.
        if self._current_view != VIEW_DASHBOARD:
            self._on_view_changed(VIEW_DASHBOARD)
        else:
            self.middle_stack.setCurrentIndex(_VIEW_PAGE_INDEX[VIEW_DASHBOARD])
            self._refresh_list()

    def show_capture_popup_near(self, anchor: QPoint) -> None:
        """Open the capture popup anchored at ``anchor`` (global coords).

        Used by the floating desktop companion: when the user
        single-clicks the mascot, the popup appears next to it
        rather than wherever the cursor happens to be. This
        reuses the same :class:`CapturePopup` instance the
        toolbar button, tray menu, and global hotkey already
        drive, so any state (e.g. a pending confirmation) is
        consistent across summon paths.
        """
        self.capture_popup.show_near(anchor)

    def _focus_search(self) -> None:
        """Show the main window and put focus in the search box.

        Called from the tray menu; we make sure the All Memories
        view is the active page so the search input is actually
        visible, then focus the input.
        """
        self._show_main_window()
        if self._current_view != VIEW_ALL_MEMORIES:
            self._current_view = VIEW_ALL_MEMORIES
            self.middle_stack.setCurrentIndex(_VIEW_PAGE_INDEX[VIEW_ALL_MEMORIES])
            self.sidebar.select_view(VIEW_ALL_MEMORIES)
            self._refresh_list()
        self.search_input.setFocus()
        self.search_input.selectAll()

    def _open_inbox_view(self) -> None:
        """Show the main window and switch to the Inbox view.

        The sidebar no longer carries an Inbox row, so this tray entry
        is the only route to the view. ``select_view`` no-ops on the
        missing row, which leaves the sidebar highlight on whichever
        view was last selected -- the page itself is correct, and the
        alternative (a hidden seventh row) costs more than the wart.
        """
        self._show_main_window()
        if self._current_view != VIEW_INBOX:
            self._current_view = VIEW_INBOX
            self.middle_stack.setCurrentIndex(_VIEW_PAGE_INDEX[VIEW_INBOX])
            self.sidebar.select_view(VIEW_INBOX)
            self._refresh_list()

    def _open_pinned(self) -> None:
        """Show the main window and switch to All Memories (pinned on top).

        The memory list already shows pinned memories at the top,
        so 'Pinned Memories' is really just 'All Memories, sorted
        with pinned first'. We don't have a dedicated filter view
        yet; this is a thin shortcut that puts the user in the
        right place.
        """
        self._focus_search()

    def _exit_application(self) -> None:
        """Quit the app cleanly from the tray menu.

        We bypass the closeEvent hide-to-tray path so the tray's
        'Exit' option actually exits. ``QApplication.quit`` posts
        the quit event so any pending event-loop work finishes
        first.
        """
        from PySide6.QtWidgets import QApplication
        QApplication.quit()

    def closeEvent(self, event) -> None:
        """Hide to tray on close, with a one-time notification.

        Clicking the window's X is the conventional "I'm done for
        now" gesture -- we treat it as "send me to the tray, I'm
        still running" rather than "shut down". The popup is the
        fast path back in, the tray menu is the slow path. A
        hard "Exit Alfred" lives in the tray menu for when the
        user actually wants the process gone.

        The first time the user does this in a session, we show
        a balloon notification explaining what just happened --
        no point repeating it on every subsequent hide.
        """
        if self._tray is not None and self._tray.isVisible():
            event.ignore()
            self.hide()
            if not self._tray_minimize_notified:
                self._tray_minimize_notified = True
                self._tray.showMessage(
                    TRAY_MINIMIZE_NOTIFICATION_TITLE,
                    TRAY_MINIMIZE_NOTIFICATION_BODY,
                    QSystemTrayIcon.Information,
                    3000,
                )
            return
        # No tray (e.g. headless / unsupported environment) -- let
        # the default close behavior take over and actually exit.
        super().closeEvent(event)
