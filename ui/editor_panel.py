"""Editor panel -- title, tags, project, content, pin, save/delete, timestamps.

Type-specific controls:
  * type='inbox'  -> "Organize as note" + "Convert to task" action row
  * type='task'   -> "Mark complete / Mark incomplete" toggle + due-date row
The widget never touches the data layer; it surfaces the new actions
via signals and lets ``MainWindow`` drive the ``core`` calls.

A preview toggle: the raw-text editing surface (a
``QPlainTextEdit``) stays the source of truth, but a ``QTextBrowser``
sibling shows the rendered markdown when the user opts in. Toggle
state resets to Edit on every ``set_memory`` so a new (empty) memory
doesn't open in preview by accident.

A Project combo: a read-only-while-unsaved dropdown
listing every project plus a "-- No Project --" placeholder at index 0.
Project changes emit ``project_changed(memory_id, project_id_or_None)``
so ``MainWindow`` can call ``set_memory_project`` on save or
immediately, depending on UX preference.

A Related section: a vertical list of chips showing
every memory currently linked to the loaded memory, each chip
clickable to load that memory and with a small "x" to remove the
link. The "+ Add related" button opens a small filter-and-pick
dialog. The whole section is hidden for unsaved (new) memories --
a link needs an existing memory_id on both sides, so it can't
exist before the row is saved.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDate, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.memory import Memory
from ui.icons import standard_icon
from ui.markdown_render import render_to_html
from ui.strings import (
    ADD_RELATED,
    CLOSE,
    CONVERT_TO_TASK,
    DELETE,
    DUE_DATE_LABEL,
    HISTORY,
    MARK_COMPLETE,
    MARK_INCOMPLETE,
    NO_DUE_DATE,
    ORGANIZE_AS_NOTE,
    PIN_LABEL,
    PREVIEW_TOGGLE_EDIT,
    PREVIEW_TOGGLE_PREVIEW,
    PROJECT_LABEL,
    PROJECT_NO_PROJECT,
    RELATED_DIALOG_TITLE,
    RELATED_EMPTY,
    RELATED_LABEL,
    RELATED_NO_MATCHES,
    RELATED_REMOVE_TOOLTIP,
    RELATED_SEARCH_PLACEHOLDER,
    SAVE,
    TAGS_LABEL,
    TAGS_PLACEHOLDER,
    TITLE_LABEL,
    TITLE_REQUIRED,
)


class _ConvertToTaskDialog(QDialog):
    """Minimal dialog prompting for an optional due date.

    Returns a single ``str | None``:
      * ``str``  (ISO YYYY-MM-DD) if the user picked a date
      * ``None`` if "no due date" was selected at accept time
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(CONVERT_TO_TASK)
        self.setModal(True)

        layout = QVBoxLayout(self)

        self.no_due_checkbox = QCheckBox(NO_DUE_DATE)
        self.no_due_checkbox.setChecked(False)
        layout.addWidget(self.no_due_checkbox)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setMinimumDate(QDate(1970, 1, 1))
        layout.addWidget(self.date_edit)

        # Toggle the date edit's enabled state with the checkbox.
        self.no_due_checkbox.toggled.connect(self.date_edit.setDisabled)
        self.date_edit.setDisabled(False)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_due_date(self) -> str | None:
        """Return the dialog's choice at accept time.

        Centralized so the caller doesn't have to know the widget layout.
        """
        if self.no_due_checkbox.isChecked():
            return None
        return self.date_edit.date().toString("yyyy-MM-dd")


class _RelatedChip(QWidget):
    """One row in the Related list: clickable title + small remove button.

    Title acts as a clickable link label (the rest of the editor
    already uses QLabel for hyperlinks conceptually); the (x) is a
    small QPushButton. Whole row is laid out in a horizontal layout
    and emits its parent's signals on click.

    Signals are forwarded rather than re-emitted with new payloads:
    the chip just announces "I was clicked" and "remove was clicked";
    the EditorPanel already knows which other_id each chip
    represents (it built the row from a Memory), so the panel
    attaches the right id at construction time.
    """

    clicked = Signal()
    remove_clicked = Signal()

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.title_label = QLabel(title)
        # Visual chip treatment without a custom QSS class -- a thin
        # border plus a slight background tint reads as a chip
        # without dragging the rest of the editor's look into a
        # different palette.
        self.title_label.setStyleSheet(
            "QLabel { padding: 2px 8px; border: 1px solid #bbb;"
            "         border-radius: 9px; background: #f4f4f4; }"
        )
        # Clickable cue without changing the cursor globally.
        self.title_label.setCursor(Qt.PointingHandCursor)
        self.title_label.mouseReleaseEvent = self._on_label_clicked  # type: ignore[method-assign]
        layout.addWidget(self.title_label)

        layout.addStretch()

        self.remove_btn = QPushButton("×")
        # Tight footprint -- the glyph is the affordance; a long
        # label would just be visual noise.
        self.remove_btn.setFixedWidth(24)
        self.remove_btn.setToolTip(RELATED_REMOVE_TOOLTIP)
        self.remove_btn.clicked.connect(self.remove_clicked.emit)
        layout.addWidget(self.remove_btn)

    def _on_label_clicked(self, event) -> None:
        # Only honor left-button release; ignore right-click etc. so
        # the chip behaves like a normal label with a click handler.
        if event.button() == Qt.LeftButton:
            self.clicked.emit()


class _AddRelatedDialog(QDialog):
    """Small filter-and-pick dialog for linking a new related memory.

    The candidate list is built once at construction from a list of
    ``Memory`` objects (filtered by the caller to exclude the
    currently-loaded memory and already-linked memories). The
    QLineEdit's ``textChanged`` signal re-filters that list in
    place. Selecting a row and pressing Enter (or double-clicking)
    accepts the dialog; the picked memory's id is read out via
    ``selected_memory_id()`` at accept time.
    """

    def __init__(
        self,
        candidates: list[Memory],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(RELATED_DIALOG_TITLE)
        self.setModal(True)
        # Keep it compact -- this is a picker, not a workspace.
        self.resize(420, 360)

        # Keep a copy of the candidate titles so we can filter
        # without re-querying the DB on every keystroke. The full
        # Memory objects are referenced by id via itemData.
        self._all_candidates = list(candidates)

        layout = QVBoxLayout(self)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(RELATED_SEARCH_PLACEHOLDER)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._refilter)
        layout.addWidget(self.search_input)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list_widget, 1)

        # Empty-state label -- shown when the filter has no matches.
        # Kept hidden when there's at least one row so the layout
        # doesn't have a stranded label.
        self.empty_label = QLabel(RELATED_NO_MATCHES)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color: #888;")
        self.empty_label.hide()
        layout.addWidget(self.empty_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        # OK starts disabled -- the user must pick something.
        buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self._ok_button = buttons.button(QDialogButtonBox.Ok)
        layout.addWidget(buttons)

        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        self._refilter("")
        # Autofocus the search box so the user can start typing
        # immediately.
        self.search_input.setFocus()

    # ----- public API -----

    def selected_memory_id(self) -> int | None:
        """Return the id of the picked memory, or None.

        The caller should not rely on this until the dialog has
        been accepted (``exec() == QDialog.Accepted``).
        """
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    # ----- slots -----

    def _refilter(self, text: str) -> None:
        """Re-populate the list to match ``text`` (case-insensitive).

        Substring match over title; an empty filter shows everything
        in ``_all_candidates`` (which the caller already trimmed
        to exclude self + already-linked).
        """
        needle = (text or "").strip().lower()
        self.list_widget.clear()
        matches: list[Memory] = []
        for m in self._all_candidates:
            if not needle or needle in m.title.lower():
                matches.append(m)
        for m in matches:
            item = QListWidgetItem(m.title)
            item.setData(Qt.UserRole, m.id)
            self.list_widget.addItem(item)
        # Empty-state handling: hide the list when there are no
        # matches so the user sees the message, not a blank box.
        if not matches:
            self.list_widget.hide()
            self.empty_label.show()
        else:
            self.list_widget.show()
            self.empty_label.hide()
            # Auto-select the first match so Enter picks the top hit
            # -- feels right for "type, Enter" workflows.
            self.list_widget.setCurrentRow(0)
        # OK button tracks "is there a selection?".
        self._ok_button.setEnabled(self.list_widget.currentItem() is not None)

    def _on_selection_changed(self, current, previous) -> None:
        self._ok_button.setEnabled(current is not None)


class EditorPanel(QWidget):
    """The right-hand form for viewing and editing a single memory.

    The panel itself does not touch the core layer -- it gathers user input
    and emits signals. ``MainWindow`` does the actual ``create_memory`` /
    ``update_memory`` / ``toggle_pin`` / task/inbox operations. This keeps
    the widget trivially testable and decoupled from the data layer.
    """

    # ``data`` is a dict with: memory_id (int | None), title, content,
    # tags (list[str]), is_pinned (bool). The parent decides whether
    # memory_id None means "create" or "update".
    save_clicked = Signal(dict)
    delete_clicked = Signal(int)
    pin_toggled = Signal(int)  # memory_id of the toggled row
    history_clicked = Signal(int)  # memory_id of the row to show history for

    # Inbox / task actions. The parent owns the core.* calls; the panel
    # just announces what the user clicked.
    organize_clicked = Signal(int)  # memory_id
    convert_to_task_clicked = Signal(int, object)  # memory_id, due_date: str | None
    task_complete_toggled = Signal(int)  # memory_id
    task_due_date_changed = Signal(int, object)  # memory_id, due_date: str | None

    # Project changes. ``project_id`` is ``int | None``; ``None`` means
    # the user picked "-- No Project --" and the memory should be
    # unassigned. Emitted only when a saved memory is loaded -- for
    # unsaved rows the combo is disabled and the desired project is
    # written via the save flow (so we never persist a project link
    # before the memory row itself has a rowid).
    project_changed = Signal(int, object)  # memory_id, project_id: int | None

    # Related (memory links). Emitted only when a saved memory is
    # loaded -- the whole Related section is hidden for unsaved rows.
    # ``link_added`` / ``link_removed`` are the user-driven add/
    # remove from this memory's side; MainWindow performs the
    # actual core.links call. ``related_memory_clicked`` is fired
    # when the user clicks a chip -- MainWindow routes that to the
    # existing "load this memory into the editor" flow, so chips
    # behave like list rows in any other view.
    link_added = Signal(int, int)  # memory_id, other_id
    link_removed = Signal(int, int)  # memory_id, other_id
    related_memory_clicked = Signal(int)  # other_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_id: int | None = None
        # Cached task state. Set when a task is loaded; cleared when a
        # non-task is loaded. Drives the Complete/Incomplete label and
        # the due-date picker initial state.
        self._current_completed_at: str | None = None
        self._current_due_date: str | None = None
        # Flag to suppress the due_date_changed signal during the
        # programmatic setDate() in set_memory (otherwise loading a task
        # would immediately fire a "due date changed" notification with
        # whatever the picker defaults to).
        self._loading_task = False
        # Flag to suppress project_changed while we programmatically
        # set the combo's current index (loading a memory, or after
        # the project list refreshes).
        self._loading_project = False
        # Tracks the memory ids currently shown as chips, so the
        # "+ Add related" picker can exclude them. Populated by
        # set_related; cleared in set_memory.
        self._related_ids: set[int] = set()
        self._build_ui()

    # ----- public API -----

    @property
    def current_id(self) -> int | None:
        return self._current_id

    def set_memory(self, memory: Memory | None) -> None:
        """Populate the form with ``memory`` (or clear it when None)."""
        self._current_id = memory.id if memory else None

        self.title_input.setText(memory.title if memory else "")
        self.content_input.setPlainText(memory.content if memory else "")
        self.tags_input.setText(", ".join(memory.tags) if memory else "")

        # Always start in Edit mode for a freshly-loaded memory so the
        # user lands on the raw-text surface; the preview is opt-in.
        # ``blockSignals`` here would also be fine, but resetting both
        # the button and the stack explicitly keeps the state-machine
        # obvious: load -> Edit.
        self._set_preview_mode(False)

        # Block the toggled signal so programmatic setChecked doesn't
        # fire pin_toggled (which would attempt to pin an unsaved row).
        self.pin_checkbox.blockSignals(True)
        self.pin_checkbox.setChecked(bool(memory and memory.is_pinned))
        self.pin_checkbox.blockSignals(False)

        self.delete_btn.setEnabled(memory is not None)
        # History button is unconditionally disabled in set_memory;
        # MainWindow will re-enable it via set_history_available()
        # after it has checked list_versions() for the loaded memory.
        self.history_btn.setEnabled(False)
        # Project combo is only enabled for saved memories -- there's
        # no rowid to link against for a not-yet-saved row, and the
        # save flow handles the initial assignment.
        self.project_combo.setEnabled(memory is not None)
        # Related section is hidden entirely for unsaved memories:
        # a link needs a memory_id on both sides, and the row doesn't
        # have one yet. MainWindow will call set_related() right
        # after this when a saved memory is loaded, populating the
        # chips; for unsaved rows the empty-state label still shows
        # inside the (now-hidden) container.
        self.related_container.setVisible(memory is not None)
        self._related_clear_chips()
        self._related_ids = set()
        self._refresh_related_empty_state()
        self.timestamps_label.setVisible(memory is not None)
        if memory is not None:
            self.timestamps_label.setText(self._format_timestamps(memory))

        # Clear any stale validation message.
        self.title_error.setVisible(False)
        self.title_error.clear()

        # Type-specific row visibility + state.
        self._refresh_type_specific(memory)

    def set_history_available(self, available: bool) -> None:
        """Enable the History button iff a memory is loaded AND has >=1 version."""
        self.history_btn.setEnabled(self._current_id is not None and available)

    def focus_title(self) -> None:
        self.title_input.setFocus()
        self.title_input.selectAll()

    def set_task_state(self, due_date: str | None, completed_at: str | None) -> None:
        """Update the cached task state shown in the task row.

        Called by ``MainWindow`` after any task operation (complete,
        uncomplete, due-date change) so the panel reflects the new
        authoritative values from the database without a full reload.
        """
        self._current_due_date = due_date
        self._current_completed_at = completed_at
        self._apply_task_state_to_widgets()

    def set_projects(self, projects: list[tuple[int, str]]) -> None:
        """Refresh the project combo's option list.

        ``projects`` is a list of ``(id, name)`` tuples in the order
        the user should see them. The combo is rebuilt: index 0 is the
        "-- No Project --" placeholder; indices 1..N are the projects.
        The current selection is preserved if its project still exists;
        otherwise we fall back to the placeholder so the user can never
        end up with a "missing" project selected.

        The combo stays disabled until ``set_memory`` is called with a
        saved memory; this is intentional -- a project assignment only
        makes sense for a row that has a rowid.
        """
        self._loading_project = True
        try:
            previous_id = self._project_selected_id()
            self.project_combo.clear()
            self.project_combo.addItem(PROJECT_NO_PROJECT, None)
            for pid, name in projects:
                self.project_combo.addItem(name, pid)
            if previous_id is not None and any(
                self.project_combo.itemData(i) == previous_id
                for i in range(self.project_combo.count())
            ):
                self._select_project_by_id(previous_id)
            else:
                self.project_combo.setCurrentIndex(0)
        finally:
            self._loading_project = False

    def set_memory_project(self, project_id: int | None) -> None:
        """Reflect the loaded memory's project in the combo.

        Called by ``MainWindow`` immediately after ``set_memory`` for
        saved memories. Does NOT enable the combo (the loaded memory
        itself, via ``set_memory``, drives enabled-state).
        """
        self._loading_project = True
        try:
            if project_id is None:
                self.project_combo.setCurrentIndex(0)
            else:
                if not self._select_project_by_id(project_id):
                    # The project was deleted out from under the memory;
                    # fall back to "-- No Project --" and let the user
                    # pick a new one rather than silently showing
                    # nothing.
                    self.project_combo.setCurrentIndex(0)
        finally:
            self._loading_project = False

    def set_related(self, related: list[Memory]) -> None:
        """Replace the chip list with ``related``.

        ``related`` is the list of memories currently linked to the
        loaded memory (from ``core.links.list_linked_memories``).
        Chips are rebuilt from scratch on every call; ordering
        matches the input list (already sorted by title in core).

        The empty-state label visibility is recomputed at the end
        so the user sees "Nothing linked yet." when the list is
        empty, not a bare row.
        """
        self._related_clear_chips()
        self._related_ids = set()
        for m in related:
            chip = _RelatedChip(m.title)
            # Capture ``m.id`` by default-arg to avoid the late-binding
            # closure bug -- every chip would otherwise emit with the
            # last ``m``'s id.
            chip.clicked.connect(
                lambda _checked=False, other_id=m.id: self.related_memory_clicked.emit(
                    other_id
                )
            )
            chip.remove_clicked.connect(
                lambda _checked=False, other_id=m.id: self.link_removed.emit(
                    self._current_id, other_id
                )
                if self._current_id is not None
                else None
            )
            self.related_chips_layout.addWidget(chip)
            self._related_chips.append(chip)
            self._related_ids.add(m.id)
        self._refresh_related_empty_state()

    # ----- UI construction -----

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.title_input = QLineEdit()
        self.title_input.returnPressed.connect(self._on_save)
        form.addRow(TITLE_LABEL, self.title_input)

        self.title_error = QLabel("")
        self.title_error.setStyleSheet("color: #c0392b; font-size: 11px;")
        self.title_error.setVisible(False)
        form.addRow("", self.title_error)

        self.tags_input = QLineEdit()
        self.tags_input.setPlaceholderText(TAGS_PLACEHOLDER)
        form.addRow(TAGS_LABEL, self.tags_input)

        self.project_combo = QComboBox()
        # Disabled by default; enabled in set_memory once a saved
        # memory is loaded. Unsaved rows can't have a project
        # assignment (no rowid yet), so showing the combo at all
        # would be misleading.
        self.project_combo.setEnabled(False)
        # currentIndexChanged fires for both user clicks AND
        # programmatic setCurrentIndex; the _loading_project flag in
        # set_projects / set_memory_project suppresses the signal in
        # the latter case so we don't double-write to the DB.
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        # The "No Project" entry makes more sense in userData form
        # than as text-comparison -- see set_projects / set_memory_project.
        form.addRow(PROJECT_LABEL, self.project_combo)

        # Related section. Lives BELOW the form so it spans the full
        # editor width and has room for the chip list to breathe.
        # The section is one QWidget (``related_container``) that
        # owns its own label + chip column + Add button. Hidden
        # entirely for unsaved memories (set_memory toggles
        # ``setVisible``) -- there's no rowid to link against yet.
        self.related_container = QWidget()
        related_outer = QVBoxLayout(self.related_container)
        related_outer.setContentsMargins(0, 4, 0, 4)
        related_outer.setSpacing(4)

        related_header = QHBoxLayout()
        related_label = QLabel(RELATED_LABEL)
        label_font = related_label.font()
        label_font.setBold(True)
        related_label.setFont(label_font)
        related_header.addWidget(related_label)
        related_header.addStretch()
        self.add_related_btn = QPushButton(ADD_RELATED)
        self.add_related_btn.clicked.connect(self._on_add_related_clicked)
        related_header.addWidget(self.add_related_btn)
        related_outer.addLayout(related_header)

        # Chip column. Each chip is a small QWidget added on demand;
        # ``related_chips_layout`` is the vertical QVBoxLayout that
        # stacks them. An inner container QWidget (``related_chips_box``)
        # holds the layout so we can show/hide it as one unit.
        self.related_chips_box = QWidget()
        self.related_chips_layout = QVBoxLayout(self.related_chips_box)
        self.related_chips_layout.setContentsMargins(0, 0, 0, 0)
        self.related_chips_layout.setSpacing(2)
        # ``addStretch`` keeps the chips top-aligned even when the
        # container has more vertical room than the chips need.
        self.related_chips_layout.addStretch()
        self._related_chips: list[_RelatedChip] = []
        related_outer.addWidget(self.related_chips_box)

        # Empty-state label -- only one of the chips box or the empty
        # label is visible at a time. Lives directly under the
        # header, NOT inside the chips box, so the chips box can
        # collapse to zero height without taking the label with it.
        self.related_empty_label = QLabel(RELATED_EMPTY)
        self.related_empty_label.setStyleSheet("color: #888; font-style: italic;")
        self.related_empty_label.setVisible(True)
        related_outer.addWidget(self.related_empty_label)

        # Hide the whole container by default; set_memory shows it
        # for saved memories.
        self.related_container.setVisible(False)
        layout.addWidget(self.related_container)

        layout.addLayout(form)

        # Content area: a small row with a right-aligned Preview/Edit
        # toggle, then a stacked widget holding either the raw-text
        # editor or the rendered preview.
        preview_row = QHBoxLayout()
        preview_row.addStretch()
        self.preview_toggle = QPushButton(PREVIEW_TOGGLE_PREVIEW)
        # Checkable so the visual state of the button matches the
        # "currently in preview" state, but the click handler also
        # updates the label. Single source of truth: the QStackedWidget
        # index. The button is just a UI affordance.
        self.preview_toggle.setCheckable(True)
        self.preview_toggle.toggled.connect(self._on_preview_toggled)
        preview_row.addWidget(self.preview_toggle)
        layout.addLayout(preview_row)

        # Page 0: the QPlainTextEdit (always present, always holds the
        # raw markdown source).
        # Page 1: the QTextBrowser (rendered HTML preview).
        self.content_stack = QStackedWidget()
        self.content_input = QPlainTextEdit()
        self.content_input.setPlaceholderText("")
        self.content_stack.addWidget(self.content_input)

        self.preview_view = QTextBrowser()
        self.preview_view.setOpenLinks(False)  # we handle clicks via QDesktopServices
        # Stylesheet scopes the visual change to code blocks (a
        # background tint and monospace font) without touching
        # any other element. Inline-style HTML would be more
        # invasive; a QSS is the minimum-fuss route.
        self.preview_view.setStyleSheet(
            "QTextBrowser { font-size: 13px; }"
            "pre { background-color: #f4f4f4; padding: 8px;"
            "      border-radius: 4px;"
            "      font-family: 'Consolas', 'Monaco', monospace; }"
            "code { background-color: #f4f4f4; padding: 1px 4px;"
            "       border-radius: 3px;"
            "       font-family: 'Consolas', 'Monaco', monospace; }"
            "table { border-collapse: collapse; }"
            "th, td { border: 1px solid #ccc; padding: 4px 8px; }"
        )
        self.preview_view.anchorClicked.connect(self._on_anchor_clicked)
        self.content_stack.addWidget(self.preview_view)

        layout.addWidget(self.content_stack, 1)

        # Inbox action row -- visible only for type='inbox'.
        self.inbox_actions = QWidget()
        inbox_layout = QHBoxLayout(self.inbox_actions)
        inbox_layout.setContentsMargins(0, 0, 0, 0)
        self.organize_btn = QPushButton(standard_icon("new"), ORGANIZE_AS_NOTE)
        self.organize_btn.clicked.connect(self._on_organize_clicked)
        inbox_layout.addWidget(self.organize_btn)
        self.convert_btn = QPushButton(standard_icon("convert"), CONVERT_TO_TASK)
        self.convert_btn.clicked.connect(self._on_convert_clicked)
        inbox_layout.addWidget(self.convert_btn)
        inbox_layout.addStretch()
        self.inbox_actions.setVisible(False)
        layout.addWidget(self.inbox_actions)

        # Task action row -- visible only for type='task'.
        self.task_actions = QWidget()
        task_layout = QHBoxLayout(self.task_actions)
        task_layout.setContentsMargins(0, 0, 0, 0)
        self.complete_btn = QPushButton(standard_icon("complete"), MARK_COMPLETE)
        self.complete_btn.clicked.connect(self._on_complete_toggled)
        task_layout.addWidget(self.complete_btn)

        task_layout.addSpacing(12)
        task_layout.addWidget(QLabel(DUE_DATE_LABEL))
        self.due_date_edit = QDateEdit()
        self.due_date_edit.setCalendarPopup(True)
        self.due_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.due_date_edit.setDate(QDate.currentDate())
        self.due_date_edit.setMinimumDate(QDate(1970, 1, 1))
        self.due_date_edit.dateChanged.connect(self._on_due_date_changed)
        task_layout.addWidget(self.due_date_edit)
        self.no_due_checkbox = QCheckBox(NO_DUE_DATE)
        self.no_due_checkbox.toggled.connect(self._on_no_due_toggled)
        task_layout.addWidget(self.no_due_checkbox)
        task_layout.addStretch()
        self.task_actions.setVisible(False)
        layout.addWidget(self.task_actions)

        # Bottom row: pin checkbox + save / delete
        button_row = QHBoxLayout()
        self.pin_checkbox = QCheckBox(PIN_LABEL)
        self.pin_checkbox.toggled.connect(self._on_pin_toggled)
        button_row.addWidget(self.pin_checkbox)
        button_row.addStretch()

        # Save is the primary action -- tag with objectName so
        # the theme gives it the gold-filled "primary" style.
        self.save_btn = QPushButton(standard_icon("save"), SAVE)
        self.save_btn.setObjectName("primaryButton")
        self.save_btn.clicked.connect(self._on_save)
        button_row.addWidget(self.save_btn)

        self.history_btn = QPushButton(standard_icon("history"), HISTORY)
        self.history_btn.setEnabled(False)  # enabled after set_history_available
        self.history_btn.clicked.connect(self._on_history_clicked)
        button_row.addWidget(self.history_btn)

        self.delete_btn = QPushButton(standard_icon("delete"), DELETE)
        self.delete_btn.setEnabled(False)  # nothing to delete yet
        self.delete_btn.clicked.connect(self._on_delete)
        button_row.addWidget(self.delete_btn)
        layout.addLayout(button_row)

        self.timestamps_label = QLabel("")
        self.timestamps_label.setStyleSheet("color: #888; font-size: 11px;")
        self.timestamps_label.setVisible(False)
        layout.addWidget(self.timestamps_label)

    # ----- preview slots -----

    def _on_preview_toggled(self, checked: bool) -> None:
        """Switch the content stack between editor and preview.

        The ``checked`` argument comes from the button's ``toggled``
        signal and reflects the new desired state: True = show
        preview, False = show editor. When transitioning into preview
        we re-render from the current textarea content (so unsaved
        edits show up). The reverse transition doesn't need to do
        anything -- the textarea already holds the raw markdown and
        was not modified by the render step.
        """
        if checked:
            self._render_preview()
            self.preview_toggle.setText(PREVIEW_TOGGLE_EDIT)
            self.content_stack.setCurrentIndex(1)
        else:
            self.preview_toggle.setText(PREVIEW_TOGGLE_PREVIEW)
            self.content_stack.setCurrentIndex(0)

    def _set_preview_mode(self, on: bool) -> None:
        """Programmatically set the preview state without firing the
        ``toggled`` signal side-effect twice. Used by ``set_memory`` to
        reset to Edit mode on every load.
        """
        self.preview_toggle.blockSignals(True)
        self.preview_toggle.setChecked(on)
        self.preview_toggle.blockSignals(False)
        if on:
            self._render_preview()
            self.preview_toggle.setText(PREVIEW_TOGGLE_EDIT)
            self.content_stack.setCurrentIndex(1)
        else:
            self.preview_toggle.setText(PREVIEW_TOGGLE_PREVIEW)
            self.content_stack.setCurrentIndex(0)

    def _render_preview(self) -> None:
        """Render the current editor text into the preview browser.

        Pulls from the QPlainTextEdit every time (not from a cached
        ``Memory.content``), so the preview reflects unsaved edits.
        """
        text = self.content_input.toPlainText()
        self.preview_view.setHtml(render_to_html(text))

    def _on_anchor_clicked(self, url: QUrl) -> None:
        """Open preview links in the system browser, not in the app.

        ``QTextBrowser`` would otherwise try to navigate internally
        (changing the document's source). We disable its built-in
        link handling and route the click through ``QDesktopServices``
        so the URL opens in whatever the user has configured as their
        default browser.
        """
        QDesktopServices.openUrl(url)

    # ----- type-specific helpers -----

    def _refresh_type_specific(self, memory: Memory | None) -> None:
        """Show the right type-specific row for ``memory``; reset state.

        Called from ``set_memory``. Any cached task state is reset
        because the loaded memory may not be a task; the parent will
        re-populate task state via ``set_task_state`` right after
        ``set_memory`` if it loaded a task.
        """
        # Reset state caches first so neither row leaks values from a
        # previous task load into the current view.
        self._current_completed_at = None
        self._current_due_date = None

        if memory is None or memory.type == "note":
            self.inbox_actions.setVisible(False)
            self.task_actions.setVisible(False)
        elif memory.type == "inbox":
            self.inbox_actions.setVisible(True)
            self.task_actions.setVisible(False)
        elif memory.type == "task":
            self.inbox_actions.setVisible(False)
            self.task_actions.setVisible(True)
            # Apply a default state (undated, incomplete). MainWindow
            # will follow up with set_task_state to overwrite with the
            # real values from the DB.
            self._apply_task_state_to_widgets()
        else:
            # Unknown type -- hide both rather than render the wrong row.
            self.inbox_actions.setVisible(False)
            self.task_actions.setVisible(False)

    def _apply_task_state_to_widgets(self) -> None:
        """Mirror ``_current_completed_at`` / ``_current_due_date`` into the widgets.

        Blocks signals on both the date edit and the no-due-date
        checkbox while doing so, so the initial application of state
        doesn't fire spurious change signals.
        """
        self._loading_task = True
        try:
            # Complete / incomplete label
            if self._current_completed_at:
                self.complete_btn.setText(MARK_INCOMPLETE)
            else:
                self.complete_btn.setText(MARK_COMPLETE)

            # Due date
            if self._current_due_date:
                qd = QDate.fromString(self._current_due_date, "yyyy-MM-dd")
                if qd.isValid():
                    self.due_date_edit.setDate(qd)
                self.no_due_checkbox.setChecked(False)
                self.due_date_edit.setEnabled(True)
            else:
                self.no_due_checkbox.setChecked(True)
                self.due_date_edit.setEnabled(False)
        finally:
            self._loading_task = False

    # ----- slots -----

    def _on_save(self) -> None:
        title = self.title_input.text().strip()
        if not title:
            self.title_error.setText(TITLE_REQUIRED)
            self.title_error.setVisible(True)
            self.title_input.setFocus()
            return

        self.title_error.setVisible(False)
        self.title_error.clear()

        data = {
            "memory_id": self._current_id,
            "title": title,
            "content": self.content_input.toPlainText(),
            "tags": self._parse_tags(self.tags_input.text()),
            "is_pinned": self.pin_checkbox.isChecked(),
        }
        self.save_clicked.emit(data)

    def _on_delete(self) -> None:
        if self._current_id is None:
            return
        self.delete_clicked.emit(self._current_id)

    def _on_history_clicked(self) -> None:
        if self._current_id is None:
            return
        self.history_clicked.emit(self._current_id)

    def _on_pin_toggled(self, checked: bool) -> None:
        # Ignore pin toggles on a not-yet-saved row; the desired pin
        # state travels with the save and is applied there.
        if self._current_id is None:
            return
        self.pin_toggled.emit(self._current_id)

    def _on_organize_clicked(self) -> None:
        if self._current_id is None:
            return
        self.organize_clicked.emit(self._current_id)

    def _on_convert_clicked(self) -> None:
        if self._current_id is None:
            return
        dialog = _ConvertToTaskDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        due_date = dialog.selected_due_date()
        self.convert_to_task_clicked.emit(self._current_id, due_date)

    def _on_complete_toggled(self) -> None:
        if self._current_id is None:
            return
        self.task_complete_toggled.emit(self._current_id)

    def _on_no_due_toggled(self, checked: bool) -> None:
        if self._loading_task:
            return
        if self._current_id is None:
            return
        self.due_date_edit.setEnabled(not checked)
        if checked:
            # Cleared
            self.task_due_date_changed.emit(self._current_id, None)
        else:
            # Set to whatever the picker currently shows
            self.task_due_date_changed.emit(
                self._current_id, self.due_date_edit.date().toString("yyyy-MM-dd")
            )

    def _on_due_date_changed(self, qdate: QDate) -> None:
        if self._loading_task:
            return
        if self._current_id is None:
            return
        if self.no_due_checkbox.isChecked():
            # The user changed the date while "no due date" is active;
            # do nothing -- the checkbox state is the source of truth.
            return
        self.task_due_date_changed.emit(
            self._current_id, qdate.toString("yyyy-MM-dd")
        )

    def _on_project_changed(self, index: int) -> None:
        """User picked a different project (or "-- No Project --").

        Suppressed while we are programmatically setting the index
        (loading a memory, refreshing the options list, or after
        MainWindow applies a project change). Only fires for saved
        memories -- the combo is disabled for unsaved rows.
        """
        if self._loading_project:
            return
        if self._current_id is None:
            return
        project_id = self.project_combo.itemData(index)
        # itemData returns whatever we stored -- ``None`` for the
        # "-- No Project --" placeholder, ``int`` for a real project.
        # Coerce defensively: if a stale QVariant sneaks through, treat
        # it as unassign rather than crash.
        if project_id is not None and not isinstance(project_id, int):
            project_id = None
        self.project_changed.emit(self._current_id, project_id)

    def _project_selected_id(self) -> int | None:
        """Return the project id currently selected, or None.

        Mirrors ``_on_project_changed``'s int-coercion so that what we
        save-and-restore on refresh is the same shape we read out.
        """
        pid = self.project_combo.currentData()
        if pid is not None and not isinstance(pid, int):
            return None
        return pid

    def _select_project_by_id(self, project_id: int) -> bool:
        """Set the combo's current index to ``project_id``'s row. Returns True on hit."""
        for i in range(self.project_combo.count()):
            if self.project_combo.itemData(i) == project_id:
                self.project_combo.setCurrentIndex(i)
                return True
        return False

    # ----- related helpers -----

    def _related_clear_chips(self) -> None:
        """Remove and discard every chip currently in the column.

        ``setParent(None)`` is the documented way to drop a widget
        from a layout so it can be garbage-collected; relying on
        ``del`` alone leaves the widget parented to the layout's
        owner, which keeps it alive and visible.
        """
        for chip in self._related_chips:
            chip.setParent(None)
            chip.deleteLater()
        self._related_chips = []

    def _refresh_related_empty_state(self) -> None:
        """Show the empty-state label iff there are no chips.

        Called by ``set_memory`` (after clearing) and by
        ``set_related`` (after re-populating). The chips box is
        shown whenever chips exist so the column has the height
        the layout needs.
        """
        has_chips = bool(self._related_chips)
        self.related_chips_box.setVisible(has_chips)
        self.related_empty_label.setVisible(not has_chips)

    def _on_add_related_clicked(self) -> None:
        """Open the picker dialog and emit link_added on accept.

        The candidate list is built from ``list_memories`` minus the
        currently-loaded memory and anything already shown as a chip.
        A simple substring filter over title drives the search box
        (no FTS round-trip -- the candidate set is small and the
        filter is purely UI-side).
        """
        if self._current_id is None:
            return
        # We import list_memories at the call site to avoid a
        # top-level import cycle; core.memory is a stable module
        # but this keeps the editor's import surface minimal.
        from core.memory import list_memories

        # ``self.window()`` walks the parent chain up to the
        # top-level QWidget (MainWindow) regardless of how many
        # intermediate containers the editor sits inside (e.g. the
        # QSplitter in main_window.py). ``self.parent()`` would
        # return the splitter, not the window.
        top = self.window()
        conn = getattr(top, "conn", None) if top is not None else None
        if conn is None:
            return
        all_memories = list_memories(conn)
        # Exclude the currently-loaded memory and already-linked
        # memories; the picker must never offer the user a no-op.
        excluded = self._related_ids | {self._current_id}
        candidates = [m for m in all_memories if m.id not in excluded]

        dialog = _AddRelatedDialog(candidates, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        other_id = dialog.selected_memory_id()
        if other_id is None:
            return
        # Emit the signal; MainWindow performs the DB write and
        # re-queries list_linked_memories, which calls us back via
        # set_related to update the chip list. We don't refresh the
        # chip list here because MainWindow is the source of truth.
        self.link_added.emit(self._current_id, other_id)

    # ----- helpers -----

    @staticmethod
    def _parse_tags(text: str) -> list[str]:
        return [t.strip() for t in text.split(",") if t.strip()]

    def _format_timestamps(self, m: Memory) -> str:
        return (
            f"Created {self._format_ts(m.created_at)}  •  "
            f"Updated {self._format_ts(m.updated_at)}"
        )

    @staticmethod
    def _format_ts(iso: str) -> str:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%b %d, %Y at %H:%M")
