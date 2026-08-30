"""Trash view -- list of soft-deleted memories with preview + restore/delete.

The view itself is purely a presentation surface; it never calls
``core``. Restore / permanent-delete / empty-trash are signalled to the
parent (``MainWindow``) which performs the actual DB writes and tells
the view to refresh.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.memory import Memory
from ui.strings import (
    DELETE_PERMANENT_CONFIRM_BODY,
    DELETE_PERMANENT_CONFIRM_TITLE,
    DELETE_PERMANENTLY,
    RESTORE,
    TRASH_DELETED_LABEL,
    TRASH_EMPTY_BTN,
    TRASH_EMPTY_CONFIRM_BODY,
    TRASH_EMPTY_CONFIRM_TITLE,
    TRASH_EMPTY_STATE,
    TRASH_PREVIEW_PLACEHOLDER,
    TRASH_TYPE_LABEL,
)


def _format_deleted_at(iso: str | None) -> str:
    """Human-readable deletion timestamp.

    Recent (<= 7 days) gets a relative label: "today", "yesterday",
    "3 days ago". Older deletions fall back to an absolute date so the
    user can still tell when something was removed without us trying
    to maintain a complicated "weeks/months ago" ladder.
    """
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso  # give up gracefully -- never crash on a bad stamp

    today = date.today()
    delta_days = (today - dt.date()).days
    if delta_days == 0:
        return "today"
    if delta_days == 1:
        return "yesterday"
    if 1 < delta_days <= 7:
        return f"{delta_days} days ago"
    return dt.strftime("%b %d, %Y")


def _format_row(m: Memory) -> str:
    """Build the list-row label: title, type badge, and removal stamp.

    The title is the anchor; type and "Removed X ago" trail it. A
    short content preview is too noisy here (the preview pane shows
    the full body) so we keep the row to title + meta.
    """
    parts = [m.title or "(untitled)"]
    if m.type:
        parts.append(m.type)
    removed = _format_deleted_at(m.deleted_at)
    if removed:
        parts.append(f"Removed {removed}")
    return "  •  ".join(parts)


def _format_preview(m: Memory) -> str:
    """Build the read-only preview text for ``m``."""
    lines: list[str] = []
    title = m.title or "(untitled)"
    lines.append(title)
    lines.append("")
    meta_bits: list[str] = []
    if m.type:
        meta_bits.append(f"{TRASH_TYPE_LABEL}: {m.type}")
    if m.deleted_at:
        meta_bits.append(f"{TRASH_DELETED_LABEL} {_format_deleted_at(m.deleted_at)}")
    if m.tags:
        meta_bits.append("Tags: " + ", ".join(m.tags))
    if meta_bits:
        lines.append("  •  ".join(meta_bits))
        lines.append("")
    lines.append(m.content or "")
    return "\n".join(lines)


class TrashView(QWidget):
    """The Trash view: list + preview + per-item actions + empty-trash.

    Layout (left-to-right):
        [ List of trashed items with Empty Trash button ] | [ Preview + Restore / Delete Permanently ]

    Signals are emitted upward; this widget never mutates the database.
    """

    empty_trash_requested = Signal()  # user clicked "Empty Trash"
    restore_requested = Signal(int)  # memory_id
    delete_permanent_requested = Signal(int)  # memory_id

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self._build_ui()
        self._wire_signals()

    # ----- public API -----

    def set_trash(self, memories: list[Memory], previous_id: int | None = None) -> None:
        """Populate the list. Re-select ``previous_id`` if still present.

        A no-op (clear preview) is performed if nothing is selected, so
        callers don't have to remember to clear state themselves.
        """
        self.list_widget.clear()
        self.preview.clear()
        self.preview.setPlaceholderText(TRASH_PREVIEW_PLACEHOLDER)
        for m in memories:
            item = QListWidgetItem(_format_row(m))
            item.setData(Qt.UserRole, m.id)
            self.list_widget.addItem(item)
        # Empty-trash button enables only when there is something to empty.
        self.empty_btn.setEnabled(len(memories) > 0)

        # Restore prior selection, otherwise select the first row so the
        # preview is populated right away (better default than "blank").
        if previous_id is not None:
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                if item.data(Qt.UserRole) == previous_id:
                    self.list_widget.setCurrentRow(i)
                    return
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        else:
            self._sync_action_buttons()

    def current_memory_id(self) -> int | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def has_items(self) -> bool:
        return self.list_widget.count() > 0

    # ----- UI construction -----

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # --- left column: Empty Trash button + list ---
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        self.empty_btn = QPushButton(TRASH_EMPTY_BTN)
        # Destructive: this one empties the trash for good.
        self.empty_btn.setObjectName("dangerButton")
        self.empty_btn.setEnabled(False)  # until items arrive
        left_layout.addWidget(self.empty_btn)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SingleSelection)
        self.list_widget.setAlternatingRowColors(True)
        left_layout.addWidget(self.list_widget, 1)

        outer.addWidget(left, 1)

        # --- right column: preview + per-item actions ---
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText(TRASH_PREVIEW_PLACEHOLDER)
        right_layout.addWidget(self.preview, 1)

        action_row = QHBoxLayout()
        self.restore_btn = QPushButton(RESTORE)
        self.restore_btn.setEnabled(False)
        action_row.addWidget(self.restore_btn)

        action_row.addStretch()

        self.delete_perm_btn = QPushButton(DELETE_PERMANENTLY)
        # Destructive, and unlike the trash itself there is no undo.
        self.delete_perm_btn.setObjectName("dangerButton")
        self.delete_perm_btn.setEnabled(False)
        action_row.addWidget(self.delete_perm_btn)
        right_layout.addLayout(action_row)

        outer.addWidget(right, 2)

    def _wire_signals(self) -> None:
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        self.empty_btn.clicked.connect(self._on_empty_clicked)
        self.restore_btn.clicked.connect(self._on_restore_clicked)
        self.delete_perm_btn.clicked.connect(self._on_delete_perm_clicked)

    # ----- slots -----

    def _on_selection_changed(self, current, previous) -> None:
        self._sync_action_buttons()
        if current is None:
            self.preview.clear()
            return
        mid = current.data(Qt.UserRole)
        # Look up the memory fresh from the DB so the preview always
        # reflects authoritative state, not whatever we cached on the
        # list row at population time.
        from core.memory import get_memory

        m = get_memory(self.conn, mid)
        if m is None:
            self.preview.clear()
            return
        self.preview.setPlainText(_format_preview(m))

    def _on_empty_clicked(self) -> None:
        reply = QMessageBox.question(
            self,
            TRASH_EMPTY_CONFIRM_TITLE,
            TRASH_EMPTY_CONFIRM_BODY,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.empty_trash_requested.emit()

    def _on_restore_clicked(self) -> None:
        mid = self.current_memory_id()
        if mid is None:
            return
        # Restore is low-risk -- the item is rehydrated as-is, not
        # modified, and the user can always delete it again. Skip the
        # confirmation dialog to keep the flow quick.
        self.restore_requested.emit(mid)

    def _on_delete_perm_clicked(self) -> None:
        mid = self.current_memory_id()
        if mid is None:
            return
        reply = QMessageBox.question(
            self,
            DELETE_PERMANENT_CONFIRM_TITLE,
            DELETE_PERMANENT_CONFIRM_BODY,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.delete_permanent_requested.emit(mid)

    # ----- helpers -----

    def _sync_action_buttons(self) -> None:
        has_selection = self.list_widget.currentItem() is not None
        self.restore_btn.setEnabled(has_selection)
        self.delete_perm_btn.setEnabled(has_selection)
