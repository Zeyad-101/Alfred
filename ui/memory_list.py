"""Memory list widget -- pinned-first rows with a content preview."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from core.memory import Memory


# Glyph used to mark a pinned row. A plain text bullet is intentional -- it
# renders consistently across systems and keeps the butler's tone quiet.
PIN_GLYPH = "●  "


def _truncate_preview(content: str, max_len: int = 60) -> str:
    """Flatten whitespace and trim to a single-line preview."""
    flat = " ".join(content.split())
    if len(flat) > max_len:
        return flat[: max_len - 1].rstrip() + "…"
    return flat


class MemoryListWidget(QListWidget):
    """A QListWidget whose rows are Alfred memories.

    Each row stores its ``memory.id`` in ``Qt.UserRole`` so the parent
    window can re-select a row by id after a refresh without needing to
    compare titles or other display state.
    """

    item_selected = Signal(int)  # emits memory_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.currentItemChanged.connect(self._on_current_changed)

    # ----- public API -----

    def set_memories(self, memories: list[Memory]) -> None:
        """Replace the current rows with ``memories`` (clears any selection)."""
        self.clear()
        for m in memories:
            item = QListWidgetItem(self._format_row(m))
            item.setData(Qt.UserRole, m.id)
            if m.is_pinned:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.addItem(item)

    def current_memory_id(self) -> int | None:
        item = self.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def select_memory(self, memory_id: int) -> bool:
        """Make ``memory_id`` the current row. Returns False if not found."""
        for i in range(self.count()):
            item = self.item(i)
            if item.data(Qt.UserRole) == memory_id:
                self.setCurrentItem(item)
                return True
        return False

    # ----- formatting -----

    @staticmethod
    def _format_row(m: Memory) -> str:
        prefix = PIN_GLYPH if m.is_pinned else "   "
        preview = _truncate_preview(m.content)
        if preview:
            return f"{prefix}{m.title}  —  {preview}"
        return f"{prefix}{m.title}"

    # ----- signal wiring -----

    def _on_current_changed(self, current, previous) -> None:
        if current is None:
            return
        mid = current.data(Qt.UserRole)
        if mid is not None:
            self.item_selected.emit(mid)
