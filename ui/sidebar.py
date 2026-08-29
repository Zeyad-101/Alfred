"""Sidebar -- view switcher for Dashboard / All Memories / Tasks / Trash / Projects,
plus a Settings row that opens the dialog rather than a pane.

Inbox has no row of its own. Inbox-*type* memories are still a live
concept -- an ambiguous capture is filed as one, and the editor still
offers "Organize as note" / "Convert to task" when you open one -- but
All Memories already lists every non-deleted memory whatever its type,
so a dedicated row was showing a subset of a list one click away.
:data:`VIEW_INBOX` and its stack page survive for the tray's "Open
Inbox" shortcut; :meth:`Sidebar.select_view` no-ops on a view id with
no row, which is what lets that keep working.

The last row, directly under Projects, is Settings. It is an *action*
row: Settings is a modal dialog owned by the main window, so the row
emits :attr:`Sidebar.settings_requested` and immediately hands the
highlight back to the view that was already showing, instead of
pretending to be a sixth view with no page behind it.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from ui.strings import (
    SIDEBAR_ALL_MEMORIES,
    SIDEBAR_DASHBOARD,
    SIDEBAR_PROJECTS,
    SIDEBAR_SETTINGS,
    SIDEBAR_TASKS,
    SIDEBAR_TRASH,
)


# Stable identifiers for the views. They are used as the payload
# of the view_selected signal and as the UserRole data on each row, so
# downstream code never has to compare display labels.
VIEW_DASHBOARD = "dashboard"
VIEW_ALL_MEMORIES = "all_memories"
VIEW_INBOX = "inbox"
VIEW_TASKS = "tasks"
VIEW_TRASH = "trash"
VIEW_PROJECTS = "projects"

# Settings is a *dialog*, not a view: there is no stack page behind it
# and no ``_current_view`` it could become. It still belongs in the
# sidebar, where the user looks for it, so it is an "action row" --
# selecting it hands the highlight straight back to the view that was
# already showing and asks the window to open the dialog instead. The
# marker is private on purpose: nothing outside this module should be
# able to pass it to :meth:`Sidebar.select_view` and expect a pane.
_ROW_SETTINGS = "action:settings"


class Sidebar(QWidget):
    """A vertical list acting as a view switcher.

    Each row is a view; selecting one emits ``view_selected``
    with the view's stable id. The widget itself owns no
    data -- it is just a navigation surface.

    The internal QListWidget is given the objectName
    ``sidebarList`` so the global theme's
    ``QListWidget#sidebarList`` selectors can target the
    active-item-with-filled-rounded-background style
    without leaking into the other lists in the app.
    """

    view_selected = Signal(str)  # emits one of the VIEW_* ids
    # The Settings row. A separate, payload-free signal rather than a
    # VIEW_* id, because the two mean different things: a view id says
    # "swap the middle pane", this says "open a dialog and change
    # nothing behind it".
    settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Set while the highlight is being put back on the row the user
        # was on, so the restoring selection change is not itself read
        # as a new selection.
        self._restoring = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.list = QListWidget()
        # The theme keys sidebar-specific styles off this
        # objectName so the other QListWidgets in the app
        # (the memory list, the inbox list, the result list
        # in the capture popup) keep their own look.
        self.list.setObjectName("sidebarList")
        self.list.setSelectionMode(QListWidget.SingleSelection)
        self.list.setFrameShape(QListWidget.NoFrame)
        self.list.currentItemChanged.connect(self._on_current_changed)
        layout.addWidget(self.list)

        for view_id, label in (
            (VIEW_DASHBOARD, SIDEBAR_DASHBOARD),
            (VIEW_ALL_MEMORIES, SIDEBAR_ALL_MEMORIES),
            (VIEW_TASKS, SIDEBAR_TASKS),
            (VIEW_TRASH, SIDEBAR_TRASH),
            (VIEW_PROJECTS, SIDEBAR_PROJECTS),
            # Last, directly under Projects. Not a view -- see
            # ``_ROW_SETTINGS``.
            (_ROW_SETTINGS, SIDEBAR_SETTINGS),
        ):
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, view_id)
            self.list.addItem(item)

        # Default to Dashboard. The main window
        # also explicitly selects Dashboard in its __init__
        # so the first paint is already on the landing
        # view rather than the All Memories fallback.
        self.list.setCurrentRow(0)

    def select_view(self, view_id: str) -> None:
        """Programmatically select the row for ``view_id`` (no signal side-effects
        of consequence -- it just re-selects, and the parent will re-refresh
        in response to the resulting view_selected emit)."""
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.UserRole) == view_id:
                self.list.setCurrentRow(i)
                return

    def _on_current_changed(self, current, previous) -> None:
        if current is None or self._restoring:
            return
        view_id = current.data(Qt.UserRole)
        if view_id is None:
            return
        if view_id == _ROW_SETTINGS:
            # An action, not a view. The highlight goes back before the
            # dialog opens: nothing behind it changed, and a sidebar
            # left sitting on "Settings" would claim the middle pane
            # had become something it has no page for.
            self._restore_selection(previous)
            self.settings_requested.emit()
            return
        self.view_selected.emit(view_id)

    def _restore_selection(self, item) -> None:
        """Put the highlight back on ``item`` without re-emitting.

        ``item`` is the row that was current before an action row was
        clicked; it is ``None`` only if the action row was somehow the
        first thing ever selected, in which case the Dashboard default
        applies, exactly as at startup.
        """
        self._restoring = True
        try:
            if item is None:
                self.list.setCurrentRow(0)
            else:
                self.list.setCurrentItem(item)
        finally:
            self._restoring = False

