"""Projects view -- flat list of projects on the left, the project's
memories on the right.

The view itself is purely a presentation surface; it never calls
``core.projects`` directly for writes. New / rename / delete signals
go upward to ``MainWindow``, which performs the DB writes and tells
the view to refresh. Memory selection goes upward via
``memory_selected`` so ``MainWindow`` can load the same editor pane
the user already has open (no second preview widget to keep in sync).
"""
from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.memory import Memory
from core.projects import Project
from ui.memory_list import MemoryListWidget
from ui.strings import (
    DELETE,
    DELETE_PROJECT_CONFIRM_BODY,
    DELETE_PROJECT_CONFIRM_TITLE,
    NEW_PROJECT,
    PROJECT_DELETED,
    PROJECT_DESCRIPTION_LABEL,
    PROJECT_DESCRIPTION_PLACEHOLDER,
    PROJECT_NAME_LABEL,
    PROJECT_NAME_PLACEHOLDER,
    PROJECT_NAME_REQUIRED,
    PROJECT_PREVIEW_PLACEHOLDER,
    PROJECTS_EMPTY,
    RENAME_PROJECT,
)


def _format_row(p: Project) -> str:
    """Build a project list row: name, optional description, count.

    Count is shown as ``(N)`` so it scans naturally without being
    the first thing the user sees. A zero-count project still shows
    ``(0)`` -- that signals "exists, but empty" rather than hiding it.
    """
    return f"{p.name}  ({p.memory_count})"


def _format_description(p: Project) -> str:
    """Build the right-pane header line(s) for the selected project."""
    lines: list[str] = [p.name]
    if p.description:
        lines.append(p.description)
    lines.append(f"{p.memory_count} entries")
    return "\n".join(lines)


class ProjectsView(QWidget):
    """Two-pane projects view: project list | project memories.

    Signals (all upward, to MainWindow):
        * ``new_project_requested(name, description)`` -- name/description
          from the New Project dialog, already validated for non-empty.
        * ``rename_project_requested(project_id, name, description)`` --
          name/description from the Rename Project dialog, may be empty
          strings (caller decides what that means).
        * ``delete_project_requested(project_id)`` -- after confirm.
        * ``memory_selected(memory_id)`` -- when the user picks a memory
          on the right, so MainWindow can populate the editor.

    The right pane's empty state is a label, not a list, so the layout
    stays calm when there are no memories yet. A "-- No Project --"
    pseudo-project is NOT modelled here; that lives in the editor
    combo and represents the absence of a project, not a project you
    can click.
    """

    new_project_requested = Signal(str, str)  # name, description
    rename_project_requested = Signal(int, str, str)  # project_id, name, description
    delete_project_requested = Signal(int)  # project_id
    memory_selected = Signal(int)  # memory_id

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self._build_ui()
        self._wire_signals()

    # ----- public API -----

    def set_projects(
        self, projects: list[Project], previous_id: int | None = None
    ) -> None:
        """Populate the left list. Re-select ``previous_id`` if present.

        We re-resolve the right pane from DB on selection rather than
        caching the memory list per project -- projects can change
        without the view being notified otherwise (e.g. a memory is
        created and immediately assigned to the currently-open project
        in another flow). Cheap query, single project.
        """
        self.list_widget.clear()
        for p in projects:
            item = QListWidgetItem(_format_row(p))
            item.setData(Qt.UserRole, p.id)
            self.list_widget.addItem(item)

        # Action buttons enable only when something is selected.
        has_any = len(projects) > 0
        self.new_btn.setEnabled(True)  # always allow new projects
        self.rename_btn.setEnabled(False)
        self.delete_btn.setEnabled(has_any)

        # Empty right-pane on a full repopulate, so a stale "12 entries"
        # from a previous project doesn't bleed across to a newly-loaded
        # one before the first selection change lands.
        self._clear_right_pane()

        # Re-select prior selection if still present, else select the
        # first project so the right pane is populated right away.
        if previous_id is not None:
            for i in range(self.list_widget.count()):
                if self.list_widget.item(i).data(Qt.UserRole) == previous_id:
                    self.list_widget.setCurrentRow(i)
                    return
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        else:
            self._sync_action_buttons()

    def current_project_id(self) -> int | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def set_right_pane(self, project: Project, memories: list[Memory]) -> None:
        """Render ``project`` header and ``memories`` on the right."""
        self.header_label.setText(_format_description(project))
        self.memory_list.set_memories(memories)
        if not memories:
            # Replace the list with a quiet empty-state label so the
            # user sees "0 entries" feedback, not an empty rectangle.
            self.memory_list.hide()
            self.empty_label.show()
            self.empty_label.setText(PROJECTS_EMPTY)
        else:
            self.memory_list.show()
            self.empty_label.hide()

    def _clear_right_pane(self) -> None:
        self.header_label.clear()
        self.memory_list.clear()
        self.memory_list.hide()
        self.empty_label.show()
        self.empty_label.setText(PROJECT_PREVIEW_PLACEHOLDER)

    # ----- UI construction -----

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # --- left column: New + Rename/Delete + list ---
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        # Top action row: New | Rename | Delete. New is leftmost because
        # it's the most-used "create something new" action and matches
        # the sidebar's "All Memories -> New entry" pattern. Rename and
        # Delete are right-aligned so they read as a destructive pair
        # attached to the selected project, not as the primary action.
        top_row = QHBoxLayout()
        self.new_btn = QPushButton(NEW_PROJECT)
        top_row.addWidget(self.new_btn)
        top_row.addStretch()
        self.rename_btn = QPushButton(RENAME_PROJECT)
        self.rename_btn.setEnabled(False)
        top_row.addWidget(self.rename_btn)
        self.delete_btn = QPushButton(DELETE)
        self.delete_btn.setEnabled(False)
        top_row.addWidget(self.delete_btn)
        left_layout.addLayout(top_row)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SingleSelection)
        self.list_widget.setAlternatingRowColors(True)
        left_layout.addWidget(self.list_widget, 1)

        outer.addWidget(left, 1)

        # --- right column: project header + memory list ---
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        self.header_label = QLabel("")
        self.header_label.setWordWrap(True)
        # The project name is the dominant element on this pane, so
        # it takes the theme's title step. It used to be
        # "current size + 1, bold", which meant the pane header was
        # a different size here than the identically-ranked headers
        # elsewhere in the app -- and that it silently changed size
        # whenever the base font did.
        self.header_label.setObjectName("titleLabel")
        right_layout.addWidget(self.header_label)

        # Memory list is reused from ui.memory_list; selection emits
        # memory_selected upward.
        self.memory_list = MemoryListWidget()
        self.memory_list.hide()
        right_layout.addWidget(self.memory_list, 1)

        self.empty_label = QLabel(PROJECT_PREVIEW_PLACEHOLDER)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        right_layout.addWidget(self.empty_label, 1)

        outer.addWidget(right, 2)

    def _wire_signals(self) -> None:
        self.list_widget.currentItemChanged.connect(self._on_project_changed)
        self.new_btn.clicked.connect(self._on_new_clicked)
        self.rename_btn.clicked.connect(self._on_rename_clicked)
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        self.memory_list.item_selected.connect(self.memory_selected.emit)

    # ----- slots -----

    def _on_project_changed(self, current, previous) -> None:
        self._sync_action_buttons()
        if current is None:
            self._clear_right_pane()
            return
        pid = current.data(Qt.UserRole)
        # Re-resolve fresh from DB on every selection so the right pane
        # always reflects authoritative state (name, description, count,
        # and membership).
        from core.projects import (
            get_project,
            list_project_memories,
        )

        project = get_project(self.conn, pid)
        if project is None:
            self._clear_right_pane()
            return
        # The memory_count on the dataclass reflects what list_projects
        # last saw; recounting here is cheap (single COUNT) and keeps
        # the header honest even if something else moved a memory
        # in/out between refreshes.
        from core.projects import list_projects

        fresh = [p for p in list_projects(self.conn) if p.id == pid]
        if fresh:
            project.memory_count = fresh[0].memory_count
        memories = list_project_memories(self.conn, pid)
        self.set_right_pane(project, memories)

    def _on_new_clicked(self) -> None:
        name, ok = QInputDialog.getText(
            self,
            NEW_PROJECT,
            PROJECT_NAME_LABEL,
            QLineEdit.Normal,
            "",
        )
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            QMessageBox.information(self, NEW_PROJECT, PROJECT_NAME_REQUIRED)
            return
        # Skip the description dialog on New -- it would force an extra
        # click for the common "just give me a project shell" case. The
        # user can rename+describe later via Rename.
        self.new_project_requested.emit(name, "")

    def _on_rename_clicked(self) -> None:
        pid = self.current_project_id()
        if pid is None:
            return
        from core.projects import get_project

        current = get_project(self.conn, pid)
        if current is None:
            return
        # Single combined dialog: name + description in one go. Two
        # dialogs in a row is more friction than the rename action
        # warrants for a butler trying to stay out of the way.
        new_name, ok = QInputDialog.getText(
            self,
            RENAME_PROJECT,
            PROJECT_NAME_LABEL,
            QLineEdit.Normal,
            current.name,
        )
        if not ok:
            return
        new_name = (new_name or "").strip() or current.name
        new_description, ok = QInputDialog.getMultiLineText(
            self,
            RENAME_PROJECT,
            PROJECT_DESCRIPTION_LABEL,
            current.description,
        )
        if not ok:
            return
        self.rename_project_requested.emit(pid, new_name, new_description or "")

    def _on_delete_clicked(self) -> None:
        pid = self.current_project_id()
        if pid is None:
            return
        from core.projects import get_project

        current = get_project(self.conn, pid)
        if current is None:
            return
        reply = QMessageBox.question(
            self,
            DELETE_PROJECT_CONFIRM_TITLE,
            f"{DELETE_PROJECT_CONFIRM_BODY}\n\nProject: {current.name}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.delete_project_requested.emit(pid)

    # ----- helpers -----

    def _sync_action_buttons(self) -> None:
        has_selection = self.list_widget.currentItem() is not None
        self.rename_btn.setEnabled(has_selection)
        # The delete button's "enabled when something is selected"
        # is layered on top of the "enabled when there's at least one
        # project" base state set in set_projects; OR them so we cover
        # both the empty-state (no projects) and the unselected-state.
        self.delete_btn.setEnabled(self.delete_btn.isEnabled() and has_selection)
