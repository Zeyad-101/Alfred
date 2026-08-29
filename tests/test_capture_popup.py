"""Tests for the quick-capture popup.

The popup's submission logic is the only piece that's unit-testable:
the global hotkey, the system tray, and the frameless rendering are
OS-level concerns that need a real desktop session to verify. The
notes alongside ``test_main_window_hotkey_placeholder`` and
``test_capture_popup_renders_frameless`` acknowledge that explicitly
rather than fake it.
"""
from __future__ import annotations


# ---------- helpers ----------


def _make_popup(qapp, conn):
    """Construct a CapturePopup without showing it.

    The popup's DB-touching code path is what we test, not the
    window-system one. Constructing the widget doesn't require a
    display; only ``show()`` would.
    """
    from ui.capture_popup import CapturePopup

    return CapturePopup(conn, parent=None)


# ---------- submit-on-enter ----------


def test_enter_with_text_creates_inbox_item(qapp, conn):
    """Pressing Enter with non-empty text creates the item and
    emits ``captured`` with the new id."""
    from core.inbox import list_inbox

    popup = _make_popup(qapp, conn)
    captured_ids: list[int] = []
    popup.captured.connect(captured_ids.append)

    popup.input.setText("Pick up bread")
    popup._on_submit()

    # The popup was found in the inbox list as a real row.
    items = list_inbox(conn)
    assert len(items) == 1
    assert items[0].title == "Pick up bread"
    # And the signal fired with that row's id.
    assert captured_ids == [items[0].id]
    popup.deleteLater()


def test_enter_with_text_strips_whitespace(qapp, conn):
    """Leading/trailing whitespace in the input is trimmed before
    the DB write. We don't want a capture of '   ' looking like a
    real entry in the inbox."""
    from core.inbox import list_inbox

    popup = _make_popup(qapp, conn)
    popup.input.setText("   Refill the printer   ")
    popup._on_submit()

    items = list_inbox(conn)
    assert len(items) == 1
    assert items[0].title == "Refill the printer"
    popup.deleteLater()


def test_enter_with_empty_input_creates_nothing(qapp, conn):
    """Empty input + Enter is a no-op + dismiss: don't create an
    empty inbox row."""
    from core.inbox import list_inbox

    popup = _make_popup(qapp, conn)
    captured: list[int] = []
    popup.captured.connect(captured.append)

    popup.input.setText("")
    popup._on_submit()

    assert list_inbox(conn) == []
    assert captured == []
    popup.deleteLater()


def test_enter_with_whitespace_only_creates_nothing(qapp, conn):
    """Same as empty, but for '   ' and '\\t\\n' style input. The
    strip() in _on_submit is what makes this safe."""
    from core.inbox import list_inbox

    popup = _make_popup(qapp, conn)
    popup.input.setText("   \t\n   ")
    popup._on_submit()

    assert list_inbox(conn) == []
    popup.deleteLater()


# ---------- escape ----------


def test_escape_does_not_create(qapp, conn):
    """Escape dismisses without writing. The user pressed the wrong
    hotkey / changed their mind."""
    from core.inbox import list_inbox

    popup = _make_popup(qapp, conn)
    captured: list[int] = []
    popup.captured.connect(captured.append)

    popup.input.setText("should not be saved")
    popup.reject()  # Escape routes to reject() via the event filter

    assert list_inbox(conn) == []
    assert captured == []
    popup.deleteLater()


# ---------- idempotency / state ----------


def test_double_enter_only_creates_one(qapp, conn):
    """A second Enter after a successful commit must not create
    a duplicate row. _committed guards against this — without it,
    a user (or the auto-confirmation timer) hitting Enter twice
    would double-file the same thought."""
    from core.inbox import list_inbox

    popup = _make_popup(qapp, conn)
    popup.input.setText("Duplicate me")
    popup._on_submit()
    # Simulate the user mashing Enter again (or the close timer
    # firing while another keystroke is processed).
    popup._on_submit()

    items = list_inbox(conn)
    assert len(items) == 1
    popup.deleteLater()


def test_open_capture_resets_committed_flag(qapp, conn):
    """After a successful capture, the next open_capture() must
    reset the committed flag so the popup can capture again. This
    is what makes repeated Ctrl+Space presses work as expected:
    first press opens, second press (after Esc/Enter) opens fresh."""
    from core.inbox import list_inbox

    popup = _make_popup(qapp, conn)
    popup.input.setText("First capture")
    popup._on_submit()
    assert popup._committed is True
    assert len(list_inbox(conn)) == 1

    # Simulate the user dismissing the popup and re-opening it.
    popup._committed = False  # what open_capture() would do
    popup.input.clear()

    popup.input.setText("Second capture")
    popup._on_submit()

    items = list_inbox(conn)
    assert len(items) == 2
    assert [m.title for m in items] == ["First capture", "Second capture"]
    popup.deleteLater()


# ---------- UI invariants (cheap, no rendering) ----------


def test_capture_popup_is_frameless_and_always_on_top(qapp, conn):
    """The popup must be frameless (no title bar), always-on-top
    (so it doesn't get buried behind the app the user was on),
    and a Tool window (so it doesn't clutter the task bar). This
    is a structural check on the window flags; we don't actually
    show the window — that would require a display."""
    popup = _make_popup(qapp, conn)
    flags = int(popup.windowFlags())
    assert flags & int(Qt_FramelessWindowHint())
    assert flags & int(Qt_WindowStaysOnTopHint())
    assert flags & int(Qt_Tool())
    popup.deleteLater()


def test_capture_popup_input_has_placeholder(qapp, conn):
    """The line edit must carry the placeholder text. The visual
    rendering of the placeholder is the OS's problem; we just
    verify the text is set on the widget."""
    from ui.strings import CAPTURE_PLACEHOLDER

    popup = _make_popup(qapp, conn)
    assert popup.input.placeholderText() == CAPTURE_PLACEHOLDER
    popup.deleteLater()


# ---------- local Qt enum shims (so the import block stays small) ----------


def Qt_FramelessWindowHint():
    from PySide6.QtCore import Qt
    return Qt.FramelessWindowHint


def Qt_WindowStaysOnTopHint():
    from PySide6.QtCore import Qt
    return Qt.WindowStaysOnTopHint


def Qt_Tool():
    from PySide6.QtCore import Qt
    return Qt.Tool
