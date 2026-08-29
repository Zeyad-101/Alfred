"""Tests for ``ui.assistant_response`` -- the response templates.

Every function in that module is a plain string builder: data in,
finished sentence out, no Qt and no database. That makes the part of
the butler layer most likely to read wrong -- the English -- testable
on its own, which is the whole reason the templates were split out of
the controller.

What is actually being pinned here is grammar and boundary counts:
zero / one / two / three-plus items, singular versus plural nouns, the
serial comma, the overflow element, and the "empty" branch of every
summary. The exact wording lives in :mod:`ui.strings` and is compared
against the constants rather than retyped, so a copy edit there is not
a test failure -- but dropping a placeholder, or losing the plural
form, is.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ui import assistant_response as R
from ui.strings import (
    ASSISTANT_CONTEXT_MEMORY,
    ASSISTANT_CREATED_AT,
    ASSISTANT_LAST_WORKED,
    ASSISTANT_LIST_AND,
    ASSISTANT_NO_MATCH,
    ASSISTANT_PENDING_NONE,
    ASSISTANT_PROFILE_FACT,
    ASSISTANT_PROJECT_ALL_DONE,
    ASSISTANT_PROJECT_EXISTS,
    ASSISTANT_PROJECT_OUTSTANDING,
    ASSISTANT_PROJECT_STARTED,
    ASSISTANT_PROJECT_STATUS_EMPTY,
    ASSISTANT_PROJECT_UNKNOWN,
    ASSISTANT_TASK_DONE_NONE,
    ASSISTANT_TASKS_NONE,
    ASSISTANT_YESTERDAY_NONE,
)


# --- pluralize -------------------------------------------------------------


def test_pluralize_picks_the_singular_only_for_one():
    """Exactly 1 takes the singular; 0 and 2+ take the plural.

    Zero is the case worth pinning: "0 task" reads as a bug even
    though it is the count the caller passed.
    """
    assert R.pluralize(1, "task", "tasks") == "1 task"
    assert R.pluralize(0, "task", "tasks") == "0 tasks"
    assert R.pluralize(2, "task", "tasks") == "2 tasks"
    assert R.pluralize(17, "entry", "entries") == "17 entries"


# --- join_list -------------------------------------------------------------


def test_join_list_empty_is_empty_string():
    """No items joins to "" -- callers test the list, not this result,
    to decide whether to use an "empty" template at all."""
    assert R.join_list([]) == ""
    # Whitespace-only members are dropped, so a list of them is empty too.
    assert R.join_list(["", "   ", "\t"]) == ""


def test_join_list_one_item_stays_bare():
    assert R.join_list(["Wiring"]) == "Wiring"


def test_join_list_two_items_take_and_with_no_comma():
    """Two items are joined with "and" alone. A comma before "and" in a
    two-element list is the classic template-off-by-one tell."""
    joined = R.join_list(["Wiring", "Sensor"])
    assert joined == f"Wiring {ASSISTANT_LIST_AND} Sensor"
    assert "," not in joined


def test_join_list_three_items_take_the_serial_comma():
    assert (
        R.join_list(["A", "B", "C"]) == f"A, B, {ASSISTANT_LIST_AND} C"
    )


def test_join_list_overflow_becomes_a_more_element():
    """Past ``max_items`` the tail collapses into one "N more" element,
    which then takes the list grammar like any other member -- so the
    sentence still reads as a list rather than trailing off."""
    joined = R.join_list(["A", "B", "C", "D", "E"], max_items=3)
    assert joined == f"A, B, C, {ASSISTANT_LIST_AND} 2 more"
    # The dropped titles are genuinely gone, not merely unmentioned.
    assert "D" not in joined and "E" not in joined


def test_join_list_at_exactly_max_items_has_no_overflow():
    """The boundary: as many items as the cap allows names all of them
    and adds no "0 more"."""
    joined = R.join_list(["A", "B", "C"], max_items=3)
    assert joined == f"A, B, {ASSISTANT_LIST_AND} C"
    assert "more" not in joined


def test_join_list_strips_and_stringifies_members():
    """Members are stripped, and a non-string is coerced rather than
    raising -- a title always comes from the database, but a count or
    an id occasionally gets passed by a caller in a hurry."""
    assert R.join_list(["  Wiring  ", 42]) == f"Wiring {ASSISTANT_LIST_AND} 42"


# --- format_when -----------------------------------------------------------


def test_format_when_renders_day_month_year():
    assert R.format_when("2026-03-05T09:30:00") == "5 March 2026"


def test_format_when_converts_utc_to_local_before_taking_the_date():
    """Stored timestamps are UTC; the user thinks in local days. A
    tz-aware value is converted first, so an evening-UTC entry does not
    read as tomorrow (or yesterday) on the wrong side of midnight.

    Asserted against the conversion Python itself computes rather than
    a fixed string, since the test machine's zone is not ours to pick.
    """
    raw = "2026-03-05T23:30:00+00:00"
    expected_day = (
        datetime.fromisoformat(raw).astimezone()
    )
    assert R.format_when(raw) == (
        f"{expected_day.day} {expected_day.strftime('%B %Y')}"
    )


def test_format_when_accepts_a_trailing_z():
    """"...Z" is valid ISO-8601 but not accepted by
    ``datetime.fromisoformat`` on every version, so the builder swaps it
    for an explicit offset first."""
    aware = datetime(2026, 3, 5, 12, 0, tzinfo=timezone.utc).astimezone()
    assert R.format_when("2026-03-05T12:00:00Z") == (
        f"{aware.day} {aware.strftime('%B %Y')}"
    )


def test_format_when_none_is_empty_and_garbage_falls_back():
    """Neither branch may raise: these values reach the templates from
    database columns that are nullable and, for imported rows, not
    strictly validated."""
    assert R.format_when(None) == ""
    # Unparseable: the leading date-shaped portion is returned as-is.
    assert R.format_when("not a timestamp at all") == "not a times"[:10]
    assert R.format_when("2026-13-45") == "2026-13-45"


# --- single-fact answers ---------------------------------------------------


def test_build_profile_fact_uses_the_template_and_trims_a_full_stop():
    """The stored value keeps its words but loses a trailing period --
    the template supplies the sentence's own punctuation, and "Your name
    is Bruce., sir." is what happens when it doesn't."""
    assert R.build_profile_fact("  name  ", " Bruce. ") == (
        ASSISTANT_PROFILE_FACT.format(field="name", value="Bruce")
    )


def test_build_created_at_and_last_worked_name_the_entry_and_the_day():
    assert R.build_created_at("Grapple hook", "2026-03-05T09:30:00") == (
        ASSISTANT_CREATED_AT.format(title="Grapple hook", when="5 March 2026")
    )
    assert R.build_last_worked_on("Grapple hook", "2026-03-05T09:30:00") == (
        ASSISTANT_LAST_WORKED.format(title="Grapple hook", when="5 March 2026")
    )


def test_build_context_memory_reads_back_what_was_last_shown():
    assert R.build_context_memory("Batmobile", "2026-03-05T09:30:00") == (
        ASSISTANT_CONTEXT_MEMORY.format(
            title="Batmobile", when="5 March 2026"
        )
    )


# --- search summary --------------------------------------------------------


def test_build_search_summary_zero_uses_the_no_match_template():
    """Zero hits is its own sentence, not "0 entries on X, sir: ."."""
    assert R.build_search_summary("grapples", 0, []) == (
        ASSISTANT_NO_MATCH.format(topic="grapples")
    )
    # A negative count (never expected, but cheap to survive) is the
    # same branch rather than a crash.
    assert R.build_search_summary("grapples", -1, []) == (
        ASSISTANT_NO_MATCH.format(topic="grapples")
    )


def test_build_search_summary_one_hit_is_singular():
    text = R.build_search_summary("grapples", 1, ["Grapple hook"])
    assert "1 entry" in text
    assert "1 entries" not in text
    assert "Grapple hook" in text


def test_build_search_summary_counts_all_hits_but_names_only_three():
    """The count is the size of the whole result set; the names are
    capped. The two numbers disagreeing on purpose is the point -- "12
    entries ... A, B, C, and 9 more" tells the user both how much there
    is and what the top of it looks like.
    """
    titles = [f"Note {i}" for i in range(12)]
    text = R.build_search_summary("wiring", 12, titles)
    assert "12 entries" in text
    assert "Note 0" in text and "Note 1" in text and "Note 2" in text
    assert "Note 3" not in text
    assert "9 more" in text


# --- task buckets ----------------------------------------------------------


def test_build_task_list_summary_empty_bucket_says_nothing_due():
    assert R.build_task_list_summary("due today", []) == (
        ASSISTANT_TASKS_NONE.format(bucket_label="due today")
    )


def test_build_task_list_summary_counts_the_titles_it_was_given():
    """Unlike the search summary this has no separate count argument --
    the number spoken is the length of the list, so it can never
    disagree with the names."""
    text = R.build_task_list_summary("still open", ["Wiring", "Sensor"])
    assert "2 tasks" in text
    assert "still open" in text
    assert f"Wiring {ASSISTANT_LIST_AND} Sensor" in text


def test_build_task_list_summary_caps_the_named_titles():
    text = R.build_task_list_summary(
        "still open", ["A", "B", "C", "D"], max_titles=3
    )
    assert "4 tasks" in text
    assert "1 more" in text
    assert "D" not in text.replace("due", "")  # no stray D from the label


# --- task completion -------------------------------------------------------


def test_build_task_completed_names_the_row_it_closed():
    """Echoing the title is how the user catches a wrong match: Alfred
    chose the row on their behalf."""
    assert "Sensor wiring" in R.build_task_completed("  Sensor wiring  ")


def test_build_task_completion_ambiguous_lists_candidates_and_asks():
    """Two matches must produce a question, never a completion. The
    builder cannot write, so what is pinned here is that both candidate
    names reach the sentence."""
    text = R.build_task_completion_ambiguous(["Sensor wiring", "Sensor mount"])
    assert "Sensor wiring" in text
    assert "Sensor mount" in text


def test_build_task_completion_none_is_a_decline():
    assert R.build_task_completion_none() == ASSISTANT_TASK_DONE_NONE


# --- pending / yesterday ---------------------------------------------------


def test_build_pending_items_summary_empty_is_nothing_outstanding():
    assert R.build_pending_items_summary("ESP32", []) == (
        ASSISTANT_PENDING_NONE.format(topic="ESP32")
    )


def test_build_pending_items_summary_names_the_open_work():
    text = R.build_pending_items_summary("ESP32", ["Solder header"])
    assert "1 task" in text
    assert "ESP32" in text
    assert "Solder header" in text


def test_build_yesterday_summary_both_branches():
    assert R.build_yesterday_summary([]) == ASSISTANT_YESTERDAY_NONE
    text = R.build_yesterday_summary(["Wiring", "Sensor", "Mount", "Case"])
    assert "Wiring" in text and "1 more" in text


# --- project status --------------------------------------------------------


def test_build_project_status_empty_project_gets_its_own_sentence():
    """No memories and no open tasks: the roll-up sentence would be all
    zeroes, so a project with nothing in it says so instead."""
    assert R.build_project_status("Batcave", 0, {"open": 0}, []) == (
        ASSISTANT_PROJECT_STATUS_EMPTY.format(project="Batcave")
    )


def test_build_project_status_speaks_open_tasks_and_not_done_ones():
    """Only the open count is spoken. ``done`` travels in the mapping
    because the caller has it, but what the user needs to know is
    whether the project wants them.
    """
    text = R.build_project_status(
        "Batcave", 5, {"open": 2, "done": 40}, ["Wiring", "Sensor"]
    )
    assert "5 memories" in text
    assert "2 open tasks" in text
    assert "40" not in text
    assert ASSISTANT_PROJECT_OUTSTANDING.format(
        titles=f"Wiring {ASSISTANT_LIST_AND} Sensor"
    ) in text


def test_build_project_status_singular_nouns_at_one():
    text = R.build_project_status("Batcave", 1, {"open": 1}, ["Wiring"])
    assert "1 memory" in text
    assert "1 open task" in text
    assert "1 memories" not in text and "1 open tasks" not in text


def test_build_project_status_no_open_work_ends_with_all_done():
    """A non-empty project with nothing outstanding still gets a second
    clause -- the sentence would otherwise stop on the counts and read
    as though the list had been cut off."""
    text = R.build_project_status("Batcave", 3, {"open": 0}, [])
    assert text.endswith(ASSISTANT_PROJECT_ALL_DONE)


def test_build_project_status_tolerates_a_missing_or_null_open_count():
    """``task_counts`` is assembled by the caller from two queries; a
    missing or ``None`` value must read as zero rather than raise inside
    a template."""
    assert R.build_project_status("Batcave", 0, {}, []) == (
        ASSISTANT_PROJECT_STATUS_EMPTY.format(project="Batcave")
    )
    assert R.build_project_status("Batcave", 0, {"open": None}, []) == (
        ASSISTANT_PROJECT_STATUS_EMPTY.format(project="Batcave")
    )


# --- project decline / writes ---------------------------------------------


def test_build_project_unknown_names_the_spelling_it_looked_for():
    text = R.build_project_unknown("Batcaev")
    assert "Batcaev" in text


def test_build_project_unknown_falls_back_when_there_is_no_name():
    """An empty name has nothing to quote back, so the generic decline
    is used rather than a sentence with a hole in it."""
    assert R.build_project_unknown("") == ASSISTANT_PROJECT_UNKNOWN
    assert R.build_project_unknown("   ") == ASSISTANT_PROJECT_UNKNOWN
    assert R.build_project_unknown(None) == ASSISTANT_PROJECT_UNKNOWN


def test_build_project_added_distinguishes_a_brand_new_project():
    """Filing into an existing project and filing into one that had to
    be created are different events, and the second is worth saying --
    it is the only notice the user gets that a project now exists.
    """
    into_existing = R.build_project_added("ESP32", "Solder header", False)
    into_new = R.build_project_added("ESP32", "Solder header", True)
    assert into_existing != into_new
    for text in (into_existing, into_new):
        assert "ESP32" in text
        assert "Solder header" in text


def test_build_project_created_reports_resolution_rather_than_claiming_a_new_one():
    """``created=False`` means the name resolved to a project already on
    file. Saying "started" then would be a lie, and would hide the
    resolution that just saved the user a near-duplicate.
    """
    assert R.build_project_created("Batcave", True) == (
        ASSISTANT_PROJECT_STARTED.format(project="Batcave")
    )
    assert R.build_project_created("Batcave", False) == (
        ASSISTANT_PROJECT_EXISTS.format(project="Batcave")
    )


def test_every_builder_returns_a_non_empty_string_for_ordinary_input():
    """A blanket guard: a template that lost its return, or that
    formats to "", shows up in the bubble as a blank answer rather than
    an error, which is the failure mode most likely to ship unnoticed.
    """
    when = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    results = [
        R.build_profile_fact("name", "Bruce"),
        R.build_created_at("Note", when),
        R.build_last_worked_on("Note", when),
        R.build_context_memory("Note", when),
        R.build_search_summary("topic", 2, ["A", "B"]),
        R.build_task_list_summary("due today", ["A"]),
        R.build_task_completed("A"),
        R.build_task_completion_ambiguous(["A", "B"]),
        R.build_task_completion_none(),
        R.build_pending_items_summary("P", ["A"]),
        R.build_yesterday_summary(["A"]),
        R.build_project_status("P", 2, {"open": 1}, ["A"]),
        R.build_project_unknown("P"),
        R.build_project_added("P", "A", True),
        R.build_project_created("P", True),
    ]
    for text in results:
        assert isinstance(text, str) and text.strip()
