"""Conversational task creation and completion (spoken, not clicked).

Two features share one file because they are two halves of the same
idea: the user talks about work in ordinary sentences, and Alfred turns
that into rows in ``task_details`` without anyone opening the editor.

* **Creation** — a capture that names a day is filed as a *task* with
  that due date instead of an inbox item. This runs on the capture path
  itself, so it applies whether or not the user said "remember".
* **Completion** — "I finished the meeting" finds the one matching open
  task and completes it. Where it can't be sure, it asks.

The date arithmetic itself is tested in ``test_date_parsing.py``; what
is under test here is the wiring — that the parsed date reaches
``task_details``, and that the three completion outcomes write what
they should and nothing more.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.memory import create_memory, get_memory
from core.projects import get_memory_project, list_projects
from core.tasks import list_tasks, set_due_date
from ui.assistant_controller import (
    KIND_TASK_COMPLETE,
    KIND_TASKS,
    OUTCOME_SUBSTANTIAL_LOOKUP,
    WEIGHT_SUBSTANTIAL,
    AssistantController,
    SessionContext,
    classify,
    execute,
)
from ui.strings import ASSISTANT_TASK_DONE_NONE


TOMORROW = (date.today() + timedelta(days=1)).isoformat()
TODAY = date.today().isoformat()


def _due_date(conn, memory_id: int) -> str | None:
    row = conn.execute(
        "SELECT due_date FROM task_details WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    return None if row is None else row["due_date"]


def _completed_at(conn, memory_id: int) -> str | None:
    row = conn.execute(
        "SELECT completed_at FROM task_details WHERE memory_id = ?",
        (memory_id,),
    ).fetchone()
    return None if row is None else row["completed_at"]


def _bucket_ids(conn, bucket: str) -> list[int]:
    return [m.id for m, _due, _done in list_tasks(conn, bucket)]


@pytest.fixture
def ctrl(conn, qapp):
    """A controller over the fixture database, no settings provider.

    Keyword auto-linking is a separate feature with its own file; with
    no provider it is off, which keeps these tests about dates alone.
    """
    controller = AssistantController(conn, SessionContext())
    yield controller
    controller.deleteLater()


# ---------- creation: a spoken date becomes a due date ----------


class TestCaptureWithADate:
    def test_a_dated_capture_is_filed_as_a_task(self, ctrl, conn):
        captured: list[int] = []
        ctrl.captured.connect(captured.append)
        ctrl.submit("I have a meeting tomorrow at 6pm")
        new_id = captured[0]
        assert new_id > 0
        stored = get_memory(conn, new_id)
        assert stored.type == "task"
        assert _due_date(conn, new_id) == TOMORROW

    def test_the_sentence_is_stored_exactly_as_spoken(self, ctrl, conn):
        """Nothing is stripped — not the date phrase, not the time.

        The date is *additional* structure, so the note still reads back
        the way the user said it. The "at 6pm" in particular has nowhere
        to go: the schema stores a date, so the time survives only as
        ordinary text and is not scheduled.
        """
        captured: list[int] = []
        ctrl.captured.connect(captured.append)
        ctrl.submit("I have a meeting tomorrow at 6pm")
        stored = get_memory(conn, captured[0])
        assert stored.title == "I have a meeting tomorrow at 6pm"

    def test_no_remember_prefix_is_required(self, ctrl, conn):
        captured: list[int] = []
        ctrl.captured.connect(captured.append)
        ctrl.submit("remember that the dentist is tomorrow")
        first = captured[0]
        ctrl.submit("the dentist is tomorrow")
        second = captured[1]
        # Both routes produce the same thing: a task due tomorrow.
        for new_id in (first, second):
            assert get_memory(conn, new_id).type == "task"
            assert _due_date(conn, new_id) == TOMORROW

    def test_a_task_due_today_lands_in_today(self, ctrl, conn):
        captured: list[int] = []
        ctrl.captured.connect(captured.append)
        ctrl.submit("call the bank today")
        new_id = captured[0]
        assert _due_date(conn, new_id) == TODAY
        assert new_id in _bucket_ids(conn, "today")
        assert new_id not in _bucket_ids(conn, "upcoming")

    def test_a_task_due_tomorrow_lands_in_upcoming(self, ctrl, conn):
        captured: list[int] = []
        ctrl.captured.connect(captured.append)
        ctrl.submit("I have a meeting tomorrow at 6pm")
        new_id = captured[0]
        assert new_id in _bucket_ids(conn, "upcoming")
        assert new_id not in _bucket_ids(conn, "today")

    def test_buckets_are_computed_live_not_stamped(self, ctrl, conn):
        """No "advance the day" job exists, and none is needed.

        ``list_tasks`` takes the reference date as an argument, so the
        same row moves from Upcoming to Today by the calendar turning
        over, not by anything writing to it. Asserted by asking the same
        question about tomorrow.
        """
        captured: list[int] = []
        ctrl.captured.connect(captured.append)
        ctrl.submit("I have a meeting tomorrow at 6pm")
        new_id = captured[0]
        tomorrow = date.today() + timedelta(days=1)
        today_tomorrow = [
            m.id for m, _d, _c in list_tasks(conn, "today", tomorrow)
        ]
        assert new_id in today_tomorrow

    def test_an_undated_capture_is_unchanged(self, ctrl, conn):
        """The regression guard: no date, no promotion, no task row."""
        captured: list[int] = []
        ctrl.captured.connect(captured.append)
        ctrl.submit("remember that the ESP32 pinout is on the whiteboard")
        new_id = captured[0]
        stored = get_memory(conn, new_id)
        assert stored.type == "inbox"
        assert _due_date(conn, new_id) is None
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM task_details WHERE memory_id = ?",
                (new_id,),
            ).fetchone()["n"]
            == 0
        )

    def test_a_project_phrase_is_not_mistaken_for_a_day(self, ctrl, conn):
        """"to the Friday project" names a project, not a due date.

        The due-date parse deliberately reads the *stored* content, from
        which the trailing project phrase has already been stripped, so
        the weekday inside the project's name cannot leak into the
        schedule.
        """
        captured: list[int] = []
        ctrl.captured.connect(captured.append)
        ctrl.submit("remember to buy solder to the Friday project")
        new_id = captured[0]
        assert get_memory(conn, new_id).type == "inbox"
        assert _due_date(conn, new_id) is None
        linked = get_memory_project(conn, new_id)
        assert linked is not None and linked.name == "Friday"

    def test_a_dated_capture_can_still_name_its_project(self, ctrl, conn):
        captured: list[int] = []
        ctrl.captured.connect(captured.append)
        ctrl.submit("test the sensor tomorrow to the ESP32 project")
        new_id = captured[0]
        assert get_memory(conn, new_id).type == "task"
        assert _due_date(conn, new_id) == TOMORROW
        linked = get_memory_project(conn, new_id)
        assert linked is not None and linked.name == "ESP32"


# ---------- completion: classify ----------


class TestClassifyCompletion:
    @pytest.mark.parametrize(
        "text",
        [
            "I finished the meeting",
            "I've finished the meeting",
            "I have finished the meeting",
            "I'm done with the meeting",
            "I am done with the meeting",
            "I completed the meeting",
            "mark the meeting as done",
            "mark the meeting done",
            "mark the meeting as complete",
            "complete the meeting",
            "finish the meeting",
        ],
    )
    def test_every_recognized_phrasing(self, text):
        cls = classify(text, SessionContext())
        assert cls.kind == KIND_TASK_COMPLETE, text
        assert cls.outcome == OUTCOME_SUBSTANTIAL_LOOKUP
        assert cls.weight == WEIGHT_SUBSTANTIAL
        assert "meeting" in cls.payload["terms"]

    def test_case_is_irrelevant(self):
        assert (
            classify("I FINISHED THE MEETING", SessionContext()).kind
            == KIND_TASK_COMPLETE
        )

    def test_trailing_punctuation_is_trimmed_off_the_subject(self):
        cls = classify("I finished the meeting.", SessionContext())
        assert cls.payload["what"] == "the meeting"

    def test_a_bare_phrase_with_no_subject_is_not_a_completion(self):
        # "I finished" alone has nothing after the verb, so the pattern
        # does not match at all and the line falls through to the
        # ordinary shapes rather than completing something at random.
        assert classify("I finished", SessionContext()).kind != KIND_TASK_COMPLETE

    def test_a_pronoun_subject_still_searches_for_that_pronoun(self):
        """A documented limitation of reusing ``extract_query_terms``.

        Its stopword pass falls back to the unfiltered tokens rather
        than returning nothing, so "it" survives as a search term and
        "I finished it" searches open tasks for the substring ``it``.
        The mitigation is downstream, not here: the confirmation names
        the task back, so a wrong pick is visible immediately, and two
        or more hits ask instead of guessing.
        """
        cls = classify("I finished it", SessionContext())
        assert cls.kind == KIND_TASK_COMPLETE
        assert cls.payload["terms"] == "it"


# ---------- completion: execute ----------


@pytest.fixture
def open_tasks(conn):
    """Three open tasks and one already-completed one."""
    meeting = create_memory(conn, "Board meeting", "with the trustees", type="task")
    set_due_date(conn, meeting, TODAY)
    solder = create_memory(conn, "Buy solder", "the thin kind", type="task")
    note = create_memory(conn, "Meeting notes", "not a task at all", type="note")
    return {"meeting": meeting, "solder": solder, "note": note}


class TestCompletionExecute:
    def test_one_match_is_completed_and_named_back(self, conn, open_tasks):
        answer = execute(
            conn, classify("I finished the meeting", SessionContext()), SessionContext()
        )
        assert answer.kind == KIND_TASK_COMPLETE
        assert "Board meeting" in answer.text
        assert _completed_at(conn, open_tasks["meeting"]) is not None
        # And it has left the open buckets.
        assert open_tasks["meeting"] not in _bucket_ids(conn, "today")
        assert open_tasks["meeting"] in _bucket_ids(conn, "completed")

    def test_a_second_phrasing_reaches_the_same_task(self, conn, open_tasks):
        execute(
            conn, classify("mark the solder as done", SessionContext()), SessionContext()
        )
        assert _completed_at(conn, open_tasks["solder"]) is not None

    def test_two_matches_are_listed_and_nothing_is_completed(
        self, conn, open_tasks
    ):
        second = create_memory(
            conn, "Meeting with the auditors", "quarterly", type="task"
        )
        set_due_date(conn, second, TOMORROW)
        answer = execute(
            conn, classify("I finished the meeting", SessionContext()), SessionContext()
        )
        assert "Board meeting" in answer.text
        assert "Meeting with the auditors" in answer.text
        # The whole point: it asked instead of guessing.
        assert _completed_at(conn, open_tasks["meeting"]) is None
        assert _completed_at(conn, second) is None
        # And it opened nothing on the way past.
        assert answer.view_id is None
        assert answer.open_project_id is None

    def test_no_match_declines_and_creates_nothing(self, conn, open_tasks):
        before = conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]
        answer = execute(
            conn,
            classify("I finished the tax return", SessionContext()),
            SessionContext(),
        )
        assert answer.text == ASSISTANT_TASK_DONE_NONE
        after = conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]
        assert after == before
        # Nothing was completed as a consolation prize either.
        for key in ("meeting", "solder"):
            assert _completed_at(conn, open_tasks[key]) is None

    def test_notes_are_not_completable(self, conn, open_tasks):
        """A matching *note* is not a matching task.

        "Meeting notes" contains the term but is type='note', so the
        search must not see it — otherwise the single-match case above
        would have been ambiguous.
        """
        answer = execute(
            conn, classify("I finished the meeting", SessionContext()), SessionContext()
        )
        assert "Meeting notes" not in answer.text
        assert get_memory(conn, open_tasks["note"]).type == "note"

    def test_an_already_completed_task_is_not_a_candidate(self, conn, open_tasks):
        # Complete it once...
        execute(
            conn, classify("I finished the meeting", SessionContext()), SessionContext()
        )
        # ...and asking again finds nothing open, rather than reporting
        # success a second time or treating it as ambiguous.
        answer = execute(
            conn, classify("I finished the meeting", SessionContext()), SessionContext()
        )
        assert answer.text == ASSISTANT_TASK_DONE_NONE

    def test_an_undated_task_can_be_completed(self, conn, open_tasks):
        """Upcoming is searched too, not just Today.

        "Buy solder" has no due date, which puts it in Upcoming; it is
        still open work the user can report finishing. Note the term has
        to be a literal substring — "solder" hits, "buying" would not,
        because there is no stemming anywhere in this path.
        """
        assert open_tasks["solder"] in _bucket_ids(conn, "upcoming")
        execute(
            conn,
            classify("I'm done with the solder", SessionContext()),
            SessionContext(),
        )
        assert _completed_at(conn, open_tasks["solder"]) is not None

    def test_every_term_must_match_not_just_one(self, conn, open_tasks):
        """All terms, not any — "the solder meeting" matches neither.

        Requiring all of them is what keeps a two-noun phrase from
        matching two unrelated single-noun tasks.
        """
        answer = execute(
            conn,
            classify("I finished the solder meeting", SessionContext()),
            SessionContext(),
        )
        assert answer.text == ASSISTANT_TASK_DONE_NONE
        assert _completed_at(conn, open_tasks["meeting"]) is None
        assert _completed_at(conn, open_tasks["solder"]) is None

    def test_completion_does_not_invent_a_project(self, conn, open_tasks):
        execute(
            conn, classify("I finished the meeting", SessionContext()), SessionContext()
        )
        assert list_projects(conn) == []


# ---------- the typo path, end to end ----------


class TestAMisspelledDayStillWorks:
    """The bug as it was reported, start to finish.

    "remember that I have meeting at tommorow 8pm" was filed as an inbox
    note with no due date, and "what do we have tommorow?" then answered
    that it had nothing on file. One missing spelling broke both halves,
    so both halves are pinned here rather than only the parser.
    """

    def test_the_capture_becomes_a_task_due_tomorrow(self, ctrl, conn):
        captured: list[int] = []
        ctrl.captured.connect(captured.append)
        ctrl.submit("remember that I have meeting at tommorow 8pm")
        new_id = captured[0]
        assert get_memory(conn, new_id).type == "task"
        assert _due_date(conn, new_id) == TOMORROW

    def test_the_typo_is_preserved_in_the_stored_text(self, ctrl, conn):
        # Tolerated on the way in, not corrected: the note still reads
        # back exactly as the user typed it.
        captured: list[int] = []
        ctrl.captured.connect(captured.append)
        ctrl.submit("remember that I have meeting at tommorow 8pm")
        assert (
            get_memory(conn, captured[0]).title
            == "remember that I have meeting at tommorow 8pm"
        )

    def test_asking_with_the_same_typo_finds_it(self, ctrl, conn):
        captured: list[int] = []
        ctrl.captured.connect(captured.append)
        ctrl.submit("remember that I have meeting at tommorow 8pm")
        answer = execute(
            conn,
            classify("what do we have tommorow?", SessionContext()),
            SessionContext(),
        )
        assert answer.kind == KIND_TASKS
        assert "tommorow 8pm" in answer.text

    def test_the_typo_on_one_side_only_still_matches(self, ctrl, conn):
        """Spelling on the way in and on the way out are independent.

        Both sides fold to the same canonical day, so a note filed with
        the typo is found by the correctly-spelled question and vice
        versa — otherwise the tolerance would only help a user who
        misspells it the same way twice.
        """
        captured: list[int] = []
        ctrl.captured.connect(captured.append)
        ctrl.submit("dentist tomorrow")
        answer = execute(
            conn,
            classify("what do we have tommorow?", SessionContext()),
            SessionContext(),
        )
        assert "dentist tomorrow" in answer.text
