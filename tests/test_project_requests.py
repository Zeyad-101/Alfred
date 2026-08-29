"""Tests for the project seam of the butler layer.

Three different sentence shapes mention a project, they are handled in
three different places, and the whole design depends on them agreeing:

* a *capture* with a trailing project phrase ("add buy solder to the
  Zeyad project") -- :func:`ui.assistant_controller.parse_project_assignment`
  splits the phrase off, the note keeps only what was said, and the link
  is made afterwards;
* an explicit *write* ("add a task to my ESP32 project: test the
  sensor") -- classified as an action and executed through
  :func:`core.projects.add_entry_to_project`;
* a *question* ("what's left on the ESP32 project") -- a lookup that
  answers out loud and navigates nowhere.

What they share is one resolution rule:
:func:`core.projects.find_project_by_name`, substring and
case-insensitive, reached through
:func:`core.projects.get_or_create_project` so that resolving and
creating can never disagree. That is the failure this file mostly
guards: a name that resolves for the question layer but not for the
capture layer silently produces two projects with the user's entries
split between them, and nothing raises.

The controller is driven with ``threaded=False`` / ``min_hold_ms=0`` so
the write has happened by the time ``submit`` returns; the routing is
the same code either way.
"""
from __future__ import annotations

import pytest

from core.memory import create_memory, get_memory
from core.projects import (
    create_project,
    find_project_by_name,
    get_or_create_project,
    list_project_memories,
    list_projects,
    set_memory_project,
)
from ui.assistant_controller import (
    KIND_PENDING_ON,
    KIND_PROJECT_ACTION,
    KIND_PROJECT_CREATE,
    WEIGHT_TRIVIAL,
    AssistantController,
    SessionContext,
    classify,
    execute,
    parse_project_assignment,
)


# --- helpers ---------------------------------------------------------------


@pytest.fixture
def ctrl(conn, qapp):
    """A controller whose lookups and writes run inline."""
    controller = AssistantController(
        conn, SessionContext(), threaded=False, min_hold_ms=0
    )
    yield controller
    controller.deleteLater()


def _capture(ctrl, text: str) -> int:
    """Submit ``text`` as a capture and return the row it filed."""
    filed: list[int] = []
    ctrl.captured.connect(filed.append)
    ctrl.submit(text)
    assert len(filed) == 1, f"{text!r} was not filed as a capture"
    return filed[0]


# --- a capture that names its project --------------------------------------


def test_a_filing_verb_is_not_part_of_the_note(conn, ctrl):
    """"add buy solder to the Zeyad project" files "buy solder".

    The leading verb addressed Alfred; it is not what the user wanted
    written down. Keeping it titled the entry "add buy solder", which is
    the kind of wrong that survives forever because it looks like the
    user typed it that way.

    Stripping is only safe *because* the sentence named a project
    outright -- that trailing phrase is what makes the verb an
    instruction rather than a word in a note.
    """
    content, name = parse_project_assignment("add buy solder to the Zeyad project")
    assert content == "buy solder"
    assert name == "Zeyad"

    memory_id = _capture(ctrl, "add buy solder to the Zeyad project")
    mem = get_memory(conn, memory_id)
    assert mem.title == "buy solder"
    assert mem.content == "buy solder"

    # ...and it landed in the project it named.
    project = find_project_by_name(conn, "Zeyad")
    assert project is not None
    assert [m.title for m in list_project_memories(conn, project.id)] == [
        "buy solder"
    ]


@pytest.mark.parametrize(
    "text,content",
    [
        ("add buy solder to the Zeyad project", "buy solder"),
        ("put a task in the Zeyad project", "put a task in the Zeyad project"),
        ("log the drift under the Zeyad project", "the drift"),
        ("file a reminder for the Zeyad project", "file a reminder for the Zeyad project"),
    ],
)
def test_a_filing_verb_only_goes_when_something_is_left_behind(text, content):
    """The guard on the stripping.

    "put a task in the Zeyad project" is all instruction and no note --
    removing the verb and the phrase would leave nothing, so the whole
    line is kept instead. A clumsy sentence in the right project beats
    an empty entry in it.
    """
    assert parse_project_assignment(text)[0] == content


def test_a_capture_without_a_project_phrase_is_left_exactly_as_typed():
    """Every capture that does not end this way must behave as it did
    before the feature existed -- the parse is a no-op, not a rewrite."""
    for text in ("buy more solder", "add milk", "remember the sensor drifts"):
        assert parse_project_assignment(text) == (text, None)


def test_a_demonstrative_is_not_a_project_name(conn, ctrl):
    """"to this project" names nothing resolvable, so the phrase stays in
    the note and no project is invented from the word "this"."""
    assert parse_project_assignment("buy solder to this project") == (
        "buy solder to this project", None
    )
    _capture(ctrl, "buy solder to this project")
    assert [p.name for p in list_projects(conn)] == []


# --- one resolution rule, shared by every shape ----------------------------


def test_a_partial_name_resolves_instead_of_near_duplicating(conn, ctrl):
    """The user says "the batcave project"; the project is called
    "Batcave Renovations".

    Resolution is case-insensitive substring matching, and the *write*
    path goes through the same function the question path does. If it
    did not, this capture would create a second project called
    "batcave" and every later question would answer from only one of
    them.
    """
    create_project(conn, "Batcave Renovations")

    resolved = find_project_by_name(conn, "batcave")
    assert resolved.name == "Batcave Renovations"

    ctrl.submit("add a task to my batcave project: check the lights")
    assert [p.name for p in list_projects(conn)] == ["Batcave Renovations"]
    assert "check the lights" in [
        m.title for m in list_project_memories(conn, resolved.id)
    ]

    # And the capture path resolves it the same way.
    _capture(ctrl, "add buy solder to the batcave project")
    assert [p.name for p in list_projects(conn)] == ["Batcave Renovations"]


def test_the_shortest_matching_name_wins_a_shared_fragment(conn):
    """With two candidates an exact name always wins; failing that the
    shortest does, on the reasoning that "bat" means "Bats" rather than
    "Batcave Renovations" when both are on file."""
    create_project(conn, "Batcave Renovations")
    create_project(conn, "Bats")
    assert find_project_by_name(conn, "bat").name == "Bats"
    # An exact (case-insensitive) name beats a shorter substring hit.
    assert find_project_by_name(
        conn, "batcave renovations"
    ).name == "Batcave Renovations"


def test_asking_twice_does_not_leave_two_projects(conn, ctrl):
    """"start a new project called Batcave", said twice.

    The second one resolves to the first rather than inserting a
    near-duplicate, and says something different so the user can see
    that it did. Reporting "started" twice would hide the resolution
    that just saved them.
    """
    first = execute(conn, classify("start a new project called Batcave"))
    second = execute(conn, classify("start a new project called Batcave"))
    assert first.kind == second.kind == KIND_PROJECT_CREATE
    assert [p.name for p in list_projects(conn)] == ["Batcave"]
    assert first.project_id == second.project_id
    assert first.text != second.text, (
        "the second request claimed to have started a project that "
        "already existed"
    )

    # ...and the same holds through the controller, which is how the
    # request actually arrives.
    ctrl.submit("start a new project called Batcave")
    ctrl.submit("start a new project named batcave")
    assert [p.name for p in list_projects(conn)] == ["Batcave"]


# --- a question leaves the bubble open; an action closes it -----------------


@pytest.mark.parametrize(
    "text",
    [
        "what's left on ESP32?",
        "what tasks are left on the ESP32 project?",
        "show me tasks on ESP32",
        "what do i have left on ESP32?",
    ],
)
def test_a_verbal_only_question_keeps_the_bubble_open(conn, text):
    """Four phrasings, one answer -- and none of them navigates.

    A question about a project is answered *in the bubble*: the user
    asked what was left, not to be taken to the Projects view. So none
    of ``view_id``, ``open_project_id`` or ``refresh_project_id`` is
    set. ``project_id`` still is, because a follow-up ("what about the
    notes on it?") has to know what "it" meant.
    """
    project, _created = get_or_create_project(conn, "ESP32")
    task_id = create_memory(conn, "Solder header", "solder header", "task")
    set_memory_project(conn, task_id, project.id)

    result = classify(text)
    assert result.kind == KIND_PENDING_ON

    answer = execute(conn, result)
    assert "Solder header" in answer.text
    assert answer.view_id is None
    assert answer.open_project_id is None, "a question navigated away"
    assert answer.refresh_project_id is None, "a question claimed to be a write"
    assert answer.project_id == project.id


def test_adding_to_an_existing_project_writes_and_links_the_row(conn, ctrl):
    """The explicit write shape: verb, noun, project, separator, body.

    The body after the colon becomes the row; the project it names is
    resolved, not created. ``refresh_project_id`` is set -- something
    changed, so redraw it if it happens to be on screen -- while
    ``open_project_id`` stays clear, because a write should not yank the
    user out of whatever they were doing.
    """
    project, _created = get_or_create_project(conn, "ESP32")
    before = list_projects(conn)

    answer = execute(
        conn, classify("add a note to the ESP32 project: order more headers")
    )
    assert answer.kind == KIND_PROJECT_ACTION
    assert answer.refresh_project_id == project.id
    assert answer.open_project_id is None
    assert answer.memory_id is not None

    filed = get_memory(conn, answer.memory_id)
    assert filed.title == "order more headers"
    assert "order more headers" in [
        m.title for m in list_project_memories(conn, project.id)
    ]
    # Resolved, never created.
    assert [p.name for p in list_projects(conn)] == [p.name for p in before]


def test_a_write_naming_a_project_that_does_not_exist_creates_it(conn):
    """The write shape is allowed to create -- filing into a project the
    user names is how projects come into being in ordinary use.

    This is the deliberate difference from ``open my X project``, which
    only ever navigates: a typo there should decline, not silently
    manufacture an empty project to open.
    """
    assert find_project_by_name(conn, "Batcave") is None

    answer = execute(
        conn, classify("add a task to my Batcave project: check the lights")
    )
    assert answer.kind == KIND_PROJECT_ACTION
    created = find_project_by_name(conn, "Batcave")
    assert created is not None
    assert [m.title for m in list_project_memories(conn, created.id)] == [
        "check the lights"
    ]


def test_the_write_shapes_are_trivial_so_no_thinking_state_shows(conn):
    """Both writes resolve one indexed row and insert one.

    They are classified trivial, which is what keeps "Lemme check, sir."
    off the screen for work that finishes before the sentence could be
    read.
    """
    for text in (
        "add a note to the ESP32 project: order more headers",
        "start a new project called Batcave",
    ):
        result = classify(text)
        assert result.weight == WEIGHT_TRIVIAL, text
        assert result.may_show_thinking is False, text


def test_all_three_shapes_land_on_the_same_project(conn, ctrl):
    """The guard the whole file exists for.

    A capture with a trailing project phrase, an explicit write, and a
    question all name the project the same loose way -- and all three
    have to resolve to one row. Three shapes reaching three different
    projects is silent: each answer is individually correct, and the
    user's entries are quietly split three ways.
    """
    project, _created = get_or_create_project(conn, "Batcave Renovations")

    _capture(ctrl, "add buy solder to the batcave project")
    execute(conn, classify("add a task to my batcave project: check the lights"))
    answer = execute(conn, classify("what's left on batcave?"))

    assert [p.name for p in list_projects(conn)] == ["Batcave Renovations"]
    assert answer.project_id == project.id
    assert sorted(
        m.title for m in list_project_memories(conn, project.id)
    ) == ["buy solder", "check the lights"]
