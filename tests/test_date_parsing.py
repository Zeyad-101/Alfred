"""Tests for the relative-date parser (``core.date_parsing``).

Every case pins a fixed ``reference_date`` rather than the system
clock — the weekday arithmetic is the whole point of the module, and a
test whose expected answer depends on the day it runs on is a test that
tells you nothing on a Tuesday.

The reference dates below are real 2026 calendar days:

* 2026-06-15 is a **Monday**
* 2026-06-19 is a **Friday**
* 2026-06-21 is a **Sunday**
"""
from __future__ import annotations

from datetime import date

import pytest

from core.date_parsing import canonical_day_word, parse_relative_date


MONDAY = date(2026, 6, 15)
FRIDAY = date(2026, 6, 19)
SUNDAY = date(2026, 6, 21)


def test_reference_dates_are_the_weekdays_the_tests_claim():
    """Guard the fixtures themselves.

    Every expectation below is built on these three dates being Monday,
    Friday and Sunday. If someone edits a constant, this fails first and
    says why, instead of a dozen date-math assertions failing obscurely.
    """
    assert MONDAY.weekday() == 0
    assert FRIDAY.weekday() == 4
    assert SUNDAY.weekday() == 6


# ---------- today / tomorrow ----------


class TestTodayAndTomorrow:
    def test_today(self):
        assert parse_relative_date("do it today", MONDAY) == MONDAY

    def test_tomorrow(self):
        assert parse_relative_date("do it tomorrow", MONDAY) == date(
            2026, 6, 16
        )

    def test_tomorrow_crosses_a_month_boundary(self):
        assert parse_relative_date("tomorrow", date(2026, 6, 30)) == date(
            2026, 7, 1
        )

    def test_case_is_irrelevant(self):
        assert parse_relative_date("TODAY", MONDAY) == MONDAY
        assert parse_relative_date("ToMoRrOw", MONDAY) == date(2026, 6, 16)

    def test_the_phrase_can_sit_anywhere_in_the_sentence(self):
        assert (
            parse_relative_date("I have a meeting tomorrow at 6pm", MONDAY)
            == date(2026, 6, 16)
        )


# ---------- bare weekday: nearest upcoming ----------


class TestBareWeekday:
    def test_later_this_week(self):
        # Monday → "friday" is this Friday, four days out.
        assert parse_relative_date("call them friday", MONDAY) == FRIDAY

    def test_todays_own_weekday_means_today(self):
        # Standing on a Friday, "friday" is the one you are in.
        assert parse_relative_date("ship it friday", FRIDAY) == FRIDAY

    def test_earlier_weekday_wraps_to_next_week(self):
        # Friday → "monday" has already passed this week, so it is the
        # coming Monday, three days out.
        assert parse_relative_date("monday", FRIDAY) == date(2026, 6, 22)

    def test_sunday_from_sunday_is_today(self):
        assert parse_relative_date("sunday", SUNDAY) == SUNDAY

    def test_every_weekday_name_is_recognized(self):
        names = (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
        for offset, name in enumerate(names):
            # From a Monday, weekday N is exactly N days out.
            assert parse_relative_date(name, MONDAY) == MONDAY.replace(
                day=15 + offset
            ), name

    def test_case_is_irrelevant(self):
        assert parse_relative_date("Friday", MONDAY) == FRIDAY
        assert parse_relative_date("FRIDAY", MONDAY) == FRIDAY


# ---------- "next <weekday>": nearest strictly future ----------


class TestNextWeekday:
    def test_agrees_with_the_bare_form_on_a_different_day(self):
        # From a Monday, both "friday" and "next friday" are this Friday.
        assert parse_relative_date("next friday", MONDAY) == FRIDAY
        assert parse_relative_date("friday", MONDAY) == FRIDAY

    def test_on_the_same_weekday_it_means_the_following_one(self):
        # This is the one case where the two forms differ: on a Friday,
        # "friday" is today but "next friday" is a week out.
        assert parse_relative_date("next friday", FRIDAY) == date(2026, 6, 26)
        assert parse_relative_date("friday", FRIDAY) == FRIDAY

    def test_extra_whitespace_between_the_words_is_tolerated(self):
        assert parse_relative_date("next   friday", MONDAY) == FRIDAY

    def test_the_qualified_form_wins_over_the_bare_name_inside_it(self):
        """"next friday" must not be read as the bare "friday".

        The distinction only shows up when the two answers differ, which
        is exactly the same-weekday case.
        """
        assert parse_relative_date("let us meet next friday", FRIDAY) == date(
            2026, 6, 26
        )


# ---------- unrecognized ----------


class TestUnrecognized:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "buy milk",
            "remember the ESP32 pinout",
            "the meeting went well",
        ],
    )
    def test_returns_none(self, text):
        assert parse_relative_date(text, MONDAY) is None

    def test_none_input_is_tolerated(self):
        assert parse_relative_date(None, MONDAY) is None

    @pytest.mark.parametrize(
        "text",
        ["on the 3rd", "March 5", "5/3", "2026-06-20", "in two weeks"],
    )
    def test_numeric_and_absolute_dates_are_out_of_scope(self, text):
        """Deliberately unrecognized — see the module docstring.

        Guessing at "5/3" (May 3rd? 5 March?) is precisely the silent
        wrong answer the parser is written to avoid; the editor's date
        field is there for these.
        """
        assert parse_relative_date(text, MONDAY) is None

    @pytest.mark.parametrize("text", ["sat down at the desk", "sun exposure"])
    def test_weekday_abbreviations_do_not_fire(self, text):
        """No abbreviations, on purpose.

        ``sat`` would match "I sat down" and ``sun`` "sun exposure". A
        false due date is a worse failure than a missed one.
        """
        assert parse_relative_date(text, MONDAY) is None

    @pytest.mark.parametrize("text", ["todays plan", "on sundays I rest"])
    def test_word_boundaries_are_enforced(self, text):
        assert parse_relative_date(text, MONDAY) is None


# ---------- scope: date only, no time ----------


def test_a_time_of_day_does_not_change_the_date():
    """"8pm tomorrow" is tomorrow's *date* and nothing more.

    ``task_details.due_date`` is a date-only TEXT column, so there is
    nowhere to put a time; the "8pm" stays in the note's content as
    ordinary text. Pinned as a test because it is a scope limit someone
    will otherwise mistake for a bug.
    """
    assert parse_relative_date("8pm tomorrow", MONDAY) == date(2026, 6, 16)
    assert parse_relative_date("tomorrow at 20:00", MONDAY) == date(
        2026, 6, 16
    )


def test_reference_date_defaults_to_the_system_clock():
    """Omitting ``reference_date`` uses today.

    Asserted relatively (today vs. today + 1) so the test does not care
    what day it runs on.
    """
    assert parse_relative_date("today") == date.today()
    assert parse_relative_date("tomorrow") != date.today()


# ---------- misspellings ----------


class TestMisspellings:
    """A typo must not silently cost the user a due date.

    The failure this pins was reported from real use: "remember that I
    have meeting at tommorow 8pm" was filed as an inbox note with no
    date, and the follow-up "what do we have tommorow?" then found
    nothing — two separate-looking bugs with one cause, a single missing
    spelling. The accepted spellings are a curated list, not a fuzzy
    distance; ``test_a_real_word_is_never_a_date`` guards the line.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "tommorow",
            "tommorrow",
            "tomorow",
            "tomorrw",
            "tomarrow",
            "tommarow",
            "tomorro",
        ],
    )
    def test_tomorrow_misspellings(self, text):
        assert parse_relative_date(text, MONDAY) == date(2026, 6, 16)

    @pytest.mark.parametrize("text", ["todya", "todday", "tooday"])
    def test_today_misspellings(self, text):
        assert parse_relative_date(text, MONDAY) == MONDAY

    def test_a_misspelling_works_mid_sentence_like_the_real_word(self):
        assert parse_relative_date(
            "remember that I have meeting at tommorow 8pm", MONDAY
        ) == date(2026, 6, 16)

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("wendsday", date(2026, 6, 17)),
            ("wensday", date(2026, 6, 17)),
            ("wedensday", date(2026, 6, 17)),
            ("wednsday", date(2026, 6, 17)),
            ("thurday", date(2026, 6, 18)),
            ("thrusday", date(2026, 6, 18)),
            ("thusday", date(2026, 6, 18)),
            ("tusday", date(2026, 6, 16)),
            ("teusday", date(2026, 6, 16)),
            ("tuseday", date(2026, 6, 16)),
        ],
    )
    def test_the_three_weekdays_people_misspell(self, text, expected):
        assert parse_relative_date(text, MONDAY) == expected

    def test_a_misspelled_weekday_still_takes_the_next_qualifier(self):
        # The alternation feeds the same branch, so "next" keeps working.
        assert parse_relative_date("next wendsday", date(2026, 6, 17)) == date(
            2026, 6, 24
        )

    @pytest.mark.parametrize("text", ["toady", "a toady of a man", "sundry"])
    def test_a_real_word_is_never_a_date(self, text):
        """"toady" is a transposition of "today" *and* an English word.

        Tolerating it would put a due date on "he is a toady", which is
        the false-positive this module refuses to make. Missing the typo
        is the cheaper failure.
        """
        assert parse_relative_date(text, MONDAY) is None


def test_canonical_day_word_folds_every_accepted_spelling():
    assert canonical_day_word("Tommorow") == "tomorrow"
    assert canonical_day_word("  TODYA ") == "today"
    assert canonical_day_word("friday") is None
    assert canonical_day_word(None) is None
    assert canonical_day_word("") is None
