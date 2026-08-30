"""Settings dialog for Alfred.

A single QDialog with a left-side category list (like a typical
settings UI: a sidebar of sections, one stacked-widget page per
section). Seven pages: General, Hotkey, Backups, Notifications,
Topics, Data, About.

The dialog does not touch global app state directly. It
collects changes locally and emits two signals:

* ``settings_saved(dict)`` -- fired when the user clicks Save,
  with the full new settings dict (post-merge with the input
  dict). The owner (MainWindow) is responsible for applying
  the changes: hotkey swap, restart-required notice
  for storage location, and starting/stopping the reminder
  QTimer.

* ``backup_restored(sqlite3.Connection)`` -- fired after a
  confirmed restore-from-backup. The new connection has
  already replaced the old one in the dialog's reference;
  the MainWindow is responsible for swapping its own
  reference to the conn and refreshing the visible views.

This separation keeps the dialog testable without a MainWindow
and keeps MainWindow free of UI-build code.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QHeaderView,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.db_stats import (
    get_backup_stats,
    get_stats,
    list_backups,
    reset_all_data,
    restore_from_backup,
    vacuum_db,
)
from core.export_import import backup_db
from core.settings import DEFAULT_SETTINGS, validate_hotkey
from ui.strings import (
    SETTINGS_ABOUT_CHALLENGE,
    SETTINGS_ABOUT_NAME,
    SETTINGS_ABOUT_TAGLINE,
    SETTINGS_ABOUT_VERSION,
    SETTINGS_ABOUT_VERSION_LABEL,
    SETTINGS_BACKUPS_BACKUP_NOW,
    SETTINGS_BACKUPS_CHANGE,
    SETTINGS_BACKUPS_CONFIRM_BODY,
    SETTINGS_BACKUPS_CONFIRM_LABEL,
    SETTINGS_BACKUPS_CONFIRM_TITLE,
    SETTINGS_BACKUPS_FOLDER_DIALOG_TITLE,
    SETTINGS_BACKUPS_FOLDER_LABEL,
    SETTINGS_BACKUPS_LIST_HEADER,
    SETTINGS_BACKUPS_NO_BACKUPS,
    SETTINGS_BACKUPS_RESTORE,
    SETTINGS_BACKUPS_RESTORED,
    SETTINGS_CATEGORY_ABOUT,
    SETTINGS_CATEGORY_BACKUPS,
    SETTINGS_CATEGORY_DATA,
    SETTINGS_CATEGORY_GENERAL,
    SETTINGS_CATEGORY_HOTKEY,
    SETTINGS_CATEGORY_NOTIFICATIONS,
    SETTINGS_CATEGORY_TOPICS,
    SETTINGS_DATA_DANGER_HEADER,
    SETTINGS_DATA_DANGER_NOTE,
    SETTINGS_DATA_REFRESH,
    SETTINGS_DATA_RESET_BUTTON,
    SETTINGS_DATA_RESET_CONFIRM_BODY,
    SETTINGS_DATA_RESET_LABEL,
    SETTINGS_DATA_RESET_PLACEHOLDER,
    SETTINGS_DATA_RESET_TITLE,
    SETTINGS_DATA_STAT_BACKUP_COUNT,
    SETTINGS_DATA_STAT_BACKUP_SIZE,
    SETTINGS_DATA_STAT_DB_SIZE,
    SETTINGS_DATA_STAT_INBOX,
    SETTINGS_DATA_STAT_MEMORIES,
    SETTINGS_DATA_STAT_NOTES,
    SETTINGS_DATA_STAT_PROJECTS,
    SETTINGS_DATA_STAT_TASKS,
    SETTINGS_DATA_STATS_HEADER,
    SETTINGS_DATA_VACUUM,
    SETTINGS_DATA_VACUUM_CONFIRM_BODY,
    SETTINGS_DATA_VACUUM_DONE_BEFORE,
    SETTINGS_DATA_VACUUM_TITLE,
    SETTINGS_GENERAL_RESTART_REQUIRED,
    SETTINGS_GENERAL_STORAGE_CHANGE,
    SETTINGS_GENERAL_STORAGE_DIALOG_FILTER,
    SETTINGS_GENERAL_STORAGE_DIALOG_TITLE,
    SETTINGS_GENERAL_STORAGE_LABEL,
    SETTINGS_HOTKEY_INVALID,
    SETTINGS_HOTKEY_LABEL,
    SETTINGS_HOTKEY_NOTE,
    SETTINGS_HOTKEY_PLACEHOLDER,
    SETTINGS_HOTKEY_UNAVAILABLE,
    SETTINGS_NOTIFICATIONS_ENABLE,
    SETTINGS_NOTIFICATIONS_INTERVAL_LABEL,
    SETTINGS_NOTIFICATIONS_INTERVAL_SUFFIX,
    SETTINGS_NOTIFICATIONS_LABEL,
    SETTINGS_NOTIFICATIONS_REMINDERS_ENABLE,
    SETTINGS_TITLE,
    SETTINGS_TOPICS_ADD,
    SETTINGS_TOPICS_COL_KEYWORDS,
    SETTINGS_TOPICS_COL_PROJECT,
    SETTINGS_TOPICS_EMPTY,
    SETTINGS_TOPICS_HEADER,
    SETTINGS_TOPICS_KEYWORDS_PLACEHOLDER,
    SETTINGS_TOPICS_NEW_PROJECT,
    SETTINGS_TOPICS_NOTE,
    SETTINGS_TOPICS_REMOVE,
)


def _format_size(n: int) -> str:
    """Human-readable size for the stats display."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"


def _format_mtime(t: float) -> str:
    """Local-time string for a backup row's mtime."""
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


class SettingsDialog(QDialog):
    """The Settings dialog. One window, seven pages, two output signals.

    Signals:
        ``settings_saved(dict)`` -- emitted on Save with the new
            settings dict. The owner applies them.
        ``backup_restored(sqlite3.Connection)`` -- emitted on a
            confirmed restore. The new connection is the
            return value of :func:`restore_from_backup`.
    """

    settings_saved = Signal(dict)
    backup_restored = Signal(object)

    def __init__(
        self,
        settings: dict[str, Any],
        conn: sqlite3.Connection,
        db_path: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(SETTINGS_TITLE)
        self.resize(720, 480)

        # Stash a working copy of the settings so Cancel really
        # does cancel. The original is preserved on the caller.
        self._settings = dict(settings)
        self._original_settings = dict(settings)
        self._conn = conn
        self._db_path = db_path

        # Per-page state, populated by the page builders.
        self._storage_path_edit: QLineEdit | None = None
        self._storage_restart_label: QLabel | None = None
        self._hotkey_edit: QLineEdit | None = None
        self._hotkey_invalid_label: QLabel | None = None
        self._backup_folder_edit: QLineEdit | None = None
        self._backup_list: QListWidget | None = None
        self._backup_overwrite_check: QCheckBox | None = None
        self._backup_now_btn: QPushButton | None = None
        self._notif_enable_check: QCheckBox | None = None
        self._reminders_enable_check: QCheckBox | None = None
        self._reminder_interval_spin: QSpinBox | None = None
        self._topics_table: QTableWidget | None = None
        self._topics_remove_btn: QPushButton | None = None
        self._topics_empty_label: QLabel | None = None
        self._stat_labels: dict[str, QLabel] = {}
        self._data_page: QWidget | None = None
        self._backups_page: QWidget | None = None

        # Build the dialog from the inside out: a vertical
        # outer layout with a horizontal body row at the top
        # and the button box at the bottom.
        self._build_ui()
        self._populate_from_settings()
        self._refresh_stats()
        self._refresh_backup_list()
        # The Save button starts enabled; the hotkey validator
        # is what gates it. The check runs once at init so the
        # initial state is correct.
        self._revalidate_hotkey()

    # ----- UI construction -----

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        # Body row: left category list + right stacked pages.
        body_row = QHBoxLayout()
        self._category_list = QListWidget()
        # This is navigation, not content: the theme styles it with the
        # same restrained "you are here" treatment as the main window's
        # sidebar rather than the strong selection content lists use.
        self._category_list.setObjectName("categoryList")
        self._category_list.setFixedWidth(160)
        for label in (
            SETTINGS_CATEGORY_GENERAL,
            SETTINGS_CATEGORY_HOTKEY,
            SETTINGS_CATEGORY_BACKUPS,
            SETTINGS_CATEGORY_NOTIFICATIONS,
            SETTINGS_CATEGORY_TOPICS,
            SETTINGS_CATEGORY_DATA,
            SETTINGS_CATEGORY_ABOUT,
        ):
            self._category_list.addItem(QListWidgetItem(label))
        self._category_list.setCurrentRow(0)
        self._category_list.currentRowChanged.connect(self._on_category_changed)
        body_row.addWidget(self._category_list)

        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_general_page())
        self._pages.addWidget(self._build_hotkey_page())
        self._pages.addWidget(self._build_backups_page())
        self._pages.addWidget(self._build_notifications_page())
        self._pages.addWidget(self._build_topics_page())
        self._pages.addWidget(self._build_data_page())
        self._pages.addWidget(self._build_about_page())
        body_row.addWidget(self._pages, 1)
        outer.addLayout(body_row, 1)

        # Bottom: Save / Cancel.
        button_box = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        # Save is this dialog's primary action, so it wears the same gold
        # fill the editor's Save does -- purely a stylesheet hook.
        _save_btn = button_box.button(QDialogButtonBox.Save)
        if _save_btn is not None:
            _save_btn.setObjectName("primaryButton")
        button_box.accepted.connect(self._on_save)
        button_box.rejected.connect(self.reject)
        outer.addWidget(button_box)

    def _on_category_changed(self, row: int) -> None:
        if row < 0:
            return
        self._pages.setCurrentIndex(row)
        # Refresh data on entry to the Data / Backups pages so
        # newly-created entries (or new backups) are visible
        # without the user having to click Refresh.
        current = self._pages.currentWidget()
        if current is self._data_page:
            self._refresh_stats()
        elif current is self._backups_page:
            self._refresh_backup_list()

    # ----- page: General -----

    def _build_general_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        # Storage location
        storage_group = QGroupBox(SETTINGS_GENERAL_STORAGE_LABEL)
        storage_layout = QHBoxLayout(storage_group)
        self._storage_path_edit = QLineEdit()
        self._storage_path_edit.setReadOnly(True)
        storage_layout.addWidget(self._storage_path_edit, 1)
        change_btn = QPushButton(SETTINGS_GENERAL_STORAGE_CHANGE)
        change_btn.clicked.connect(self._on_storage_change_clicked)
        storage_layout.addWidget(change_btn)
        layout.addWidget(storage_group)

        # The "restart required" notice is hidden by default; it
        # shows up the moment the user picks a new storage path,
        # not just on Save.
        self._storage_restart_label = QLabel(SETTINGS_GENERAL_RESTART_REQUIRED)
        # Gold, not amber: "needs a restart" is a heads-up, and the theme
        # already owns exactly one colour that means look-here.
        self._storage_restart_label.setObjectName("warningNote")
        self._storage_restart_label.setVisible(False)
        self._storage_restart_label.setWordWrap(True)
        layout.addWidget(self._storage_restart_label)

        layout.addStretch()
        return page

    def _on_storage_change_clicked(self) -> None:
        """Open a file picker; if the user picks something different,
        update the field and show the restart-required notice."""
        if self._storage_path_edit is None:
            return
        current = self._storage_path_edit.text()
        # Seed the dialog at the current file's directory so the
        # picker opens in a sensible place.
        seed_dir = os.path.dirname(current) if current else ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            SETTINGS_GENERAL_STORAGE_DIALOG_TITLE,
            seed_dir,
            SETTINGS_GENERAL_STORAGE_DIALOG_FILTER,
        )
        if not path:
            return
        if path == current:
            return
        self._storage_path_edit.setText(path)
        # Show the notice immediately: it belongs at the moment
        # of change, not deferred to Save.
        self._storage_restart_label.setVisible(True)

    # ----- page: Hotkey -----

    def _build_hotkey_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel(SETTINGS_HOTKEY_LABEL))
        self._hotkey_edit = QLineEdit()
        self._hotkey_edit.setPlaceholderText(SETTINGS_HOTKEY_PLACEHOLDER)
        self._hotkey_edit.textChanged.connect(self._revalidate_hotkey)
        layout.addWidget(self._hotkey_edit)

        self._hotkey_invalid_label = QLabel(SETTINGS_HOTKEY_INVALID)
        self._hotkey_invalid_label.setObjectName("errorLabel")
        self._hotkey_invalid_label.setVisible(False)
        layout.addWidget(self._hotkey_invalid_label)

        note = QLabel(SETTINGS_HOTKEY_NOTE)
        note.setWordWrap(True)
        note.setObjectName("noteLabel")
        layout.addWidget(note)

        layout.addStretch()
        return page

    def _revalidate_hotkey(self) -> None:
        """Update the validity label and the Save button enable state."""
        if self._hotkey_edit is None or self._hotkey_invalid_label is None:
            return
        text = self._hotkey_edit.text().strip()
        valid = validate_hotkey(text)
        self._hotkey_invalid_label.setVisible(bool(text) and not valid)
        # Find the Save button in the dialog's button box and gate
        # it. The button box is the last widget added in __init__,
        # so it has a known position in the children list.
        save_btn = self._find_save_button()
        if save_btn is not None:
            save_btn.setEnabled(valid)

    def _find_save_button(self) -> QPushButton | None:
        """Locate the Save button in the dialog's button box.

        Implemented as a one-shot scan because the button box is
        built in __init__ and we don't keep a reference to it
        separately. A small lookup beats re-architecting for a
        single control reference.
        """
        for child in self.findChildren(QDialogButtonBox):
            btn = child.button(QDialogButtonBox.Save)
            if btn is not None:
                return btn
        return None

    # ----- page: Backups -----

    def _build_backups_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        # Folder picker
        folder_group = QGroupBox(SETTINGS_BACKUPS_FOLDER_LABEL)
        folder_layout = QHBoxLayout(folder_group)
        self._backup_folder_edit = QLineEdit()
        self._backup_folder_edit.setReadOnly(True)
        folder_layout.addWidget(self._backup_folder_edit, 1)
        change_btn = QPushButton(SETTINGS_BACKUPS_CHANGE)
        change_btn.clicked.connect(self._on_backup_folder_change_clicked)
        folder_layout.addWidget(change_btn)
        layout.addWidget(folder_group)

        # Backup Now -- convenience duplicate of the File-menu action.
        self._backup_now_btn = QPushButton(SETTINGS_BACKUPS_BACKUP_NOW)
        self._backup_now_btn.clicked.connect(self._on_backup_now_clicked)
        layout.addWidget(self._backup_now_btn)

        # Existing backups list
        layout.addWidget(QLabel(SETTINGS_BACKUPS_LIST_HEADER))
        self._backup_list = QListWidget()
        # ``currentRowChanged`` would also work; the per-row
        # "Restore" button is what the user actually clicks, so
        # we don't need a selection-driven UI here.
        layout.addWidget(self._backup_list, 1)

        # Confirmation gate: a single checkbox that enables all
        # the per-row Restore buttons. Spec: "restore requires:
        # (1) a confirmation checkbox... (2) then a QMessageBox
        # question final confirm, both required before it fires".
        # The checkbox gates the buttons; the QMessageBox is
        # shown on the Restore click.
        self._backup_overwrite_check = QCheckBox(SETTINGS_BACKUPS_CONFIRM_LABEL)
        self._backup_overwrite_check.toggled.connect(self._on_overwrite_toggle)
        layout.addWidget(self._backup_overwrite_check)

        self._backups_page = page
        return page

    def _on_overwrite_toggle(self, checked: bool) -> None:
        """Walk the backup list and enable/disable every Restore button."""
        if self._backup_list is None:
            return
        for i in range(self._backup_list.count()):
            item = self._backup_list.item(i)
            widget = self._backup_list.itemWidget(item)
            if widget is None:
                continue
            btn = widget.findChild(QPushButton)
            if btn is not None:
                btn.setEnabled(checked)

    def _on_backup_folder_change_clicked(self) -> None:
        if self._backup_folder_edit is None:
            return
        current = self._backup_folder_edit.text()
        seed = current if current else ""
        path = QFileDialog.getExistingDirectory(
            self,
            SETTINGS_BACKUPS_FOLDER_DIALOG_TITLE,
            seed,
        )
        if not path:
            return
        self._backup_folder_edit.setText(path)
        # Backup folder is read at click-time, so changing it is
        # effective immediately. No restart required.

    def _on_backup_now_clicked(self) -> None:
        try:
            backup_db(
                self._db_path,
                backup_dir=self._settings.get(
                    "backup_dir", DEFAULT_SETTINGS["backup_dir"]
                ),
            )
        except OSError as exc:
            QMessageBox.warning(
                self, SETTINGS_BACKUPS_BACKUP_NOW, f"Could not back up: {exc}"
            )
            return
        self._refresh_backup_list()

    def _refresh_backup_list(self) -> None:
        """Rebuild the backup list from the disk contents."""
        if self._backup_list is None:
            return
        self._backup_list.clear()
        backup_dir = self._settings.get(
            "backup_dir", DEFAULT_SETTINGS["backup_dir"]
        )
        entries = list_backups(backup_dir)
        if not entries:
            placeholder = QListWidgetItem(SETTINGS_BACKUPS_NO_BACKUPS)
            placeholder.setFlags(Qt.NoItemFlags)
            self._backup_list.addItem(placeholder)
            return
        gate_enabled = (
            self._backup_overwrite_check.isChecked()
            if self._backup_overwrite_check is not None
            else False
        )
        for entry in entries:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(8, 4, 8, 4)
            label = QLabel(
                f"{entry['name']}    "
                f"{_format_mtime(entry['mtime'])}    "
                f"{_format_size(entry['size'])}"
            )
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row_layout.addWidget(label, 1)
            restore_btn = QPushButton(SETTINGS_BACKUPS_RESTORE)
            restore_btn.setEnabled(gate_enabled)
            restore_btn.clicked.connect(
                lambda _checked=False, p=entry["path"], n=entry["name"]: self._on_restore_clicked(p, n)
            )
            row_layout.addWidget(restore_btn)
            item = QListWidgetItem(self._backup_list)
            item.setSizeHint(row_widget.sizeHint())
            self._backup_list.addItem(item)
            self._backup_list.setItemWidget(item, row_widget)

    def _on_restore_clicked(self, backup_path: str, backup_name: str) -> None:
        """The per-row Restore button. Two-step confirm: the checkbox
        (which gates the button's enabled state) + this QMessageBox."""
        if self._backup_overwrite_check is None:
            return
        if not self._backup_overwrite_check.isChecked():
            # Defensive: the checkbox is supposed to gate the
            # button, but if it was somehow bypassed, refuse.
            return
        reply = QMessageBox.question(
            self,
            SETTINGS_BACKUPS_CONFIRM_TITLE,
            SETTINGS_BACKUPS_CONFIRM_BODY.format(name=backup_name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            new_conn = restore_from_backup(
                self._conn, self._db_path, backup_path
            )
        except OSError as exc:
            QMessageBox.warning(
                self,
                SETTINGS_BACKUPS_CONFIRM_TITLE,
                f"Could not restore: {exc}",
            )
            return
        self._conn = new_conn
        # Re-init our cached connection reference and refresh
        # any pages that read from the DB (Data, Backups).
        self._refresh_stats()
        self._refresh_backup_list()
        # Tell the owner to swap its reference and refresh views.
        self.backup_restored.emit(new_conn)
        # Status-bar-friendly message; the owner surfaces it.
        self._settings["_last_restore_message"] = (
            SETTINGS_BACKUPS_RESTORED.format(name=backup_name)
        )

    # ----- page: Notifications -----

    def _build_notifications_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel(SETTINGS_NOTIFICATIONS_LABEL))

        self._notif_enable_check = QCheckBox(SETTINGS_NOTIFICATIONS_ENABLE)
        layout.addWidget(self._notif_enable_check)

        self._reminders_enable_check = QCheckBox(
            SETTINGS_NOTIFICATIONS_REMINDERS_ENABLE
        )
        self._reminders_enable_check.toggled.connect(self._on_reminders_toggled)
        layout.addWidget(self._reminders_enable_check)

        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel(SETTINGS_NOTIFICATIONS_INTERVAL_LABEL))
        self._reminder_interval_spin = QSpinBox()
        self._reminder_interval_spin.setRange(1, 24 * 60)
        self._reminder_interval_spin.setSuffix(SETTINGS_NOTIFICATIONS_INTERVAL_SUFFIX)
        interval_row.addWidget(self._reminder_interval_spin)
        interval_row.addStretch()
        layout.addLayout(interval_row)

        layout.addStretch()
        return page

    def _on_reminders_toggled(self, checked: bool) -> None:
        if self._reminder_interval_spin is not None:
            self._reminder_interval_spin.setEnabled(checked)

    # ----- page: Topics -----

    def _build_topics_page(self) -> QWidget:
        """Keyword to project rules, as a two-column editable table.

        One row per project: its name on the left, its keywords as a
        comma-separated string on the right. A table rather than a
        nested widget-per-keyword editor because the whole rule set is
        small, and "type a project, type some words" is the entire
        interaction; anything richer would be scaffolding around two
        strings.
        """
        page = QWidget()
        layout = QVBoxLayout(page)

        header = QLabel(SETTINGS_TOPICS_HEADER)
        header.setObjectName("sectionHeader")
        layout.addWidget(header)

        note = QLabel(SETTINGS_TOPICS_NOTE)
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        self._topics_table = QTableWidget(0, 2)
        self._topics_table.setHorizontalHeaderLabels(
            [SETTINGS_TOPICS_COL_PROJECT, SETTINGS_TOPICS_COL_KEYWORDS]
        )
        self._topics_table.verticalHeader().setVisible(False)
        self._topics_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._topics_table.setSelectionMode(QAbstractItemView.SingleSelection)
        # The project column sizes to its content; keywords take the
        # rest, since that is the field that actually gets long.
        header_view = self._topics_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.Stretch)
        self._topics_table.itemSelectionChanged.connect(
            self._on_topics_selection_changed
        )
        layout.addWidget(self._topics_table, 1)

        self._topics_empty_label = QLabel(SETTINGS_TOPICS_EMPTY)
        self._topics_empty_label.setObjectName("muted")
        self._topics_empty_label.setWordWrap(True)
        layout.addWidget(self._topics_empty_label)

        button_row = QHBoxLayout()
        add_btn = QPushButton(SETTINGS_TOPICS_ADD)
        add_btn.clicked.connect(self._on_topics_add)
        button_row.addWidget(add_btn)
        self._topics_remove_btn = QPushButton(SETTINGS_TOPICS_REMOVE)
        self._topics_remove_btn.clicked.connect(self._on_topics_remove)
        self._topics_remove_btn.setEnabled(False)
        button_row.addWidget(self._topics_remove_btn)
        button_row.addStretch()
        layout.addLayout(button_row)

        return page

    def _on_topics_selection_changed(self) -> None:
        """Remove only means something with a row picked."""
        if self._topics_remove_btn is None or self._topics_table is None:
            return
        self._topics_remove_btn.setEnabled(
            self._topics_table.currentRow() >= 0
        )

    def _on_topics_add(self) -> None:
        """Append a blank rule row and put the cursor in its name cell.

        The placeholder name and keywords are real text, not a grey
        hint: a row is a rule, and an all-empty row would be dropped on
        save without the user seeing why. Typing over the placeholder
        costs one keystroke; recovering a silently discarded row costs
        the user their trust in the page.
        """
        if self._topics_table is None:
            return
        row = self._topics_table.rowCount()
        self._topics_table.insertRow(row)
        self._topics_table.setItem(
            row, 0, QTableWidgetItem(SETTINGS_TOPICS_NEW_PROJECT)
        )
        self._topics_table.setItem(
            row, 1, QTableWidgetItem(SETTINGS_TOPICS_KEYWORDS_PLACEHOLDER)
        )
        self._topics_table.setCurrentCell(row, 0)
        self._topics_table.editItem(self._topics_table.item(row, 0))
        self._update_topics_empty_label()

    def _on_topics_remove(self) -> None:
        """Drop the selected rule row.

        No confirmation: a keyword group is two strings the user can
        retype, and nothing is committed until Save, so Cancel still
        puts it back.
        """
        if self._topics_table is None:
            return
        row = self._topics_table.currentRow()
        if row < 0:
            return
        self._topics_table.removeRow(row)
        self._update_topics_empty_label()
        self._on_topics_selection_changed()

    def _update_topics_empty_label(self) -> None:
        """Show the no-rules note only while there are no rules."""
        if self._topics_table is None:
            return
        self._topics_empty_label.setVisible(
            self._topics_table.rowCount() == 0
        )

    def _populate_topics_table(self) -> None:
        """Fill the table from the working settings copy."""
        if self._topics_table is None:
            return
        raw = self._settings.get("topic_keywords") or {}
        if not isinstance(raw, dict):
            raw = {}
        self._topics_table.setRowCount(0)
        for name, keywords in raw.items():
            if isinstance(keywords, str):
                keywords = [keywords]
            if not isinstance(keywords, (list, tuple)):
                continue
            row = self._topics_table.rowCount()
            self._topics_table.insertRow(row)
            self._topics_table.setItem(row, 0, QTableWidgetItem(str(name)))
            self._topics_table.setItem(
                row, 1, QTableWidgetItem(", ".join(str(k) for k in keywords))
            )
        self._update_topics_empty_label()
        self._on_topics_selection_changed()

    def _collect_topic_keywords(self) -> dict[str, list[str]]:
        """Read the table back into a fresh rules dict.

        Fresh on purpose: ``load_settings`` copies
        :data:`core.settings.DEFAULT_SETTINGS` shallowly, so the default
        empty dict is one shared object. Mutating whatever dict happens
        to sit in ``self._settings`` would write through to that default
        and leak these rules into every later ``load_settings`` call in
        the process.

        Rows with no project name are dropped, as are rows whose
        keywords are all blank: a rule with nothing on one side cannot
        match anything, and keeping it only makes the file harder to
        read.
        """
        out: dict[str, list[str]] = {}
        if self._topics_table is None:
            return out
        for row in range(self._topics_table.rowCount()):
            name_item = self._topics_table.item(row, 0)
            keywords_item = self._topics_table.item(row, 1)
            name = (name_item.text() if name_item else "").strip()
            if not name:
                continue
            raw = keywords_item.text() if keywords_item else ""
            keywords = [k.strip() for k in raw.split(",") if k.strip()]
            if not keywords:
                continue
            # Two rows naming the same project are plainly meant as one
            # set, so a repeat merges rather than clobbers.
            out.setdefault(name, [])
            for keyword in keywords:
                if keyword not in out[name]:
                    out[name].append(keyword)
        return out

    # ----- page: Data -----

    def _build_data_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        # Stats group
        stats_group = QGroupBox(SETTINGS_DATA_STATS_HEADER)
        stats_layout = QFormLayout(stats_group)
        self._stat_labels = {}
        for key, label_text in (
            ("memories", SETTINGS_DATA_STAT_MEMORIES),
            ("notes", SETTINGS_DATA_STAT_NOTES),
            ("inbox", SETTINGS_DATA_STAT_INBOX),
            ("tasks", SETTINGS_DATA_STAT_TASKS),
            ("projects", SETTINGS_DATA_STAT_PROJECTS),
            ("db_size", SETTINGS_DATA_STAT_DB_SIZE),
            ("backup_count", SETTINGS_DATA_STAT_BACKUP_COUNT),
            ("backup_size", SETTINGS_DATA_STAT_BACKUP_SIZE),
        ):
            value = QLabel("—")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            stats_layout.addRow(label_text + ":", value)
            self._stat_labels[key] = value
        refresh_btn = QPushButton(SETTINGS_DATA_REFRESH)
        refresh_btn.clicked.connect(self._refresh_stats)
        stats_layout.addRow(refresh_btn)
        layout.addWidget(stats_group)

        # Vacuum button
        vacuum_btn = QPushButton(SETTINGS_DATA_VACUUM)
        vacuum_btn.clicked.connect(self._on_vacuum_clicked)
        layout.addWidget(vacuum_btn)

        # Danger zone
        danger_group = QGroupBox(SETTINGS_DATA_DANGER_HEADER)
        danger_layout = QVBoxLayout(danger_group)
        note = QLabel(SETTINGS_DATA_DANGER_NOTE)
        note.setWordWrap(True)
        note.setObjectName("dangerNote")
        danger_layout.addWidget(note)
        danger_layout.addWidget(QLabel(SETTINGS_DATA_RESET_LABEL))
        self._reset_input = QLineEdit()
        self._reset_input.setPlaceholderText(SETTINGS_DATA_RESET_PLACEHOLDER)
        self._reset_input.textChanged.connect(self._on_reset_text_changed)
        danger_layout.addWidget(self._reset_input)
        self._reset_btn = QPushButton(SETTINGS_DATA_RESET_BUTTON)
        # The one button in Settings that destroys data.
        self._reset_btn.setObjectName("dangerButton")
        self._reset_btn.setEnabled(False)
        self._reset_btn.clicked.connect(self._on_reset_clicked)
        danger_layout.addWidget(self._reset_btn)
        layout.addWidget(danger_group)

        layout.addStretch()
        self._data_page = page
        return page

    def _refresh_stats(self) -> None:
        try:
            stats = get_stats(self._conn, self._db_path)
        except Exception:
            stats = {
                "total_memories": 0,
                "by_type": {"note": 0, "inbox": 0, "task": 0},
                "project_count": 0,
                "db_size": 0,
            }
        bstats = get_backup_stats(
            self._settings.get("backup_dir", DEFAULT_SETTINGS["backup_dir"])
        )
        self._stat_labels["memories"].setText(str(stats["total_memories"]))
        self._stat_labels["notes"].setText(str(stats["by_type"]["note"]))
        self._stat_labels["inbox"].setText(str(stats["by_type"]["inbox"]))
        self._stat_labels["tasks"].setText(str(stats["by_type"]["task"]))
        self._stat_labels["projects"].setText(str(stats["project_count"]))
        self._stat_labels["db_size"].setText(_format_size(stats["db_size"]))
        self._stat_labels["backup_count"].setText(str(bstats["count"]))
        self._stat_labels["backup_size"].setText(_format_size(bstats["total_size"]))

    def _on_vacuum_clicked(self) -> None:
        try:
            size_before = os.path.getsize(self._db_path)
        except OSError:
            size_before = 0
        reply = QMessageBox.question(
            self,
            SETTINGS_DATA_VACUUM_TITLE,
            SETTINGS_DATA_VACUUM_CONFIRM_BODY.format(
                size_before=_format_size(size_before)
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            vacuum_db(self._conn)
        except Exception as exc:
            QMessageBox.warning(
                self,
                SETTINGS_DATA_VACUUM_TITLE,
                f"Vacuum failed: {exc}",
            )
            return
        try:
            size_after = os.path.getsize(self._db_path)
        except OSError:
            size_after = 0
        # Surface the before/after in the status bar of the
        # owner; we don't have a status bar here. As a courtesy
        # we also re-render the Data page.
        self._refresh_stats()
        self._settings["_last_vacuum_message"] = (
            SETTINGS_DATA_VACUUM_DONE_BEFORE.format(
                size_before=_format_size(size_before),
                size_after=_format_size(size_after),
            )
        )

    def _on_reset_text_changed(self, text: str) -> None:
        self._reset_btn.setEnabled(text.strip() == SETTINGS_DATA_RESET_PLACEHOLDER)

    def _on_reset_clicked(self) -> None:
        reply = QMessageBox.question(
            self,
            SETTINGS_DATA_RESET_TITLE,
            SETTINGS_DATA_RESET_CONFIRM_BODY,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            reset_all_data(self._conn)
        except Exception as exc:
            QMessageBox.warning(
                self,
                SETTINGS_DATA_RESET_TITLE,
                f"Reset failed: {exc}",
            )
            return
        # The input is no longer valid; lock the button again so
        # a stray double-click can't re-fire.
        self._reset_input.clear()
        self._refresh_stats()
        self._settings["_last_reset_message"] = (
            "All data has been cleared."
        )

    # ----- page: About -----

    def _build_about_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        # The app's own name on the About page is the one display-
        # sized line in the dialog, so it takes the same theme step
        # the dashboard greeting does -- but not its gold, which stays
        # reserved for that one line. Was "current size + 8", which put
        # it at a size nothing else in the app used.
        name_label = QLabel(SETTINGS_ABOUT_NAME)
        name_label.setObjectName("displayTitle")
        layout.addWidget(name_label)

        tagline = QLabel(SETTINGS_ABOUT_TAGLINE)
        tagline.setObjectName("subheadLabel")
        tagline.setWordWrap(True)
        layout.addWidget(tagline)

        version_row = QHBoxLayout()
        version_row.addWidget(
            QLabel(f"{SETTINGS_ABOUT_VERSION_LABEL}:")
        )
        version_value = QLabel(SETTINGS_ABOUT_VERSION)
        version_row.addWidget(version_value)
        version_row.addStretch()
        layout.addLayout(version_row)

        challenge = QLabel(SETTINGS_ABOUT_CHALLENGE)
        challenge.setWordWrap(True)
        challenge.setObjectName("noteLabel")
        layout.addWidget(challenge)

        layout.addStretch()
        return page

    # ----- population / save -----

    def _populate_from_settings(self) -> None:
        """Initialize the controls from the working settings copy."""
        if self._storage_path_edit is not None:
            self._storage_path_edit.setText(
                self._settings.get("db_path", DEFAULT_SETTINGS["db_path"])
            )
        if self._hotkey_edit is not None:
            self._hotkey_edit.setText(
                self._settings.get("hotkey", DEFAULT_SETTINGS["hotkey"])
            )
        if self._backup_folder_edit is not None:
            self._backup_folder_edit.setText(
                self._settings.get(
                    "backup_dir", DEFAULT_SETTINGS["backup_dir"]
                )
            )
        if self._notif_enable_check is not None:
            self._notif_enable_check.setChecked(
                bool(
                    self._settings.get(
                        "notifications_enabled",
                        DEFAULT_SETTINGS["notifications_enabled"],
                    )
                )
            )
        if self._reminders_enable_check is not None:
            enabled = bool(
                self._settings.get(
                    "reminders_enabled",
                    DEFAULT_SETTINGS["reminders_enabled"],
                )
            )
            self._reminders_enable_check.setChecked(enabled)
        if self._reminder_interval_spin is not None:
            self._reminder_interval_spin.setValue(
                int(
                    self._settings.get(
                        "reminder_interval_minutes",
                        DEFAULT_SETTINGS["reminder_interval_minutes"],
                    )
                )
            )
            self._reminder_interval_spin.setEnabled(
                self._reminders_enable_check.isChecked()
            )
        self._populate_topics_table()

    def _collect_from_ui(self) -> dict[str, Any]:
        """Snapshot the current control values into a settings dict.

        The hotkey is forced through ``validate_hotkey`` here too
        as a belt-and-suspenders (the dialog already disables
        Save on invalid input, but a programmatic caller could
        reach this method).
        """
        out: dict[str, Any] = dict(self._settings)
        # Storage
        if self._storage_path_edit is not None:
            out["db_path"] = self._storage_path_edit.text()
        # Hotkey (validated; falls back to current on garbage input)
        if self._hotkey_edit is not None:
            hk = self._hotkey_edit.text().strip()
            if validate_hotkey(hk):
                out["hotkey"] = hk
        # Backup folder
        if self._backup_folder_edit is not None:
            out["backup_dir"] = self._backup_folder_edit.text()
        # Notifications
        if self._notif_enable_check is not None:
            out["notifications_enabled"] = self._notif_enable_check.isChecked()
        if self._reminders_enable_check is not None:
            out["reminders_enabled"] = self._reminders_enable_check.isChecked()
        if self._reminder_interval_spin is not None:
            out["reminder_interval_minutes"] = self._reminder_interval_spin.value()
        # Topic keywords. Always a brand-new dict; see
        # ``_collect_topic_keywords`` for why that matters.
        if self._topics_table is not None:
            out["topic_keywords"] = self._collect_topic_keywords()
        return out

    def _on_save(self) -> None:
        """Validate, then emit the saved-settings signal and close."""
        new_settings = self._collect_from_ui()
        # Final safety: hotkey must validate before we let the
        # signal escape.
        if not validate_hotkey(new_settings.get("hotkey", "")):
            return
        self._settings = new_settings
        self.settings_saved.emit(new_settings)
        self.accept()
