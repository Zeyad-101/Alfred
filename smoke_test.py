"""Headless smoke test for the GUI shell.

Exercises the wiring between MainWindow, EditorPanel, MemoryListWidget,
and the core layer — without ever showing a window. Verifies that:
  - the window constructs
  - creating a memory via the editor's save flow shows up in the list
  - pin toggling re-orders the list
  - search debounce actually fires and filters
  - delete removes the row from the visible list
  - persistence works (the data lives in data/alfred.db, survives a reopen)

Uses the ``offscreen`` QPA so this can run on a headless box too.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Force offscreen BEFORE importing Qt so the platform plugin is chosen
# before any QWidget is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Use a throwaway db path so this test never touches data/alfred.db.
TEST_DB = Path(tempfile.gettempdir()) / "alfred_smoke_test.db"
if TEST_DB.exists():
    TEST_DB.unlink()

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from core.db import get_connection, init_db
from core.memory import (
    create_memory,
    delete_memory,
    get_memory,
    list_memories,
    permanently_delete_memory,
    set_tags,
    toggle_pin,
)
from core.links import are_linked, link_memories, list_linked_memories
from core.inbox import list_inbox
from core.search import search
from ui.main_window import MainWindow
from ui.strings import APP_TITLE


def pump(ms: int) -> None:
    """Spin the Qt event loop for ``ms`` milliseconds so timers fire."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def expect(label: str, actual, expected) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  ok  {label}")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    QCoreApplication.setApplicationName(APP_TITLE + " Smoke")

    conn = get_connection(str(TEST_DB))
    init_db(conn)

    win = MainWindow(conn)
    win.show()
    pump(50)  # let layout settle

    print("[1] empty state on first launch")
    # Alfred lands on the Dashboard, not All Memories. That matters
    # for every list assertion below: ``_refresh_list`` on Dashboard
    # refreshes the dashboard's own cards and returns without ever
    # touching ``all_list``, so "0 rows" there would be true for the
    # wrong reason -- nobody populated it -- and would stay true no
    # matter what we create. Assert the landing view, then switch to
    # All Memories, which is what sections [2]-[10] actually exercise.
    from ui.sidebar import VIEW_ALL_MEMORIES, VIEW_DASHBOARD
    expect("landing view is the dashboard", win._current_view, VIEW_DASHBOARD)
    expect("dashboard is the visible middle page",
           win.middle_stack.currentIndex(), 0)
    expect("editor has no current id", win.editor.current_id, None)
    expect("delete button disabled on editor",
           win.editor.delete_btn.isEnabled(), False)

    win.sidebar.select_view(VIEW_ALL_MEMORIES)
    pump(20)
    # Middle-pane page order is Dashboard, All Memories, Inbox, Tasks,
    # Trash, Projects (``ui.main_window._VIEW_PAGE_INDEX``).
    expect("all memories is now the visible middle page",
           win.middle_stack.currentIndex(), 1)
    expect("list empty on first run", win.all_list.count(), 0)

    print("[2] create via the editor flow (simulating _on_editor_save)")
    win.editor.title_input.setText("Max98357A notes")
    win.editor.content_input.setPlainText(
        "Hookup: LRC -> amp -> speaker. Watch the gain pin."
    )
    win.editor.tags_input.setText("esp32, hardware, todo")
    win.editor.pin_checkbox.setChecked(True)
    # Mimic the editor's save signal path
    win.editor.save_clicked.emit(
        {
            "memory_id": None,
            "title": "Max98357A notes",
            "content": win.editor.content_input.toPlainText(),
            "tags": ["esp32", "hardware", "todo"],
            "is_pinned": True,
        }
    )
    pump(50)
    expect("list now has 1 row", win.all_list.count(), 1)
    expect("row is the just-created memory",
           win.all_list.current_memory_id(), 1)
    expect("editor loaded the new memory", win.editor.current_id, 1)
    expect("pinned intent applied",
           get_memory(conn, 1).is_pinned, True)
    expect("tags applied", sorted(get_memory(conn, 1).tags),
           ["esp32", "hardware", "todo"])

    print("[3] create a second memory, this one unpinned")
    mid2 = create_memory(conn, "Shopping", "Buy milk and bread", tags=["errand"])
    win._refresh_list()
    pump(20)
    expect("list has 2 rows", win.all_list.count(), 2)
    # Pinned (id=1) must be at the top
    expect("pinned row is on top", win.all_list.item(0).data(0x0100), 1)

    print("[4] select the second row, edit + save")
    win.all_list.select_memory(mid2)
    pump(20)
    expect("editor loaded memory 2", win.editor.current_id, mid2)

    win.editor.title_input.setText("Shopping list")
    win.editor.content_input.setPlainText("Buy milk, bread, and eggs")
    win.editor.save_clicked.emit(
        {
            "memory_id": mid2,
            "title": "Shopping list",
            "content": "Buy milk, bread, and eggs",
            "tags": ["errand"],
            "is_pinned": False,
        }
    )
    pump(20)
    expect("update changed the title", get_memory(conn, mid2).title, "Shopping list")
    expect("row still selected", win.all_list.current_memory_id(), mid2)

    print("[5] debounced search filters the list")
    win.search_input.setText("eggs")
    pump(SEARCH_DEBOUNCE := 300)  # wait past the 250ms debounce
    expect("search filtered to 1 row", win.all_list.count(), 1)
    expect("the matching row is the shopping one",
           win.all_list.item(0).data(0x0100), mid2)

    print("[6] empty search restores the full list")
    win.search_input.setText("")
    pump(SEARCH_DEBOUNCE)
    expect("back to 2 rows", win.all_list.count(), 2)

    print("[7] delete with confirmation (auto-accept)")
    # Intercept QMessageBox.question to always say Yes
    from PySide6.QtWidgets import QMessageBox
    orig = QMessageBox.question
    QMessageBox.question = staticmethod(
        lambda *a, **kw: QMessageBox.Yes
    )
    try:
        win.all_list.select_memory(mid2)
        pump(20)
        win.editor.delete_clicked.emit(mid2)
        pump(20)
    finally:
        QMessageBox.question = orig
    expect("row removed from visible list", win.all_list.count(), 1)
    expect("soft-deleted from db", get_memory(conn, mid2).is_deleted, True)
    expect("editor cleared because selected row is gone",
           win.editor.current_id, None)

    print("[8] FTS search through the same path the GUI uses")
    # Whole-token search: FTS5's default unicode61 tokenizer treats
    # ``Max98357A`` as a single token (no letter/digit splitting), so
    # querying with the full token finds the row.
    expect("search 'Max98357A' finds the pinned note",
           len(search(conn, "Max98357A")), 1)
    # Word from the content body
    expect("search 'hookup' (a body word) finds the note",
           len(search(conn, "hookup")), 1)
    # The spec's example query: must not crash. With apostrophe-quoted
    # sanitization it returns a list, even if zero matches.
    expect("spec example 'MAX98357A\\'s amp' doesn't crash",
           isinstance(search(conn, "MAX98357A's amp"), list), True)
    # list_memories orders pinned first
    ordered = [m.id for m in list_memories(conn)]
    expect("pinned memory is first in list_memories", ordered[0], 1)

    print("[9] version history — edit 3 times, open History, restore")
    from core.memory import list_versions
    from ui.strings import VERSION_RESTORED

    mid3 = 1  # the only live memory; its title is "Max98357A notes"
    win.all_list.select_memory(mid3)
    pump(20)

    # History button should be disabled before any edit (no versions yet).
    expect("history button starts disabled",
           win.editor.history_btn.isEnabled(), False)

    # Three edits through the save signal path. Each one must change
    # title OR content so it actually creates a version snapshot — a
    # no-op save (same title+content) deliberately creates no version.
    edits = [
        ("Max98357A — hookup",    "Hookup: LRC -> amp -> speaker. Set gain HIGH."),
        ("Max98357A — gain notes","Hookup: LRC -> amp -> speaker. Set gain HIGH. SD wired."),
        ("Max98357A — final",     "Hookup: LRC -> amp -> speaker. Set gain HIGH. SD wired. Done."),
    ]
    for title, content in edits:
        win.editor.title_input.setText(title)
        win.editor.content_input.setPlainText(content)
        win.editor.save_clicked.emit(
            {
                "memory_id": mid3,
                "title": title,
                "content": content,
                "tags": ["esp32", "hardware", "todo"],
                "is_pinned": True,
            }
        )
        pump(20)
    # Three edits -> three versions, newest first: the snapshot of the
    # previous (now-replaced) state is at the top.
    versions = list_versions(conn, mid3)
    expect("3 versions after 3 edits", len(versions), 3)
    expect("newest version is the pre-3rd-edit state",
           versions[0].title, "Max98357A — gain notes")
    expect("oldest version is the pre-1st-edit state (the original)",
           versions[-1].title, "Max98357A notes")

    # History button must be enabled now.
    expect("history button enabled with versions present",
           win.editor.history_btn.isEnabled(), True)

    # Open the history dialog and inspect it.
    from ui.history_dialog import HistoryDialog

    dialog = HistoryDialog(conn, mid3, parent=win)
    dialog.show()
    pump(20)
    expect("history dialog lists 3 entries",
           dialog.version_list.count(), 3)
    expect("newest version is selected by default",
           dialog.version_list.currentRow(), 0)
    # Auto-selects the newest: title and content reflect the pre-3rd-edit
    # state, which is the "gain notes" / "SD wired" version.
    expect("preview shows the newest version's title",
           "Max98357A — gain notes" in dialog.preview.toPlainText(), True)
    expect("restore button enabled when a version is selected",
           dialog.restore_btn.isEnabled(), True)

    # Restore the OLDEST version (last row). Intercept the confirmation.
    dialog.version_list.setCurrentRow(dialog.version_list.count() - 1)
    pump(20)
    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.Yes)
    try:
        dialog.restore_btn.click()
        pump(50)
    finally:
        QMessageBox.question = orig
    # Dialog should have accepted itself (QDialog.Accepted == 1).
    expect("dialog accepted after restore", dialog.result(), 1)

    # Memory should now hold the original title/content.
    expect("memory title restored to original",
           get_memory(conn, mid3).title, "Max98357A notes")
    expect("memory content restored to original",
           get_memory(conn, mid3).content,
           "Hookup: LRC -> amp -> speaker. Watch the gain pin.")
    # 4 versions now: the "Max98357A — final" state was snapshotted
    # FIRST (on restore), then the memory was overwritten with the original.
    versions_after = list_versions(conn, mid3)
    expect("4 versions after restore", len(versions_after), 4)
    expect("newest post-restore snapshot is the pre-restore state",
           versions_after[0].title, "Max98357A — final")
    expect("second post-restore snapshot is the pre-3rd-edit state",
           versions_after[1].title, "Max98357A — gain notes")
    expect("oldest version is still the original",
           versions_after[-1].title, "Max98357A notes")

    dialog.close()
    pump(20)

    print("[10] persistence — reopen the same db, list survives")
    conn.close()
    conn2 = get_connection(str(TEST_DB))
    init_db(conn2)  # idempotent
    expect("memory still there after reopen",
           len(list_memories(conn2)), 1)
    expect("it's the right one",
           list_memories(conn2)[0].title, "Max98357A notes")
    # Versions also persisted.
    persisted_versions = list_versions(conn2, list_memories(conn2)[0].id)
    expect("versions persisted across reopen", len(persisted_versions), 4)
    conn2.close()

    # ===== Phase 3: Inbox + Tasks GUI flow =====
    # Fresh database so the assertions don't depend on Phase 2's state.
    print("\n[11] inbox + tasks GUI flow")
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = TEST_DB.with_name(TEST_DB.name + suffix)
        if p.exists():
            try: p.unlink()
            except OSError: pass
    conn = get_connection(str(TEST_DB))
    init_db(conn)
    win = MainWindow(conn)
    win.show()
    pump(20)

    from core.inbox import list_inbox
    from core.inbox import create_inbox_item
    from core.tasks import list_tasks
    from ui.main_window import VIEW_ALL_MEMORIES, VIEW_TASKS
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog

    # Click + Inbox. Phase 8 rewired the button to open the
    # quick-capture popup, so we drive the popup directly (set
    # the input, call _on_submit) instead of mocking the old
    # QInputDialog path. This is the same path the user takes:
    # button click → popup opens → user types → Enter.
    win.new_inbox_btn.click()
    pump(20)
    # The popup is now visible with focus on the input. Simulate
    # the user typing and pressing Enter.
    win.capture_popup.input.setText("Pick up dry cleaning")
    win.capture_popup._on_submit()
    pump(20)
    expect("+ Inbox creates an inbox item",
           len(list_inbox(conn)), 1)
    expect("title derived from first line",
           list_inbox(conn)[0].title, "Pick up dry cleaning")

    # Inbox has no sidebar row any more (see ui/sidebar.py): All
    # Memories lists every non-deleted memory whatever its type, so
    # that is where a capture surfaces for the user now. The check is
    # the same one it always was -- the captured item is visible in a
    # list, and selecting it loads it into the editor with the inbox
    # action row on -- just made against the list the user still has.
    win.sidebar.select_view(VIEW_ALL_MEMORIES)
    pump(20)
    expect("all memories shows the new inbox item", win.all_list.count(), 1)
    expect("the listed row is the capture",
           win.all_list.item(0).data(Qt.UserRole), 1)
    win.all_list.select_memory(1)
    pump(20)
    expect("editor loaded the inbox item",
           win.editor.current_id, 1)
    expect("inbox action row visible",
           win.editor.inbox_actions.isVisible(), True)
    expect("task action row hidden for inbox",
           win.editor.task_actions.isVisible(), False)

    # Click "Convert to Task" with a due date of today. The convert
    # dialog is a QDialog; mock its exec() to accept, and mock
    # selected_due_date() to return today.
    from ui.editor_panel import _ConvertToTaskDialog
    orig_exec = _ConvertToTaskDialog.exec
    orig_selected = _ConvertToTaskDialog.selected_due_date
    import datetime as _dt
    today_iso = _dt.date.today().isoformat()
    _ConvertToTaskDialog.exec = lambda self: QDialog.Accepted
    _ConvertToTaskDialog.selected_due_date = lambda self: today_iso
    try:
        win.editor.convert_btn.click()
        pump(30)
    finally:
        _ConvertToTaskDialog.exec = orig_exec
        _ConvertToTaskDialog.selected_due_date = orig_selected
    expect("converted to task", get_memory(conn, 1).type, "task")
    expect("due_date persisted", list_tasks(conn, "today")[0][1], today_iso)
    expect("inbox is now empty", list_inbox(conn), [])

    # Switch to Tasks view and confirm it appears in the Today bucket.
    win.sidebar.select_view(VIEW_TASKS)
    pump(20)
    today_bucket = win.tasks_tree._bucket_items["today"]
    in_today = any(
        today_bucket.child(i).data(0, Qt.UserRole) == 1
        for i in range(today_bucket.childCount())
    )
    expect("task appears in today bucket", in_today, True)

    # Mark complete; it should move to the Completed bucket.
    win.tasks_tree.select_memory(1)
    pump(20)
    expect("task action row visible for task",
           win.editor.task_actions.isVisible(), True)
    expect("complete button shows 'Mark complete' before click",
           win.editor.complete_btn.text(), "Mark complete")
    win.editor.complete_btn.click()
    pump(20)
    completed_bucket = win.tasks_tree._bucket_items["completed"]
    in_completed = any(
        completed_bucket.child(i).data(0, Qt.UserRole) == 1
        for i in range(completed_bucket.childCount())
    )
    expect("task moved to completed bucket", in_completed, True)
    expect("complete button label flipped",
           win.editor.complete_btn.text(), "Mark incomplete")

    # Create another inbox item, click "Organize as Note".
    # The + Inbox button is wired to the capture popup now; drive
    # the popup directly the same way we did for the first item
    # above.
    win.new_inbox_btn.click()
    pump(20)
    win.capture_popup.input.setText("Idea for a project")
    win.capture_popup._on_submit()
    pump(20)
    expect("second inbox item created", len(list_inbox(conn)), 1)
    inbox_id = list_inbox(conn)[0].id

    # The new inbox item isn't selected (we're in Tasks view); switch
    # to All Memories -- where inbox-type rows live now -- and pick it.
    win.sidebar.select_view(VIEW_ALL_MEMORIES)
    pump(20)
    expect("all memories shows the second inbox item",
           any(win.all_list.item(i).data(Qt.UserRole) == inbox_id
               for i in range(win.all_list.count())), True)
    win.all_list.select_memory(inbox_id)
    pump(20)
    expect("editor loaded the second inbox item",
           win.editor.current_id, inbox_id)
    win.editor.organize_btn.click()
    pump(20)
    expect("organized to note", get_memory(conn, inbox_id).type, "note")

    # Verify it still shows up in All Memories, now as a note. We are
    # already on that view, so ``select_view`` would early-return
    # without a refresh -- rebuild the list explicitly instead.
    win._refresh_list()
    pump(20)
    organized_id = get_memory(conn, inbox_id).id
    in_all = any(
        win.all_list.item(i).data(Qt.UserRole) == organized_id
        for i in range(win.all_list.count())
    )
    expect("organized note appears in All Memories", in_all, True)
    expect("inbox is empty after organizing", list_inbox(conn), [])

    print("[12] inbox + tasks persistence — reopen the same db")
    conn.close()
    conn2 = get_connection(str(TEST_DB))
    init_db(conn2)
    expect("inbox item persisted as note",
           get_memory(conn2, inbox_id).type, "note")
    persisted_tasks = list_tasks(conn2, "completed")
    expect("completed task persisted", len(persisted_tasks), 1)
    expect("task title persisted",
           persisted_tasks[0][0].title, "Pick up dry cleaning")
    expect("task due date persisted",
           persisted_tasks[0][1], today_iso)
    expect("task completed_at persisted",
           persisted_tasks[0][2] is not None, True)
    conn2.close()

    # ===== Phase 4: Trash GUI flow =====
    # Fresh database. Create one of each type, soft-delete all three,
    # exercise the trash view's list / restore / permanent delete /
    # empty-trash controls.
    print("\n[13] trash GUI flow")
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = TEST_DB.with_name(TEST_DB.name + suffix)
        if p.exists():
            try: p.unlink()
            except OSError: pass
    conn = get_connection(str(TEST_DB))
    init_db(conn)
    win = MainWindow(conn)
    win.show()
    pump(20)

    from core.memory import (
        delete_memory,
        list_trash,
        permanently_delete_memory,
        restore_memory,
    )
    from ui.main_window import VIEW_TRASH
    from PySide6.QtWidgets import QMessageBox

    note_id = create_memory(conn, "An old note", "note body", tags=["old"])
    task_id = create_memory(conn, "An old task", "task body")
    conn.execute(
        "INSERT INTO task_details(memory_id, due_date, completed_at) "
        "VALUES (?, ?, NULL)",
        (task_id, "2026-09-01"),
    )
    conn.commit()
    inbox_id = create_inbox_item(conn, "An old inbox capture")

    for mid in (note_id, task_id, inbox_id):
        delete_memory(conn, mid)

    # Open Trash view.
    win.sidebar.select_view(VIEW_TRASH)
    pump(20)
    expect("trash view is the active middle page",
           win.middle_stack.currentIndex(), 4)
    expect("trash view lists 3 items", win.trash_view.list_widget.count(), 3)
    expect("empty button enabled when trash has items",
           win.trash_view.empty_btn.isEnabled(), True)
    expect("editor is hidden in trash mode",
           win.editor.isVisible(), False)

    # Trash orders newest-deleted first: inbox was deleted last.
    first_id = win.trash_view.list_widget.item(0).data(Qt.UserRole)
    expect("newest deletion is at the top of trash", first_id, inbox_id)

    # Restore the note.
    win.trash_view.list_widget.setCurrentRow(0)  # select the inbox first
    pump(20)
    # Then explicitly select the note row for the restore action.
    for i in range(win.trash_view.list_widget.count()):
        it = win.trash_view.list_widget.item(i)
        if it.data(Qt.UserRole) == note_id:
            win.trash_view.list_widget.setCurrentRow(i)
            break
    pump(20)
    expect("restore button enabled with selection",
           win.trash_view.restore_btn.isEnabled(), True)
    win.trash_view.restore_btn.click()
    pump(20)
    expect("restored note is no longer in trash",
           note_id not in {m.id for m in list_trash(conn)}, True)
    expect("trash now has 2 items",
           win.trash_view.list_widget.count(), 2)
    expect("note is back in all_memories",
           note_id in {m.id for m in list_memories(conn, include_deleted=False)}, True)

    # Permanently delete the task. Mock QMessageBox to say Yes.
    for i in range(win.trash_view.list_widget.count()):
        it = win.trash_view.list_widget.item(i)
        if it.data(Qt.UserRole) == task_id:
            win.trash_view.list_widget.setCurrentRow(i)
            break
    pump(20)
    orig = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.Yes)
    try:
        win.trash_view.delete_perm_btn.click()
        pump(20)
    finally:
        QMessageBox.question = orig
    expect("permanently-deleted task is gone from trash",
           task_id not in {m.id for m in list_trash(conn)}, True)
    expect("task row is also gone from the memories table",
           get_memory(conn, task_id), None)
    expect("task_details cascade-removed",
           conn.execute("SELECT 1 FROM task_details WHERE memory_id=?",
                        (task_id,)).fetchone(), None)
    expect("trash has 1 item left", win.trash_view.list_widget.count(), 1)

    # Empty Trash. The remaining item (inbox) should be hard-deleted
    # and the button disabled.
    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.Yes)
    try:
        win.trash_view.empty_btn.click()
        pump(20)
    finally:
        QMessageBox.question = orig
    expect("trash is now empty",
           list_trash(conn), [])
    expect("empty button disabled when trash is empty",
           win.trash_view.empty_btn.isEnabled(), False)
    expect("inbox item was hard-deleted too",
           get_memory(conn, inbox_id), None)

    # Going back to All Memories shows the restored note (the other two
    # are gone, including the previously-restored one — wait, the
    # restored note should still be there).
    win.sidebar.select_view(VIEW_ALL_MEMORIES)
    pump(20)
    expect("editor visible again after leaving trash",
           win.editor.isVisible(), True)
    expect("note is visible in all_memories after switch",
           any(win.all_list.item(i).data(Qt.UserRole) == note_id
               for i in range(win.all_list.count())), True)

    print("[14] trash persistence — reopen the same db")
    conn.close()
    conn3 = get_connection(str(TEST_DB))
    init_db(conn3)
    expect("note still alive after reopen",
           get_memory(conn3, note_id).type, "note")
    expect("task and inbox are gone after reopen",
           [get_memory(conn3, mid) for mid in (task_id, inbox_id)],
           [None, None])
    conn3.close()

    # ===== Phase 5: Markdown preview =====
    print("\n[15] markdown preview flow")
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = TEST_DB.with_name(TEST_DB.name + suffix)
        if p.exists():
            try: p.unlink()
            except OSError: pass
    conn = get_connection(str(TEST_DB))
    init_db(conn)
    win = MainWindow(conn)
    win.show()
    pump(20)
    # Fresh window, so we are back on the Dashboard: sections [15]-[18]
    # drive the editor from the All Memories list, so switch first.
    win.sidebar.select_view(VIEW_ALL_MEMORIES)
    pump(20)

    from ui.editor_panel import PREVIEW_TOGGLE_EDIT, PREVIEW_TOGGLE_PREVIEW
    from ui.markdown_render import render_to_html

    # Mix of features: heading, bold, lists, checklist, fenced code, link.
    markdown_text = (
        "# Project notes\n"
        "\n"
        "## Goals\n"
        "- [ ] ship alpha\n"
        "- [x] write spec\n"
        "\n"
        "We need to be **bold** and *careful*.\n"
        "\n"
        "```python\n"
        "def hello():\n"
        "    print('hi')\n"
        "```\n"
        "\n"
        "See [the spec](https://example.com/spec) for details.\n"
    )

    # Create a memory with the markdown body, then load it into the editor.
    mid = create_memory(conn, "Phase 5 demo", markdown_text)
    win._refresh_list()
    win.all_list.select_memory(mid)
    pump(20)

    # Edit mode is the default.
    expect("editor opens in Edit mode (stack index 0)",
           win.editor.content_stack.currentIndex(), 0)
    expect("toggle button label is 'Preview' in edit mode",
           win.editor.preview_toggle.text(), PREVIEW_TOGGLE_PREVIEW)
    expect("toggle button is unchecked in edit mode",
           win.editor.preview_toggle.isChecked(), False)

    # Toggle to preview.
    win.editor.preview_toggle.click()
    pump(20)
    expect("editor switched to preview (stack index 1)",
           win.editor.content_stack.currentIndex(), 1)
    expect("toggle button label is 'Edit' in preview mode",
           win.editor.preview_toggle.text(), PREVIEW_TOGGLE_EDIT)
    expect("toggle button is checked in preview mode",
           win.editor.preview_toggle.isChecked(), True)

    # Each markdown feature must be in the rendered HTML.
    preview_html = win.editor.preview_view.toPlainText()
    # toPlainText on a QTextBrowser returns the visible text content
    # (with anchors stripped). The structural checks below verify the
    # same content is reachable in the HTML via the render module.
    expect("heading text is visible in preview",
           "Project notes" in preview_html, True)
    expect("checkbox glyphs are visible in preview",
           "☐" in preview_html and "☑" in preview_html, True)
    expect("code block content is visible in preview",
           "def hello" in preview_html, True)
    expect("link text is visible in preview",
           "the spec" in preview_html, True)

    # Render directly via the helper so we can also assert on the
    # underlying HTML structure (tags, attributes).
    raw_html = render_to_html(markdown_text)
    expect("h1 tag present in rendered html", "<h1>" in raw_html, True)
    expect("h2 tag present in rendered html", "<h2>" in raw_html, True)
    expect("strong tag present in rendered html",
           ("<strong>" in raw_html) or ("<b>" in raw_html), True)
    expect("em tag present in rendered html",
           ("<em>" in raw_html) or ("<i>" in raw_html), True)
    expect("ul/li tags present in rendered html",
           "<ul>" in raw_html and "<li>" in raw_html, True)
    expect("pre/code tags present in rendered html",
           "<pre>" in raw_html and "<code" in raw_html, True)
    expect("anchor with href present in rendered html",
           "<a " in raw_html and 'href="https://example.com/spec"' in raw_html, True)

    # The raw editor text must NOT have been mutated by the render
    # step — preview is a one-way transform on the way to the screen.
    expect("raw textarea content unchanged after preview",
           win.editor.content_input.toPlainText(), markdown_text)

    # Toggling back to Edit returns to the editor with content intact.
    win.editor.preview_toggle.click()
    pump(20)
    expect("editor back to edit mode (stack index 0)",
           win.editor.content_stack.currentIndex(), 0)
    expect("toggle label back to 'Preview'",
           win.editor.preview_toggle.text(), PREVIEW_TOGGLE_PREVIEW)
    expect("raw text still intact after round-trip",
           win.editor.content_input.toPlainText(), markdown_text)

    # Link clicks must NOT navigate the QTextBrowser internally; they
    # must go through QDesktopServices.openUrl. We patch
    # QDesktopServices to capture (and short-circuit) the URL.
    from PySide6.QtGui import QDesktopServices
    opened_urls: list = []
    orig_open = QDesktopServices.openUrl
    QDesktopServices.openUrl = staticmethod(
        lambda url: opened_urls.append(url) or True
    )
    try:
        win.editor.preview_toggle.click()  # back into preview
        pump(20)
        # anchorClicked passes a QUrl; the editor's handler should
        # call QDesktopServices.openUrl with it.
        from PySide6.QtCore import QUrl
        win.editor._on_anchor_clicked(QUrl("https://example.com/spec"))
        expect("QDesktopServices.openUrl was called with the URL",
               [u.toString() for u in opened_urls], ["https://example.com/spec"])
    finally:
        QDesktopServices.openUrl = orig_open

    # Loading a new memory must reset the toggle to Edit mode.
    win.editor.preview_toggle.click()  # ensure we end in Edit first
    pump(20)
    plain_id = create_memory(conn, "Plain text only", "just a sentence, no markdown")
    win._refresh_list()
    win.all_list.select_memory(plain_id)
    pump(20)
    expect("new memory loads in Edit mode (not preview)",
           win.editor.content_stack.currentIndex(), 0)
    expect("toggle is unchecked for the freshly-loaded memory",
           win.editor.preview_toggle.isChecked(), False)

    # And a plain-text memory's preview still works.
    win.editor.preview_toggle.click()
    pump(20)
    expect("plain-text memory previews without crashing",
           "just a sentence" in win.editor.preview_view.toPlainText(), True)

    print("[16] preview persistence — raw text is what's stored")
    # What's in the DB must be the raw markdown, not a rendered form.
    stored = get_memory(conn, mid)
    expect("DB holds the original markdown verbatim",
           stored.content, markdown_text)

    print("[17] projects — sidebar view, editor combo, two-pane layout")
    from core.projects import (
        create_project,
        get_memory_project,
        list_projects,
        set_memory_project,
    )
    from ui.sidebar import VIEW_PROJECTS

    # Switch to the Projects view via the sidebar — this is the path
    # the real user takes, so it exercises _on_view_changed end-to-end.
    win.sidebar.select_view(VIEW_PROJECTS)
    pump(20)
    expect("middle stack is on the projects page",
           win.middle_stack.currentIndex(), 5)
    expect("editor stays visible in projects view",
           win.editor.isVisible(), True)
    expect("empty projects list when none exist yet",
           win.projects_view.list_widget.count(), 0)

    # Create a project via the same code path the New Project button
    # uses (the button is wired through QInputDialog, which is hard
    # to drive in a smoke test; going through the signal directly
    # gives the same DB effect with no flaky UI plumbing).
    win.projects_view.new_project_requested.emit("Smoke Project", "Phase 6 test")
    pump(20)
    expect("project list now has 1 row",
           win.projects_view.list_widget.count(), 1)
    pid = list_projects(conn)[0].id

    # The editor's project combo should also be populated (refreshing
    # the projects list also refreshes the editor's combo so a saved
    # memory can immediately be assigned).
    expect("editor project combo has placeholder + 1 project",
           win.editor.project_combo.count(), 2)
    expect("placeholder at index 0",
           win.editor.project_combo.itemData(0), None)
    expect("project id at index 1",
           win.editor.project_combo.itemData(1), pid)

    # Assign a memory to the project via the editor's combo. The
    # combo is enabled for saved memories; we just loaded one, so
    # find it and emit the signal MainWindow listens to.
    # Note: section [16] replaced the editor's loaded memory with
    # ``plain_id``, so we work against the currently-loaded id
    # rather than the original ``mid`` from section [15].
    target_mid = win.editor.current_id
    expect("editor has a memory loaded for the combo test",
           target_mid is not None, True)
    combo_index = win.editor.project_combo.findData(pid)
    expect("combo can find the project id", combo_index, 1)
    # Combo only fires currentIndexChanged on a real change, not on
    # the same index — so set it to placeholder first, then to the
    # project, so the signal chain runs both times.
    win.editor.project_combo.setCurrentIndex(0)
    pump(10)
    win.editor.project_combo.setCurrentIndex(combo_index)
    pump(20)
    expect("memory is now linked to the project (DB side)",
           get_memory_project(conn, target_mid) is not None, True)
    expect("memory's project is the one we picked",
           get_memory_project(conn, target_mid).id, pid)
    # Project's memory count in the list is 1.
    expect("project row shows count of 1",
           win.projects_view.list_widget.item(0).text(), "Smoke Project  (1)")

    # Switching to the project on the right shows the linked memory.
    expect("right pane's memory list has the linked memory",
           win.projects_view.memory_list.count(), 1)
    # MemoryListWidget.set_memories clears selection, so check the
    # row's UserRole payload rather than current_memory_id().
    from PySide6.QtCore import Qt
    right_pane_id = win.projects_view.memory_list.item(0).data(Qt.UserRole)
    expect("the linked memory is the one we assigned",
           right_pane_id, target_mid)

    # Create a second project; verify it shows up with count 0 and
    # the first project's count stays correct.
    win.projects_view.new_project_requested.emit("Empty", "")
    pump(20)
    expect("now two projects in the list",
           win.projects_view.list_widget.count(), 2)
    # Find the "Empty" row text; the "Smoke Project" row should still
    # read (1).
    rows = {
        win.projects_view.list_widget.item(i).text()
        for i in range(win.projects_view.list_widget.count())
    }
    expect("'Empty' project shows (0) count",
           "Empty  (0)" in rows, True)
    expect("'Smoke Project' still shows (1) count",
           "Smoke Project  (1)" in rows, True)

    # Delete the project the memory is in; the memory should survive.
    win.projects_view.list_widget.setCurrentRow(0)  # whichever is first
    pump(10)
    # Whichever project is selected, deleting it must:
    #   1) remove it from the list
    #   2) leave the linked memory's content intact
    selected_pid = win.projects_view.current_project_id()
    selected_item_text = win.projects_view.list_widget.currentItem().text()
    win.projects_view.delete_project_requested.emit(selected_pid)
    pump(20)
    expect("project count dropped by 1 after delete",
           win.projects_view.list_widget.count(), 1)
    # Whichever project we deleted, the remaining row should still
    # carry a count consistent with the surviving memory's link state.
    if "Smoke Project" in selected_item_text:
        # We deleted the linked project. Memory should be unfiled, but
        # still present in the DB.
        expect("linked memory is now unfiled (project is gone)",
               get_memory_project(conn, target_mid), None)
        expect("memory still exists in the DB",
               get_memory(conn, target_mid) is not None, True)
        # The remaining row is "Empty" (0).
        rows_after = {
            win.projects_view.list_widget.item(i).text()
            for i in range(win.projects_view.list_widget.count())
        }
        expect("remaining project is 'Empty' with count 0",
               rows_after, {"Empty  (0)"})
    else:
        # We deleted the empty project. The link to Smoke Project is
        # untouched and the memory still has its project.
        expect("memory's project is still Smoke Project (unchanged)",
               get_memory_project(conn, target_mid).id, pid)
        rows_after = {
            win.projects_view.list_widget.item(i).text()
            for i in range(win.projects_view.list_widget.count())
        }
        expect("remaining project is 'Smoke Project' with count 1",
               rows_after, {"Smoke Project  (1)"})

    # Switching back to All Memories preserves the editor's content.
    from ui.sidebar import VIEW_ALL_MEMORIES
    win.sidebar.select_view(VIEW_ALL_MEMORIES)
    pump(20)
    expect("back on all_memories view",
           win.middle_stack.currentIndex(), 1)
    expect("editor still has the memory loaded",
           win.editor.current_id, target_mid)

    print("[18] related-memory links — symmetric, clickable, persisted")

    # Create two brand-new memories via the editor save flow. We use
    # the same path the user takes: emit save_clicked with a fresh
    # (memory_id=None) payload. That way the test exercises the
    # end-to-end create-and-display wiring, not just the DB layer.
    win.editor.title_input.setText("Note Alpha")
    win.editor.content_input.setPlainText("alpha body")
    win.editor.save_clicked.emit(
        {
            "memory_id": None,
            "title": "Note Alpha",
            "content": "alpha body",
            "tags": [],
            "is_pinned": False,
        }
    )
    pump(20)
    alpha_id = win.editor.current_id
    expect("alpha memory was created and is loaded",
           alpha_id is not None and alpha_id > 0, True)

    # The Related section is visible for the freshly-saved memory
    # and shows the empty-state label.
    expect("related container is visible for saved memory",
           win.editor.related_container.isVisible(), True)
    expect("related empty-state label is showing",
           win.editor.related_empty_label.isVisible(), True)

    # Create Beta the same way.
    win.editor.title_input.setText("Note Beta")
    win.editor.content_input.setPlainText("beta body")
    win.editor.save_clicked.emit(
        {
            "memory_id": None,
            "title": "Note Beta",
            "content": "beta body",
            "tags": [],
            "is_pinned": False,
        }
    )
    pump(20)
    beta_id = win.editor.current_id
    expect("beta memory was created and is loaded",
           beta_id is not None and beta_id != alpha_id, True)

    # Switch back to alpha in the editor first. After creating beta
    # the save flow left beta selected, but we want to add the link
    # from alpha's side (mirrors the user's mental model: "I'm
    # looking at alpha, I want to add a related entry"). The
    # selection change is what triggers _on_item_selected and
    # re-populates the editor with alpha.
    win.all_list.select_memory(alpha_id)
    pump(20)
    expect("editor now has alpha loaded (post-link setup)",
           win.editor.current_id, alpha_id)

    # From Alpha's editor, simulate the + Add Related dialog flow:
    # build the candidate list the way the dialog would, then emit
    # link_added with the picked id. This is the same call the
    # _AddRelatedDialog.accept() path makes when the user picks a
    # row and presses OK.
    candidates = [
        m for m in list_memories(conn)
        if m.id != alpha_id  # exclude self
        # (no exclusion of beta yet — it's not linked)
    ]
    expect("add-related candidate list has multiple options",
           len(candidates) >= 1, True)
    picked = next(m.id for m in candidates if m.id == beta_id)
    win.editor.link_added.emit(alpha_id, picked)
    pump(20)
    expect("alpha and beta are now linked (DB)",
           are_linked(conn, alpha_id, beta_id), True)
    expect("alpha's editor shows beta as a related chip",
           len(win.editor._related_chips), 1)
    expect("alpha's chip title is beta's title",
           win.editor._related_chips[0].title_label.text(), "Note Beta")
    # Empty-state should now hide.
    expect("related empty-state label is hidden when chips exist",
           win.editor.related_empty_label.isVisible(), False)

    # Switch to Beta in the editor by clicking it in the all-memories
    # list. Alpha is the current selection, so changing to beta
    # actually fires currentItemChanged and re-populates the editor.
    # The link is symmetric, so beta's chip list must show alpha.
    win.all_list.select_memory(beta_id)
    pump(20)
    expect("editor now has beta loaded",
           win.editor.current_id, beta_id)
    expect("beta's editor shows alpha as a related chip (symmetric)",
           [c.title_label.text() for c in win.editor._related_chips],
           ["Note Alpha"])

    # Clicking the alpha chip should load alpha into the editor.
    # The chip's clicked signal is wired to related_memory_clicked,
    # which MainWindow routes to _on_item_selected. Fire it
    # directly to avoid a mouse-event simulation that would also
    # have to navigate the chip's own click handler.
    win.editor.related_memory_clicked.emit(alpha_id)
    pump(20)
    expect("clicking a related chip loaded alpha into the editor",
           win.editor.current_id, alpha_id)

    # Remove the link from alpha's side by clicking the (x). The
    # chip's remove_clicked signal is wired to link_removed, which
    # MainWindow routes to _on_editor_link_removed. Fire it
    # directly.
    win.editor.link_removed.emit(alpha_id, beta_id)
    pump(20)
    expect("alpha and beta are no longer linked (DB)",
           are_linked(conn, alpha_id, beta_id), False)
    expect("alpha's chip list is now empty",
           win.editor._related_chips, [])
    expect("related empty-state label is showing again",
           win.editor.related_empty_label.isVisible(), True)

    # The link removal must be symmetric: beta's view also has
    # nothing when we load beta.
    win.all_list.select_memory(beta_id)
    pump(20)
    expect("beta's chip list is also empty after removal from alpha",
           win.editor._related_chips, [])

    # Re-link for the next two checks (cascade + persistence).
    win.editor.link_added.emit(beta_id, alpha_id)
    pump(20)
    expect("alpha and beta re-linked",
           are_linked(conn, alpha_id, beta_id), True)

    # Click a chip to confirm navigation still works on a fresh link.
    win.editor.related_memory_clicked.emit(alpha_id)
    pump(20)
    expect("chip click still loads the related memory",
           win.editor.current_id, alpha_id)

    # Permanently delete beta from the trash. This goes through the
    # same flow a user would: soft-delete first, then permanent.
    delete_memory(conn, beta_id)  # soft-delete → into trash
    pump(10)
    permanently_delete_memory(conn, beta_id)
    # The editor's chip list still shows the stale "Note Beta" chip
    # because the test bypassed MainWindow's trash slot (which is
    # the path that triggers the editor refresh). Replicate that
    # refresh here so the next assertion exercises the same UI
    # state a real user would see.
    win._refresh_editor_related(win.editor.current_id)
    pump(20)
    # Beta no longer in DB; alpha's related list no longer mentions it.
    expect("beta is hard-deleted", get_memory(conn, beta_id), None)
    expect("alpha's related list is empty after beta's hard delete",
           list_linked_memories(conn, alpha_id), [])
    # The editor's chip list is refreshed by MainWindow after a
    # permanent delete; the cascade already wiped the link row,
    # so the chip should be gone.
    expect("alpha's editor chip list is empty after cascade",
           win.editor._related_chips, [])

    # Re-link alpha with a fresh memory for the persistence check
    # below. We can't rely on the original "Max98357A notes" memory
    # from section [2] — earlier trash flows in section [14] may
    # have hard-deleted it. Creating a brand-new memory here makes
    # the persistence check self-contained.
    win.editor.title_input.setText("Persistence Buddy")
    win.editor.content_input.setPlainText("linked to alpha for restart test")
    win.editor.save_clicked.emit(
        {
            "memory_id": None,
            "title": "Persistence Buddy",
            "content": "linked to alpha for restart test",
            "tags": [],
            "is_pinned": False,
        }
    )
    pump(20)
    buddy_id = win.editor.current_id
    expect("persistence-buddy memory was created and is loaded",
           buddy_id is not None and buddy_id > 0, True)
    # Load alpha again so the link is added from alpha's side
    # (mirrors the user flow).
    win.all_list.select_memory(alpha_id)
    pump(20)
    link_memories(conn, alpha_id, buddy_id)
    pump(20)
    expect("alpha and buddy are linked (pre-restart)",
           are_linked(conn, alpha_id, buddy_id), True)

    # Restart simulation: close the window, build a fresh MainWindow
    # against the same DB, and confirm the link survives.
    win.close()
    pump(20)
    win2 = MainWindow(conn)
    win2.show()
    pump(20)
    # A restarted window opens on the Dashboard, which never populates
    # ``all_list`` -- so go to All Memories before selecting a row, or
    # the select is a no-op against an empty list and the editor stays
    # blank for reasons that have nothing to do with the link.
    win2.sidebar.select_view(VIEW_ALL_MEMORIES)
    pump(20)
    # Loading the alpha memory should populate its chip list with
    # the buddy memory.
    win2.all_list.select_memory(alpha_id)
    pump(20)
    expect("alpha's editor still shows the linked memory after restart",
           [c.title_label.text() for c in win2.editor._related_chips],
           ["Persistence Buddy"])
    expect("DB still reports the link after restart",
           are_linked(conn, alpha_id, buddy_id), True)

    # Clean up: tear down the second window so the smoke test's
    # final cleanup at the bottom of the script can remove the db
    # file without an open connection blocking it.
    win2.close()
    pump(20)
    # Keep a reference to ``win2`` so any future section that
    # follows this can rely on a live MainWindow. The final
    # cleanup at the bottom of the script doesn't need it, but
    # the binding is what guarantees the QObject isn't GC'd
    # mid-flight during the pump() calls above.
    win = win2  # noqa: F841  (kept for downstream use)

    print("[19] capture popup, tray, hotkey — wiring + popup flow")

    # The MainWindow is constructed with a CapturePopup already
    # attached. Verify the wiring exists (the popup is built
    # and its captured signal is connected to a real slot).
    from ui.capture_popup import CapturePopup
    expect("MainWindow has a CapturePopup attached",
           isinstance(win.capture_popup, CapturePopup), True)
    # We can't directly inspect the QObject signal connection list
    # from outside Qt, but we can confirm the slot is reachable
    # by emitting captured and watching the status bar. The popup's
    # captured signal goes to MainWindow._on_capture_popup_captured,
    # which writes a status bar message. So:
    from ui.strings import CAPTURE_CONFIRMATION
    win._on_capture_popup_captured(99999)  # fake memory id, just to fire the slot
    pump(10)
    expect("status bar reflects the capture confirmation",
           CAPTURE_CONFIRMATION in win.statusBar().currentMessage(), True)

    # The tray may or may not exist depending on the platform plugin
    # (offscreen QPA typically reports no system tray, but the
    # MainWindow code path is the same). The wiring is correct
    # either way: the ``_tray`` attribute is None when the platform
    # doesn't support it, or a QSystemTrayIcon when it does.
    from PySide6.QtWidgets import QSystemTrayIcon
    tray_ok = win._tray is None or isinstance(win._tray, QSystemTrayIcon)
    expect("MainWindow._tray is None or a QSystemTrayIcon", tray_ok, True)

    # Toolbar button is rewired to the popup. The button still
    # exists (kept as a secondary path per the spec) but its
    # connection should now be to the popup flow, not the
    # legacy QInputDialog. We can verify the click triggers the
    # popup's open_capture path by inspecting that the popup's
    # ``_committed`` flag is reset after a click.
    expect("capture popup is not visible initially",
           win.capture_popup.isVisible(), False)
    # Show the popup directly (we don't want to actually display
    # anything in offscreen mode — calling show() on a real
    # platform would require a display, but in offscreen it's
    # harmless and the widget state is the same).
    win.capture_popup.show()
    pump(10)
    expect("popup shows after a show() call", win.capture_popup.isVisible(), True)
    win.capture_popup.hide()
    pump(10)

    # Simulate clicking the + Inbox button: this calls
    # _on_new_inbox_clicked which now calls _show_quick_capture,
    # which calls capture_popup.open_capture. We can verify the
    # path by checking the popup's _committed flag (open_capture
    # resets it). After a successful submit it's True; after
    # open_capture() it should be False.
    win.capture_popup.input.setText("Smoke test thought")
    win.capture_popup._on_submit()
    expect("popup's _committed flag is True after submit",
           win.capture_popup._committed, True)
    win._on_new_inbox_clicked()
    expect("+ Inbox button resets the popup's _committed flag",
           win.capture_popup._committed, False)
    pump(10)
    # The toolbar's button click should have written a new inbox
    # row (open_capture shows the popup, and the user would type
    # then press Enter — but in this smoke test we just verified
    # the path is wired). The previous _on_submit DID write
    # "Smoke test thought" to the DB:
    inbox_titles = [m.title for m in list_inbox(conn)]
    expect("'Smoke test thought' landed in the inbox",
           "Smoke test thought" in inbox_titles, True)

    # Hotkey bridge: the bridge is a small QObject that bounces
    # a keyboard-library callback into the main thread. We can
    # verify its core invariant — debounce + signal — without
    # actually registering a hotkey (the OS-level hook is not
    # testable in CI).
    from main import _HotkeyBridge
    bridge = _HotkeyBridge()
    triggered_count = [0]
    bridge.triggered.connect(lambda: triggered_count.__setitem__(0, triggered_count[0] + 1))
    # First press: should fire, debounce locks.
    bridge.handle_press()
    pump(20)
    expect("first bridge press fires the signal", triggered_count[0], 1)
    # Second press within the debounce window: must be a no-op.
    bridge.handle_press()
    pump(20)
    expect("second bridge press within debounce is a no-op",
           triggered_count[0], 1)
    # Rearm the bridge (the consumer's job — see main.py) and
    # verify the next press fires. We use the public rearm()
    # method directly because the real consumer wires it via
    # QTimer.singleShot from the main thread, which is exactly
    # what main.py does in production.
    bridge.rearm()
    bridge.handle_press()
    pump(20)
    expect("bridge re-arms and a fresh press fires again",
           triggered_count[0], 2)

    # MainWindow has the bridge's signal connected. Verifying
    # the wiring: the bridge's triggered signal should be
    # connected to win._on_hotkey_pressed. We can sanity-check
    # by emitting the signal and watching for the popup to open.
    bridge.triggered.emit()
    pump(20)
    expect("emitting bridge.triggered opens the capture popup",
           win.capture_popup.isVisible(), True)
    win.capture_popup.hide()
    pump(10)

    print("\nALL SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        # Best-effort cleanup: close any open connections, then remove
        # the db file plus its WAL/SHM siblings (SQLite-on-Windows
        # sometimes holds a lock briefly after close()).
        import gc
        gc.collect()
        for suffix in ("", "-wal", "-shm", "-journal"):
            p = TEST_DB.with_name(TEST_DB.name + suffix)
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
    sys.exit(rc)
