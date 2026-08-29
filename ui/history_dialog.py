"""History dialog -- list versions, preview, restore."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from core.memory import MemoryVersion, get_version, list_versions, restore_version
from ui.strings import (
    CLOSE,
    HISTORY_DIALOG_TITLE,
    HISTORY_EMPTY,
    RESTORE_CONFIRM_BODY,
    RESTORE_CONFIRM_TITLE,
    RESTORE_VERSION,
)


class HistoryDialog(QDialog):
    """A modal dialog showing a memory's version history.

    Layout: a list of versions on top (newest first), a read-only
    preview pane below showing the currently selected version's full
    title + content, and Restore / Close buttons at the bottom.
    """

    def __init__(self, conn: sqlite3.Connection, memory_id: int, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.memory_id = memory_id
        self.setWindowTitle(HISTORY_DIALOG_TITLE)
        self.resize(640, 520)
        self._build_ui()
        self._populate()

    # ----- UI -----

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self.version_list = QListWidget()
        self.version_list.currentItemChanged.connect(self._on_version_selected)
        layout.addWidget(self.version_list, 1)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("")
        layout.addWidget(self.preview, 2)

        button_row = QHBoxLayout()
        self.restore_btn = QPushButton(RESTORE_VERSION)
        self.restore_btn.setEnabled(False)
        self.restore_btn.clicked.connect(self._on_restore)
        button_row.addWidget(self.restore_btn)
        button_row.addStretch()

        close_btn = QPushButton(CLOSE)
        close_btn.clicked.connect(self.accept)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)

    def _populate(self) -> None:
        versions = list_versions(self.conn, self.memory_id)
        if not versions:
            self.preview.setPlainText(HISTORY_EMPTY)
            self.restore_btn.setEnabled(False)
            return
        for v in versions:
            item = QListWidgetItem(self._format_version_row(v))
            item.setData(Qt.UserRole, v.id)
            self.version_list.addItem(item)
        # Auto-select the newest version so the preview pane is populated
        # as soon as the dialog opens.
        self.version_list.setCurrentRow(0)

    # ----- formatting -----

    @staticmethod
    def _format_version_row(v: MemoryVersion) -> str:
        ts = HistoryDialog._format_ts(v.saved_at)
        preview = HistoryDialog._truncate(v.content, 60)
        if preview:
            return f"{ts}\n   {preview}"
        return ts

    @staticmethod
    def _format_ts(iso: str) -> str:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%b %d, %Y at %H:%M")

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        flat = " ".join(text.split())
        if len(flat) > max_len:
            return flat[: max_len - 1].rstrip() + "…"
        return flat

    # ----- slots -----

    def _on_version_selected(self, current, previous) -> None:
        if current is None:
            self.preview.clear()
            self.restore_btn.setEnabled(False)
            return
        version_id = current.data(Qt.UserRole)
        v = get_version(self.conn, version_id)
        if v is None:
            self.preview.clear()
            self.restore_btn.setEnabled(False)
            return
        # Title on its own line, blank line, then content. The preview
        # pane is read-only so the user can't accidentally edit history.
        self.preview.setPlainText(f"{v.title}\n\n{v.content}")
        self.restore_btn.setEnabled(True)

    def _on_restore(self) -> None:
        current = self.version_list.currentItem()
        if current is None:
            return
        version_id = current.data(Qt.UserRole)
        reply = QMessageBox.question(
            self,
            RESTORE_CONFIRM_TITLE,
            RESTORE_CONFIRM_BODY,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        # restore_version() takes care of snapshotting the current
        # state first, then overwriting. We don't have to do anything
        # else here -- the main window will reload the memory after
        # the dialog closes.
        restore_version(self.conn, self.memory_id, version_id)
        self.accept()
