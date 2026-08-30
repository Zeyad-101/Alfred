"""Quick-capture popup -- a tiny frameless dialog for grabbing a thought
or asking a quick question.

The popup is summoned from anywhere: a global hotkey (main.py), a
tray menu item, the "+ Inbox" toolbar button, or a single-click on
the floating desktop companion. It is intentionally minimal -- one
line of text, Enter to commit, Escape to dismiss -- so the user can
file a thought in well under a second and get back to whatever
they were doing.

Ask or remember
---------------
The popup operates in two visual states:

* ``INPUT`` -- the original single-line entry. The placeholder
  has been widened to hint that the same field accepts both
  "remember that ..." captures and "what is ...?" questions.
* ``ANSWER`` -- entered automatically when the submitted text
  classifies as a question. The input clears and stays
  focused; a results panel grows below it listing up to
  three matching memories, each row clickable. Clicking a
  row emits :attr:`memory_opened` with the row's id so the
  main window can show the memory in the editor.

The popup never auto-closes on the answer path -- the user
keeps the same popup, can ask a follow-up or start a new
"remember that ..." capture in the same focus, and only
dismisses explicitly with Escape.

Butler interaction layer
------------------------
Submissions are no longer routed straight to ``core.assistant.ask``;
they go to :class:`ui.assistant_controller.AssistantController`, which
recognizes a curated set of question shapes and answers them from real
data with hand-written templates. The ANSWER state has become a compact
speech bubble: one sentence from Alfred at the top, the (optional)
matching entries below it, anchored beside the companion.

The bubble shows "Lemme check, sir." only when a *substantial* lookup is
still running after 300ms -- never for a capture, a view switch, a stored
single fact, or a follow-up, however long those happen to take.

Speaking out of the sprite
--------------------------
The bubble is anchored to the companion on *every* path (hotkey included,
not just a click on the mascot), carries a painted tail pointing back at
him, and grows out of him instead of appearing at full size. It is also
no longer modal: a speech bubble that freezes the app behind it is a
dialog in costume.

Two separate cues drive the sprite and the words, and they are
deliberately not the same signal. ``pose_thinking_started`` fires on
*every* submission -- being asked something is what makes him think --
while ``thinking_started`` stays substantial-only, so "Lemme check, sir."
keeps meaning there is genuinely something to check.

It is ``QDialog`` rather than a custom QWidget so it gets
the modal-loop, focus, and Escape-handling behavior for free,
and so the OS treats it as a real top-level window
(frameless dialogs still show in the task bar / alt-tab list,
which is what we want for a "summonable from anywhere" tool).
"""
from __future__ import annotations

import sqlite3

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.memory import Memory
from ui.assistant_controller import (
    AssistantAnswer,
    AssistantController,
    SessionContext,
)
from ui.strings import (
    ASSISTANT_ACTION_CLOSE_MS,
    ASSISTANT_ANSWER_AUTO_CLOSE_MS,
    ASSISTANT_THINKING,
    CAPTURE_ANSWER_HEADER_PREFIX,
    CAPTURE_ANSWER_RESULT_OPEN_TOOLTIP,
    CAPTURE_ANSWER_RESULT_SNIPPET_FALLBACK,
    CAPTURE_CONFIRMATION,
    CAPTURE_CONFIRMATION_MS,
    CAPTURE_PLACEHOLDER,
    CAPTURE_WINDOW_TITLE,
)
from ui.theme import _BG_PANEL, _BORDER_STRONG


# Visual states the popup can be in. Exposed as module-level
# constants so tests and call sites can refer to them by name
# without restating the string literal.
STATE_INPUT = "input"
STATE_ANSWER = "answer"

# Compact footprint when only the input line is visible.
_INPUT_SIZE = QSize(440, 96)
# Slightly taller when showing an answer; the actual height is
# also re-tightened per-content in ``_recompute_answer_size``.
_ANSWER_BASE_SIZE = QSize(440, 320)

# The tail that makes the bubble read as *his* speech rather than a
# floating panel. It is drawn into the window's own margin, on whichever
# side faces the sprite, so the bubble's rounded rect is untouched.
_TAIL_W = 16
_TAIL_H = 22
# How far the bubble travels while emerging, and for how long. Short
# enough to feel like a pop rather than a slide-in animation.
_EMERGE_MS = 170
_EMERGE_TRAVEL = 26

# Where the sprite actually *is* inside its window. The companion's box is
# ``DesktopCompanion.DISPLAY_SIZE`` square but the drawn figure only
# occupies part of it, so anchoring to the box edge would leave the tail pointing at fifty
# pixels of empty transparent canvas. These three numbers are measured
# from the frame art (the alpha bounding box across every pose, and the
# moustache row within the head) -- they are what makes the bubble look
# attached to him rather than merely near him.
_SPRITE_BODY_LEFT = 53
_SPRITE_BODY_RIGHT = 114
_SPRITE_MOUTH_DY = 42

# Bubble fill / border. A stylesheet cannot paint the tail -- the triangle
# has to come from ``paintEvent`` -- so these are the two ``#bubbleFrame``
# colours reached for directly instead of copied: the first version pasted
# the hex literals here, which meant a repaint of the theme silently left
# the tail a different colour from the bubble it hangs off.
_BUBBLE_FILL = _BG_PANEL
_BUBBLE_LINE = _BORDER_STRONG

# Snippet length for the answer-result list rows. Chosen to be
# long enough to convey what the entry is about but short
# enough that a single row never overflows the popup width.
_SNIPPET_MAX_CHARS = 80


class CapturePopup(QDialog):
    """A minimal frameless capture / ask dialog.

    Signals
    -------
    ``captured(memory_id)``
        Emitted when a capture (remember) is committed, with
        the new inbox row id. Lets the main window refresh
        the inbox view without the popup knowing about
        views at all.
    ``memory_opened(memory_id)``
        Emitted when the user clicks a result row in the
        answer panel. Lets the main window switch to the
        memory in the editor. The popup closes itself
        after the click -- the user is clearly done
        browsing this question once they've picked an
        entry to open.

    Behavior
    --------
    * ``returnPressed`` (Enter) on the line edit submits.
      Empty / whitespace text dismisses without creating.
      Non-empty text is handed to ``core.assistant.ask``,
      which classifies it as a capture or a question.
    * Capture path: file the note, show a
      brief confirmation label, then auto-close.
    * Question path: transition to ANSWER state. The input
      clears and stays focused; up to three matching
      memories are listed below; clicking a row opens it.
      The popup does NOT auto-close; the user can keep
      asking or start a new capture in the same session.
    * Escape closes the popup from either state.
    """

    captured = Signal(int)  # memory_id of the new inbox item
    memory_opened = Signal(int)  # id of the clicked answer row
    # Butler layer. ``thinking_started`` / ``answer_shown`` drive the
    # companion's pose from main.py; the two navigation signals let the
    # main window switch views without the popup importing it.
    thinking_started = Signal()
    answer_shown = Signal()
    # Pose cues, separate from the two above. ``thinking_started`` fires
    # only for substantial lookups (it is what puts "Lemme check, sir."
    # in the bubble); these two fire for *every* exchange, because the
    # sprite should visibly think whenever it is spoken to and stop when
    # it has finished replying.
    pose_thinking_started = Signal()
    pose_released = Signal()
    view_requested = Signal(str)
    project_shown = Signal(int)
    # A project the answer *changed* rather than one it wants shown.
    # Separate from ``project_shown`` because the two ask for different
    # things: shown means bring the window forward and go to that
    # project, changed means refresh it if it is already visible and
    # otherwise do nothing at all.
    project_changed = Signal(int)

    def __init__(
        self,
        conn: sqlite3.Connection,
        parent=None,
        threaded: bool = True,
        settings_provider=None,
    ):
        super().__init__(parent)
        self.conn = conn
        # Current visual state. Tests inspect this to confirm
        # the input/answer transition logic.
        self._state: str = STATE_INPUT
        # The most recent question we asked. Kept so the
        # answer header can echo "You asked: <text>" and so
        # tests can assert on the routed query.
        self._last_question: str = ""
        # Track whether we've already committed a capture this
        # round so a second Enter can't create a duplicate.
        self._committed: bool = False
        # Which side of the bubble the tail sticks out of. Recomputed
        # every time the bubble is placed, because ``_move_near`` flips
        # the bubble to the other side of the sprite near a screen edge
        # and a tail on the wrong side would point at nothing.
        self._tail_side: str = "left"
        # Vertical position of the tail's tip, in window coordinates.
        self._tail_tip_y: int = _INPUT_SIZE.height() // 2
        # Where the sprite is, asked fresh each time the popup opens so a
        # dragged companion is never stale. ``None`` means "no companion"
        # (headless tests, or the sprite hidden) and the popup falls back
        # to the screen-centred placement it has always used.
        self._anchor_provider = None
        self._emerge: QParallelAnimationGroup | None = None
        self._build_ui()
        self._wire_signals()

        # One controller and one session context per popup instance.
        # The context is what makes "what about it?" resolvable, and it
        # dies with the popup -- nothing is persisted between sessions.
        self.context = SessionContext()
        # ``settings_provider`` is a zero-arg callable returning the
        # live settings dict, not the dict itself: the main window
        # *rebinds* ``self.settings`` when the Settings dialog saves, so
        # a reference captured here would go stale the first time the
        # user edited a keyword group. ``None`` (the default, and what
        # every existing test constructs) means no settings are visible
        # and keyword auto-linking is simply off.
        self.controller = AssistantController(
            conn,
            self.context,
            threaded=threaded,
            parent=self,
            settings_provider=settings_provider,
        )
        self.controller.captured.connect(self._on_controller_captured)
        self.controller.answer_ready.connect(self._on_controller_answer)
        self.controller.thinking_started.connect(self._on_controller_thinking)
        self.controller.request_started.connect(self.pose_thinking_started.emit)

        # Start in INPUT state with the answer panel hidden.
        self._set_state(STATE_INPUT)

    # ----- public API -----

    def set_anchor_provider(self, provider) -> None:
        """Register a callable returning the sprite's top-left, in globals.

        Wired to the desktop companion at startup. Every path to the
        popup then anchors to the mascot -- the hotkey included, which
        previously dropped the bubble in the middle of the screen while
        Alfred stood somewhere else entirely, tail pointing at nobody.
        A callable rather than a stored point because the user drags him
        around, so the position has to be read at open time.
        """
        self._anchor_provider = provider

    def _current_anchor(self) -> QPoint | None:
        if self._anchor_provider is None:
            return None
        try:
            anchor = self._anchor_provider()
        except Exception:  # noqa: BLE001 - a missing sprite is not fatal
            return None
        return anchor if isinstance(anchor, QPoint) else None

    def open_capture(self) -> None:
        """Reset state and show the popup in INPUT mode.

        Called by the hotkey handler, tray menu, and toolbar
        button. Centralizing the reset here means every
        caller gets the same fresh-input experience without
        having to remember to clear the previous run.
        """
        self._reset_for_new_round()
        anchor = self._current_anchor()
        if anchor is None:
            # No sprite to speak from: centre it, and hide the tail so
            # the bubble doesn't point at empty desktop.
            self._tail_side = "none"
            self._apply_tail_margins()
            self._center_on_current_screen()
            self._show_and_focus()
        else:
            self._place_and_emerge(anchor)

    def show_near(self, anchor: QPoint) -> None:
        """Reset state, then show the popup anchored near ``anchor``.

        Used by the floating desktop companion: when the
        user single-clicks the mascot, the popup appears
        beside it rather than wherever the cursor happens
        to be. The popup is placed to the right of and
        slightly below the anchor, with an off-screen clamp
        that falls back to a screen-relative placement.

        The bubble grows out of the anchor rather than
        appearing at full size: see :meth:`_place_and_emerge`.
        """
        self._reset_for_new_round()
        self._place_and_emerge(anchor)

    def state(self) -> str:
        """Return the current state (``"input"`` or ``"answer"``)."""
        return self._state

    def answer_results(self) -> list[Memory]:
        """Return the memories currently listed in the answer panel.

        Useful for tests that want to confirm the popup
        routed a question to the right results without
        having to scrape the QListWidget itself.
        """
        return [self._results_list.item(i).data(Qt.UserRole)
                for i in range(self._results_list.count())
                if self._results_list.item(i).data(Qt.UserRole) is not None]

    # ----- UI construction -----

    def _build_ui(self) -> None:
        # Frameless + always-on-top. The dialog is modeless
        # in the Qt sense but stays visually on top so it
        # doesn't get buried behind whatever the user was
        # looking at when they hit the hotkey.
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # keep it out of the task bar
        )
        # Deliberately *not* modal. A speech bubble that blocks the app
        # behind it is a dialog wearing a costume; the user should be able
        # to keep clicking around while Alfred talks. Focus still lands in
        # the input because ``_show_and_focus`` asks for it explicitly.
        self.setModal(False)
        self.setWindowTitle(CAPTURE_WINDOW_TITLE)
        # The dialog is only a host for the bubble frame: it paints
        # nothing itself (see the QSS id selector) so the rounded
        # corners read as a speech bubble floating next to Alfred.
        self.setObjectName("capturePopup")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # A fixed width keeps the wrapped answer text -- and therefore
        # the height calculation in ``_recompute_answer_size`` -- stable.
        self.setFixedWidth(_INPUT_SIZE.width() + _TAIL_W)
        # A compact footprint when only the input line is
        # visible; the resize in ``_set_state`` adjusts the
        # window for the answer panel.
        self.resize(_INPUT_SIZE)

        # The vertical layout is shared across both states;
        # the answer panel is just hidden when in INPUT
        # mode. Keeping it in the tree (rather than building
        # a second dialog) makes the resize animation-free
        # and the focus traversal predictable.
        outer = QVBoxLayout(self)
        # A small outer margin lets the bubble's rounded border and
        # shadow-free edge sit clear of the window bounds. The side facing
        # the sprite gets extra room for the speech tail; see
        # ``_apply_tail_margins``, which owns these numbers from here on.
        self._outer_layout = outer
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(0)

        self.bubble = QFrame()
        self.bubble.setObjectName("bubbleFrame")
        self.bubble.setFrameShape(QFrame.NoFrame)
        outer.addWidget(self.bubble)

        layout = QVBoxLayout(self.bubble)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.input = QLineEdit()
        self.input.setPlaceholderText(CAPTURE_PLACEHOLDER)
        # Larger than the main editor -- the popup is the primary
        # capture surface and the text is the entire UX. The size
        # comes from the theme's "captureInput" step rather than
        # from "current + 2" here, so it lands on the same scale as
        # every other deliberately-large piece of text.
        self.input.setObjectName("captureInput")
        # Clear button is a nice-to-have on a small popup;
        # keep it in case the user wants to wipe and retype.
        self.input.setClearButtonEnabled(True)
        layout.addWidget(self.input)

        # Confirmation label -- used in INPUT mode after a
        # capture commit. Same role as before.
        self.confirmation_label = QLabel("")
        self.confirmation_label.setAlignment(Qt.AlignCenter)
        confirmation_font: QFont = self.confirmation_label.font()
        confirmation_font.setItalic(True)
        self.confirmation_label.setFont(confirmation_font)
        self.confirmation_label.hide()
        layout.addWidget(self.confirmation_label)

        # --- Answer panel --------------------------------
        # A frame holding the question echo + results list.
        # The frame is hidden in INPUT mode and shown in
        # ANSWER mode.
        self.answer_frame = QFrame()
        self.answer_frame.setObjectName("answerFrame")
        self.answer_frame.setFrameShape(QFrame.NoFrame)
        answer_layout = QVBoxLayout(self.answer_frame)
        answer_layout.setContentsMargins(0, 4, 0, 0)
        answer_layout.setSpacing(4)

        # Quiet echo of what was asked, so the user can see what
        # Alfred heard.
        self.answer_header = QLabel("")
        self.answer_header.setObjectName("askedEcho")
        self.answer_header.setWordWrap(True)
        header_font: QFont = self.answer_header.font()
        header_font.setItalic(True)
        self.answer_header.setFont(header_font)
        answer_layout.addWidget(self.answer_header)

        # Alfred's reply: the templated sentence. This is the line the
        # user actually reads, so it leads the bubble.
        self.answer_label = QLabel("")
        self.answer_label.setObjectName("assistantSays")
        self.answer_label.setWordWrap(True)
        self.answer_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        answer_layout.addWidget(self.answer_label)

        self._results_list = QListWidget()
        self._results_list.setFrameShape(QListWidget.NoFrame)
        # No keyboard navigation needed: the user is going to
        # either click a row or hit Escape. Tab order returns
        # to the input field naturally (QListWidget would
        # otherwise steal it).
        self._results_list.setFocusPolicy(Qt.NoFocus)
        self._results_list.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        self._results_list.itemClicked.connect(self._on_result_clicked)
        answer_layout.addWidget(self._results_list, 1)

        self.answer_frame.hide()
        layout.addWidget(self.answer_frame, 0)

    def _wire_signals(self) -> None:
        # Enter on the line edit submits. QLineEdit emits
        # returnPressed for both Enter keys (Key_Return and
        # Key_Enter) on every platform, which is what we
        # want.
        self.input.returnPressed.connect(self._on_submit)
        # Escape: QDialog's default keyPressEvent ignores
        # Escape when the dialog is modeless, so we install
        # an event filter on the input to catch it. The
        # keyPressEvent override is a belt-and-suspenders
        # for any other focus recipient.
        self.input.installEventFilter(self)

    # ----- state management -----

    def _set_state(self, state: str) -> None:
        """Switch the popup between INPUT and ANSWER modes.

        Showing / hiding the answer frame is the visible
        change; the resize is also part of the state
        transition so the popup's hit area matches what the
        user can see.
        """
        if state not in (STATE_INPUT, STATE_ANSWER):
            raise ValueError(f"Unknown popup state: {state!r}")
        self._state = state
        if state == STATE_INPUT:
            self.answer_frame.hide()
            self.confirmation_label.hide()
            self.input.setEnabled(True)
            self.resize(_INPUT_SIZE)
        else:
            # ANSWER state. The answer frame is already in
            # the layout; show it and tighten the window
            # to fit the content.
            self.confirmation_label.hide()
            self.input.setEnabled(True)
            self.answer_frame.show()
            self._recompute_answer_size()

    def _reset_for_new_round(self) -> None:
        """Wipe any prior state in preparation for a fresh round.

        Called from both ``open_capture`` and ``show_near`` so
        re-summoning the popup never inherits a stale
        answer panel from the previous question.
        """
        self._committed = False
        self._last_question = ""
        self.input.clear()
        self._results_list.clear()
        self.answer_header.setText("")
        self.answer_label.setText("")
        # A new round is a new conversation: drop the follow-up
        # reference so "what about it?" can't resolve against
        # something the user asked about ten minutes ago.
        self.controller.reset_context()
        self._set_state(STATE_INPUT)

    def _recompute_answer_size(self) -> None:
        """Resize the bubble to fit exactly what it is showing.

        The width is fixed, so Qt can compute the wrapped height of the
        reply line for us: pin the result list to a whole number of rows
        (they are uniform height), let ``adjustSize`` do the arithmetic,
        then clamp between the bare-input height and the ANSWER ceiling
        so a long list never pushes the popup off screen.
        """
        row_h = 48
        rows = self._results_list.count()
        if rows and self._results_list.isVisible():
            self._results_list.setFixedHeight(min(rows, 3) * row_h)
        else:
            self._results_list.setFixedHeight(0)
        self.adjustSize()
        target_h = min(
            max(self.height(), _INPUT_SIZE.height()),
            _ANSWER_BASE_SIZE.height(),
        )
        self.resize(self.width(), int(target_h))

    # ----- event handling -----

    def eventFilter(self, source, event) -> bool:
        """Catch Escape on the line edit and dismiss the popup.

        We only filter events on the line edit; everything
        else propagates normally.
        """
        from PySide6.QtCore import QEvent

        if source is self.input and event.type() == QEvent.KeyPress:
            assert isinstance(event, QKeyEvent)
            if event.key() == Qt.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(source, event)

    def keyPressEvent(self, event) -> None:
        """Escape elsewhere on the dialog (e.g. on a result
        row) also dismisses."""
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def _on_submit(self) -> None:
        """Enter pressed. Hand the line to the controller.

        The popup deliberately makes no routing decision of
        its own: classification, lookups and threading all
        live in :class:`ui.assistant_controller.AssistantController`,
        which answers back over signals. Empty / whitespace
        input is a no-op + dismiss (the user probably hit
        Enter by accident, and an empty inbox item would be
        visual noise).
        """
        if self._committed:
            return
        text = self.input.text().strip()
        if not text:
            self.reject()
            return
        self._last_question = text
        self.input.clear()
        self.controller.submit(text)

    # ----- controller callbacks -----
    #
    # These three run on the main thread (the controller's
    # signals are emitted from, or marshalled back to, the
    # GUI thread) so touching widgets here is safe.

    def _on_controller_thinking(self) -> None:
        """A substantial lookup has started.

        Shows the butler's placeholder line in the bubble,
        immediately -- the controller emits this on submission
        rather than after a delay, and then holds the answer
        back long enough for the line to actually be read.
        Only ever reached for *substantial* lookups, so
        "what's my name?" cannot flash this even on a slow
        machine.
        """
        self._echo_question()
        self.answer_label.setText(ASSISTANT_THINKING)
        self._results_list.clear()
        self._results_list.hide()
        self._set_state(STATE_ANSWER)
        self.thinking_started.emit()

    def _on_controller_answer(self, answer: AssistantAnswer) -> None:
        """An answer arrived. Render it, then apply the close rules."""
        self._render_assistant_answer(answer)
        self.answer_shown.emit()
        # He has said his line; let the sprite go back to idle. The
        # companion finishes the animation lap it is on before actually
        # switching, so this never cuts a sway in half.
        self.pose_released.emit()

        if answer.view_id:
            # ACTION: confirm, switch the main window, get
            # out of the way almost immediately.
            self.view_requested.emit(answer.view_id)
            QTimer.singleShot(ASSISTANT_ACTION_CLOSE_MS, self.accept)
            return

        if answer.open_project_id is not None:
            # LOOKUP_AND_SHOW: the verbal answer is on screen
            # *and* the Projects view is opening behind us.
            # Leave the sentence up long enough to read.
            self.project_shown.emit(int(answer.open_project_id))
            QTimer.singleShot(
                ASSISTANT_ANSWER_AUTO_CLOSE_MS, self.accept
            )
            return

        if answer.refresh_project_id is not None:
            # A write: a project started, or an entry filed under one.
            # The sentence in the bubble is the entire interaction, so
            # nothing is surfaced and the main window is left exactly
            # where the user had it. The id still goes out so a Projects
            # view that happens to be open behind the bubble is not
            # showing a stale count.
            self.project_changed.emit(int(answer.refresh_project_id))
            QTimer.singleShot(
                ASSISTANT_ANSWER_AUTO_CLOSE_MS, self.accept
            )

    def _on_controller_captured(self, new_id: int) -> None:
        """A "remember that ..." line was stored."""
        self._committed = True
        self.captured.emit(int(new_id))
        self.pose_released.emit()
        # Show the confirmation inline, then close after the
        # configured delay, so the popup doesn't look "stuck"
        # in its last-typed state.
        self.input.setEnabled(False)
        self.confirmation_label.setText(CAPTURE_CONFIRMATION)
        self.confirmation_label.show()
        QTimer.singleShot(CAPTURE_CONFIRMATION_MS, self.accept)

    def _echo_question(self) -> None:
        """Put the asked question in the bubble's quiet top line."""
        if self._last_question:
            self.answer_header.setText(
                f'{CAPTURE_ANSWER_HEADER_PREFIX}: "{self._last_question}"'
            )
        else:
            self.answer_header.setText("")

    def _render_assistant_answer(self, answer: AssistantAnswer) -> None:
        """Populate the bubble from a controller answer.

        The sentence in ``answer.text`` is the answer; the
        results list is supporting detail and only appears
        when the lookup actually produced clickable entries.
        """
        self._echo_question()
        self.answer_label.setText(answer.text)
        self._results_list.clear()
        if answer.results:
            for mem in answer.results:
                item = QListWidgetItem(self._format_result_row(mem))
                item.setData(Qt.UserRole, mem)
                item.setToolTip(CAPTURE_ANSWER_RESULT_OPEN_TOOLTIP)
                self._results_list.addItem(item)
            self._results_list.show()
        else:
            self._results_list.hide()
        self._set_state(STATE_ANSWER)
        # Refocus the input so the user can ask a follow-up
        # without re-clicking.
        self.input.setFocus()

    def _format_result_row(self, mem: Memory) -> str:
        """Compose a single-row display string for a result.

        A row is a title plus a short content snippet of
        about 80 characters. We glue the title (always
        present) and a snippet of the content; if the
        content is empty, the snippet slot shows a
        ``"(no preview)"`` fallback rather than an empty
        string that would look like a row that didn't
        load.
        """
        title = (mem.title or "").strip() or "(untitled)"
        content = (mem.content or "").strip()
        if not content:
            snippet = CAPTURE_ANSWER_RESULT_SNIPPET_FALLBACK
        else:
            # Collapse internal whitespace to single spaces
            # so a long pasted paragraph doesn't make the
            # snippet line wrap weirdly.
            one_line = " ".join(content.split())
            snippet = one_line[:_SNIPPET_MAX_CHARS]
            if len(one_line) > _SNIPPET_MAX_CHARS:
                snippet = snippet.rstrip() + "…"
        return f"{title}\n{snippet}"

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        """A result row was clicked: emit + close.

        Clicking a result opens it in the main window,
        which handles the
        actual view switch via the ``memory_opened``
        signal; the popup's job is to forward the id and
        get out of the way. We use ``accept()`` rather
        than ``reject()`` so any closeEvent cleanup runs
        the same way as a normal commit.
        """
        mem = item.data(Qt.UserRole)
        if mem is None:
            return
        self.memory_opened.emit(int(mem.id))
        self.accept()

    # ----- helpers -----

    def _center_on_current_screen(self) -> None:
        """Position the popup on whichever screen the cursor is on.

        Using the cursor's screen (rather than the main
        window's screen) matches the user's expectation: if
        they hit the hotkey while working on a secondary
        monitor, the popup should appear there, not jump
        them back to the primary.
        """
        cursor_pos = self.input.cursor().pos()
        screen = QGuiApplication.screenAt(cursor_pos)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        screen_geometry = screen.availableGeometry()
        # Center horizontally, sit a bit above vertical
        # center so the input line is roughly where the
        # user's eye was.
        x = (
            screen_geometry.x()
            + (screen_geometry.width() - self.width()) // 2
        )
        y = (
            screen_geometry.y()
            + (screen_geometry.height() - self.height()) // 3
        )
        self.move(x, y)

    def _show_and_focus(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def _place_and_emerge(self, anchor: QPoint) -> None:
        """Anchor the bubble beside the sprite, then pop it out of him.

        Order matters. The window is moved to its final position *first*
        so ``self.geometry()`` is correct from the moment it is shown --
        the animation then plays with the window already where it belongs,
        which keeps the answer-time ``resize`` in
        :meth:`_recompute_answer_size` from fighting a running animation.
        Only the position and the opacity are animated; the size is never
        touched.
        """
        self._move_near(anchor)
        self._show_and_focus()
        self._start_emergence(anchor)

    def _start_emergence(self, anchor: QPoint) -> None:
        """Slide-and-fade the bubble out from the sprite.

        A short travel *towards* the final position, starting from a
        point offset back at the sprite, reads as the bubble being spoken
        rather than a panel appearing. If animations are unavailable (or
        the travel would be zero) the window simply stays put -- it is
        already at its final geometry, so there is nothing to recover
        from.
        """
        if self._emerge is not None:
            self._emerge.stop()
            self._emerge = None
        end = self.pos()
        # Start offset lies along the line from the bubble back to the
        # sprite, so he appears to push it out sideways.
        dx = -_EMERGE_TRAVEL if self._tail_side == "left" else _EMERGE_TRAVEL
        if self._tail_side == "none":
            dx = 0
        start = QPoint(end.x() + dx, end.y() + 8)

        group = QParallelAnimationGroup(self)
        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(_EMERGE_MS)
        slide.setStartValue(start)
        slide.setEndValue(end)
        slide.setEasingCurve(QEasingCurve.OutCubic)
        group.addAnimation(slide)

        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(_EMERGE_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        group.addAnimation(fade)

        # Whatever happens, end up opaque and in the right place: an
        # interrupted animation must never leave the bubble invisible.
        def _settle() -> None:
            self.setWindowOpacity(1.0)
            self.move(end)

        group.finished.connect(_settle)
        self._emerge = group
        group.start()

    def _apply_tail_margins(self) -> None:
        """Reserve the tail's width in the window margin on its side."""
        left = 6 + (_TAIL_W if self._tail_side == "left" else 0)
        right = 6 + (_TAIL_W if self._tail_side == "right" else 0)
        self._outer_layout.setContentsMargins(left, 6, right, 6)

    def paintEvent(self, event) -> None:
        """Draw the speech tail into the reserved margin.

        The bubble body is a stylesheet-painted ``QFrame``; QSS has no way
        to grow a triangle off one edge, so the tail is painted here and
        the two colours are kept in step with the stylesheet by hand. The
        triangle is drawn with the fill first and the outline second, and
        it overlaps the frame's border by a pixel so the seam between
        tail and bubble closes instead of showing a hairline.
        """
        super().paintEvent(event)
        if self._tail_side not in ("left", "right"):
            return
        body = self.bubble.geometry()
        tip_y = max(body.top() + _TAIL_H, min(self._tail_tip_y, body.bottom() - _TAIL_H))
        if self._tail_side == "left":
            base_x, tip_x = body.left() + 1, body.left() - _TAIL_W
        else:
            base_x, tip_x = body.right() - 1, body.right() + _TAIL_W

        path = QPainterPath()
        path.moveTo(base_x, tip_y - _TAIL_H / 2.0)
        path.lineTo(tip_x, tip_y)
        path.lineTo(base_x, tip_y + _TAIL_H / 2.0)
        path.closeSubpath()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(_BUBBLE_FILL))
        painter.drawPath(path)
        # Only the two sloping edges get an outline; the base is inside
        # the bubble and drawing it there would show as a line across the
        # tail's root.
        painter.setPen(QPen(QColor(_BUBBLE_LINE), 1))
        painter.drawLine(base_x, int(tip_y - _TAIL_H / 2.0), tip_x, int(tip_y))
        painter.drawLine(tip_x, int(tip_y), base_x, int(tip_y + _TAIL_H / 2.0))
        painter.end()

    def _move_near(self, anchor: QPoint) -> None:
        """Place the bubble beside the sprite and aim its tail at him.

        ``anchor`` is the companion's top-left in global coordinates, so
        the drawn figure sits at a known offset inside it. The bubble
        is placed clear of that box -- to his right if there is room,
        flipped to his left if there isn't -- and the tail side is set to
        match, because a tail on the far side would point away from the
        one thing it is supposed to connect to.

        Vertically the bubble is centred on his mouth rather than hung
        below his feet, which is what makes the tail short enough to read
        as a tail. Both axes are clamped to the screen, and an anchor on
        no screen at all (a stale position from a disconnected monitor)
        falls back to the centred placement with no tail.
        """
        screen = QGuiApplication.screenAt(anchor)
        if screen is None:
            self._tail_side = "none"
            self._apply_tail_margins()
            self._center_on_current_screen()
            return
        screen_geo = screen.availableGeometry()
        popup_w = self.width()
        popup_h = self.height()
        gap = 6  # the tail itself provides most of the separation

        # Prefer his right-hand side: tail on the bubble's left.
        self._tail_side = "left"
        x = anchor.x() + _SPRITE_BODY_RIGHT + gap
        if x + popup_w > screen_geo.right():
            # No room -- speak out of his other side instead.
            self._tail_side = "right"
            x = anchor.x() + _SPRITE_BODY_LEFT - gap - popup_w
        if x < screen_geo.left():
            # Cornered on both sides: keep it on screen and accept that
            # the bubble now overlaps him a little.
            x = screen_geo.left()
        self._apply_tail_margins()

        mouth_y = anchor.y() + _SPRITE_MOUTH_DY
        y = mouth_y - popup_h // 2
        if y + popup_h > screen_geo.bottom():
            y = screen_geo.bottom() - popup_h
        if y < screen_geo.top():
            y = screen_geo.top()
        self.move(x, y)
        # Window coordinates, for ``paintEvent``. Recorded rather than
        # recomputed because the bubble is resized when an answer lands
        # and the tail must keep pointing at the same spot on him.
        self._tail_tip_y = mouth_y - y
