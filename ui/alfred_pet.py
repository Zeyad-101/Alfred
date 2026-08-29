"""Animated Alfred companion -- the QLabel-based sprite engine.

Vendored from the standalone ``alfred-animated-pet`` module (plus
its ``frames/`` directory and ``manifest.json``). This copy carries
one small fix over the original: that
file references ``Qt.WidgetAttribute.WA_TranslucentBackground``
without importing the ``Qt`` namespace. We import it here so the
class works as-is when constructed by ``ui.desktop_companion``.

The artwork and this implementation are the reference for Alfred's
character, animation design, and frame timing. Treat the animation
logic as vendored: extend it rather than redrawing or rewriting it,
so the companion keeps matching the shipped frames.

Two behavioural fixes have been made on top of the vendored frame
engine, both because the host app drives it differently from the
upstream demo (which only ever changed pose on a keypress):

* ``set_pose`` is idempotent. Upstream restarted the loop from frame 0
  on every call, so an app that re-asserts the current pose on each
  event -- as the companion does -- pinned the sprite to frame 0 and
  looked static. Switching *between* states still resets to frame 0;
  re-asserting the state already running is now a no-op, and a genuine
  restart is available with ``restart=True``.
* ``cycle_completed`` is emitted when a looping state wraps past its
  last frame, so a caller can let an animation finish reading as motion
  instead of cutting it off mid-sway.

Neither changes the artwork, the frame order, or the frame timing.

Supported states (matches the upstream manifest):

* ``idle``     -- gentle breathing/posture loop (8 frames, 120 ms)
* ``thinking`` -- subtle side-to-side motion during processing
                 (12 frames, 110 ms)
* ``greeting`` -- one-shot acknowledgment that auto-returns to
                 ``idle`` (8 frames, 115 ms)

There are intentionally NO walking, running, sleeping, jumping,
falling, or pillow states. Alfred is a stationary desktop butler,
not a game character.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel


class AlfredPet(QLabel):
    """Small animated Alfred companion for a PySide6 desktop app.

    Supported states:
      - idle      : gentle breathing/posture loop
      - thinking  : slightly more active sway while processing
      - greeting  : short acknowledgment animation, then idle

    No running/walking/sleeping modes are used.
    """

    state_changed = Signal(str)
    #: Emitted with the state name each time a looping animation wraps
    #: past its final frame back to frame 0.
    cycle_completed = Signal(str)

    INTERVALS = {
        "idle": 120,
        "thinking": 110,
        "greeting": 115,
    }

    def __init__(self, assets_dir: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.assets_dir = Path(assets_dir)
        self.frames: dict[str, list[QPixmap]] = {}
        self.state = "idle"
        self.index = 0
        #: Completed loops of the state currently playing. Reset on
        #: every state change; used by callers that want a pose to run
        #: at least one full cycle before being replaced.
        self.cycles = 0

        self.setFixedSize(160, 160)
        self.setScaledContents(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._load_frames()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._next_frame)

        self.set_pose("idle")

    def _load_frames(self) -> None:
        for state in ("idle", "thinking", "greeting"):
            frame_dir = self.assets_dir / "frames" / state
            files = sorted(frame_dir.glob("*.png"))
            pixmaps = [QPixmap(str(p)) for p in files if not QPixmap(str(p)).isNull()]
            if not pixmaps:
                raise FileNotFoundError(f"No frames found for state: {state}")
            self.frames[state] = pixmaps
        logging.getLogger(__name__).info(
            "Alfred frames loaded: %s",
            ", ".join(
                "{0}={1}".format(name, len(self.frames[name]))
                for name in ("idle", "thinking", "greeting")
            ),
        )

    def set_pose(self, state: str, restart: bool = False) -> None:
        """Play ``state`` from frame 0, re-arming the frame timer.

        A call naming the state already playing is ignored unless
        ``restart`` is set: re-asserting a pose must not drag the sprite
        back to frame 0, which is what made a running animation look
        like a still image.
        """
        if state not in self.frames:
            raise ValueError(f"Unsupported Alfred state: {state}")

        if state == self.state and self.timer.isActive() and not restart:
            return

        self.state = state
        self.index = 0
        self.cycles = 0
        self._show_current()
        self.state_changed.emit(state)

        self.timer.stop()
        self.timer.start(self.INTERVALS[state])

    def frame_counts(self) -> dict[str, int]:
        """Frames actually loaded per state, for runtime verification."""
        return {name: len(frames) for name, frames in self.frames.items()}

    def _show_current(self) -> None:
        self.setPixmap(self.frames[self.state][self.index])

    def _next_frame(self) -> None:
        self.index += 1

        if self.index >= len(self.frames[self.state]):
            if self.state == "greeting":
                # One-shot: hand back to the looping idle animation.
                self.set_pose("idle", restart=True)
                return
            self.index = 0
            self.cycles += 1
            self._show_current()
            self.cycle_completed.emit(self.state)
            return

        self._show_current()

    def idle(self) -> None:
        self.set_pose("idle")

    def thinking(self) -> None:
        self.set_pose("thinking")

    def greet(self) -> None:
        # Always from the top: a greeting is an acknowledgment of the
        # event that just happened, so a second one replays it.
        self.set_pose("greeting", restart=True)

    def stop(self) -> None:
        self.timer.stop()
