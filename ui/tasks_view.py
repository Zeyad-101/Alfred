"""Tasks view -- QTreeWidget with three bucket top-level items."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget

from core.memory import Memory
from ui.strings import (
    BUCKET_COMPLETED,
    BUCKET_TODAY,
    BUCKET_UPCOMING,
    TASKS_EMPTY,
)


# Order in which buckets appear top-to-bottom: Today (due today or
# overdue), Upcoming (due later, or never dated), Completed. Three
# buckets, and between them every task is in exactly one.
_BUCKET_ORDER: tuple[tuple[str, str], ...] = (
    ("today", BUCKET_TODAY),
    ("upcoming", BUCKET_UPCOMING),
    ("completed", BUCKET_COMPLETED),
)


def _format_task_row(
    memory: Memory, due_date: str | None, completed_at: str | None
) -> str:
    """Build the label for a task row.

    Title is the anchor; due date (if any) and a completion marker (for
    completed tasks) are appended with em-dash separators. We do not
    render the task body -- the editor pane owns that -- only enough
    context to scan the bucket without opening each one.
    """
    parts = [memory.title]
    if due_date:
        parts.append(f"Due {due_date}")
    if completed_at:
        parts.append("Completed")
    return "  —  ".join(parts)


class TasksTreeWidget(QTreeWidget):
    """Three-bucket task list.

    Top-level items are the buckets; their children are the tasks in
    that bucket. Selecting a child emits ``item_selected`` with the
    task's memory id. Selecting a bucket header is a no-op (bucket
    items have no UserRole payload).
    """

    item_selected = Signal(int)  # memory_id

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setSelectionMode(QTreeWidget.SingleSelection)
        self.setExpandsOnDoubleClick(False)
        self.currentItemChanged.connect(self._on_current_changed)
        # Track which bucket is which so we can rebuild after refresh
        # and so MainWindow can ask "is this memory still in its bucket?".
        self._bucket_items: dict[str, QTreeWidgetItem] = {}
        self._build_skeleton()

    # ----- public API -----

    def set_tasks(
        self,
        tasks_by_bucket: dict[str, list[tuple[Memory, str | None, str | None]]],
    ) -> None:
        """Replace each bucket's children with ``tasks_by_bucket[bucket]``.

        Buckets not in the dict render empty. The skeleton (the three
        top-level items) is created once in ``__init__`` and never torn
        down, so the tree's expand state and selection state survive a
        refresh.
        """
        for bucket_key, label in _BUCKET_ORDER:
            top = self._bucket_items[bucket_key]
            # Detach all existing children. removeChild is the documented
            # way to drop a child item -- setHidden on each child would
            # leave them in the model.
            for child in list(top.takeChildren()):
                del child  # let GC reclaim
            tasks = tasks_by_bucket.get(bucket_key, [])
            for memory, due_date, completed_at in tasks:
                child = QTreeWidgetItem([_format_task_row(memory, due_date, completed_at)])
                child.setData(0, Qt.UserRole, memory.id)
                top.addChild(child)
            # Bold the bucket label so empty buckets still stand out a
            # little (e.g. "Today" is a real bucket even when empty).
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            # Show "(0)" suffix when empty? No -- keep labels clean. The
            # empty bucket still expands so the user sees the structure.

    def select_memory(self, memory_id: int) -> bool:
        """Re-select the row for ``memory_id`` across all buckets.

        Returns True if found. Used by MainWindow after a refresh to
        restore the previously selected task's selection.
        """
        for top in self._bucket_items.values():
            for i in range(top.childCount()):
                child = top.child(i)
                if child.data(0, Qt.UserRole) == memory_id:
                    self.setCurrentItem(child)
                    return True
        return False

    def current_memory_id(self) -> int | None:
        item = self.currentItem()
        if item is None:
            return None
        # Top-level bucket items have no memory id; selection on them
        # returns None so MainWindow can treat that as "nothing
        # useful selected".
        if item.parent() is None:
            return None
        return item.data(0, Qt.UserRole)

    def has_any_tasks(self) -> bool:
        """True iff any bucket has at least one child."""
        return any(top.childCount() > 0 for top in self._bucket_items.values())

    # ----- internals -----

    def _build_skeleton(self) -> None:
        for bucket_key, label in _BUCKET_ORDER:
            top = QTreeWidgetItem([label])
            self.addTopLevelItem(top)
            top.setExpanded(True)
            self._bucket_items[bucket_key] = top

    def _on_current_changed(self, current, previous) -> None:
        if current is None:
            return
        if current.parent() is None:
            # Bucket header -- no memory to load.
            return
        mid = current.data(0, Qt.UserRole)
        if mid is not None:
            self.item_selected.emit(mid)
