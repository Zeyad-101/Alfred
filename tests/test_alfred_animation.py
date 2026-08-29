"""Tests for the sprite engine (``ui.alfred_pet``) and the pose lock
that :mod:`ui.desktop_companion` wraps around it.

``AlfredPet`` is vendored artwork plus a frame timer, so what is worth
testing is not the drawing but the two contracts the host app depends
on and the upstream demo never exercised:

* ``set_pose`` is *idempotent*. The app re-asserts the current pose on
  ordinary events (a keystroke in the search box re-asserts idle), and
  the upstream version restarted from frame 0 every time, which pinned
  the sprite to a single frame and made a running animation look like a
  still image. Switching between states still resets; re-asserting does
  not.
* ``cycle_completed`` fires when a looping state wraps, which is the
  animation's own clock. The companion's deferred pose release rides on
  it so a pose is never cut off mid-sway.

The pose lock on top (``force_pose`` / ``release_pose``) exists because
a lookup finishing in single-digit milliseconds would otherwise show
three frames of a twelve-frame sway -- motion too slight to read as
motion. These tests drive the frame timer by hand
(``_next_frame``) rather than waiting on real milliseconds, so they
assert the state machine rather than the wall clock.

Frame counts and intervals are cross-checked against
``assets/alfred/manifest.json`` instead of being retyped, so
regenerating the artwork with a different frame count fails here rather
than silently shortening an animation.
"""
from __future__ import annotations

import json

import pytest

from core.paths import asset_path
from ui.alfred_pet import AlfredPet
from ui.desktop_companion import (
    ASSET_SUBDIR,
    STATE_GREETING,
    STATE_IDLE,
    STATE_THINKING,
    DesktopCompanion,
)
from core.settings import DEFAULT_SETTINGS


# --- helpers ---------------------------------------------------------------


def _assets_dir():
    """The same directory the companion hands its pet."""
    return asset_path(ASSET_SUBDIR)


def _manifest() -> dict:
    with (_assets_dir() / "manifest.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _pet() -> AlfredPet:
    """A parentless pet. Never shown, so no real paint is needed."""
    return AlfredPet(_assets_dir())


def _companion() -> DesktopCompanion:
    settings = dict(DEFAULT_SETTINGS)
    settings["companion_position"] = [80, 80]
    return DesktopCompanion(settings=settings)


def _run_one_cycle(pet: AlfredPet, state: str) -> None:
    """Advance exactly to the wrap of ``state``'s frame sequence."""
    for _ in range(len(pet.frames[state])):
        pet._next_frame()


# --- construction ----------------------------------------------------------


def test_pet_loads_all_three_states_and_nothing_else(qapp):
    """Exactly the three supported poses load. A fourth directory
    appearing under ``frames/`` would mean art was added for a state the
    engine has no timing for."""
    pet = _pet()
    try:
        assert set(pet.frames) == {STATE_IDLE, STATE_THINKING, STATE_GREETING}
    finally:
        pet.stop()
        pet.deleteLater()


def test_pet_frame_counts_match_the_manifest(qapp):
    """``frame_counts()`` is the runtime proof the sequences are intact.

    The manifest is the artwork's own record of how many frames each
    pose has; if the two disagree, either frames went missing from the
    package or the generator wrote a different count than it drew.
    """
    pet = _pet()
    try:
        expected = {
            name: spec["frames"]
            for name, spec in _manifest()["states"].items()
        }
        assert pet.frame_counts() == expected
        # And the counts the whole design is built around.
        assert expected == {"idle": 8, "thinking": 12, "greeting": 8}
    finally:
        pet.stop()
        pet.deleteLater()


def test_pet_intervals_match_the_manifest(qapp):
    """Frame timing is part of the character, not a tuning knob -- the
    thinking sway is deliberately faster than the idle breath."""
    manifest_intervals = {
        name: spec["interval_ms"]
        for name, spec in _manifest()["states"].items()
    }
    assert AlfredPet.INTERVALS == manifest_intervals


def test_pet_frames_are_loaded_pixmaps_at_the_manifest_size(qapp):
    """A missing PNG loads as a null QPixmap, which paints as nothing at
    all rather than raising -- so the sprite would simply vanish."""
    pet = _pet()
    try:
        width, height = _manifest()["size"]
        for state, pixmaps in pet.frames.items():
            for i, pix in enumerate(pixmaps):
                assert not pix.isNull(), f"null pixmap {state}[{i}]"
                assert pix.width() == width
                assert pix.height() == height
    finally:
        pet.stop()
        pet.deleteLater()


def test_pet_starts_idle_with_a_running_timer(qapp):
    """Construction ends in ``set_pose("idle")``, so a pet is animating
    from the moment it exists -- no separate "start" call."""
    pet = _pet()
    try:
        assert pet.state == STATE_IDLE
        assert pet.index == 0
        assert pet.cycles == 0
        assert pet.timer.isActive()
        assert pet.timer.interval() == AlfredPet.INTERVALS[STATE_IDLE]
        assert pet.pixmap() is not None and not pet.pixmap().isNull()
    finally:
        pet.stop()
        pet.deleteLater()


def test_pet_rejects_a_state_it_has_no_art_for(qapp):
    """No walking, sleeping or jumping. An unknown pose is a programming
    error and says so, rather than silently holding the current frame."""
    pet = _pet()
    try:
        with pytest.raises(ValueError):
            pet.set_pose("walking")
        for absent in ("sleeping", "jumping", "running", "", "IDLE"):
            with pytest.raises(ValueError):
                pet.set_pose(absent)
        # ...and the rejected call changed nothing.
        assert pet.state == STATE_IDLE
    finally:
        pet.stop()
        pet.deleteLater()


def test_pet_raises_when_a_state_directory_has_no_frames(qapp, tmp_path):
    """Loading is strict: an empty (or wrong) assets directory fails at
    construction rather than producing a pet with an empty frame list
    that would raise later, from inside a timer callback."""
    (tmp_path / "frames" / "idle").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        AlfredPet(tmp_path)


# --- set_pose: idempotency and restart -------------------------------------


def test_re_asserting_the_running_pose_is_a_no_op(qapp):
    """The central fix over the vendored engine.

    Mid-animation, asking for the pose already playing must not touch
    the frame index, the cycle count, or emit ``state_changed``. The
    search box re-asserts idle on every keystroke; without this the
    sprite sat on frame 0 forever.
    """
    pet = _pet()
    try:
        seen: list[str] = []
        pet.state_changed.connect(seen.append)

        pet._next_frame()
        pet._next_frame()
        assert pet.index == 2

        pet.set_pose(STATE_IDLE)
        assert pet.index == 2, "re-assertion dragged the sprite back to frame 0"
        assert seen == []
        pet.idle()  # the public wrapper takes the same path
        assert pet.index == 2
        assert seen == []
    finally:
        pet.stop()
        pet.deleteLater()


def test_switching_pose_resets_the_frame_and_announces_it(qapp):
    """A genuine change starts the new sequence from its first frame and
    re-arms the timer at that state's interval."""
    pet = _pet()
    try:
        seen: list[str] = []
        pet.state_changed.connect(seen.append)

        pet._next_frame()
        assert pet.index == 1

        pet.set_pose(STATE_THINKING)
        assert pet.state == STATE_THINKING
        assert pet.index == 0
        assert pet.cycles == 0
        assert seen == [STATE_THINKING]
        assert pet.timer.interval() == AlfredPet.INTERVALS[STATE_THINKING]
    finally:
        pet.stop()
        pet.deleteLater()


def test_restart_replays_the_pose_already_running(qapp):
    """``restart=True`` is the escape hatch idempotency needs: a
    greeting acknowledges the event that just happened, so a second
    greeting must replay rather than be swallowed."""
    pet = _pet()
    try:
        seen: list[str] = []
        pet.state_changed.connect(seen.append)

        pet.greet()
        assert pet.state == STATE_GREETING
        pet._next_frame()
        pet._next_frame()
        assert pet.index == 2

        pet.greet()  # greet() always restarts
        assert pet.index == 0
        assert seen == [STATE_GREETING, STATE_GREETING]
    finally:
        pet.stop()
        pet.deleteLater()


def test_re_asserting_a_pose_whose_timer_was_stopped_revives_it(qapp):
    """Idempotency is conditional on the timer actually running.

    ``stop()`` leaves the state name in place but kills the animation,
    so re-asserting that same state has to start it again -- otherwise a
    stopped pet could never be revived without a state change.
    """
    pet = _pet()
    try:
        pet._next_frame()
        pet.stop()
        assert not pet.timer.isActive()

        pet.set_pose(STATE_IDLE)
        assert pet.timer.isActive()
        assert pet.index == 0
    finally:
        pet.stop()
        pet.deleteLater()


# --- looping and the one-shot ---------------------------------------------


def test_looping_states_wrap_and_report_each_completed_cycle(qapp):
    """Idle and thinking are loops: past the last frame they return to
    frame 0, bump ``cycles``, and announce the wrap. The announcement is
    what the companion's deferred release waits on.
    """
    pet = _pet()
    try:
        for state in (STATE_IDLE, STATE_THINKING):
            pet.set_pose(state, restart=True)
            wraps: list[str] = []
            pet.cycle_completed.connect(wraps.append)

            _run_one_cycle(pet, state)
            assert pet.state == state, "a looping state changed itself"
            assert pet.index == 0
            assert pet.cycles == 1
            assert wraps == [state]

            _run_one_cycle(pet, state)
            assert pet.cycles == 2
            assert wraps == [state, state]

            pet.cycle_completed.disconnect()
    finally:
        pet.stop()
        pet.deleteLater()


def test_greeting_is_one_shot_and_hands_back_to_idle(qapp):
    """The greeting ends by switching itself to idle, and does *not*
    report a completed cycle -- it never wraps, so a caller waiting on
    ``cycle_completed`` for a greeting would wait forever. That is
    exactly why the pose lock treats greeting as non-deferrable.
    """
    pet = _pet()
    try:
        wraps: list[str] = []
        pet.cycle_completed.connect(wraps.append)
        seen: list[str] = []
        pet.state_changed.connect(seen.append)

        pet.greet()
        assert pet.state == STATE_GREETING

        _run_one_cycle(pet, STATE_GREETING)
        assert pet.state == STATE_IDLE
        assert pet.index == 0
        assert pet.timer.isActive()
        assert pet.timer.interval() == AlfredPet.INTERVALS[STATE_IDLE]
        assert wraps == [], "greeting reported a cycle it never completed"
        assert seen == [STATE_GREETING, STATE_IDLE]

        # And once handed back it loops like any other idle.
        _run_one_cycle(pet, STATE_IDLE)
        assert pet.state == STATE_IDLE
        assert wraps == [STATE_IDLE]
    finally:
        pet.stop()
        pet.deleteLater()


def test_the_displayed_pixmap_follows_the_frame_index(qapp):
    """The frame counter is not bookkeeping -- every advance actually
    swaps the pixmap on the label."""
    pet = _pet()
    try:
        pet.set_pose(STATE_THINKING, restart=True)
        first = pet.pixmap().cacheKey()
        pet._next_frame()
        assert pet.index == 1
        assert pet.pixmap().cacheKey() != first
        assert pet.pixmap().cacheKey() == (
            pet.frames[STATE_THINKING][1].cacheKey()
        )
    finally:
        pet.stop()
        pet.deleteLater()


def test_stop_halts_the_animation_without_forgetting_the_pose(qapp):
    pet = _pet()
    try:
        pet.set_pose(STATE_THINKING)
        pet.stop()
        assert not pet.timer.isActive()
        assert pet.state == STATE_THINKING
    finally:
        pet.deleteLater()


# --- the companion's pose lock -------------------------------------------


def test_force_pose_holds_against_the_ordinary_play_calls(qapp):
    """While a pose is forced, the three ``play_*`` methods are no-ops.

    This is the whole point of the lock: the thinking pose has to stay
    put for as long as a lookup is running, and some other hook -- the
    search debounce, a focus change -- would otherwise cut it short.
    """
    c = _companion()
    try:
        c.force_pose(STATE_THINKING)
        assert c.forced_pose() == STATE_THINKING
        assert c.current_state() == STATE_THINKING

        c.play_idle()
        c.play_greeting()
        assert c.current_state() == STATE_THINKING, "the lock let go"
        assert c.forced_pose() == STATE_THINKING
    finally:
        c.shutdown()
        c.deleteLater()


def test_force_pose_rejects_a_pose_that_does_not_exist(qapp):
    """The lock adds no new art, so it accepts only the three poses that
    already ship -- matching ``AlfredPet.set_pose``'s own rejection."""
    c = _companion()
    try:
        with pytest.raises(ValueError):
            c.force_pose("dancing")
        assert c.forced_pose() is None
    finally:
        c.shutdown()
        c.deleteLater()


def test_re_forcing_the_held_pose_does_not_restart_it(qapp):
    """A caller that asserts "thinking" more than once per request must
    not keep the sprite on frame 0 -- the lock inherits ``set_pose``'s
    idempotency rather than working around it."""
    c = _companion()
    try:
        c.force_pose(STATE_THINKING)
        c._pet._next_frame()
        c._pet._next_frame()
        assert c._pet.index == 2

        c.force_pose(STATE_THINKING)
        assert c._pet.index == 2
    finally:
        c.shutdown()
        c.deleteLater()


def test_release_waits_for_the_loop_to_wrap_before_going_idle(qapp):
    """A lookup that resolves in milliseconds would show four frames of
    a twelve-frame sway. So a release arriving before the pose has
    completed one cycle is queued on ``cycle_completed`` -- the
    animation's own clock, not a second timer.

    Nothing about the answer waits on this; it is already on screen.
    """
    c = _companion()
    try:
        c.force_pose(STATE_THINKING)
        assert c._pet.cycles == 0

        c.release_pose()
        assert c.release_pending() is True
        assert c.current_state() == STATE_THINKING, "release cut the sway short"
        assert c.forced_pose() == STATE_THINKING

        _run_one_cycle(c._pet, STATE_THINKING)
        assert c.release_pending() is False
        assert c.forced_pose() is None
        assert c.current_state() == STATE_IDLE
    finally:
        c.shutdown()
        c.deleteLater()


def test_release_after_a_full_cycle_returns_to_idle_at_once(qapp):
    """The wait is only for the *first* cycle. A pose the user has
    already watched for a full sway needs no grace period."""
    c = _companion()
    try:
        c.force_pose(STATE_THINKING)
        _run_one_cycle(c._pet, STATE_THINKING)
        assert c._pet.cycles == 1

        c.release_pose()
        assert c.release_pending() is False
        assert c.forced_pose() is None
        assert c.current_state() == STATE_IDLE
    finally:
        c.shutdown()
        c.deleteLater()


def test_releasing_a_greeting_does_not_queue_behind_it(qapp):
    """Greeting never reports a cycle -- it switches itself to idle --
    so a release queued against it would never complete."""
    c = _companion()
    try:
        c.force_pose(STATE_GREETING)
        assert c.current_state() == STATE_GREETING

        c.release_pose()
        assert c.release_pending() is False
        assert c.forced_pose() is None
        assert c.current_state() == STATE_IDLE
    finally:
        c.shutdown()
        c.deleteLater()


def test_release_without_a_lock_is_harmless(qapp):
    """Callers pair force/release across signal handlers, so an unpaired
    release does happen; it must not drag the sprite anywhere."""
    c = _companion()
    try:
        assert c.forced_pose() is None
        c.play_thinking()
        c.release_pose()
        assert c.forced_pose() is None
        assert c.release_pending() is False
        # Untouched -- a stray release is not a disguised play_idle.
        assert c.current_state() == STATE_THINKING
    finally:
        c.shutdown()
        c.deleteLater()


def test_a_queued_release_never_outlives_the_pose_it_waited_on(qapp):
    """If the held pose is replaced while a release is queued, the queue
    is dropped rather than left waiting on a cycle that will never be
    reported. Without this guard the lock could stay set forever, and
    every later ``play_idle`` would be silently ignored.
    """
    c = _companion()
    try:
        c.force_pose(STATE_THINKING)
        c.release_pose()
        assert c.release_pending() is True

        # The pose changes out from under the queued release.
        c._pet.set_pose(STATE_IDLE)
        assert c.release_pending() is False
        assert c.forced_pose() is None

        # ...and the companion takes ordinary direction again.
        c.play_thinking()
        assert c.current_state() == STATE_THINKING
    finally:
        c.shutdown()
        c.deleteLater()


def test_companion_frame_counts_are_the_pets(qapp):
    """``frame_counts()`` is forwarded so ``main.py`` can prove the
    sequences loaded without reaching into the private widget."""
    c = _companion()
    try:
        assert c.frame_counts() == c._pet.frame_counts()
        assert c.frame_counts() == {"idle": 8, "thinking": 12, "greeting": 8}
    finally:
        c.shutdown()
        c.deleteLater()
