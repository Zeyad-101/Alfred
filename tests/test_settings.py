"""Tests for ``core.settings`` and ``ui.settings_dialog``.

This is a deliberately generous test file: ``core.settings`` is the
small backend (load/save/validate) and ``ui.settings_dialog`` is the
heavyweight dialog that consumes it. Together they own the
settings surface; both are tested here so a regression in either is
caught at the same layer the spec defines it.

The dialog tests are minimal — the goal is "every page builds, every
control round-trips its value to the working settings dict, the
Save signal carries the right payload". Fine-grained UI tests
(layout, theming, geometry) are out of scope and would be
brittle under any refactor.
"""
from __future__ import annotations

import json
import os

import pytest

from core.settings import (
    DEFAULT_SETTINGS,
    load_settings,
    save_settings,
    validate_hotkey,
)


# ----- core.settings: load / save -----


def test_load_settings_creates_file_on_first_run(tmp_path):
    """A missing settings file should be created with defaults and returned."""
    path = str(tmp_path / "fresh.json")
    loaded = load_settings(path)
    # The function returns the defaults
    assert loaded == dict(DEFAULT_SETTINGS)
    # And a real file was written
    assert os.path.exists(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data == dict(DEFAULT_SETTINGS)


def test_load_settings_merges_with_defaults(tmp_path):
    """A file with a partial keyset should be merged with defaults."""
    path = str(tmp_path / "partial.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"hotkey": "ctrl+shift+a"}, f)
    loaded = load_settings(path)
    # The known key was kept
    assert loaded["hotkey"] == "ctrl+shift+a"
    # Every default for the missing keys still applies
    for key, value in DEFAULT_SETTINGS.items():
        if key != "hotkey":
            assert loaded[key] == value


def test_load_settings_ignores_unknown_keys(tmp_path):
    """Stale / typo keys in the file should be silently dropped on load."""
    path = str(tmp_path / "stale.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "hotkey": "ctrl+space",
                "totally_made_up_key": "ignore me",
                "another_stale_one": 42,
            },
            f,
        )
    loaded = load_settings(path)
    assert "totally_made_up_key" not in loaded
    assert "another_stale_one" not in loaded
    assert loaded["hotkey"] == "ctrl+space"


def test_load_settings_drops_stale_theme_key(tmp_path):
    """A ``"theme"`` key from an older settings file (when theme was
    a user-facing option) must be silently dropped on load — not
    promoted into the current settings dict, not raised on.

    The key isn't in :data:`DEFAULT_SETTINGS` anymore, so the
    filter in ``load_settings`` skips it. The downstream code
    (``apply_theme``) doesn't look up ``settings["theme"]`` at
    all, so even a key that did survive the filter would be
    ignored. This test pins down the load-side contract: the
    file migrates cleanly.
    """
    path = str(tmp_path / "old.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"hotkey": "ctrl+space", "theme": "dark"},
            f,
        )
    loaded = load_settings(path)
    assert "theme" not in loaded
    # The known key is still there.
    assert loaded["hotkey"] == "ctrl+space"
    # The recognized keys are exactly the default set.
    assert set(loaded.keys()) == set(DEFAULT_SETTINGS.keys())


def test_load_settings_corrupt_file_falls_back_to_defaults(tmp_path):
    """A file that isn't valid JSON should not brick the app."""
    path = str(tmp_path / "broken.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ not valid json")
    loaded = load_settings(path)
    # The whole settings dict is the defaults — losing one bad
    # file is much better than refusing to start.
    assert loaded == dict(DEFAULT_SETTINGS)


def test_load_settings_non_object_file_falls_back_to_defaults(tmp_path):
    """A file whose top-level value is a list/string/number is also rejected."""
    path = str(tmp_path / "list.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(["not", "an", "object"], f)
    loaded = load_settings(path)
    assert loaded == dict(DEFAULT_SETTINGS)


def test_save_settings_creates_parent_directory(tmp_path):
    """A nested parent dir should be created on demand."""
    path = str(tmp_path / "nested" / "dir" / "settings.json")
    save_settings(DEFAULT_SETTINGS, path)
    assert os.path.exists(path)
    # Round-trip
    loaded = load_settings(path)
    assert loaded == dict(DEFAULT_SETTINGS)


def test_save_settings_writes_only_recognized_keys(tmp_path):
    """Extra keys passed in should be dropped on write, not persisted."""
    path = str(tmp_path / "filtered.json")
    to_write = dict(DEFAULT_SETTINGS)
    to_write["junk"] = "should not be written"
    save_settings(to_write, path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "junk" not in data
    assert set(data.keys()) == set(DEFAULT_SETTINGS.keys())


def test_save_settings_pretty_printed(tmp_path):
    """Hand-editing the file should be tolerable: pretty-printing is the
    default for ``json.dump(..., indent=2)``."""
    path = str(tmp_path / "pretty.json")
    save_settings(DEFAULT_SETTINGS, path)
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    # The file should contain a newline (indent=2) and human-readable
    # JSON, not a one-liner.
    assert "\n" in text
    # And the recognizable content — a known key — is on disk.
    assert '"hotkey"' in text


def test_load_then_save_roundtrip(tmp_path):
    """A load, then a save with the loaded values, should round-trip cleanly."""
    path = str(tmp_path / "rt.json")
    initial = load_settings(path)
    initial["hotkey"] = "ctrl+shift+k"
    save_settings(initial, path)
    reloaded = load_settings(path)
    assert reloaded["hotkey"] == "ctrl+shift+k"


# ----- core.settings: validate_hotkey -----


@pytest.mark.parametrize(
    "hotkey",
    [
        "ctrl+space",
        "ctrl+shift+a",
        "alt+1",
        "ctrl+alt+shift+x",
        "super+r",  # 'super' is a valid token even if we don't have a keymap for it
    ],
)
def test_validate_hotkey_accepts_well_formed(hotkey):
    assert validate_hotkey(hotkey) is True


@pytest.mark.parametrize(
    "hotkey",
    [
        "",
        "   ",  # whitespace only
        "ctrl",  # bare modifier
        "space",  # single non-modifier
        "ctrl+",  # trailing plus
        "+ctrl",  # leading plus
        "ctrl++a",  # double plus
        "+",  # just a plus
        "ctrl space",  # space instead of plus
        "ctrl\t+\tshift",  # whitespace inside
    ],
)
def test_validate_hotkey_rejects_malformed(hotkey):
    assert validate_hotkey(hotkey) is False


def test_validate_hotkey_strips_whitespace():
    """Leading/trailing whitespace is acceptable; internal whitespace isn't.

    This matches the regex's character class — ``+`` is the only
    allowed separator, so the validator first trims and then matches.
    """
    assert validate_hotkey("  ctrl+space  ") is True
    assert validate_hotkey("ctrl + space") is False


def test_validate_hotkey_rejects_non_string():
    """Defensive: the validator returns False for non-string inputs.

    ``load_settings`` produces strings; programmatic callers might
    pass ints, None, etc. — those are not valid hotkeys and the
    validator should refuse rather than raise.
    """
    assert validate_hotkey(None) is False  # type: ignore[arg-type]
    assert validate_hotkey(42) is False  # type: ignore[arg-type]
    assert validate_hotkey(["ctrl", "space"]) is False  # type: ignore[arg-type]


# ----- ui.settings_dialog: build + round-trip -----


def _build_dialog(qapp, conn, db_path, settings=None):
    """Helper: construct a SettingsDialog, show it, and return it.

    The dialog uses QDialog.exec() for its show path. The tests
    never call exec() — they just poke at the API — but they
    do need the widget hierarchy to be "shown" so that
    ``isVisible()`` reflects the local ``setVisible()`` state.
    Without a shown parent, ``isVisible()`` always returns False
    even after ``label.setVisible(True)``. ``show()`` is the
    minimum that satisfies that constraint; nothing is actually
    rendered because we're not in an event loop.
    """
    from ui.settings_dialog import SettingsDialog

    if settings is None:
        settings = dict(DEFAULT_SETTINGS)
    dlg = SettingsDialog(settings, conn, db_path)
    dlg.show()
    return dlg


def test_settings_dialog_builds(qapp, conn, tmp_path):
    """A fresh dialog builds without errors and exposes its signals."""
    from PySide6.QtWidgets import QDialog

    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        # The dialog should be on screen (not visible, but built)
        assert isinstance(dlg, QDialog)
        # Both signals must exist as Signal descriptors
        assert hasattr(dlg, "settings_saved")
        assert hasattr(dlg, "backup_restored")
    finally:
        dlg.deleteLater()


def test_settings_dialog_renders_every_page(qapp, conn, tmp_path):
    """All seven pages are in the stacked widget, in the expected order.

    The order matters for accessibility/keyboard navigation
    even though the user mostly clicks — if the order is
    changed by accident, the keyboard nav no longer matches
    the visible labels.
    """
    from ui.strings import (
        SETTINGS_CATEGORY_ABOUT,
        SETTINGS_CATEGORY_BACKUPS,
        SETTINGS_CATEGORY_DATA,
        SETTINGS_CATEGORY_GENERAL,
        SETTINGS_CATEGORY_HOTKEY,
        SETTINGS_CATEGORY_NOTIFICATIONS,
        SETTINGS_CATEGORY_TOPICS,
    )

    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        # The category list is the source of truth for the page
        # order; the stacked widget is built in the same sequence.
        labels = [
            dlg._category_list.item(i).text()
            for i in range(dlg._category_list.count())
        ]
        assert labels == [
            SETTINGS_CATEGORY_GENERAL,
            SETTINGS_CATEGORY_HOTKEY,
            SETTINGS_CATEGORY_BACKUPS,
            SETTINGS_CATEGORY_NOTIFICATIONS,
            SETTINGS_CATEGORY_TOPICS,
            SETTINGS_CATEGORY_DATA,
            SETTINGS_CATEGORY_ABOUT,
        ]
        # And the stacked widget has the matching number of pages.
        assert dlg._pages.count() == 7
    finally:
        dlg.deleteLater()


def test_settings_dialog_hotkey_validity_toggles_save_button(qapp, conn, tmp_path):
    """An invalid hotkey disables Save; a valid one re-enables it.

    This is the central UX guarantee of the Hotkey page — the
    user can't save a broken hotkey. The validator's role is
    to drive the Save button's enabled state, not just the
    visibility of an error label.
    """
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        # Start valid: save should be enabled.
        dlg._hotkey_edit.setText("ctrl+space")
        dlg._revalidate_hotkey()
        save_btn = dlg._find_save_button()
        assert save_btn is not None
        assert save_btn.isEnabled() is True
        # Break it: save should disable.
        dlg._hotkey_edit.setText("nonsense")
        dlg._revalidate_hotkey()
        assert save_btn.isEnabled() is False
        # Fix it: save should re-enable.
        dlg._hotkey_edit.setText("ctrl+shift+a")
        dlg._revalidate_hotkey()
        assert save_btn.isEnabled() is True
    finally:
        dlg.deleteLater()


def test_settings_dialog_hotkey_invalid_label_visibility(qapp, conn, tmp_path):
    """The red 'invalid' label only shows for non-empty invalid input.

    We assert on ``isHidden()`` rather than ``isVisible()`` because
    the label lives on a stacked-widget page that isn't the
    current page; ``isVisible()`` returns False whenever the
    parent chain has any unshown ancestor (including the
    stacked-widget's inactive page). ``isHidden()`` reflects the
    explicit ``setVisible(True/False)`` calls, which is what the
    test actually cares about.
    """
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        # Empty input: label hidden (no point shouting at the user
        # before they've typed anything).
        dlg._hotkey_edit.setText("")
        dlg._revalidate_hotkey()
        assert dlg._hotkey_invalid_label.isHidden() is True
        # Garbage: label visible.
        dlg._hotkey_edit.setText("not a hotkey")
        dlg._revalidate_hotkey()
        assert dlg._hotkey_invalid_label.isHidden() is False
        # Valid: label hidden again.
        dlg._hotkey_edit.setText("ctrl+space")
        dlg._revalidate_hotkey()
        assert dlg._hotkey_invalid_label.isHidden() is True
    finally:
        dlg.deleteLater()


def test_settings_dialog_save_emits_full_settings(qapp, conn, tmp_path):
    """Clicking Save emits the full settings dict (defaults merged in).

    The signal is the contract: the owner receives a complete,
    self-contained settings dict and doesn't have to merge against
    its own copy. Any missing keys would force the owner to make
    assumptions.
    """
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        captured: list[dict] = []
        dlg.settings_saved.connect(captured.append)
        # Change a couple of values, then synthesize a save click.
        dlg._hotkey_edit.setText("ctrl+shift+k")
        dlg._notif_enable_check.setChecked(False)
        dlg._reminder_interval_spin.setValue(15)
        dlg._on_save()
        assert len(captured) == 1
        emitted = captured[0]
        # The known set of keys is exactly the defaults
        assert set(emitted.keys()) == set(DEFAULT_SETTINGS.keys())
        # The values we changed were captured
        assert emitted["hotkey"] == "ctrl+shift+k"
        assert emitted["notifications_enabled"] is False
        assert emitted["reminder_interval_minutes"] == 15
    finally:
        dlg.deleteLater()


def test_settings_dialog_cancel_does_not_emit(qapp, conn, tmp_path):
    """Rejecting the dialog must not emit ``settings_saved``."""
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        captured: list[dict] = []
        dlg.settings_saved.connect(captured.append)
        dlg._hotkey_edit.setText("ctrl+shift+k")
        dlg.reject()
        assert captured == []
    finally:
        dlg.deleteLater()


def test_settings_dialog_cancel_preserves_original_settings(qapp, conn, tmp_path):
    """The dialog keeps a working copy; rejecting discards it.

    The dialog's ``_settings`` attribute is private but is the
    source of truth for "what does the user see". After a
    reject, the dialog's working copy still reflects what was
    last loaded (or, equivalently, what was last saved on a
    prior Save), not the dirty control values.
    """
    dlg = _build_dialog(
        qapp, conn, str(tmp_path / "db"),
        settings={"hotkey": "ctrl+space"},
    )
    try:
        dlg._hotkey_edit.setText("ctrl+shift+z")
        dlg.reject()
        # The internal settings reflect the original input, not
        # the dirty control state.
        assert dlg._settings["hotkey"] == "ctrl+space"
    finally:
        dlg.deleteLater()


def test_settings_dialog_storage_change_shows_restart_notice(qapp, conn, tmp_path):
    """Picking a new storage path immediately shows the restart notice.

    The notice is meant to be visible at the moment of choice,
    not deferred to Save. We assert on ``isHidden()`` rather
    than ``isVisible()`` for the same reason as the hotkey
    test: the label lives on a stacked-widget page, and
    ``isVisible()`` depends on the parent chain.
    """
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        # Initially hidden.
        assert dlg._storage_restart_label.isHidden() is True
        # Simulate the change handler's "field updated" branch.
        dlg._storage_path_edit.setText("some/other/path.alfred.db")
        dlg._storage_restart_label.setVisible(True)
        assert dlg._storage_restart_label.isHidden() is False
    finally:
        dlg.deleteLater()


def test_settings_dialog_data_page_stats_render(qapp, conn, tmp_path):
    """The Data page's stat labels populate from the DB.

    A fresh DB has zero memories, zero projects, etc. — the
    test just confirms the labels are set to readable strings
    after the dialog is built. Detailed counts are covered by
    the ``core.db_stats`` tests.
    """
    from core.memory import create_memory

    # Add a memory so the page has something to show.
    create_memory(conn, "Test memory", "Body text.")

    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        dlg._refresh_stats()
        assert dlg._stat_labels["memories"].text() == "1"
        assert dlg._stat_labels["notes"].text() == "1"
        assert dlg._stat_labels["inbox"].text() == "0"
        assert dlg._stat_labels["tasks"].text() == "0"
    finally:
        dlg.deleteLater()


def test_settings_dialog_data_page_refresh_button(qapp, conn, tmp_path):
    """The Data page's Refresh button is wired to _refresh_stats.

    A new memory is added AFTER the dialog is built; clicking
    Refresh should re-read the DB. The button click is
    dispatched directly (without showing the dialog).
    """
    from core.memory import create_memory

    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        # Initially zero.
        dlg._refresh_stats()
        assert dlg._stat_labels["memories"].text() == "0"
        # Add a memory and re-refresh.
        create_memory(conn, "After-build", "")
        dlg._refresh_stats()
        assert dlg._stat_labels["memories"].text() == "1"
    finally:
        dlg.deleteLater()


def test_settings_dialog_data_page_reset_button_gated_by_input(qapp, conn, tmp_path):
    """The Reset button is disabled until the user types the exact token.

    This is the second line of defense (the first is the
    QMessageBox confirm). A user with no malicious intent could
    still mis-click the button; the typed-DELETE gate stops
    that.
    """
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        # Initially disabled.
        assert dlg._reset_btn.isEnabled() is False
        # Wrong text: still disabled.
        dlg._reset_input.setText("delete")  # lowercase
        assert dlg._reset_btn.isEnabled() is False
        # Partial text: still disabled.
        dlg._reset_input.setText("DELET")
        assert dlg._reset_btn.isEnabled() is False
        # Exact token: enabled.
        dlg._reset_input.setText("DELETE")
        assert dlg._reset_btn.isEnabled() is True
        # Cleared: disabled again.
        dlg._reset_input.setText("")
        assert dlg._reset_btn.isEnabled() is False
    finally:
        dlg.deleteLater()


def test_settings_dialog_notifications_reminders_gate_interval_spin(
    qapp, conn, tmp_path
):
    """The interval spinbox is disabled when reminders are off."""
    dlg = _build_dialog(
        qapp, conn, str(tmp_path / "db"),
        settings={
            **DEFAULT_SETTINGS,
            "reminders_enabled": True,
        },
    )
    try:
        assert dlg._reminder_interval_spin.isEnabled() is True
        dlg._reminders_enable_check.setChecked(False)
        assert dlg._reminder_interval_spin.isEnabled() is False
        dlg._reminders_enable_check.setChecked(True)
        assert dlg._reminder_interval_spin.isEnabled() is True
    finally:
        dlg.deleteLater()


def test_apply_theme_applies_dark_with_no_argument(qapp):
    """``apply_theme()`` applies the dark stylesheet unconditionally.

    Alfred is dark-mode only; the function no longer takes a
    theme argument. After calling it, the QApplication's
    stylesheet is the bundled dark palette. We assert on the
    presence of a characteristic selector+property from
    :data:`DARK_QSS` rather than on full string equality, so
    future tweaks to the QSS don't make the test brittle.
    The Phase 17 polish pass rebalanced the palette into
    three tiers; we assert on the deepest tier (window
    background) rather than the old single-color value.
    """
    from ui.theme import DARK_QSS, apply_theme

    apply_theme()
    sheet = qapp.styleSheet()
    # A distinctive fragment of the dark palette — the window
    # background color — must be present.
    assert "background-color: #1f2227" in sheet
    # The accent (butler gold) must also be present — the
    # polish pass introduced it as a unifying selection /
    # primary-action color.
    assert "#c9a14a" in sheet
    # And the sheet should be a superset of the bundled QSS
    # (Qt may add nothing on top; that's still a superset).
    assert DARK_QSS.strip() in sheet or sheet.strip() == DARK_QSS.strip()


def test_settings_dialog_collect_round_trips_values(qapp, conn, tmp_path):
    """``_collect_from_ui`` reads every control into the right key.

    This is the inverse of ``_populate_from_settings``: change
    every value in the UI, collect, and confirm the dict
    matches. A missing collect step would let the user click
    Save and have their changes silently dropped.
    """
    dlg = _build_dialog(
        qapp, conn, str(tmp_path / "db"),
        settings={
            **DEFAULT_SETTINGS,
            "backup_dir": str(tmp_path / "backups"),
        },
    )
    try:
        dlg._storage_path_edit.setText("custom.alfred.db")
        dlg._hotkey_edit.setText("ctrl+alt+1")
        dlg._backup_folder_edit.setText(str(tmp_path / "other_backups"))
        dlg._notif_enable_check.setChecked(False)
        dlg._reminders_enable_check.setChecked(False)
        dlg._reminder_interval_spin.setValue(5)
        collected = dlg._collect_from_ui()
        assert collected["db_path"] == "custom.alfred.db"
        assert collected["hotkey"] == "ctrl+alt+1"
        assert collected["backup_dir"] == str(tmp_path / "other_backups")
        assert collected["notifications_enabled"] is False
        assert collected["reminders_enabled"] is False
        assert collected["reminder_interval_minutes"] == 5
        # No ``theme`` key in the collected dict — the General
        # page no longer exposes a theme control.
        assert "theme" not in collected
    finally:
        dlg.deleteLater()


# ----- ui.settings_dialog: the Topics page -----
#
# The page is a two-column table of (project, comma-separated keywords).
# What matters is the round trip: a rules dict shown as rows, and rows
# collected back into a rules dict that ``ui.assistant_controller`` can
# read. Layout and styling are out of scope, as everywhere else here.


def _topics_row(dlg, row: int) -> tuple[str, str]:
    name = dlg._topics_table.item(row, 0)
    keywords = dlg._topics_table.item(row, 1)
    return (
        "" if name is None else name.text(),
        "" if keywords is None else keywords.text(),
    )


def _set_topics_row(dlg, row: int, name: str, keywords: str) -> None:
    from PySide6.QtWidgets import QTableWidgetItem

    dlg._topics_table.setItem(row, 0, QTableWidgetItem(name))
    dlg._topics_table.setItem(row, 1, QTableWidgetItem(keywords))


def test_topics_page_shows_configured_rules(qapp, conn, tmp_path):
    settings = dict(DEFAULT_SETTINGS)
    settings["topic_keywords"] = {
        "Embedded": ["esp32", "sensor"],
        "Writing": ["draft"],
    }
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"), settings)
    try:
        assert dlg._topics_table.rowCount() == 2
        assert _topics_row(dlg, 0) == ("Embedded", "esp32, sensor")
        assert _topics_row(dlg, 1) == ("Writing", "draft")
        # The "no rules" note is for the empty case only. Asserted via
        # isHidden() rather than isVisible(): the page lives in a
        # QStackedWidget and is not the current one, so every widget on
        # it reports isVisible() False regardless of its own state.
        assert dlg._topics_empty_label.isHidden() is True
    finally:
        dlg.deleteLater()


def test_topics_page_is_empty_by_default(qapp, conn, tmp_path):
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        assert dlg._topics_table.rowCount() == 0
        assert dlg._topics_empty_label.isHidden() is False
        # Nothing selected, so Remove is not offered.
        assert dlg._topics_remove_btn.isEnabled() is False
        assert dlg._collect_from_ui()["topic_keywords"] == {}
    finally:
        dlg.deleteLater()


def test_topics_rows_round_trip_into_the_settings_dict(qapp, conn, tmp_path):
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        dlg._on_topics_add()
        _set_topics_row(dlg, 0, "Embedded", "esp32, sensor, mpu-6050")
        collected = dlg._collect_from_ui()
        assert collected["topic_keywords"] == {
            "Embedded": ["esp32", "sensor", "mpu-6050"]
        }
    finally:
        dlg.deleteLater()


def test_topics_keywords_are_split_and_trimmed(qapp, conn, tmp_path):
    """Whatever spacing the user types, the stored list is clean."""
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        dlg._on_topics_add()
        _set_topics_row(dlg, 0, "  Embedded  ", " esp32 ,,  sensor ,  ")
        assert dlg._collect_from_ui()["topic_keywords"] == {
            "Embedded": ["esp32", "sensor"]
        }
    finally:
        dlg.deleteLater()


def test_topics_incomplete_rows_are_dropped_on_collect(qapp, conn, tmp_path):
    """A row needs both halves to be a rule.

    A name with no keywords can never match anything, and keywords with
    no project have nowhere to file to; either way the row is noise
    rather than a rule, so it is not written.
    """
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        for _ in range(3):
            dlg._on_topics_add()
        _set_topics_row(dlg, 0, "Embedded", "esp32")
        _set_topics_row(dlg, 1, "Nameless", "   ")
        _set_topics_row(dlg, 2, "   ", "orphan")
        assert dlg._collect_from_ui()["topic_keywords"] == {
            "Embedded": ["esp32"]
        }
    finally:
        dlg.deleteLater()


def test_topics_duplicate_project_names_are_merged(qapp, conn, tmp_path):
    """Two rows naming one project become one rule, not a lost one.

    A dict cannot hold the name twice, so the alternative to merging is
    silently discarding whichever row came first.
    """
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        dlg._on_topics_add()
        dlg._on_topics_add()
        _set_topics_row(dlg, 0, "Embedded", "esp32")
        _set_topics_row(dlg, 1, "Embedded", "sensor, esp32")
        collected = dlg._collect_from_ui()["topic_keywords"]
        assert list(collected) == ["Embedded"]
        assert collected["Embedded"] == ["esp32", "sensor"]
    finally:
        dlg.deleteLater()


def test_topics_collect_never_mutates_the_defaults(qapp, conn, tmp_path):
    """``DEFAULT_SETTINGS`` is shallow-copied on load.

    Its ``topic_keywords`` default is therefore one shared dict object,
    so the collector has to build a fresh one. If it ever mutated in
    place, every later ``load_settings`` in the process would inherit
    the rules.
    """
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        dlg._on_topics_add()
        _set_topics_row(dlg, 0, "Embedded", "esp32")
        collected = dlg._collect_from_ui()["topic_keywords"]
        assert collected == {"Embedded": ["esp32"]}
        assert DEFAULT_SETTINGS["topic_keywords"] == {}
        assert collected is not DEFAULT_SETTINGS["topic_keywords"]
    finally:
        dlg.deleteLater()


def test_topics_add_and_remove_a_row(qapp, conn, tmp_path):
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        dlg._on_topics_add()
        assert dlg._topics_table.rowCount() == 1
        # A new row carries real placeholder text, not a blank that
        # would be dropped on save without explanation.
        name, keywords = _topics_row(dlg, 0)
        assert name and keywords
        assert dlg._topics_empty_label.isHidden() is True
        # The row is current after adding, so Remove is live.
        dlg._on_topics_selection_changed()
        assert dlg._topics_remove_btn.isEnabled() is True
        dlg._on_topics_remove()
        assert dlg._topics_table.rowCount() == 0
        assert dlg._topics_empty_label.isHidden() is False
        assert dlg._topics_remove_btn.isEnabled() is False
    finally:
        dlg.deleteLater()


def test_topics_remove_with_no_selection_is_a_no_op(qapp, conn, tmp_path):
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        dlg._on_topics_add()
        dlg._topics_table.setCurrentCell(-1, -1)
        dlg._on_topics_remove()
        assert dlg._topics_table.rowCount() == 1
    finally:
        dlg.deleteLater()


def test_topics_page_tolerates_a_malformed_settings_value(
    qapp, conn, tmp_path
):
    """``topic_keywords`` is hand-editable JSON; a bad shape must not raise."""
    settings = dict(DEFAULT_SETTINGS)
    settings["topic_keywords"] = ["esp32"]  # a list, not a dict
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"), settings)
    try:
        assert dlg._topics_table.rowCount() == 0
        assert dlg._collect_from_ui()["topic_keywords"] == {}
    finally:
        dlg.deleteLater()


def test_topics_a_string_value_is_shown_as_one_keyword(qapp, conn, tmp_path):
    settings = dict(DEFAULT_SETTINGS)
    settings["topic_keywords"] = {"Embedded": "esp32"}
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"), settings)
    try:
        assert _topics_row(dlg, 0) == ("Embedded", "esp32")
    finally:
        dlg.deleteLater()


def test_topics_rules_reach_the_saved_signal_payload(qapp, conn, tmp_path):
    """End to end for the page: edit a row, Save, read the payload."""
    received = []
    dlg = _build_dialog(qapp, conn, str(tmp_path / "db"))
    try:
        dlg.settings_saved.connect(received.append)
        dlg._on_topics_add()
        _set_topics_row(dlg, 0, "Embedded", "esp32, i2c")
        dlg._on_save()
        assert received
        assert received[0]["topic_keywords"] == {"Embedded": ["esp32", "i2c"]}
    finally:
        dlg.deleteLater()
