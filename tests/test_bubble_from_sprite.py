"""Tests for where the speech bubble lands (``ui.capture_popup``).

The popup's *content* is covered in ``test_capture_popup.py``. This file
is only about its placement: the bubble is meant to read as Alfred
speaking, which means it has to sit beside the drawn figure -- not beside
his window -- with the tail on the side that faces him and its tip level
with his mouth.

None of that is checkable by reading the code, because all four of the
mistakes it can make produce a perfectly valid window:

* anchoring to the companion's 160x160 box instead of the figure inside
  it, which leaves the tail pointing at fifty pixels of transparent
  canvas
* keeping the tail on the bubble's left after the bubble has been
  flipped to Alfred's left near a screen edge, so it points away from him
* hanging the bubble below his feet rather than level with his mouth,
  which makes the tail too long to read as a tail
* letting the answer-time resize move the tail's tip off him

So the numbers are asserted against the three measured sprite offsets
rather than restated: ``_SPRITE_BODY_LEFT`` / ``_SPRITE_BODY_RIGHT`` are
the alpha bounding box of the frame art and ``_SPRITE_MOUTH_DY`` is the
moustache row within the head.

**These tests read the real screen's geometry.** They must not run under
``QT_QPA_PLATFORM=offscreen``: the offscreen platform reports a fixed
800x800 screen, which is narrow enough that the flip-to-the-other-side
branch fires for anchors that would have room on a real display, and the
clamp assertions then measure the wrong edge.

Placement is asserted through :meth:`CapturePopup._move_near` where the
final position is the subject, because ``open_capture`` starts a short
emergence animation and ``pos()`` during it is the *start* offset. The
animation itself is tested separately, by fast-forwarding it.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication

from core.memory import create_memory, get_memory
from ui.assistant_controller import AssistantAnswer
from ui.capture_popup import (
    STATE_ANSWER,
    STATE_INPUT,
    CapturePopup,
    _ANSWER_BASE_SIZE,
    _EMERGE_TRAVEL,
    _INPUT_SIZE,
    _SPRITE_BODY_LEFT,
    _SPRITE_BODY_RIGHT,
    _SPRITE_MOUTH_DY,
    _TAIL_W,
)

#: Matches the ``gap`` in ``_move_near``: the tail supplies most of the
#: separation, so this is only the hairline between tail tip and figure.
GAP = 6


# --- helpers ---------------------------------------------------------------


@pytest.fixture
def popup(conn, qapp):
    """A popup whose lookups run inline, on the real primary screen."""
    p = CapturePopup(conn, threaded=False)
    yield p
    p.close()
    p.deleteLater()


@pytest.fixture
def screen(qapp):
    geo = QGuiApplication.primaryScreen().availableGeometry()
    if geo.width() < 1024:
        pytest.skip(
            "placement is measured against a real screen; this looks like "
            "the offscreen platform's fixed 800x800"
        )
    return geo


def _settle(popup: CapturePopup) -> None:
    """Run the emergence animation to its end without waiting for it."""
    assert popup._emerge is not None, "no emergence animation was started"
    popup._emerge.setCurrentTime(10 ** 6)


# --- anchoring to the figure, not to the window ----------------------------


def test_open_capture_anchors_to_the_sprite(popup, screen):
    """The hotkey path anchors to Alfred too.

    ``open_capture`` is what the global hotkey, the tray menu and the
    toolbar button all call. It used to centre the bubble on the screen
    while Alfred stood somewhere else entirely, tail pointing at nobody;
    the anchor provider is asked fresh on every open so a dragged
    companion is never stale.
    """
    anchor = QPoint(screen.left() + 300, screen.top() + 300)
    asked: list[int] = []

    def _provider() -> QPoint:
        asked.append(1)
        return anchor

    popup.set_anchor_provider(_provider)
    popup.open_capture()

    assert asked, "open_capture did not ask where the sprite was"
    assert popup.state() == STATE_INPUT
    assert popup.isVisible()
    assert popup._tail_side == "left"

    _settle(popup)
    assert popup.pos().x() == anchor.x() + _SPRITE_BODY_RIGHT + GAP
    assert popup.pos().y() == anchor.y() + _SPRITE_MOUTH_DY - popup.height() // 2


def test_the_bubble_clears_the_drawn_figure_not_the_window(popup, screen):
    """The gap is measured from the figure's own right edge.

    Anchoring to the companion window instead would put the bubble
    ``DISPLAY_SIZE - _SPRITE_BODY_RIGHT`` pixels further out -- far
    enough that the tail spans empty canvas and the bubble reads as a
    panel that happens to be nearby.
    """
    anchor = QPoint(screen.left() + 400, screen.top() + 400)
    popup._move_near(anchor)
    assert popup.pos().x() == anchor.x() + _SPRITE_BODY_RIGHT + GAP
    # Well inside the 160px companion box, which is the whole point.
    assert popup.pos().x() - anchor.x() < 160


def test_the_tail_tip_is_level_with_his_mouth(popup, screen):
    """Vertically the bubble is centred on the moustache row, so the
    tail is short. ``_tail_tip_y`` is stored in *window* coordinates,
    and it has to resolve back to the mouth in globals."""
    anchor = QPoint(screen.left() + 300, screen.top() + 300)
    popup._move_near(anchor)
    assert popup.pos().y() + popup._tail_tip_y == anchor.y() + _SPRITE_MOUTH_DY
    # Centred, not hung below his feet.
    assert popup._tail_tip_y == pytest.approx(popup.height() // 2, abs=1)


def test_the_bubble_flips_and_takes_its_tail_with_it(popup, screen):
    """Cornered on the right, Alfred speaks out of his other side.

    Both halves have to move together: the bubble goes to his left *and*
    the tail moves to the bubble's right. A flip that forgot the tail
    would leave it pointing at whatever is on the far side of the
    bubble.
    """
    anchor = QPoint(screen.right() - 100, screen.top() + 300)
    popup._move_near(anchor)

    assert popup._tail_side == "right"
    assert popup.pos().x() == anchor.x() + _SPRITE_BODY_LEFT - GAP - popup.width()
    assert popup.pos().x() + popup.width() <= anchor.x() + _SPRITE_BODY_LEFT


def test_the_tail_gets_its_width_from_the_margin_on_its_own_side(popup, screen):
    """The tail is painted into the window margin, so the layout has to
    reserve that width on whichever side it sticks out of -- otherwise
    the triangle is drawn over the bubble's own rounded border."""
    popup._move_near(QPoint(screen.left() + 300, screen.top() + 300))
    margins = popup._outer_layout.contentsMargins()
    assert popup._tail_side == "left"
    assert margins.left() == 6 + _TAIL_W
    assert margins.right() == 6

    popup._move_near(QPoint(screen.right() - 100, screen.top() + 300))
    margins = popup._outer_layout.contentsMargins()
    assert popup._tail_side == "right"
    assert margins.right() == 6 + _TAIL_W
    assert margins.left() == 6


def test_the_window_is_wide_enough_for_the_bubble_plus_its_tail(popup):
    """The fixed width covers the bubble *and* the reserved tail column,
    so the text width is the same on either side of a flip."""
    assert popup.width() == _INPUT_SIZE.width() + _TAIL_W


# --- staying on screen -----------------------------------------------------


def test_a_sprite_at_the_top_of_the_screen_does_not_push_the_bubble_off(
    popup, screen
):
    """Centring on the mouth would put half the bubble above the screen
    when Alfred is parked at the top edge, so the vertical placement is
    clamped. The tail follows the clamp rather than the mouth."""
    popup._move_near(QPoint(screen.left() + 300, screen.top()))
    assert popup.pos().y() == screen.top()
    assert popup._tail_tip_y == _SPRITE_MOUTH_DY


def test_a_sprite_at_the_bottom_of_the_screen_does_not_push_the_bubble_off(
    popup, screen
):
    popup._move_near(QPoint(screen.left() + 300, screen.bottom() - 5))
    assert popup.pos().y() + popup.height() <= screen.bottom() + 1
    assert popup.pos().y() >= screen.top()


def test_an_anchor_on_no_screen_at_all_falls_back_to_centred_and_tailless(
    popup, screen
):
    """A stale companion position from a monitor that has since been
    unplugged. ``screenAt`` returns ``None``; the bubble goes to the
    middle of the current screen and hides its tail, because there is
    nothing on screen for it to point at.
    """
    popup.set_anchor_provider(lambda: QPoint(-100_000, -100_000))
    popup.open_capture()

    assert popup._tail_side == "none"
    margins = popup._outer_layout.contentsMargins()
    assert (margins.left(), margins.right()) == (6, 6)
    assert popup.pos().x() == screen.x() + (screen.width() - popup.width()) // 2


def test_with_no_companion_the_bubble_centres_itself_and_hides_the_tail(
    popup, screen
):
    """Headless runs, and the case where the sprite is switched off. No
    provider is registered, so there is no anchor and the popup keeps the
    screen-centred placement it has always had."""
    assert popup._current_anchor() is None
    popup.open_capture()
    assert popup._tail_side == "none"
    assert popup.pos().x() == screen.x() + (screen.width() - popup.width()) // 2


@pytest.mark.parametrize(
    "provider",
    [
        lambda: (5, 5),          # a tuple, not a QPoint
        lambda: None,
        lambda: 1 / 0,           # a provider whose widget has gone away
    ],
)
def test_a_provider_that_misbehaves_is_treated_as_no_sprite(popup, provider):
    """The provider reaches into a live widget, so it can raise during
    shutdown. A missing sprite must never be fatal to the popup -- the
    bubble simply centres itself."""
    popup.set_anchor_provider(provider)
    assert popup._current_anchor() is None
    popup.open_capture()
    assert popup._tail_side == "none"
    assert popup.isVisible()


# --- emerging out of him ---------------------------------------------------


def test_the_bubble_is_moved_before_it_is_shown(popup, screen):
    """Order matters: the window is placed at its final geometry first,
    then animated. Showing first would flash the bubble wherever Qt
    happened to put it, and the answer-time resize would be fighting a
    running animation for the same geometry.
    """
    anchor = QPoint(screen.left() + 300, screen.top() + 300)
    popup.set_anchor_provider(lambda: anchor)
    popup.show_near(anchor)

    slide = popup._emerge.animationAt(0)
    assert slide.endValue().x() == anchor.x() + _SPRITE_BODY_RIGHT + GAP
    _settle(popup)
    assert popup.pos() == slide.endValue()


def test_the_bubble_travels_out_of_him_rather_than_towards_him(popup, screen):
    """The emergence starts offset back *at* the sprite and travels to
    the final position, which is what reads as being spoken. On his left
    the offset is mirrored, so it still starts from his direction."""
    right_of = QPoint(screen.left() + 300, screen.top() + 300)
    popup.set_anchor_provider(lambda: right_of)
    popup.open_capture()
    slide = popup._emerge.animationAt(0)
    assert popup._tail_side == "left"
    assert slide.startValue().x() == slide.endValue().x() - _EMERGE_TRAVEL

    left_of = QPoint(screen.right() - 100, screen.top() + 300)
    popup.set_anchor_provider(lambda: left_of)
    popup.open_capture()
    slide = popup._emerge.animationAt(0)
    assert popup._tail_side == "right"
    assert slide.startValue().x() == slide.endValue().x() + _EMERGE_TRAVEL


def test_a_tailless_bubble_does_not_slide_sideways(popup, screen):
    """With no sprite there is no direction to come out of, so only the
    fade is left -- a horizontal slide from nowhere would just look like
    a glitch."""
    popup.set_anchor_provider(lambda: QPoint(-100_000, -100_000))
    popup.open_capture()
    slide = popup._emerge.animationAt(0)
    assert popup._tail_side == "none"
    assert slide.startValue().x() == slide.endValue().x()


def test_the_emergence_always_ends_opaque_and_in_place(popup, screen):
    """The fade starts from fully transparent, so an interrupted
    animation could leave the bubble invisible on screen with no way for
    the user to tell it is there. The finish handler pins both."""
    anchor = QPoint(screen.left() + 300, screen.top() + 300)
    popup.set_anchor_provider(lambda: anchor)
    popup.open_capture()

    fade = popup._emerge.animationAt(1)
    assert (fade.startValue(), fade.endValue()) == (0.0, 1.0)

    _settle(popup)
    assert popup.windowOpacity() == 1.0
    assert popup.pos() == popup._emerge.animationAt(0).endValue()


def test_re_opening_replaces_the_running_emergence(popup, screen):
    """Two opens in quick succession -- the hotkey pressed twice. The
    first animation is stopped and dropped rather than left running
    against the second one's geometry."""
    anchor = QPoint(screen.left() + 300, screen.top() + 300)
    popup.set_anchor_provider(lambda: anchor)
    popup.open_capture()
    first = popup._emerge
    popup.open_capture()
    assert popup._emerge is not first
    _settle(popup)
    assert popup.pos() == popup._emerge.animationAt(0).endValue()


# --- the tail survives the answer -----------------------------------------


def test_the_tail_keeps_pointing_at_his_mouth_after_an_answer_lands(
    conn, popup, screen
):
    """The bubble grows when the answer arrives, and it grows downward
    from a fixed top-left -- so the tail's tip, recorded in window
    coordinates, still resolves to the same point on him. Recomputing it
    from the new height instead would slide it down his chest.
    """
    anchor = QPoint(screen.left() + 300, screen.top() + 300)
    popup.set_anchor_provider(lambda: anchor)
    popup.open_capture()
    _settle(popup)
    before = popup.pos().y() + popup._tail_tip_y
    assert before == anchor.y() + _SPRITE_MOUTH_DY

    popup._on_controller_answer(
        AssistantAnswer(text="1 task left, sir: Sensor wiring.", weight="substantial")
    )
    assert popup.state() == STATE_ANSWER
    assert popup.height() > _INPUT_SIZE.height(), "the bubble did not grow"
    assert popup.pos().y() + popup._tail_tip_y == before


def test_a_long_answer_with_results_is_capped_rather_than_growing_forever(
    conn, popup, screen
):
    """A wall of text plus five result rows would otherwise push the
    bubble off the bottom of the screen. The height is clamped to the
    ANSWER ceiling, and the tail is unaffected because the window's
    top-left has not moved.
    """
    ids = [create_memory(conn, f"Note {i}", "body " * 60, "note") for i in range(5)]
    results = [get_memory(conn, i) for i in ids]

    anchor = QPoint(screen.left() + 300, screen.top() + 300)
    popup.set_anchor_provider(lambda: anchor)
    popup.open_capture()
    _settle(popup)
    top_before, tip_before = popup.pos().y(), popup._tail_tip_y

    popup._on_controller_answer(
        AssistantAnswer(
            text="A very long reply. " * 40, results=results, weight="substantial"
        )
    )
    assert popup.height() == _ANSWER_BASE_SIZE.height()
    assert popup.width() == _INPUT_SIZE.width() + _TAIL_W, "the width moved"
    assert len(popup.answer_results()) == 5
    assert (popup.pos().y(), popup._tail_tip_y) == (top_before, tip_before)


def test_re_opening_after_an_answer_shrinks_back_and_re_anchors(
    conn, popup, screen
):
    """A new round starts from the compact input footprint, and is placed
    again from scratch -- the previous round's taller geometry must not
    decide where this one's tail points."""
    anchor = QPoint(screen.left() + 300, screen.top() + 300)
    popup.set_anchor_provider(lambda: anchor)
    popup.open_capture()
    compact = popup.height()
    results = [
        get_memory(conn, create_memory(conn, f"Note {i}", "body " * 60, "note"))
        for i in range(5)
    ]
    popup._on_controller_answer(
        AssistantAnswer(
            text="A very long reply. " * 40, results=results, weight="substantial"
        )
    )
    with_answer = popup.height()
    assert with_answer > compact

    moved = QPoint(screen.left() + 700, screen.top() + 500)
    popup.set_anchor_provider(lambda: moved)
    popup.open_capture()
    _settle(popup)

    assert popup.state() == STATE_INPUT
    assert not popup.answer_frame.isVisible()
    # Shrunk back. Not asserted against ``_INPUT_SIZE`` exactly: once the
    # window has been realized Qt will not shrink it below the layout's
    # own minimum, and that minimum is a little larger than it was before
    # the answer panel had ever been populated.
    assert popup.height() < with_answer, "the taller answer footprint stuck"
    assert popup.pos().x() == moved.x() + _SPRITE_BODY_RIGHT + GAP
    assert popup.pos().y() + popup._tail_tip_y == moved.y() + _SPRITE_MOUTH_DY
