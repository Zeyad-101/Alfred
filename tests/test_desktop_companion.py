"""Tests for the floating desktop companion (``ui.desktop_companion``).

The companion is a small, standalone QWidget that hosts a
single :class:`ui.alfred_pet.AlfredPet` child. The tests
cover the behaviors that matter most:

* The widget constructs cleanly, owns exactly one ``AlfredPet``
  child, and that pet's frames load successfully
* Window flags: frameless, always-on-top, ``Tool`` so the
  window stays out of the taskbar
* Translucent background so the unfilled pixels composite
  against the desktop wallpaper
* Mouse interaction correctly distinguishes a drag (no click
  signal) from a single click (capture-popup signal) and a
  double click (main-window signal)
* Position persistence: moving the window updates the live
  settings dict, and a fresh companion constructed with that
  dict restores the position
* State machine: ``play_idle`` / ``play_thinking`` /
  ``play_greeting`` switch the pet's state, the
  ``state_changed`` signal fires, and ``greeting`` is a
  one-shot that auto-returns to ``idle``

Headless construction is handled by the session-scoped ``qapp``
fixture in ``conftest.py`` plus Qt's
``setAttribute(Qt.WA_DontShowOnScreen, True)`` where we want
to bypass the actual screen paint. We do NOT test the full
paint cycle here — that would require a real display and is
exactly what the spec's "GUI verification" step (run the
app, look at it) is for.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from core.settings import DEFAULT_SETTINGS
from ui.desktop_companion import (
    DISPLAY_SIZE,
    DRAG_THRESHOLD_PX,
    STATE_GREETING,
    STATE_IDLE,
    STATE_THINKING,
    DesktopCompanion,
)
from ui.alfred_pet import AlfredPet


# --- helpers ---------------------------------------------------------------


def _settings_with_position(x: int, y: int) -> dict:
    """Return a fresh settings dict with companion_position set."""
    s = dict(DEFAULT_SETTINGS)
    s["companion_position"] = [x, y]
    return s


def _press(companion: DesktopCompanion, x: int, y: int) -> QMouseEvent:
    """Build a left-button press event at widget-local (x, y).

    Returns the event so the caller can call ``mousePressEvent``
    on the widget. We don't pass the event through Qt's
    notification pipeline — direct dispatch is faster and lets
    us test internal state without a real screen.

    The 7-arg QMouseEvent constructor (PySide6 ≥ 6.5) takes
    localPos, scenePos, and globalPos explicitly. The shorter
    5-arg form leaves ``globalPos()`` as a sentinel value
    (``8388608, 8388608``), which would break the companion's
    drag math (it computes deltas from the press's global pos).
    """
    return QMouseEvent(
        QEvent.MouseButtonPress,
        QPointF(x, y),
        QPointF(x, y),
        QPointF(x, y),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )


def _move(companion: DesktopCompanion, x: int, y: int) -> QMouseEvent:
    """Build a left-button-held move event at widget-local (x, y).

    See ``_press`` for why the 7-arg form is used.
    """
    return QMouseEvent(
        QEvent.MouseMove,
        QPointF(x, y),
        QPointF(x, y),
        QPointF(x, y),
        Qt.NoButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )


def _release(companion: DesktopCompanion, x: int, y: int) -> QMouseEvent:
    """Build a left-button release event at widget-local (x, y).

    See ``_press`` for why the 7-arg form is used.
    """
    return QMouseEvent(
        QEvent.MouseButtonRelease,
        QPointF(x, y),
        QPointF(x, y),
        QPointF(x, y),
        Qt.LeftButton,
        Qt.NoButton,
        Qt.NoModifier,
    )


def _double_click(companion: DesktopCompanion, x: int, y: int) -> QMouseEvent:
    """Build a left-button double-click event at widget-local (x, y).

    See ``_press`` for why the 7-arg form is used.
    """
    return QMouseEvent(
        QEvent.MouseButtonDblClick,
        QPointF(x, y),
        QPointF(x, y),
        QPointF(x, y),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )


# --- construction / window flags ------------------------------------------


def test_desktop_companion_constructs(qapp):
    """A fresh DesktopCompanion builds without error, owns ONE
    AlfredPet child, and that pet loads all three states'
    frame sets successfully.
    """
    settings = _settings_with_position(150, 200)
    c = DesktopCompanion(settings=settings)
    try:
        # Single pet, single instance — the brief explicitly
        # requires "only one AlfredPet class instance
        # referenced throughout the codebase".
        assert isinstance(c._pet, AlfredPet)
        # All three state frame sets must have loaded.
        assert set(c._pet.frames.keys()) == {"idle", "thinking", "greeting"}
        for state, pixmaps in c._pet.frames.items():
            assert pixmaps, f"no frames loaded for state {state!r}"
            for pix in pixmaps:
                assert not pix.isNull(), (
                    f"null QPixmap in {state!r} frame set"
                )
    finally:
        c.deleteLater()


def test_desktop_companion_window_flags(qapp):
    """The companion is frameless, on top, and a ``Tool`` window.

    These three flags together are the spec's "floating mascot
    that doesn't crowd the taskbar" requirement. If any of them
    is missing, the companion either grows a title bar, gets
    hidden behind other windows, or shows up in the taskbar —
    all wrong.
    """
    c = DesktopCompanion(settings=_settings_with_position(50, 50))
    try:
        flags = c.windowFlags()
        assert flags & Qt.FramelessWindowHint, "FramelessWindowHint missing"
        assert flags & Qt.WindowStaysOnTopHint, "WindowStaysOnTopHint missing"
        assert flags & Qt.Tool, "Tool flag missing (would show in taskbar)"
    finally:
        c.deleteLater()


def test_desktop_companion_translucent_background(qapp):
    """The widget has WA_TranslucentBackground set, which is what
    makes the unfilled pixels render as truly transparent rather
    than as a default-colored rectangle."""
    c = DesktopCompanion(settings=_settings_with_position(50, 50))
    try:
        assert c.testAttribute(Qt.WA_TranslucentBackground), (
            "WA_TranslucentBackground not set — sprite would render "
            "against a visible default-colored rectangle"
        )
    finally:
        c.deleteLater()


def test_desktop_companion_display_size_matches_pet(qapp):
    """The companion's fixed size equals the AlfredPet's fixed
    size so the sprite fills the window with no scaling
    artifact. The Phase 16 design uses 160x160 throughout.
    """
    c = DesktopCompanion(settings=_settings_with_position(50, 50))
    try:
        assert c.width() == DISPLAY_SIZE == 160
        assert c.height() == DISPLAY_SIZE == 160
        assert c._pet.width() == 160
        assert c._pet.height() == 160
    finally:
        c.deleteLater()


def test_desktop_companion_pet_is_child_widget(qapp):
    """The AlfredPet is a child of the companion, not a
    standalone top-level widget. This is what gives the pet
    the companion's frameless + translucent window attributes
    via Qt's widget-tree inheritance.
    """
    c = DesktopCompanion(settings=_settings_with_position(50, 50))
    try:
        assert c._pet.parent() is c
    finally:
        c.deleteLater()


# --- state machine --------------------------------------------------------


def test_state_transitions_update_pet_and_emit_signal(qapp):
    """A *transition* switches the pet's state and emits
    ``state_changed`` with the matching name; re-asserting the state
    already playing emits nothing. The companion forwards the pet's
    signal so callers can observe transitions without poking at the
    private widget.

    The no-op half matters as much as the transition half: the search
    box re-asserts idle on every keystroke, and an emit per keystroke
    would drag the sprite back to frame 0 (see ``AlfredPet.set_pose``,
    which returns early when the requested state is already running).
    """
    c = DesktopCompanion(settings=_settings_with_position(50, 50))
    try:
        observed: list[str] = []
        c.state_changed.connect(lambda s: observed.append(s))

        # Constructed idle already, so this is a re-assertion: state
        # holds, nothing is emitted.
        c.play_idle()
        assert c.current_state() == STATE_IDLE
        assert observed == []

        c.play_thinking()
        assert c.current_state() == STATE_THINKING
        assert observed[-1] == STATE_THINKING

        c.play_greeting()
        assert c.current_state() == STATE_GREETING
        assert observed[-1] == STATE_GREETING

        # ...and the transition back out of greeting does emit.
        c.play_idle()
        assert c.current_state() == STATE_IDLE
        assert observed[-1] == STATE_IDLE
    finally:
        c.deleteLater()


def test_greeting_is_one_shot_and_returns_to_idle(qapp):
    """The greeting state is a one-shot animation that
    auto-returns to ``idle`` when the frame sequence
    completes. This is the supplied :class:`AlfredPet`
    contract from ``alfred-animated-pet`` and the brief
    explicitly forbids persistent greeting.
    """
    c = DesktopCompanion(settings=_settings_with_position(50, 50))
    try:
        c.play_greeting()
        assert c.current_state() == STATE_GREETING
        # The pet is frame-driven by a QTimer. Manually
        # advance through every frame of the greeting
        # sequence; the last advance should auto-trigger
        # the return-to-idle transition.
        for _ in range(len(c._pet.frames[STATE_GREETING])):
            c._pet._next_frame()
        assert c.current_state() == STATE_IDLE, (
            f"greeting did not auto-return to idle (got {c.current_state()!r})"
        )
    finally:
        c.deleteLater()


def test_idle_and_thinking_loop_continuously(qapp):
    """``idle`` and ``thinking`` are looping states — the
    pet stays in the state after the frame sequence
    completes (it wraps back to frame 0).
    """
    c = DesktopCompanion(settings=_settings_with_position(50, 50))
    try:
        c.play_idle()
        for _ in range(len(c._pet.frames[STATE_IDLE]) + 5):
            c._pet._next_frame()
        assert c.current_state() == STATE_IDLE

        c.play_thinking()
        for _ in range(len(c._pet.frames[STATE_THINKING]) + 5):
            c._pet._next_frame()
        assert c.current_state() == STATE_THINKING
    finally:
        c.deleteLater()


def test_shutdown_stops_pet_timer(qapp):
    """``shutdown`` stops the pet's frame timer so no stray
    ``_next_frame`` calls land on a torn-down widget.
    """
    c = DesktopCompanion(settings=_settings_with_position(50, 50))
    try:
        c.play_thinking()
        assert c._pet.timer.isActive()
        c.shutdown()
        assert not c._pet.timer.isActive()
    finally:
        c.deleteLater()


# --- mouse interaction ----------------------------------------------------


def test_click_without_drag_arms_capture_popup_timer(qapp):
    """A press+release with no significant move should arm a
    pending single-click timer that, when fired, emits
    ``capture_popup_requested``."""
    c = DesktopCompanion(settings=_settings_with_position(100, 100))
    try:
        received: list[QPoint] = []
        c.capture_popup_requested.connect(lambda p: received.append(p))

        c.mousePressEvent(_press(c, 30, 30))
        # No move — a real click.
        c.mouseReleaseEvent(_release(c, 30, 30))

        # The pending single-click timer should now be active.
        timer = getattr(c, "_pending_click_timer", None)
        assert timer is not None, "no pending-click timer after click-without-drag"
        assert timer.isActive(), "pending-click timer not started"
        # _moved must be False after release so the next interaction
        # starts from a clean slate.
        assert c._moved is False

        # Force the timer to fire (don't wait the real interval).
        timer.timeout.emit()
        assert len(received) == 1, (
            f"expected one capture_popup_requested emission, got {len(received)}"
        )
    finally:
        c.deleteLater()


def test_click_with_drag_does_not_emit_capture_popup(qapp):
    """A press followed by a move past the drag threshold, then a
    release, should be treated as a drag — no capture-popup
    signal, but the new position should be saved into settings."""
    settings = _settings_with_position(100, 100)
    c = DesktopCompanion(settings=settings)
    try:
        received: list[QPoint] = []
        c.capture_popup_requested.connect(lambda p: received.append(p))

        c.mousePressEvent(_press(c, 30, 30))
        # Move well past the drag threshold.
        c.mouseMoveEvent(_move(c, 30 + DRAG_THRESHOLD_PX + 10, 30))
        # The widget should have moved and flagged _moved=True.
        assert c._moved is True
        # The pending-click timer should have been killed by the
        # move handler (we know it's a drag now).
        assert getattr(c, "_pending_click_timer", None) is None

        c.mouseReleaseEvent(_release(c, 30 + DRAG_THRESHOLD_PX + 10, 30))

        # No signal should have fired.
        assert received == [], (
            f"capture_popup_requested fired on drag ({received!r})"
        )
        # The position should be persisted to the live settings dict.
        # We don't assert exact values because the widget's move
        # is delta-based and the math depends on the press origin,
        # but the keys must be present and well-formed.
        saved = settings["companion_position"]
        assert isinstance(saved, list) and len(saved) == 2
        assert all(isinstance(v, int) for v in saved)
    finally:
        c.deleteLater()


def test_tiny_movement_is_not_a_drag(qapp):
    """A 1- or 2-pixel jitter during a click is not a drag.

    The companion should still treat press+release as a click
    and arm the single-click timer. This is the threshold-based
    anti-jitter behavior — without it, a normal click with a
    slightly shaky hand would be classified as a drag.
    """
    c = DesktopCompanion(settings=_settings_with_position(100, 100))
    try:
        c.mousePressEvent(_press(c, 30, 30))
        # Move well below the threshold.
        c.mouseMoveEvent(_move(c, 30 + 1, 30 + 1))
        assert c._moved is False, "tiny move incorrectly classified as drag"
        c.mouseReleaseEvent(_release(c, 30 + 1, 30 + 1))
        timer = getattr(c, "_pending_click_timer", None)
        assert timer is not None and timer.isActive(), (
            "tiny move should still allow single-click to fire"
        )
    finally:
        c.deleteLater()


def test_double_click_emits_main_window_request(qapp):
    """A double click fires ``main_window_requested`` and cancels
    any pending single-click that the first release scheduled.

    We simulate the second half of a double-click — Qt has
    already processed the first press/release; we just need
    to feed the ``MouseButtonDblClick`` event and verify the
    signal fires. The internal cancellation logic is covered
    by ``test_double_click_cancels_pending_single_click``.
    """
    c = DesktopCompanion(settings=_settings_with_position(100, 100))
    try:
        received = []
        c.main_window_requested.connect(lambda: received.append(True))
        # No spurious popup signal should fire either.
        c.capture_popup_requested.connect(lambda p: received.append(("popup", p)))

        c.mouseDoubleClickEvent(_double_click(c, 30, 30))
        assert received == [True], (
            f"expected only main_window_requested, got {received!r}"
        )
    finally:
        c.deleteLater()


def test_double_click_cancels_pending_single_click(qapp):
    """If a click-without-drag is followed (within the double-click
    window) by a double-click event, the pending single-click
    timer must be cancelled so the capture popup doesn't fire
    on top of the main window being restored.

    Sequence simulated:
      press, release  → arms pending-click timer
      press, release, MouseButtonDblClick  → main_window_requested
                                            AND pending timer killed
    """
    c = DesktopCompanion(settings=_settings_with_position(100, 100))
    try:
        main_calls = []
        popup_calls = []
        c.main_window_requested.connect(lambda: main_calls.append(True))
        c.capture_popup_requested.connect(lambda p: popup_calls.append(p))

        # First press+release arms the pending single-click.
        c.mousePressEvent(_press(c, 30, 30))
        c.mouseReleaseEvent(_release(c, 30, 30))
        timer = c._pending_click_timer
        assert timer is not None and timer.isActive()

        # Now a second press followed by a double-click.
        c.mousePressEvent(_press(c, 30, 30))
        c.mouseDoubleClickEvent(_double_click(c, 30, 30))

        # Pending timer must be gone.
        assert c._pending_click_timer is None, (
            "pending single-click timer survived the double-click"
        )
        assert main_calls == [True]
        # The popup signal must NOT have fired yet. (It would have
        # fired on the pending timer's timeout, but we killed it.)
        assert popup_calls == []

        # Belt-and-suspenders: even if the now-dead timer were
        # somehow re-triggered, no further signal should land.
        timer = getattr(c, "_pending_click_timer", None)
        if timer is not None:
            timer.timeout.emit()
        assert popup_calls == [], (
            f"capture_popup_requested fired after double-click: {popup_calls!r}"
        )
    finally:
        c.deleteLater()


# --- position persistence -------------------------------------------------


def test_position_save_load_round_trip(qapp, tmp_path):
    """Move a companion, then construct a fresh one with the same
    settings dict; the new one should land at the same position
    (clamped to the primary screen's geometry)."""
    # First companion: simulate a drag-end by directly invoking
    # the save path with a known position.
    settings_a = _settings_with_position(200, 300)
    c_a = DesktopCompanion(settings=settings_a)
    try:
        # _save_position writes the current ``self.pos()`` to
        # settings. Move the widget first so the save has a
        # non-default value to write.
        c_a.move(400, 500)
        c_a._save_position()
        assert settings_a["companion_position"] == [400, 500]
    finally:
        c_a.deleteLater()

    # Second companion: built with the same dict.
    c_b = DesktopCompanion(settings=settings_a)
    try:
        # The widget should have landed at (400, 500) (or close,
        # if the primary screen clamps it — but the test env's
        # primary screen is always large enough that no clamp
        # applies at 400x500).
        assert c_b.pos().x() == 400
        assert c_b.pos().y() == 500
    finally:
        c_b.deleteLater()


def test_position_invalid_value_falls_back_to_default(qapp):
    """A settings dict with a malformed ``companion_position``
    (e.g. a string, the wrong length, a non-numeric) should not
    crash construction — the companion just doesn't move from
    its default top-left-ish position."""
    bad_settings = dict(DEFAULT_SETTINGS)
    bad_settings["companion_position"] = "not a list"
    c = DesktopCompanion(settings=bad_settings)
    try:
        # The widget should exist; the position is whatever Qt
        # gives a freshly-constructed top-level widget. We just
        # assert that the construction succeeded and the widget
        # has a valid pos() (i.e. no exception during restore).
        assert isinstance(c.pos(), QPoint)
    finally:
        c.deleteLater()
