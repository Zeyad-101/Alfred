"""Tests for ``ui.assistant_controller`` -- classification, lookup, and
the reveal that sits between them.

The module has three layers and they are tested as three, because they
fail in three different ways:

* :func:`classify` is pure. It decides *outcome* (capture, direct
  answer, action, lookup) and *weight* (none / trivial / substantial)
  from the words alone, with no database and no Qt, in a fixed rule
  order. The failure mode is a rule that shadows a later one -- and it
  never raises: a project write misread as a capture files a note with
  the whole instruction as its title and looks like a typo.
* :func:`execute` runs the lookup and builds the reply. Here the
  failure modes are writes that should not have happened, view switches
  that should not have happened, and the follow-up bookkeeping
  (``project_id`` / ``memory_id`` / ``topic``) going out on the wrong
  answer.
* :class:`AssistantController` joins them to the reveal. ``weight`` is a
  property of the *question*, not of the clock, so what is pinned here
  is that trivial work can never show thinking even in principle, and
  that the minimum-visibility hold delays the answer without anything
  sleeping.

Two things are deliberately not tested through the worker thread. The
controller is driven with ``threaded=False`` (and mostly
``min_hold_ms=0``), which is the documented path for an in-memory or
test database: it runs the same :func:`execute` on the calling thread,
so the routing is identical and the assertions do not race a thread
pool. ``connection_path`` -- the thing that actually decides threaded
versus inline -- is tested directly instead.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.db import get_connection, init_db
from core.memory import create_memory, get_memory
from core.projects import (
    find_project_by_name,
    get_or_create_project,
    list_projects,
    set_memory_project,
)
from core.tasks import list_tasks, set_due_date
from ui.assistant_controller import (
    ACTION_SETTINGS,
    KIND_CAPABILITIES,
    KIND_CONTEXT_REUSE,
    KIND_CREATED_AT,
    KIND_FALLBACK_SEARCH,
    KIND_LAST_WORKED,
    KIND_PENDING_ON,
    KIND_PROFILE_FIELD,
    KIND_PROJECT_ACTION,
    KIND_PROJECT_CREATE,
    KIND_PROJECT_OPEN,
    KIND_PROJECT_STATUS,
    KIND_TASK_COMPLETE,
    KIND_TASKS,
    KIND_TOPIC_SEARCH,
    KIND_VIEW,
    KIND_YESTERDAY,
    OUTCOME_ACTION,
    OUTCOME_CAPTURE,
    OUTCOME_DIRECT_ANSWER,
    OUTCOME_LOOKUP_AND_SHOW,
    OUTCOME_SUBSTANTIAL_LOOKUP,
    OUTCOME_TRIVIAL_LOOKUP,
    WEIGHT_NONE,
    WEIGHT_SUBSTANTIAL,
    WEIGHT_TRIVIAL,
    AssistantController,
    Classification,
    SessionContext,
    classify,
    connection_path,
    execute,
)
from ui.sidebar import VIEW_INBOX, VIEW_PROJECTS, VIEW_TASKS, VIEW_TRASH
from ui.strings import (
    ASSISTANT_ACTION_CONFIRM,
    ASSISTANT_CAPABILITIES,
    ASSISTANT_CONTEXT_NONE,
    ASSISTANT_CREATED_UNKNOWN,
    ASSISTANT_ERROR,
    ASSISTANT_PROJECT_UNKNOWN,
    ASSISTANT_TASK_BUCKET_OPEN,
    ASSISTANT_TASK_BUCKET_TODAY,
    ASSISTANT_TASK_BUCKET_TOMORROW,
    ASSISTANT_TASK_DONE_NONE,
    ASSISTANT_UNKNOWN_FIELD,
)

TODAY = date.today().isoformat()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()


# --- helpers ---------------------------------------------------------------


def _task(conn, title: str, content: str = "", due: str | None = None) -> int:
    """A task row, optionally dated. Titles are what answers quote back."""
    memory_id = create_memory(conn, title, content or title, "task")
    if due is not None:
        set_due_date(conn, memory_id, due)
    return memory_id


def _controller(conn, **kwargs) -> AssistantController:
    """A controller that runs its lookups inline.

    ``min_hold_ms=0`` unless a test is about the hold itself: these
    assertions are about routing, and a live timer would only decide
    when the same answer arrives.
    """
    kwargs.setdefault("threaded", False)
    kwargs.setdefault("min_hold_ms", 0)
    return AssistantController(conn, SessionContext(), **kwargs)


# ===========================================================================
# classify -- pure routing
# ===========================================================================


def test_an_empty_line_is_a_capture_that_needs_nothing():
    """The bubble can be submitted empty. That must not become a
    question, a search, or an exception."""
    for text in ("", "   ", "\n\t"):
        cls = classify(text)
        assert cls.outcome == OUTCOME_CAPTURE
        assert cls.weight == WEIGHT_NONE
        assert cls.needs_lookup is False
        assert cls.may_show_thinking is False


def test_the_remember_prefix_wins_over_every_question_shape():
    """Rule 1 is first for a reason: "remember" is the user saying
    outright that this is a note. "remember what's my name" is a thing to
    file, not a profile question to answer.
    """
    assert classify("remember the sensor drifts").outcome == OUTCOME_CAPTURE
    assert classify("remember what's my name").outcome == OUTCOME_CAPTURE
    assert classify("remember what are my tasks").kind == ""
    # A bare "remember" with nothing after it is still a capture -- the
    # capture path decides what to do with an empty note, not classify.
    assert classify("remember").outcome == OUTCOME_CAPTURE


def test_the_two_shapes_that_need_no_database_say_so():
    """Capabilities and view switches carry weight ``none``, which is
    what lets :func:`execute` answer them with ``conn=None``."""
    caps = classify("what can you do")
    assert (caps.outcome, caps.weight, caps.kind) == (
        OUTCOME_DIRECT_ANSWER, WEIGHT_NONE, KIND_CAPABILITIES
    )
    assert caps.needs_lookup is False

    view = classify("open my tasks")
    assert (view.outcome, view.weight, view.kind) == (
        OUTCOME_ACTION, WEIGHT_NONE, KIND_VIEW
    )
    assert view.needs_lookup is False


@pytest.mark.parametrize(
    "text,view_id",
    [
        ("open my tasks", VIEW_TASKS),
        ("show me my task list", VIEW_TASKS),
        ("take me to my todos", VIEW_TASKS),
        ("bring up the inbox", VIEW_INBOX),
        ("please open the trash", VIEW_TRASH),
        ("go to the bin", VIEW_TRASH),
        ("go to my projects", VIEW_PROJECTS),
        ("open settings", ACTION_SETTINGS),
    ],
)
def test_a_view_switch_names_the_view_it_opens(text, view_id):
    """Every accepted phrasing resolves through ``_ACTION_TARGETS`` to a
    real view id -- the popup switches on this value, so a synonym that
    resolved to ``None`` would silently do nothing."""
    cls = classify(text)
    assert cls.kind == KIND_VIEW
    assert cls.payload["view_id"] == view_id


def test_the_write_shapes_are_trivial_so_they_stay_on_the_owning_connection():
    """A resolve plus an insert is not a search.

    Trivial weight is what keeps these three off the worker thread --
    which matters more for the writes than for the reads, since the
    worker opens its own connection and the UI's would not see the row.
    """
    for text, kind in (
        ("add a task to my ESP32 project: test the sensor", KIND_PROJECT_ACTION),
        ("start a new project called Batcave", KIND_PROJECT_CREATE),
        ("open my ESP32 project", KIND_PROJECT_OPEN),
    ):
        cls = classify(text)
        assert cls.kind == kind, text
        assert cls.weight == WEIGHT_TRIVIAL, text
        assert cls.needs_lookup is True
        assert cls.may_show_thinking is False, (
            f"{text!r} would show a thinking state for one indexed row"
        )


def test_a_project_write_needs_a_name_and_a_body_to_be_an_instruction():
    """The separator plus a non-empty body is what makes this a write
    rather than a note that happens to mention a project.

    "add a task to my ESP32 project", with nothing after it, is the user
    trailing off; filing it as an empty task under ESP32 would be worse
    than filing the sentence.
    """
    assert classify("add a task to my ESP32 project").outcome == OUTCOME_CAPTURE
    cls = classify("add a task to my ESP32 project: test the sensor")
    assert cls.payload["name"] == "ESP32"
    assert cls.payload["body"] == "test the sensor"
    # The name as typed travels alongside the cleaned one, because
    # resolution tries the unabridged phrase first.
    assert cls.payload["raw"] == "ESP32"


@pytest.mark.parametrize(
    "text,entry_type",
    [
        ("add a task to my ESP32 project: charge it", "task"),
        ("add a todo to my ESP32 project: charge it", "task"),
        ("add a reminder to my ESP32 project: charge it", "task"),
        ("add an item to my ESP32 project: charge it", "task"),
        ("add a note to my ESP32 project: charge it", "note"),
        ("add to my ESP32 project: charge it", "task"),
    ],
)
def test_the_noun_the_user_said_decides_what_gets_created(text, entry_type):
    """"add a note to my X project: ..." files a note; everything else,
    including saying no noun at all, files a task -- which is what
    "add ... to my project" almost always means."""
    cls = classify(text)
    assert cls.kind == KIND_PROJECT_ACTION, text
    assert cls.payload["entry_type"] == entry_type


def test_a_bare_reference_never_resolves_or_creates_a_project():
    """"this project" names nothing.

    All three project shapes run their name through ``_project_entity``,
    so a demonstrative falls through to the next rule rather than
    resolving -- or, for the create and write branches, *creating* -- a
    project called "this".
    """
    assert classify("add a task to this project: buy solder").outcome == (
        OUTCOME_CAPTURE
    )
    # Trailed off before naming it: filler-only names are not names.
    assert classify("create a project and name it").outcome == OUTCOME_CAPTURE
    # The roll-up shape matches but has nothing to resolve, so it
    # degrades to a search rather than being filed as a note.
    assert classify("tell me about this project").kind == KIND_FALLBACK_SEARCH


def test_completing_a_task_is_substantial_because_it_has_to_search_first():
    """"I finished X" is the one write that needs a real search.

    Nothing in the sentence identifies a row, so the terms have to be
    matched against every open task before anything can be closed --
    which is why this shape, alone among the writes, is substantial and
    may show a thinking state.
    """
    cls = classify("I finished the sensor wiring")
    assert cls.kind == KIND_TASK_COMPLETE
    assert cls.weight == WEIGHT_SUBSTANTIAL
    assert cls.may_show_thinking is True
    assert cls.payload["what"] == "the sensor wiring"
    # The terms are the stripped form that reaches the search.
    assert cls.payload["terms"] == "sensor wiring"


@pytest.mark.parametrize(
    "text,bucket",
    [
        ("what are my tasks", None),
        ("what tasks do i have today", "today"),
        ("what's due today", "today"),
        ("what's due tomorrow", "tomorrow"),
        ("what's due tommorow", "tomorrow"),
    ],
)
def test_a_task_question_carries_the_day_it_asked_about(text, bucket):
    """``bucket`` is what :func:`execute` switches on, and ``None`` is a
    real value -- an unqualified "what are my tasks" means everything
    open, not everything due today.

    The misspelling is deliberate: "tommorow" is the typo people
    actually make, and folding it here is cheaper than an answer about
    the wrong day.
    """
    cls = classify(text)
    assert cls.kind == KIND_TASKS
    assert cls.payload["bucket"] == bucket


def test_a_question_without_a_day_word_is_not_a_task_question():
    """"anything on X" has the shape of a task question minus the day,
    and it means something else entirely: search that project, do not
    list the whole task board."""
    assert classify("anything on the Batman project").kind == KIND_TOPIC_SEARCH
    assert classify("do i have anything about wiring").kind == KIND_TOPIC_SEARCH


@pytest.mark.parametrize(
    "text",
    [
        "what about it?",
        "how's that",
        "what's its status",
        "tell me more about it",
        "that?",
    ],
)
def test_a_pronoun_follow_up_is_only_a_follow_up_when_there_is_a_reference(text):
    """Rule 8 is conditional on the session having something to refer
    back to.

    With a reference these are trivial context re-use. Without one the
    words cannot be resolved at all, so they must fall through to a
    later rule -- never be answered about nothing, and never be
    answered about whatever the *previous* session was looking at.
    """
    with_ref = classify(text, SessionContext(last_shown_project_id=1))
    assert with_ref.outcome == OUTCOME_TRIVIAL_LOOKUP
    assert with_ref.kind == KIND_CONTEXT_REUSE
    assert with_ref.weight == WEIGHT_TRIVIAL

    # A memory is a reference too -- either id is enough.
    assert classify(
        text, SessionContext(last_shown_memory_id=7)
    ).kind == KIND_CONTEXT_REUSE

    for empty in (SessionContext(), None):
        assert classify(text, empty).kind != KIND_CONTEXT_REUSE
        # ...and it reaches the last rule, which routes on sentence
        # shape: the questions get searched, the bare imperative
        # ("tell me more about it") is filed like any other statement.
        assert classify(text, empty).kind in (KIND_FALLBACK_SEARCH, "")


def test_a_topic_alone_is_not_a_reference_for_a_follow_up():
    """``last_topic`` is remembered for wording, not for resolution.

    ``has_reference`` deliberately ignores it: a topic string names no
    row, so "what about it?" after a search that found nothing has
    nothing to re-answer.
    """
    ctx = SessionContext(last_topic="wiring")
    assert ctx.has_reference() is False
    assert classify("what about it?", ctx).kind == KIND_FALLBACK_SEARCH


def test_asking_for_the_notes_on_a_project_searches_rather_than_rolls_up():
    """Rule order between the search and the roll-up.

    "the notes on X" and "anything on X" both mention a project, but
    what they ask for is the entries -- so the topic search is checked
    first and the project roll-up never sees them.
    """
    cls = classify("show me the notes on the Batman project")
    assert cls.kind == KIND_TOPIC_SEARCH
    assert cls.payload["topic"] == "Batman"


def test_saying_the_word_project_makes_the_roll_up_strict():
    """``strict`` is what :func:`execute` uses to decide whether an
    unresolvable name is a mistake or just a topic.

    "tell me about the Batcave project" names a project, so a name that
    does not resolve is worth saying so about. "tell me about the
    grapple" might be a project or might be a thing, so it is allowed to
    degrade into a search instead of declining.
    """
    named = classify("tell me about the Batcave project")
    assert named.kind == KIND_PROJECT_STATUS
    assert named.outcome == OUTCOME_LOOKUP_AND_SHOW
    assert named.weight == WEIGHT_SUBSTANTIAL
    assert named.payload["name"] == "Batcave"
    assert named.payload["strict"] is True

    loose = classify("tell me about the grapple")
    assert loose.kind == KIND_PROJECT_STATUS
    assert loose.payload["strict"] is False

    # The progress phrasing is the same rule, and naming the project
    # makes it strict too.
    assert classify("how's the ESP32 project doing").payload["strict"] is True


def test_a_profile_question_is_trivial_and_names_the_field():
    """One indexed row by field name -- no thinking state for that.

    The field travels in the payload rather than being re-derived, since
    it is also what the answer quotes back.
    """
    cls = classify("what's my name")
    assert (cls.outcome, cls.weight, cls.kind) == (
        OUTCOME_TRIVIAL_LOOKUP, WEIGHT_TRIVIAL, KIND_PROFILE_FIELD
    )
    assert cls.payload["field"] == "name"
    assert cls.may_show_thinking is False


def test_the_last_rule_splits_questions_from_statements():
    """Nothing matched, so the shape of the sentence decides.

    A question gets searched -- the user asked for something, and a
    search is the most useful thing left to try. A statement gets filed,
    because that is what an assistant with a notebook should do with a
    sentence it was not asked to interpret. Getting this backwards is
    the expensive mistake: it either files questions as notes or
    silently searches for the note the user meant to keep.
    """
    question = classify("what's the airspeed velocity of a swallow")
    assert question.outcome == OUTCOME_SUBSTANTIAL_LOOKUP
    assert question.kind == KIND_FALLBACK_SEARCH

    statement = classify("buy more solder")
    assert statement.outcome == OUTCOME_CAPTURE
    assert statement.kind == ""
    assert statement.needs_lookup is False


@pytest.mark.parametrize(
    "text",
    [
        "",
        "remember the sensor drifts",
        "what can you do",
        "open my tasks",
        "add a task to my ESP32 project: test the sensor",
        "start a new project called Batcave",
        "open my ESP32 project",
        "I finished the sensor wiring",
        "what are my tasks",
        "what's left on the ESP32 project",
        "when was the last time i worked on the Batmobile",
        "what did i work on yesterday",
        "when did i create the grapple hook",
        "do i have anything about wiring",
        "tell me about the Batcave project",
        "what's my name",
        "what's the airspeed velocity of a swallow",
        "buy more solder",
    ],
)
def test_only_substantial_work_may_show_a_thinking_state(text):
    """The property the whole two-axis design exists for.

    ``may_show_thinking`` is derived from the weight, never set by hand,
    so a new rule cannot accidentally opt a one-row lookup into a
    thinking animation. This sweeps every shape the classifier knows.
    """
    cls = classify(text)
    assert cls.may_show_thinking == (cls.weight == WEIGHT_SUBSTANTIAL), text
    # And weight is only ever one of the three declared values.
    assert cls.weight in (WEIGHT_NONE, WEIGHT_TRIVIAL, WEIGHT_SUBSTANTIAL)


def test_every_classification_carries_the_stripped_text_it_came_from():
    """``text`` is what the capture path files and what the search path
    searches for, so a rule that forgot to pass it would file an empty
    note or search for nothing -- silently, in both cases.
    """
    for text in (
        "  buy more solder  ",
        "remember the sensor drifts",
        "what can you do",
        "\tI finished the sensor wiring\n",
        "what's my name",
    ):
        assert classify(text).text == text.strip()


def test_classify_needs_no_database_and_no_session_context():
    """The signature's whole point: routing is pure.

    ``session_context`` is optional and the function is never handed a
    connection, which is what lets the popup classify on the UI thread
    before deciding whether a worker is needed at all.
    """
    assert classify("what are my tasks", None).kind == KIND_TASKS
    assert classify("what are my tasks").kind == KIND_TASKS


# ===========================================================================
# execute -- the lookup and the reply
# ===========================================================================


@pytest.fixture
def seeded(conn):
    """A small database with one project, one dated task in it, and two
    loose notes -- enough for every answer shape to have something real
    to find, and small enough that the counts in the answers are
    readable.
    """
    project, _created = get_or_create_project(conn, "Batcave")
    task_id = _task(conn, "Sensor wiring", due=TODAY)
    set_memory_project(conn, task_id, project.id)
    create_memory(conn, "About me", "my name is Bruce", "note")
    create_memory(conn, "Grapple hook", "spare line in the drawer", "note")
    return conn


def _answer(conn, text, context=None):
    """Classify and execute in one step, the way the controller does."""
    return execute(conn, classify(text, context), context)


def test_the_two_no_lookup_shapes_answer_without_a_connection():
    """``conn=None`` is a supported argument, not an accident: the
    controller deliberately skips opening anything for weight ``none``,
    so these two branches must come before the connection is touched.
    """
    caps = execute(None, classify("what can you do"))
    assert caps.text == ASSISTANT_CAPABILITIES
    assert caps.kind == KIND_CAPABILITIES
    assert caps.view_id is None

    view = execute(None, classify("open the trash"))
    assert view.text == ASSISTANT_ACTION_CONFIRM
    assert view.view_id == VIEW_TRASH


def test_a_profile_question_reads_the_fact_back_or_declines():
    """The fact is scraped out of an ordinary note ("my name is Bruce"),
    so the field asked for and the field found have to agree. A field
    with nothing on file declines rather than guessing from a
    near-match.
    """
    conn = get_connection(":memory:")
    init_db(conn)
    create_memory(conn, "About me", "my name is Bruce", "note")

    known = _answer(conn, "what's my name")
    assert "Bruce" in known.text
    assert known.weight == WEIGHT_TRIVIAL
    assert known.topic == "name"

    unknown = _answer(conn, "what's my shoe size")
    assert unknown.text == ASSISTANT_UNKNOWN_FIELD
    assert unknown.topic == "shoe size"
    conn.close()


def test_an_unqualified_task_question_answers_everything_still_open(seeded):
    """No day word means the whole open board -- today *and* upcoming --
    under the "still open" label, not today's tasks. A user with nothing
    due today and three overdue items must not be told they are clear.
    """
    _task(seeded, "Solder header", due=TOMORROW)
    answer = _answer(seeded, "what are my tasks")
    assert ASSISTANT_TASK_BUCKET_OPEN in answer.text
    assert "Sensor wiring" in answer.text
    assert "Solder header" in answer.text
    assert answer.kind == KIND_TASKS


def test_tomorrow_means_tomorrow_and_not_everything_up_to_it(seeded):
    """"due tomorrow" is the exact day, so today's task must not appear
    in it -- the bucket is ``due_on``, not a horizon."""
    _task(seeded, "Solder header", due=TOMORROW)
    answer = _answer(seeded, "what's due tomorrow")
    assert ASSISTANT_TASK_BUCKET_TOMORROW in answer.text
    assert "Solder header" in answer.text
    assert "Sensor wiring" not in answer.text

    today = _answer(seeded, "what tasks do i have today")
    assert ASSISTANT_TASK_BUCKET_TODAY in today.text
    assert "Sensor wiring" in today.text
    assert "Solder header" not in today.text


def test_asking_what_is_left_on_a_project_answers_without_going_anywhere(seeded):
    """A question about a project is verbal only.

    None of ``view_id`` / ``open_project_id`` / ``refresh_project_id``
    is set, which is what keeps the bubble open for a follow-up instead
    of raising the main window over whatever the user was doing.
    ``project_id`` still travels, because the *next* question may be
    "what about it?".
    """
    answer = _answer(seeded, "what's left on the Batcave project")
    assert "Sensor wiring" in answer.text
    assert answer.kind == KIND_PENDING_ON
    assert answer.project_id is not None
    assert answer.topic == "Batcave"
    assert answer.view_id is None
    assert answer.open_project_id is None
    assert answer.refresh_project_id is None


def test_asking_what_is_left_on_an_unknown_project_declines(seeded):
    """No project of that name: say so, and carry no id -- a follow-up
    must not inherit a reference the answer never established."""
    answer = _answer(seeded, "what's left on the Nowhere project")
    assert answer.text == ASSISTANT_PROJECT_UNKNOWN
    assert answer.project_id is None
    assert answer.results == []


def test_filing_into_a_project_writes_the_row_and_links_it(seeded):
    """The write actually happens, and the project it landed in is the
    one that was named."""
    before = list_tasks(seeded, "all")
    answer = _answer(seeded, "add a task to my Batcave project: check the lights")

    assert answer.kind == KIND_PROJECT_ACTION
    assert answer.weight == WEIGHT_TRIVIAL
    assert "Batcave" in answer.text
    assert "check the lights" in answer.text
    assert len(list_tasks(seeded, "all")) == len(before) + 1

    project = find_project_by_name(seeded, "Batcave")
    assert answer.project_id == project.id
    assert answer.memory_id is not None
    # The refresh id is set (the project changed) but the open id is
    # not: filing something is not a request to be taken anywhere.
    assert answer.refresh_project_id == project.id
    assert answer.open_project_id is None
    assert answer.view_id is None


def test_a_write_that_the_core_layer_refuses_reports_an_error_not_a_crash(seeded):
    """``add_entry_to_project`` raises on a blank body, and the branch
    catches it.

    :func:`classify` will not produce this shape -- a missing body is a
    capture -- but :func:`execute` also runs on a worker thread, where
    an escaping exception would surface as a dead bubble rather than a
    sentence. The classification is hand-built precisely because the
    classifier is not the only caller.
    """
    cls = Classification(
        OUTCOME_ACTION,
        WEIGHT_TRIVIAL,
        KIND_PROJECT_ACTION,
        text="add a task to my Batcave project:",
        payload={"name": "Batcave", "raw": "Batcave", "body": "", "entry_type": "task"},
    )
    answer = execute(seeded, cls)
    assert answer.text == ASSISTANT_ERROR
    assert answer.kind == KIND_PROJECT_ACTION
    assert answer.memory_id is None


def test_starting_the_same_project_twice_lands_on_one_row(seeded):
    """Resolve-or-create, not create.

    Asking for a project that already exists reports the existing one
    and says so differently -- otherwise a user who forgets they already
    started "Batcave" ends up with two of them and their entries split
    across both.
    """
    first = _answer(seeded, "start a new project called Batmobile")
    assert first.kind == KIND_PROJECT_CREATE
    assert first.project_id is not None
    assert first.refresh_project_id == first.project_id
    assert first.open_project_id is None

    names_after_first = [p.name for p in list_projects(seeded)]
    second = _answer(seeded, "start a new project called Batmobile")
    assert second.project_id == first.project_id
    assert second.text != first.text, (
        "an existing project reported as newly started"
    )
    assert [p.name for p in list_projects(seeded)] == names_after_first


def test_opening_a_project_switches_the_view_and_never_creates_one(seeded):
    """"open my X project" is navigation.

    It carries ``open_project_id`` -- the one shape that does -- and a
    name with nothing behind it declines by name rather than quietly
    creating an empty project to open.
    """
    known = _answer(seeded, "open my Batcave project")
    project = find_project_by_name(seeded, "Batcave")
    assert known.text == ASSISTANT_ACTION_CONFIRM
    assert known.open_project_id == project.id
    assert known.project_id == project.id
    assert known.refresh_project_id is None

    before = [p.name for p in list_projects(seeded)]
    unknown = _answer(seeded, "open my Nowhere project")
    assert unknown.open_project_id is None
    assert "Nowhere" in unknown.text
    assert [p.name for p in list_projects(seeded)] == before, (
        "opening an unknown project created it"
    )


def test_a_strict_roll_up_declines_instead_of_degrading_to_a_search(seeded):
    """The user said "project", so a name that resolves to nothing is a
    mistake worth naming -- searching for it instead would answer a
    question that was not asked, about entries that merely mention the
    word.
    """
    answer = _answer(seeded, "tell me about the Nowhere project")
    assert answer.kind == KIND_PROJECT_STATUS
    assert "Nowhere" in answer.text
    assert answer.open_project_id is None
    assert answer.project_id is None


def test_a_loose_roll_up_degrades_into_a_search(seeded):
    """"tell me about the grapple" might name a project or might name a
    thing. With no project by that name the answer comes back as a topic
    search -- the kind changes with it, so the popup renders it as
    results rather than as a project.
    """
    answer = _answer(seeded, "tell me about the grapple")
    assert answer.kind == KIND_TOPIC_SEARCH
    assert "Grapple hook" in answer.text
    assert answer.memory_id is not None
    assert answer.open_project_id is None


def test_a_known_roll_up_answers_and_takes_the_user_there(seeded):
    """The roll-up is the one *question* that opens a view.

    It is a summary of a whole project -- counts plus the outstanding
    work -- and the results behind it are worth looking at, so this
    shape both speaks and navigates. Contrast the pending-on question,
    which is verbal only.
    """
    answer = _answer(seeded, "tell me about the Batcave project")
    project = find_project_by_name(seeded, "Batcave")
    assert answer.kind == KIND_PROJECT_STATUS
    assert answer.weight == WEIGHT_SUBSTANTIAL
    assert "Batcave" in answer.text
    assert "Sensor wiring" in answer.text
    assert answer.open_project_id == project.id
    assert answer.project_id == project.id
    assert answer.results


def test_a_search_names_the_terms_it_actually_searched_for(seeded):
    """The stripped terms are quoted back, not the sentence.

    A user who gets "nothing on file about X" needs to see what X was
    reduced to -- that is how they discover the stop-word stripping ate
    the word they cared about.
    """
    hit = _answer(seeded, "do i have anything about grapple")
    assert "Grapple hook" in hit.text
    assert hit.kind == KIND_TOPIC_SEARCH
    assert hit.topic == "grapple"
    assert hit.results

    miss = _answer(seeded, "do i have anything about penguins")
    assert "penguins" in miss.text
    assert miss.results == []


def test_completing_a_task_writes_only_on_exactly_one_match(seeded):
    """Alfred picks the row on the user's behalf, so it may only pick
    when there is no choice to make.

    One match closes it and names it back; two ask which, because
    closing the wrong task is a silent data change the user has no
    reason to go looking for; none declines rather than filing the
    sentence as new work.
    """
    def _open_titles():
        return [
            mem.title
            for mem, _due, _done in list_tasks(seeded, "today")
            + list_tasks(seeded, "upcoming")
        ]

    # None -- nothing matches, and nothing is created either.
    before = _open_titles()
    missing = _answer(seeded, "I finished the batwing service")
    assert missing.text == ASSISTANT_TASK_DONE_NONE
    assert missing.memory_id is None
    assert _open_titles() == before

    # Two -- a question, not a completion, and both rows stay open.
    _task(seeded, "Sensor mount", due=TOMORROW)
    ambiguous = _answer(seeded, "I finished the sensor")
    assert "Sensor wiring" in ambiguous.text
    assert "Sensor mount" in ambiguous.text
    assert ambiguous.memory_id is None
    assert set(_open_titles()) == {"Sensor wiring", "Sensor mount"}

    # One -- the only branch that writes.
    done = _answer(seeded, "I finished the sensor wiring")
    assert "Sensor wiring" in done.text
    assert done.memory_id is not None
    assert done.topic == "Sensor wiring"
    assert _open_titles() == ["Sensor mount"]
    assert "Sensor wiring" in [
        mem.title for mem, _due, _done in list_tasks(seeded, "completed")
    ]


def test_last_worked_on_answers_from_the_project_table_first(seeded):
    """A project name appears in no memory's text.

    Searching for "Batcave" would find nothing, so the branch resolves
    the project first and answers from its newest entry -- reporting the
    project's name and the entry's date together.
    """
    answer = _answer(seeded, "when was the last time i worked on Batcave")
    project = find_project_by_name(seeded, "Batcave")
    assert "Batcave" in answer.text
    assert answer.kind == KIND_LAST_WORKED
    assert answer.project_id == project.id
    assert answer.memory_id is not None
    assert answer.results


def test_last_worked_on_falls_back_to_searching_the_entries(seeded):
    """Not everything the user works on is a project. A phrase that
    resolves to no project is searched, and the newest hit answers."""
    answer = _answer(seeded, "when was the last time i worked on the grapple")
    assert "Grapple hook" in answer.text
    assert answer.project_id is None
    assert answer.memory_id is not None


def test_yesterday_reads_the_day_before_and_not_today(seeded):
    """"what did i work on yesterday" is a calendar question.

    Today's entries -- every row the fixture just wrote -- must not
    appear in it, which is the whole failure mode: a query that used
    ``>=`` instead of the exact day would answer with everything.
    """
    empty = _answer(seeded, "what did i work on yesterday")
    assert empty.kind == KIND_YESTERDAY
    assert "Sensor wiring" not in empty.text
    assert empty.results == []

    # Backdate one row and ask again. Stored timestamps are UTC and the
    # query compares local days, so the value is written as yesterday
    # *local* noon converted to UTC -- midnight would land on the wrong
    # side of the date line in half the world's timezones.
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    seeded.execute(
        "UPDATE memories SET updated_at = datetime(?, 'utc') WHERE title = ?",
        (f"{yesterday} 12:00:00", "Grapple hook"),
    )
    seeded.commit()
    answer = _answer(seeded, "what did i work on yesterday")
    assert "Grapple hook" in answer.text
    assert answer.memory_id is not None
    assert "Sensor wiring" not in answer.text


def test_created_at_names_the_entry_and_declines_when_there_is_none(seeded):
    """The date question resolves the phrase to a single entry.

    "the grapple hook" is tried as typed before the stripped topic, so
    the answer quotes the row's real title rather than the user's
    paraphrase -- and an entry that does not exist is declined outright,
    since a date is not something to guess at.
    """
    answer = _answer(seeded, "when did i create the grapple hook")
    assert "Grapple hook" in answer.text
    assert answer.kind == KIND_CREATED_AT
    assert answer.memory_id is not None
    assert answer.results

    missing = _answer(seeded, "when did i create the batwing")
    assert missing.text == ASSISTANT_CREATED_UNKNOWN
    assert missing.memory_id is None


# ===========================================================================
# follow-ups -- the session context
# ===========================================================================


def test_a_follow_up_re_answers_the_last_project_without_reopening_it(seeded):
    """The point of the context: "what about it?" is cheap.

    The roll-up is rebuilt from the remembered id, at trivial weight
    (nothing has to be found), and deliberately without
    ``open_project_id`` -- the user is already looking at whatever the
    first answer opened.
    """
    project = find_project_by_name(seeded, "Batcave")
    ctx = SessionContext(last_shown_project_id=project.id)
    answer = _answer(seeded, "what about it?", ctx)
    assert answer.kind == KIND_CONTEXT_REUSE
    assert answer.weight == WEIGHT_TRIVIAL
    assert "Batcave" in answer.text
    assert answer.open_project_id is None


def test_a_follow_up_falls_back_to_the_last_memory(seeded):
    """With no project remembered but a memory shown, the follow-up
    reads that entry back instead."""
    mem_id = _answer(seeded, "when did i create the grapple hook").memory_id
    ctx = SessionContext(last_shown_memory_id=mem_id)
    answer = _answer(seeded, "tell me more about it", ctx)
    assert "Grapple hook" in answer.text
    assert answer.kind == KIND_CONTEXT_REUSE


def test_a_follow_up_pointing_at_a_deleted_row_says_it_does_not_know(seeded):
    """The context holds ids, and the rows behind them can go away --
    the Trash view is one click. A dangling reference declines rather
    than raising inside a worker thread.
    """
    ctx = SessionContext(last_shown_project_id=9999)
    assert _answer(seeded, "what about it?", ctx).text == ASSISTANT_CONTEXT_NONE

    ctx = SessionContext(last_shown_memory_id=9999)
    assert _answer(seeded, "what about it?", ctx).text == ASSISTANT_CONTEXT_NONE


def test_publishing_an_answer_records_what_it_was_about(seeded, qapp):
    """``_remember`` is the bookkeeping the next question depends on.

    A project answer and a memory answer are mutually exclusive -- the
    newer one clears the other, so a follow-up can never resolve against
    something two answers ago -- and the topic is kept for wording.
    """
    ctrl = _controller(seeded)
    try:
        ctrl.submit("tell me about the Batcave project")
        project = find_project_by_name(seeded, "Batcave")
        assert ctrl.context.last_shown_project_id == project.id
        assert ctrl.context.last_shown_memory_id is None

        ctrl.submit("when did i create the grapple hook")
        assert ctrl.context.last_shown_memory_id is not None
        assert ctrl.context.last_shown_project_id is None, (
            "a memory answer left the previous project as the reference"
        )

        ctrl.submit("do i have anything about wiring")
        assert ctrl.context.last_topic == "wiring"
    finally:
        ctrl.deleteLater()


def test_reset_context_forgets_the_last_exchange(seeded, qapp):
    """The popup resets between sessions, so a follow-up typed after a
    reopen must not resolve against what was on screen an hour ago."""
    ctrl = _controller(seeded)
    try:
        ctrl.submit("tell me about the Batcave project")
        assert ctrl.context.has_reference() is True
        ctrl.reset_context()
        assert ctrl.context.has_reference() is False
        assert ctrl.context.last_topic == ""
    finally:
        ctrl.deleteLater()


# ===========================================================================
# AssistantController -- the reveal
# ===========================================================================


def test_every_submission_announces_itself_but_only_substantial_work_thinks(
    seeded, qapp
):
    """Two signals, two different jobs.

    ``request_started`` fires for *everything*, captures included -- it
    is what clears the previous answer out of the bubble.
    ``thinking_started`` fires only where the classification allowed it,
    which is the whole two-axis design arriving at the widget.
    """
    ctrl = _controller(seeded)
    try:
        starts: list[int] = []
        thinks: list[int] = []
        ctrl.request_started.connect(lambda: starts.append(1))
        ctrl.thinking_started.connect(lambda: thinks.append(1))

        ctrl.submit("what can you do")          # weight none
        ctrl.submit("what's my name")           # trivial
        ctrl.submit("remember buy more solder")  # capture
        assert len(starts) == 3
        assert thinks == [], "a one-row lookup showed a thinking state"
        assert ctrl.thinking_engaged_count == 0
        assert ctrl.thinking_shown is False

        ctrl.submit("do i have anything about wiring")  # substantial
        assert len(starts) == 4
        assert len(thinks) == 1
        assert ctrl.thinking_engaged_count == 1
    finally:
        ctrl.deleteLater()


def test_a_capture_files_the_note_and_reports_its_id(seeded, qapp):
    """The capture path is the one that does not answer.

    ``captured`` carries the new row's id so the popup can offer to open
    it, and no ``answer_ready`` follows -- there is nothing to say back
    beyond the confirmation the widget draws itself.
    """
    ctrl = _controller(seeded)
    try:
        captured: list[int] = []
        answers: list[object] = []
        ctrl.captured.connect(captured.append)
        ctrl.answer_ready.connect(answers.append)

        ctrl.submit("the sensor drifts after twenty minutes")
        assert len(captured) == 1
        assert answers == []

        mem = get_memory(seeded, captured[0])
        assert mem is not None
        assert "sensor drifts" in mem.content
        # A capture is not something to refer back to with "what about
        # it?" -- it was filed, not shown.
        assert ctrl.context.last_shown_memory_id is None
    finally:
        ctrl.deleteLater()


def test_the_minimum_hold_parks_a_finished_answer_instead_of_sleeping(seeded, qapp):
    """The reveal is bounded, the query is not.

    A lookup that finishes in four milliseconds would flash a thinking
    state too briefly to read, so the *answer* waits on a single-shot
    timer -- nothing blocks, nothing sleeps, and the lookup itself was
    never slowed down. The timer firing is what publishes.
    """
    ctrl = _controller(seeded, min_hold_ms=5000)
    try:
        answers: list[object] = []
        ctrl.answer_ready.connect(answers.append)

        ctrl.submit("do i have anything about wiring")
        assert answers == [], "the answer beat the minimum hold"
        assert ctrl._hold_timer.isActive() is True
        assert ctrl._held_answer is not None

        # Fire the timer by hand rather than waiting five seconds.
        ctrl._on_hold_elapsed()
        assert len(answers) == 1
        assert answers[0].kind == KIND_TOPIC_SEARCH
        assert ctrl._held_answer is None
    finally:
        ctrl.deleteLater()


def test_a_newer_question_supersedes_one_still_being_held(seeded, qapp):
    """The user can type again while an answer is parked.

    Only the newest submission may publish: the stale one is dropped
    rather than arriving after its replacement, which would leave the
    bubble showing an answer to a question two questions ago.
    """
    ctrl = _controller(seeded, min_hold_ms=5000)
    try:
        answers: list[object] = []
        ctrl.answer_ready.connect(answers.append)

        ctrl.submit("do i have anything about wiring")
        assert ctrl._held_answer is not None

        ctrl.submit("what's my name")  # trivial, no hold
        assert len(answers) == 1
        assert "Bruce" in answers[0].text
        assert ctrl._held_answer is None
        assert ctrl._hold_timer.isActive() is False
    finally:
        ctrl.deleteLater()


def test_with_no_hold_configured_the_answer_publishes_at_once(seeded, qapp):
    """``min_hold_ms=0`` is the documented test path, and it must not
    merely shorten the wait -- the timer is never started at all, so
    nothing depends on an event loop running."""
    ctrl = _controller(seeded, min_hold_ms=0)
    try:
        answers: list[object] = []
        ctrl.answer_ready.connect(answers.append)
        ctrl.submit("do i have anything about wiring")
        assert len(answers) == 1
        assert ctrl._hold_timer.isActive() is False
    finally:
        ctrl.deleteLater()


def test_a_failed_lookup_reports_the_error_and_still_renders_something(seeded, qapp):
    """A worker exception has to reach the user as a sentence.

    ``failed`` carries the message for the log; the bubble still gets an
    ``AssistantAnswer`` it can draw, because an empty bubble after a
    visible thinking animation looks like the app hung.
    """
    ctrl = _controller(seeded)
    try:
        failures: list[str] = []
        answers: list[object] = []
        ctrl.failed.connect(failures.append)
        ctrl.answer_ready.connect(answers.append)

        ctrl.submit("do i have anything about wiring")
        answers.clear()
        ctrl._on_failed("database is locked", ctrl._request_id)

        assert failures == ["database is locked"]
        assert len(answers) == 1
        assert answers[0].text == ASSISTANT_ERROR
        assert answers[0].kind == KIND_FALLBACK_SEARCH
    finally:
        ctrl.deleteLater()


def test_an_in_memory_database_is_looked_up_inline_even_when_threaded(qapp):
    """Threading is conditional on there being a file to reopen.

    A worker opens its *own* connection to the same path, so an
    in-memory database would hand it a different, empty one. The
    controller checks the path first and runs inline when there is none
    -- which is also what makes the in-memory fixtures usable at all.
    """
    conn = get_connection(":memory:")
    init_db(conn)
    create_memory(conn, "Wiring", "the sensor wiring is done", "note")

    ctrl = AssistantController(
        conn, SessionContext(), threaded=True, min_hold_ms=0
    )
    try:
        assert ctrl._db_path is None
        answers: list[object] = []
        ctrl.answer_ready.connect(answers.append)
        ctrl.submit("do i have anything about wiring")
        # Synchronous: the answer is already here, with no event loop
        # spun and no thread pool waited on.
        assert len(answers) == 1
        assert "Wiring" in answers[0].text
    finally:
        ctrl.deleteLater()
        conn.close()


def test_connection_path_is_what_decides_threaded_from_inline(tmp_path):
    """The one function the threading decision rests on.

    A file-backed connection reports its path (the worker can reopen
    it); an in-memory one reports ``None`` (it cannot).
    """
    db = tmp_path / "alfred_path_probe.db"
    conn = get_connection(str(db))
    init_db(conn)
    try:
        assert connection_path(conn) == str(db)
    finally:
        conn.close()

    mem = get_connection(":memory:")
    init_db(mem)
    try:
        assert connection_path(mem) is None
    finally:
        mem.close()
