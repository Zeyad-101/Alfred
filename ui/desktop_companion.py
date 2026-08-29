"""Floating always-on-top desktop companion -- Alfred's mascot window.

A top-level ``QWidget`` (independent of ``MainWindow``) that
hosts a single :class:`ui.alfred_pet.AlfredPet` child and
reacts to mouse input. The animation itself belongs to the
multi-state engine in :mod:`ui.alfred_pet`, which owns Alfred's
character, frame timing, and animation logic; this module only
places the window and turns mouse events into poses.

Window behavior
---------------
* Frameless, transparent background, on top of all windows
* ``Tool`` window flag (off the taskbar)
* Drag any part of the sprite to move the window; the position
  is persisted in ``settings.json`` and restored on next launch
* Single click with no drag: emits :attr:`capture_popup_requested`
  with the companion's global position so ``main.py`` can open
  the capture popup nearby
* Double click: emits :attr:`main_window_requested` so
  ``main.py`` can show/restore the main window

Animation
---------
The companion owns ONE :class:`AlfredPet` instance -- a
``QLabel``-based sprite engine that cycles through three
states (``idle`` / ``thinking`` / ``greeting``) at fixed
intervals (120 / 110 / 115 ms). ``greeting`` is one-shot and
auto-returns to ``idle``. There are intentionally no
walking/running/sleeping/jumping/falling/pillow states --
the character is a stationary desktop butler.

Public state API
----------------
* :meth:`play_idle` -- switch to the looping idle state
* :meth:`play_thinking` -- switch to the looping thinking state
* :meth:`play_greeting` -- play the one-shot greeting, then auto-idle

Lifecycle
---------
* Created by ``main.py`` alongside ``MainWindow`` at startup
* Stays visible while ``MainWindow`` is hidden or closed-to-tray
* Only closes when the user picks "Exit Alfred" from the tray
  menu (``main.py`` tears the companion down explicitly then)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QPoint,
    QSize,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from core.paths import asset_path
from ui.alfred_pet import AlfredPet


# Display size of the companion in screen pixels. Matches the
# fixed size of the underlying :class:`AlfredPet` QLabel (160x160
# per the upstream manifest), so the sprite fills the window
# with no scaling artifact.
DISPLAY_SIZE: int = 160

# Drag threshold, in pixels. Mouse moves smaller than this
# between press and release do NOT count as a drag, so a tiny
# hand-tremble during a click won't move the window. Anything
# larger flips the press into a drag.
DRAG_THRESHOLD_PX: int = 4

# Asset root for the animated pet (frames/{state}/*.png and
# manifest.json live underneath this). Resolved through
# :func:`core.paths.asset_path` so the path is correct in both
# dev mode and the PyInstaller bundle.
ASSET_SUBDIR: str = "assets/alfred"

# Three valid states -- the same set :class:`AlfredPet` accepts.
# Re-exported here so callers don't have to import the pet
# class just to drive the companion.
STATE_IDLE = "idle"
STATE_THINKING = "thinking"
STATE_GREETING = "greeting"


class DesktopCompanion(QWidget):
    """The floating desktop mascot window.

    Signals
    -------
    ``capture_popup_requested(QPoint)``
        Emitted on a single click (no drag) with the companion's
        global screen position, so the popup can be anchored
        near the mascot.
    ``main_window_requested()``
        Emitted on a double click, so the main window can be
        shown or restored from the tray.
    ``state_changed(str)``
        Forwarded from the underlying :class:`AlfredPet` so
        callers (e.g. ``main.py``) can observe state
        transitions without holding their own reference to the
        pet widget.
    """

    capture_popup_requested = Signal(QPoint)
    main_window_requested = Signal()
    state_changed = Signal(str)

    def __init__(self, settings: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Settings: a reference to the live settings dict owned
        # by main.py. We mutate the ``companion_position`` entry
        # in place and persist via ``save_settings`` on drag-end.
        self._settings = settings

        # Drag-tracking state. Initialized on every press so a
        # stale value from a previous gesture never leaks into
        # a new one.
        self._press_pos: Optional[QPoint] = None
        self._press_window_pos: Optional[QPoint] = None
        self._moved: bool = False

        # --- Window flags and chrome ---------------------------------
        # Frameless: no title bar / borders -- the sprite is the
        # entire window. StaysOnTop: visible above other apps
        # so the user can find the mascot when they need it.
        # Tool: keeps the window off the taskbar (a floating
        # mascot on the taskbar would be visual noise).
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        # Translucent background: every pixel of the window that
        # isn't part of the sprite is fully transparent, so the
        # mascot sits on the user's wallpaper with no visible
        # rectangle. ``WA_TranslucentBackground`` is the Qt
        # attribute that enables per-pixel alpha compositing.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # No system background fill either -- AlfredPet's QLabel
        # is the only place that draws to the widget tree.
        self.setAttribute(Qt.WA_NoSystemBackground, True)

        self.setFixedSize(DISPLAY_SIZE, DISPLAY_SIZE)

        # --- The animated pet -----------------------------------------
        # ONE AlfredPet instance per process -- confirmed by
        # construction here. The widget is a child of this
        # companion so it inherits the frameless + translucent
        # attributes and gets cleaned up with us. Its frames
        # come from ``assets/alfred/frames/{state}/*.png``,
        # resolved through asset_path() so PyInstaller is happy.
        assets_dir: Path = asset_path(ASSET_SUBDIR)
        self._pet = AlfredPet(assets_dir, parent=self)

        # When set, one pose is held and the play_* helpers stand
        # down (see force_pose / release_pose).
        self._forced_pose: str | None = None
        # Set when release_pose() was called before the held animation
        # had completed a loop: the release is deferred to the pet's
        # next cycle_completed so the pose is never cut mid-sway.
        self._pending_release = False
        # The pet is a 160x160 QLabel that fills the companion
        # window exactly (DISPLAY_SIZE is also 160). Top-left
        # anchored so the user can drag from anywhere on the
        # sprite.
        self._pet.move(0, 0)
        self._pet.show()
        # Forward the pet's state changes so callers can
        # observe transitions without poking at the private
        # widget.
        self._pet.state_changed.connect(self.state_changed.emit)
        # Drives the deferred release (see release_pose).
        self._pet.cycle_completed.connect(self._on_cycle_completed)
        self._pet.state_changed.connect(self._on_pet_state_changed)

        # Restore the last-saved position, clamped to the
        # current screen layout. If the saved position is
        # off-screen (the user disconnected a monitor, etc.)
        # we fall back to a sensible corner of the primary
        # screen so the companion is never launched into the
        # void.
        self._restore_position()

    # ----- public API -----

    def start(self) -> None:
        """Show the companion and play the startup greeting.

        Called once at app startup, after the widget is
        constructed and the QApplication is up. Plays a
        one-shot greeting to match the previous embedded
        mascot's first-show behavior; ``greeting`` auto-returns
        to ``idle`` when its frame sequence completes.
        """
        self.show()
        self.raise_()
        self.play_greeting()

    def play_idle(self) -> None:
        """Switch to the looping idle state.

        Idempotent -- calling while already idle is a no-op, so a
        caller that re-asserts idle on every event cannot pin the
        sprite to frame 0 (``AlfredPet.set_pose`` returns early when
        the requested state is the one already running).
        """
        if self._forced_pose is not None:
            return
        self._pet.idle()

    def play_thinking(self) -> None:
        """Switch to the looping thinking state.

        Used to give a visible cue while Alfred is processing
        a user request. Idempotent for the same reason
        :meth:`play_idle` is; a caller that wants the pose held
        against other hooks should use :meth:`force_pose`.
        """
        if self._forced_pose is not None:
            return
        self._pet.thinking()

    def play_greeting(self) -> None:
        """Play the one-shot greeting, then auto-return to idle.

        Safe to call from outside (e.g. a startup hook): a greeting
        acknowledges the event that just happened, so ``AlfredPet.greet``
        replays it from frame 0 even if one is already running.
        """
        if self._forced_pose is not None:
            return
        self._pet.greet()

    # ----- pose lock -----

    def force_pose(self, name: str) -> None:
        """Hold one of the existing poses until :meth:`release_pose`.

        The butler interaction layer needs the thinking pose to stay put
        for exactly as long as a lookup is actually running, without a
        stray ``play_idle`` from some other hook (the search debounce,
        say) cutting it short. While a pose is forced, ``play_idle`` /
        ``play_thinking`` / ``play_greeting`` are no-ops.

        Only the three poses that already exist are accepted -- this adds
        no new art and no new animation state. Unknown names raise
        ``ValueError``, matching ``AlfredPet.set_pose``.
        """
        if name not in (STATE_IDLE, STATE_THINKING, STATE_GREETING):
            raise ValueError(f"unknown pose: {name!r}")
        # Re-forcing the pose already held must not restart it, or a
        # caller that asserts "thinking" more than once per request
        # would keep the sprite on frame 0 forever.
        self._pet.set_pose(name)
        self._forced_pose = name
        self._pending_release = False

    def release_pose(self) -> None:
        """Drop the lock and return to idle. Safe to call unforced.

        A looping pose that has not yet completed a single cycle is
        allowed to finish it first. Lookups routinely resolve in
        single-digit milliseconds, and releasing on that timing showed
        the user the first three or four frames of a twelve-frame sway --
        movement too slight to register as animation at all. The release
        is therefore queued on the pet's ``cycle_completed`` signal,
        which is the animation's own clock rather than a second timer.

        This defers only the *companion's* return to idle. The answer is
        already on screen by then; nothing about the reply waits on it.
        """
        if self._forced_pose is None:
            return
        state = self._pet.state
        loops = self._pet.frames.get(state, [])
        deferrable = (
            state != STATE_GREETING  # one-shot: returns to idle by itself
            and len(loops) > 1
            and self._pet.cycles == 0
            and self._pet.timer.isActive()
        )
        if deferrable:
            self._pending_release = True
            return
        self._finish_release()

    def _finish_release(self) -> None:
        self._forced_pose = None
        self._pending_release = False
        self._pet.idle()

    def _on_cycle_completed(self, _state: str) -> None:
        """Complete a release that was waiting for the loop to wrap."""
        if self._pending_release:
            self._finish_release()

    def _on_pet_state_changed(self, state: str) -> None:
        """Never leave a release queued against a pose that is gone.

        The greeting animation ends by switching itself to idle, so a
        release queued behind it would otherwise wait for a cycle that
        will never be reported.
        """
        if self._pending_release and state != self._forced_pose:
            self._pending_release = False
            self._forced_pose = None

    def release_pending(self) -> bool:
        """True when a release is waiting on the current loop to wrap."""
        return self._pending_release

    def frame_counts(self) -> dict[str, int]:
        """Frames loaded per state -- runtime proof the sequences exist."""
        return self._pet.frame_counts()

    def forced_pose(self) -> str | None:
        """The currently held pose name, or ``None`` when unlocked."""
        return self._forced_pose

    def current_state(self) -> str:
        """Return the pet's current state name.

        Useful for tests and for ``main.py`` to query what
        the mascot is doing without poking at the private
        widget.
        """
        return self._pet.state

    def shutdown(self) -> None:
        """Tear the companion down cleanly (called on Exit).

        Stops the pet's frame timer, saves the final position,
        and hides the window. After this the widget should be
        deleted by the caller (``main.py``).
        """
        self._pet.stop()
        self._save_position()
        self.hide()

    # ----- mouse handling -----

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Record the press site; do not act yet.

        Drag detection and click-vs-double-click both
        require the release to happen first. A press is
        always the start of either a drag or a click, so
        the only state we need to capture is the press
        position (in global coords, for drag math) and the
        window's position at that moment (so the first
        ``mouseMoveEvent`` has a reference for ``move()``).
        """
        if event.button() != Qt.LeftButton:
            return
        # Cancel any pending single-click from a previous
        # press. If the user is partway through a double
        # click, the first release started a single-click
        # timer; the second press needs to void that timer
        # or we'd open the capture popup after the
        # double-click already opened the main window.
        self._kill_pending_single_click()
        self._press_pos = event.globalPosition().toPoint()
        self._press_window_pos = self.pos()
        self._moved = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Move the window once the cursor has traveled past
        the drag threshold.

        We update the window's position in global coords
        using ``QWidget.move``; the first move event after
        the threshold sets ``_moved = True`` so the matching
        ``mouseReleaseEvent`` knows to skip the click logic.
        """
        if self._press_pos is None:
            return
        if not (event.buttons() & Qt.LeftButton):
            # The cursor moved but the button isn't held --
            # not a drag, not our concern. (Can happen if
            # another widget captured the press.)
            return
        delta = event.globalPosition().toPoint() - self._press_pos
        if not self._moved and delta.manhattanLength() < DRAG_THRESHOLD_PX:
            # Small movement, not a drag yet. Don't move the
            # window so the user can still single-click
            # without nudging the mascot.
            return
        self._moved = True
        # Move relative to the press, so the cursor's
        # global position relative to the window stays
        # consistent (no "jump" on the first move event).
        new_pos = self._press_window_pos + delta
        self.move(new_pos)
        # Also clear any pending single-click from this
        # press -- we now know it's a drag, not a click.
        self._kill_pending_single_click()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Click vs drag decision happens here.

        A drag (the cursor traveled past the threshold)
        saves the new position and otherwise does nothing.
        A real click (no drag) arms a single-shot timer
        that fires the capture-popup request after the
        double-click interval. The timer approach lets
        ``mouseDoubleClickEvent`` cleanly cancel the
        single-click action if the press turns out to be
        part of a double-click.
        """
        if event.button() != Qt.LeftButton:
            return
        was_drag = self._moved
        # Reset drag-tracking state immediately so a stray
        # event after release doesn't see stale data.
        self._press_pos = None
        self._press_window_pos = None
        self._moved = False
        if was_drag:
            self._save_position()
            return
        # Schedule the single-click action. If a second
        # press arrives within the double-click window,
        # ``mouseDoubleClickEvent`` will cancel this timer
        # before it fires.
        interval = QGuiApplication.styleHints().mouseDoubleClickInterval() \
            if hasattr(QGuiApplication.styleHints(), "mouseDoubleClickInterval") \
            else 250
        # Fallback: ``QApplication.doubleClickInterval()`` is
        # the older static accessor. Use it if the style
        # hint isn't available.
        if interval <= 0:
            interval = QApplication.doubleClickInterval()
        self._pending_click_timer = QTimer(self)
        self._pending_click_timer.setSingleShot(True)
        self._pending_click_timer.timeout.connect(self._fire_single_click)
        self._pending_click_timer.start(max(50, interval))

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """A real double click: cancel any pending single-click
        and emit the main-window request.
        """
        if event.button() != Qt.LeftButton:
            return
        self._kill_pending_single_click()
        # Reset drag state in case the double-click is
        # processed after the second release has already
        # been queued.
        self._press_pos = None
        self._press_window_pos = None
        self._moved = False
        self.main_window_requested.emit()

    # ----- click emission helpers -----

    def _kill_pending_single_click(self) -> None:
        """Cancel the post-release single-click timer if one is armed."""
        timer = getattr(self, "_pending_click_timer", None)
        if timer is not None:
            timer.stop()
            self._pending_click_timer = None

    def _fire_single_click(self) -> None:
        """The single-click window elapsed -- emit the popup request.

        Carries the companion's global screen position so the
        popup can be anchored next to the mascot, not next to
        wherever the user's cursor happens to be.
        """
        self._pending_click_timer = None
        # The companion's top-left in global coords is
        # ``self.mapToGlobal(QPoint(0, 0))``. That's the
        # anchor the popup uses to sit just to the right.
        anchor = self.mapToGlobal(QPoint(0, 0))
        self.capture_popup_requested.emit(anchor)

    def bubble_anchor(self) -> QPoint:
        """Return the sprite's top-left in global coordinates.

        The same point :meth:`_fire_single_click` sends with
        ``capture_popup_requested``, exposed so the popup can ask for it
        directly on the paths that have no click to carry it (the global
        hotkey, the tray item, the toolbar button). Read at open time
        rather than cached, because he is draggable.
        """
        return self.mapToGlobal(QPoint(0, 0))

    # ----- position persistence -----

    def _restore_position(self) -> None:
        """Apply the saved companion_position to this window.

        Reads ``self._settings["companion_position"]`` (a
        ``[x, y]`` list). The list is validated, then clamped
        to the current primary screen's available geometry so
        a position saved on a now-disconnected monitor doesn't
        land the companion off-screen.

        The actual move is a no-op if the values aren't
        well-formed; the companion just stays at its default
        position (Qt's choice for a freshly-constructed
        top-level window) until the user drags it.
        """
        raw = self._settings.get("companion_position") if self._settings else None
        if not (isinstance(raw, (list, tuple)) and len(raw) == 2):
            # First run, corrupt value, or unrecognized type --
            # leave the window at its default top-left-ish
            # position. The user can drag it where they want.
            return
        try:
            x = int(raw[0])
            y = int(raw[1])
        except (TypeError, ValueError):
            return
        # Clamp to the primary screen so the companion is
        # always reachable. If the saved position is on a
        # screen that no longer exists, this also nudges the
        # companion back to a sensible spot.
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            w = self.width()
            h = self.height()
            x = max(geo.left(), min(x, geo.right() - w))
            y = max(geo.top(), min(y, geo.bottom() - h))
        self.move(x, y)

    def _save_position(self) -> None:
        """Write the current window position back into settings.

        Called on drag-end and on shutdown. The settings dict
        is mutated in place (the main window holds the same
        reference) so a later ``save_settings(settings)`` from
        the main window picks up the new value. We also do
        not call ``save_settings`` here -- that would create a
        tight coupling between the companion and the
        settings file's path. The Settings dialog or
        ``_on_settings_saved`` slot can persist whenever it
        likes; the in-memory mutation is what we need
        immediately.
        """
        if self._settings is None:
            return
        p = self.pos()
        self._settings["companion_position"] = [p.x(), p.y()]

    # ----- Qt overrides -----

    def sizeHint(self) -> QSize:
        """The widget's natural size is the display size."""
        return QSize(DISPLAY_SIZE, DISPLAY_SIZE)
