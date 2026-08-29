"""Relative date phrases -> a plain calendar date.

A deliberately small, fully local parser. It recognizes four shapes
and nothing else:

* ``today``
* ``tomorrow``
* ``next <weekday>`` -- e.g. "next friday"
* a bare weekday name -- e.g. "friday"

Everything else returns ``None``, which is how the capture path
tells "this line mentions a day" from "this line doesn't".

Scope, on purpose
-----------------
**Date only, no time.** The schema's ``task_details.due_date`` is a
TEXT ISO ``YYYY-MM-DD`` with no time component, so "8pm tomorrow"
resolves to tomorrow's *date*; the "8pm" survives as ordinary text in
the saved content and is not scheduled. Adding a due *time* would
mean a schema change, so it is out of scope here.

**Full weekday names only.** No abbreviations: ``sat`` would fire on
"I sat down" and ``sun`` on "sun exposure", and a false due date is a
worse failure than a missed one -- the user can always set the date in
the editor.

**No numeric or absolute dates.** "on the 3rd", "March 5", "5/3" are
all unrecognized. Those are unambiguous enough to type into the
editor's date field, and guessing at ``5/3`` (May 3rd? 5 March?) is
exactly the kind of silent wrong answer this module is written to
avoid.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional


#: Weekday name -> ``date.weekday()`` index (Monday is 0).
_WEEKDAYS: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

#: Misspellings that map to the same weekday. Curated, not computed:
#: no edit distance, no fuzzy scoring -- each entry is a spelling
#: someone actually types, listed once. Nothing here is a real English
#: word, so tolerating them cannot invent a due date out of ordinary
#: prose. Only the three weekdays people genuinely misspell are
#: covered; the list is trivial to extend when a real one turns up.
_WEEKDAY_MISSPELLINGS: dict[str, int] = {
    "tusday": 1,
    "teusday": 1,
    "tuseday": 1,
    "wendsday": 2,
    "wensday": 2,
    "wedensday": 2,
    "wednsday": 2,
    "thurday": 3,
    "thrusday": 3,
    "thusday": 3,
}

#: Every weekday spelling the parser answers to.
_ALL_WEEKDAYS: dict[str, int] = {**_WEEKDAYS, **_WEEKDAY_MISSPELLINGS}

#: Accepted spellings of "today" and "tomorrow". These two words carry
#: nearly every spoken date phrase, so a typo in one of them is the
#: difference between a scheduled task and an inbox note the user
#: cannot ask about later.
#:
#: "toady" is deliberately absent even though it is a common
#: transposition of "today": it is a real English word, and a false due
#: date is a worse failure than a missed one -- the same reasoning that
#: keeps the "sat" and "sun" abbreviations out.
_TODAY_SPELLINGS: tuple[str, ...] = ("today", "todya", "todday", "tooday")
_TOMORROW_SPELLINGS: tuple[str, ...] = (
    "tomorrow",
    "tommorow",
    "tommorrow",
    "tomorow",
    "tomorrw",
    "tomarrow",
    "tommarow",
    "tomorro",
)


def _alternation(words) -> str:
    """Join spellings into a regex alternation, longest first.

    "tomorro" is a prefix of "tomorrow"; the pattern's trailing
    word-boundary anchor makes the engine backtrack correctly either
    way, but ordering by length keeps correctness a property of the
    pattern rather than a fact about how Python backtracks.
    """
    return "|".join(sorted(set(words), key=len, reverse=True))


_DAY_ALTERNATION = _alternation(_ALL_WEEKDAYS)

#: Regex fragment matching any accepted spelling of today/tomorrow.
#: Exported because :mod:`ui.assistant_controller` builds its own
#: "what's due <day>" question patterns and has to accept exactly the
#: spellings this module does -- otherwise a capture gets filed as a
#: task that the matching question can no longer find.
DAY_WORD_ALTERNATION = _alternation(_TODAY_SPELLINGS + _TOMORROW_SPELLINGS)


def canonical_day_word(word: Optional[str]) -> Optional[str]:
    """Fold any accepted spelling to ``"today"`` or ``"tomorrow"``.

    Returns ``None`` for anything else, including ``None`` itself, so a
    caller can hand it an unmatched regex group directly.
    """
    if not word:
        return None
    lowered = word.strip().lower()
    if lowered in _TODAY_SPELLINGS:
        return "today"
    if lowered in _TOMORROW_SPELLINGS:
        return "tomorrow"
    return None

#: One pass over the text, first match wins. The alternation order
#: matters less than it looks: ``next friday`` is found at the "next",
#: which is an earlier offset than the "friday", so the qualified
#: shape always beats the bare one when both are present in the same
#: phrase. Word boundaries on both ends keep "todays" and "Sundays"
#: from matching.
_RE_DATE_PHRASE = re.compile(
    r"\b(?:"
    rf"(?P<today>{_alternation(_TODAY_SPELLINGS)})"
    rf"|(?P<tomorrow>{_alternation(_TOMORROW_SPELLINGS)})"
    rf"|next\s+(?P<next_day>{_DAY_ALTERNATION})"
    rf"|(?P<day>{_DAY_ALTERNATION})"
    r")\b",
    re.IGNORECASE,
)


def parse_relative_date(
    text: str, reference_date: Optional[date] = None
) -> Optional[date]:
    """Return the date ``text`` refers to, or ``None`` if it names none.

    ``reference_date`` defaults to today (local) and exists so tests can
    freeze "what is today" without monkey-patching the system clock --
    the same convention :func:`core.tasks.list_tasks` uses.

    Weekday semantics:

    * A bare weekday is the **nearest upcoming** occurrence, and if it
      is today's own weekday that means *today* (0 days out). Saying
      "friday" on a Friday plainly means the one you are standing in.
    * ``next <weekday>`` is the nearest **strictly future** occurrence,
      so on a Friday "next friday" is 7 days out. Otherwise it agrees
      with the bare form -- on a Monday, both "friday" and "next friday"
      are the same Friday.
    """
    if not text:
        return None

    match = _RE_DATE_PHRASE.search(text)
    if match is None:
        return None

    today = reference_date or date.today()

    if match.group("today"):
        return today
    if match.group("tomorrow"):
        return today + timedelta(days=1)

    qualified = match.group("next_day")
    bare = match.group("day")
    name = (qualified or bare or "").lower()
    target = _ALL_WEEKDAYS.get(name)
    if target is None:  # pragma: no cover - the regex can't produce this
        return None

    delta = (target - today.weekday()) % 7
    if qualified and delta == 0:
        # "next monday" on a Monday means the following one; the bare
        # "monday" is what means today.
        delta = 7
    return today + timedelta(days=delta)
