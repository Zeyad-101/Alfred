"""Classification and orchestration for Alfred's butler interaction layer.

What this is
------------
A curated pattern-matching layer with templated output. A fixed set of
question *shapes* (regexes, listed below) are recognized and answered
with real data from the existing ``core`` helpers; the wording comes
from hand-written templates in :mod:`ui.assistant_response`. Anything
outside that set falls back to a plain FTS5 search or a polite decline.

There is no NLU, no model, no inference, and no learning here. "What's
my name?" works because someone wrote a regex for ``what's my <field>``
and a SQL LIKE for ``my <field> is ...`` -- not because Alfred
understands the question.

Two axes, not one
-----------------
Every input is classified on two independent axes:

* **Does it need a database lookup at all?** (``capture``,
  ``direct_answer`` and ``action`` do not.)
* **If it does, is that lookup trivial or substantial?** A single-row
  fact ("what's my name") is ``trivial``; a search, a task bucket or a
  project roll-up is ``substantial``.

The weight is a property of the *question*, not of the clock. Trivial
and none-weight work never shows the "Lemme check, sir." state or the
thinking pose, however slow the machine happens to be that second.
Substantial work always shows it, immediately, and the answer is then
held back until :data:`THINKING_MIN_HOLD_MS` has elapsed. An earlier
version announced itself only once a lookup had already run for 300ms,
which in practice meant never: a local SQLite query over a few thousand
rows finishes in single-digit milliseconds, so the state it was meant
to explain flickered past unread.

The hold bounds the *reveal*, not the query. The lookup starts the
instant it is submitted and runs at full speed; the answer appears once
both it and the minimum have finished, whichever is later. An 80ms
query is shown at ~500ms; a 900ms query is shown at ~900ms, never
padded to 1400ms. Nothing sleeps and nothing blocks the event loop to
achieve this -- the two completions are joined by parking the finished
answer until the timer fires, so the window stays responsive and
repaints the thinking state throughout. See
:meth:`AssistantController._settle`.

Threading
---------
Substantial lookups run on a :class:`~PySide6.QtCore.QThreadPool`
worker. A SQLite connection may not be shared across threads, so the
worker **opens its own short-lived connection** to the same file and
closes it when done (the alternative -- serializing every query through
a queue onto the owning thread -- would defeat the point of moving the
work off the UI thread). The main thread's connection is never touched
from the worker; results come back through a Qt signal, exactly like
the hotkey bridge, and no widget is touched off the main thread. When
the database has no file path (``:memory:``, as in some tests), the
lookup runs synchronously instead. There are no artificial delays
anywhere in this module.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from core.assistant import (
    INTENT_QUESTION,
    MAX_QUESTION_RESULTS,
    ask as _core_ask,
    classify_input,
    extract_query_terms,
    find_entry_by_title_or_text,
    find_profile_fact,
)
from core.date_parsing import (
    DAY_WORD_ALTERNATION,
    canonical_day_word,
    parse_relative_date,
)
from core.db import get_connection
from core.inbox import convert_to_task
from core.memory import get_memory, list_memories_updated_on
from core.projects import (
    add_entry_to_project,
    find_project_by_name,
    get_or_create_project,
    get_project,
    list_project_memories,
    set_memory_project,
)
from core.search import search
from core.tasks import complete_task, list_tasks
from ui import assistant_response as R
from ui.sidebar import (
    VIEW_INBOX,
    VIEW_PROJECTS,
    VIEW_TASKS,
    VIEW_TRASH,
)
from ui.strings import (
    ASSISTANT_ACTION_CONFIRM,
    ASSISTANT_CAPABILITIES,
    ASSISTANT_CONTEXT_NONE,
    ASSISTANT_CREATED_UNKNOWN,
    ASSISTANT_DECLINE,
    ASSISTANT_ERROR,
    ASSISTANT_PROJECT_UNKNOWN,
    ASSISTANT_TASK_BUCKET_OPEN,
    ASSISTANT_TASK_BUCKET_TODAY,
    ASSISTANT_TASK_BUCKET_TOMORROW,
    ASSISTANT_UNKNOWN_FIELD,
)

# ---------------------------------------------------------------------------
# outcomes, weights, kinds
# ---------------------------------------------------------------------------

OUTCOME_CAPTURE = "capture"
OUTCOME_DIRECT_ANSWER = "direct_answer"
OUTCOME_TRIVIAL_LOOKUP = "trivial_lookup"
OUTCOME_SUBSTANTIAL_LOOKUP = "substantial_lookup"
OUTCOME_LOOKUP_AND_SHOW = "lookup_and_show"
OUTCOME_ACTION = "action"

WEIGHT_NONE = "none"
WEIGHT_TRIVIAL = "trivial"
WEIGHT_SUBSTANTIAL = "substantial"

# What the lookup actually is, once we know we need one.
KIND_CAPABILITIES = "capabilities"
KIND_VIEW = "view"
KIND_PROFILE_FIELD = "profile_field"
KIND_CONTEXT_REUSE = "context_reuse"
KIND_CREATED_AT = "created_at"
KIND_TOPIC_SEARCH = "topic_search"
KIND_TASKS = "tasks"
KIND_TASK_COMPLETE = "task_complete"
KIND_PENDING_ON = "pending_on"
KIND_LAST_WORKED = "last_worked"
KIND_YESTERDAY = "yesterday"
KIND_PROJECT_STATUS = "project_status"
KIND_PROJECT_OPEN = "project_open"
KIND_PROJECT_ACTION = "project_action"
KIND_PROJECT_CREATE = "project_create"
KIND_FALLBACK_SEARCH = "fallback_search"

#: How long the "Lemme check, sir." state stays up before a substantial
#: answer is revealed. Long enough to read a four-word line; short
#: enough to stay under the ~1s mark where a wait starts to feel like a
#: stall. This bounds the reveal, never the query itself.
THINKING_MIN_HOLD_MS = 500

#: Pseudo-view id for "open settings" -- the settings dialog isn't a
#: sidebar view, so the main window special-cases this one.
ACTION_SETTINGS = "settings"

_ACTION_TARGETS = {
    "tasks": VIEW_TASKS,
    "task list": VIEW_TASKS,
    "todos": VIEW_TASKS,
    "inbox": VIEW_INBOX,
    "trash": VIEW_TRASH,
    "bin": VIEW_TRASH,
    "projects": VIEW_PROJECTS,
    "settings": ACTION_SETTINGS,
}


# ---------------------------------------------------------------------------
# data carriers
# ---------------------------------------------------------------------------

@dataclass
class Classification:
    """The routing decision for one input, before any DB call."""

    outcome: str
    weight: str
    kind: str = ""
    text: str = ""
    payload: dict = field(default_factory=dict)

    @property
    def needs_lookup(self) -> bool:
        return self.weight in (WEIGHT_TRIVIAL, WEIGHT_SUBSTANTIAL)

    @property
    def may_show_thinking(self) -> bool:
        """Only substantial work is ever allowed to show thinking."""
        return self.weight == WEIGHT_SUBSTANTIAL


@dataclass
class SessionContext:
    """What the last exchange was about, for pronoun follow-ups.

    Lives for as long as the popup instance does -- this is *not*
    persisted across sessions.
    """

    last_shown_project_id: Optional[int] = None
    last_shown_memory_id: Optional[int] = None
    last_topic: str = ""

    def has_reference(self) -> bool:
        return (
            self.last_shown_project_id is not None
            or self.last_shown_memory_id is not None
        )

    def clear(self) -> None:
        self.last_shown_project_id = None
        self.last_shown_memory_id = None
        self.last_topic = ""


@dataclass
class AssistantAnswer:
    """Everything the popup needs to render one reply."""

    text: str
    results: list = field(default_factory=list)
    weight: str = WEIGHT_NONE
    kind: str = ""
    #: Set when the Projects view should be opened afterwards.
    open_project_id: Optional[int] = None
    #: Set by a write, meaning "this project changed" -- refresh it if it
    #: is already on screen, and do nothing otherwise. Deliberately not
    #: ``project_id``, which every project answer carries for follow-up
    #: bookkeeping: a *question* about a project must leave the bubble
    #: open for the next question, and only an action closes it.
    refresh_project_id: Optional[int] = None
    #: Set when a view switch was requested (ACTION outcome).
    view_id: Optional[str] = None
    #: Bookkeeping for follow-up questions.
    project_id: Optional[int] = None
    memory_id: Optional[int] = None
    topic: str = ""


# ---------------------------------------------------------------------------
# the recognized question shapes
# ---------------------------------------------------------------------------
# Every pattern below is one shape the butler layer knows. Adding a
# shape means adding a regex here and a branch in ``_execute``; nothing
# is inferred at runtime.

_RE_CAPABILITIES = re.compile(
    r"^what\s+(?:can\s+you\s+(?:do|help\s+(?:me\s+)?with)|do\s+you\s+do)"
    r"[\s?.!]*$",
    re.IGNORECASE,
)

_RE_ACTION = re.compile(
    r"^(?:please\s+)?(?:open|show|go\s+to|take\s+me\s+to|bring\s+up)\s+"
    r"(?:me\s+)?(?:my\s+|the\s+)?"
    r"(tasks|task\s+list|todos|inbox|settings|trash|bin|projects)"
    r"[\s?.!]*$",
    re.IGNORECASE,
)

_RE_PROFILE_FIELD = re.compile(
    r"^what(?:'s|s|\s+is)\s+my\s+([a-z][a-z0-9'\- ]{0,40}?)\s*[?.!]*$",
    re.IGNORECASE,
)

# Pronoun follow-ups. Kept as an explicit tuple rather than one giant
# alternation so each accepted shape is readable on its own line.
_CONTEXT_PATTERNS = (
    re.compile(
        r"^(?:and\s+|so\s+)?what\s+about\s+(?:it|that|this|them|those)"
        r"[\s?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:and\s+|so\s+)?how(?:'s|s|\s+is|\s+about)\s+(?:it|that|this)"
        r"[\s?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^what(?:'s|s|\s+is)\s+(?:its|the)\s+status[\s?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^tell\s+me\s+more(?:\s+about)?\s+(?:it|that|this)[\s?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:and\s+)?(?:it|that)\s*\?+$", re.IGNORECASE),
)

# Task buckets. Each accepted phrasing is its own pattern (rather than
# one unreadable alternation) and every one of them captures the bucket
# in group 1 -- ``None`` meaning "everything still open".
_TASK_PATTERNS = (
    re.compile(
        r"^what\s+(?:are|is)\s+my\s+"
        r"(?:unfinished\s+|pending\s+|open\s+|outstanding\s+|remaining\s+)?"
        rf"tasks?(?:\s+(?:for|due))?(?:\s*({DAY_WORD_ALTERNATION}))?[\s?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^what\s+tasks?\s+(?:are|is)\s+"
        r"(?:due|left|open|pending|outstanding)"
        rf"(?:\s+({DAY_WORD_ALTERNATION}))?[\s?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^what\s+tasks?\s+do\s+i\s+have"
        rf"(?:\s+(?:for|due|left))?(?:\s+({DAY_WORD_ALTERNATION}))?[\s?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^what(?:'s|s|\s+is)\s+due\s+({DAY_WORD_ALTERNATION})[\s?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^what(?:'s|s|\s+is)\s+on\s+my\s+plate"
        rf"(?:\s+({DAY_WORD_ALTERNATION}))?[\s?.!]*$",
        re.IGNORECASE,
    ),
    # "what do I have tomorrow" / "what do we have today" -- no mention
    # of tasks at all; the day is carrying the whole question.
    re.compile(
        r"^what\s+do\s+(?:i|we)\s+have\s+"
        rf"(?:on\s+|due\s+|going\s+on\s+)?({DAY_WORD_ALTERNATION})[\s?.!]*$",
        re.IGNORECASE,
    ),
    # "what's happening today" / "what's on today".
    re.compile(
        r"^what(?:'s|s|\s+is)\s+"
        r"(?:happening|on|going\s+on|up|scheduled|planned)\s+"
        rf"({DAY_WORD_ALTERNATION})[\s?.!]*$",
        re.IGNORECASE,
    ),
    # "anything due tomorrow" / "is there anything on today". The day
    # word is required immediately after, which is what keeps
    # "anything on the Batman project" a topic search (rule 10) rather
    # than a task bucket.
    re.compile(
        r"^(?:is\s+there\s+)?anything\s+"
        r"(?:due|left|open|pending|on|happening|scheduled|planned)\s+"
        rf"({DAY_WORD_ALTERNATION})[\s?.!]*$",
        re.IGNORECASE,
    ),
)

# Conversational completion: the user reporting work as finished
# rather than asking about it. Every shape captures the same group --
# what they finished -- which the handler reduces to search terms.
#
# The tail is deliberately greedy up to trailing punctuation and no
# further: whatever the user said the thing was, all of it is evidence
# for matching, and the stopword pass in ``extract_query_terms`` is what
# throws away the parts that aren't. Anchored at the start of the line
# so "the note says I finished the wiring" is not read as a command.
_TASK_COMPLETE_PATTERNS = (
    # "I finished the meeting" / "I've completed the wiring" /
    # "I am done with the report". The verb list is closed on purpose:
    # "I did the wiring" is too easily an ordinary note to act on.
    re.compile(
        r"^i(?:'ve|\s+have|'m|\s+am)?\s+"
        r"(?:finished|completed|done)\s+"
        r"(?:with\s+)?(?P<what>.+?)[\s?.!]*$",
        re.IGNORECASE,
    ),
    # "mark the meeting as done" / "mark wiring complete".
    re.compile(
        r"^mark\s+(?P<what>.+?)\s+"
        r"(?:as\s+)?(?:done|complete|completed|finished)[\s?.!]*$",
        re.IGNORECASE,
    ),
    # Bare imperative: "complete the wiring" / "finish the report".
    re.compile(
        r"^(?:complete|finish)\s+(?P<what>.+?)[\s?.!]*$",
        re.IGNORECASE,
    ),
)

# Project task lists. Each shape asks the same question -- "which
# tasks belong to this project and are still open" -- so they all
# resolve to KIND_PENDING_ON rather than getting a handler each.
_PROJECT_TASK_PATTERNS = (
    re.compile(
        r"^what(?:'s|s|\s+is)\s+left\s+(?:on|for|in)\s+(.+?)[\s?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^what\s+tasks\s+(?:are\s+|do\s+i\s+have\s+)?"
        r"(?:left\s+)?(?:on|in|for|under)\s+(.+?)[\s?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:show\s+me\s+|list\s+)(?:the\s+|my\s+)?tasks\s+"
        r"(?:on|in|for|under)\s+(.+?)[\s?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^what\s+(?:do\s+i\s+have|have\s+i\s+got|is\s+there)\s+"
        r"(?:still\s+)?left\s+(?:on|in|for)\s+(.+?)[\s?.!]*$",
        re.IGNORECASE,
    ),
)

#: Kept as a single-pattern alias so the older name still resolves.
_RE_PENDING_ON = _PROJECT_TASK_PATTERNS[0]

# "Open my ESP32 project" -- navigation, not a question. The word
# "project" is required so this can never swallow "open my tasks"
# (rule 4's view switch) or a bare capture. Resolution is one indexed
# row, so this is trivial weight: no thinking state is ever shown for
# it, and no search is run.
_RE_PROJECT_OPEN = re.compile(
    r"^(?:please\s+)?(?:open|go\s+to|take\s+me\s+to|switch\s+to|pull\s+up|"
    r"bring\s+up|jump\s+to)\s+(?:me\s+)?(?:the\s+|my\s+)?"
    r"(?P<name>[^.;:!?\n]{1,60}?)\s+projects?[\s?.!]*$",
    re.IGNORECASE,
)

# "Add a task to my ESP32 project: test the sensor." A write, so the
# body after the separator has to be non-empty; the separator itself is
# what distinguishes this from a capture that merely mentions a project.
_RE_PROJECT_ACTION = re.compile(
    r"^(?:please\s+)?(?:add|put|file|stick|log)\s+"
    r"(?:a\s+|an\s+|this\s+)?(?P<what>task|note|item|reminder|todo)?\s*"
    r"(?:to|in|on|under|into)\s+(?:the\s+|my\s+)?"
    r"(?P<name>[^.;:!?\n]{1,60}?)\s+projects?\s*"
    # Separators that introduce the body. ``to`` is here because
    # "add a task to my ESP32 project to test the sensor" is the way
    # people actually phrase it out loud -- without it that line fell
    # through to the capture branch and was filed as a raw inbox note
    # with the whole sentence as its title and no project link.
    r"(?::|-|–|—|,\s*saying|\s+called|\s+that\s+says|\s+to\b|\s+for\b)\s*"
    r"(?P<body>.+?)[\s.!]*$",
    re.IGNORECASE,
)

#: Progress verbs that trail a project name in "how's the ESP32 project
#: doing" -- stripped before resolution so the name is left clean.
_RE_PROGRESS_TAIL = re.compile(
    r"\s+(?:doing|going|coming\s+along|coming|looking|progressing|"
    r"shaping\s+up|getting\s+on)$",
    re.IGNORECASE,
)

#: Entry types a project action may create, keyed by the noun the user
#: said. Anything unstated becomes a task, which is what "add ... to my
#: project" almost always means.
_PROJECT_ACTION_TYPES = {
    "task": "task",
    "todo": "task",
    "reminder": "task",
    "item": "task",
    "note": "note",
}

# "Start a new project called Batcave." Creation as the thing asked
# for, rather than as a side effect of adding an entry to a name that
# happened not to exist yet. Two patterns, both requiring the literal
# word "project", which is what keeps them from touching any other
# shape: the verb-led form, and the bare "new project: X" form.

#: What sits between the word "project" and the name the user wants.
#: Spoken instructions put a whole clause there -- "and name it X",
#: ", call it X" -- and every word of it has to be consumed, or it ends
#: up *as* the project's name, which is the bug this alternation exists
#: to close. The participle forms ("called", "named") come first so
#: "a project called Call Logs" reads its connector as "called" and
#: keeps "Call" as part of the name.
_PROJECT_NAME_CONNECTOR = (
    r"(?:[,;]\s*)?(?:and\s+|then\s+)?"
    r"(?:"
    r"called\s+|named\s+|titled\s+|labell?ed\s+|"
    r"(?:name|call|title|label|dub)\s+(?:it|this|them)\s+|"
    r"for\s+|"
    r"[:\u2013\u2014-]\s*"
    r")"
)

_PROJECT_CREATE_PATTERNS = (
    # "create a new project called X", "make me a project for X",
    # "set up another project: X", "start a project X", "create a
    # project and name it X".
    re.compile(
        r"^(?:please\s+|can\s+you\s+|could\s+you\s+|"
        r"i\s+(?:want|need)\s+to\s+|let'?s\s+)?"
        r"(?:create|make|start|begin|open|add|set\s+up|spin\s+up)\s+"
        r"(?:me\s+)?(?:a\s+|an\s+|another\s+)?(?:new\s+)?"
        r"projects?\s*"
        # Optional, so "start a project Batcave" still works; when it is
        # there it is consumed, so the name never keeps it.
        rf"(?:{_PROJECT_NAME_CONNECTOR})?"
        r"(?P<name>[^.;:!?\n]{1,60}?)[\s.!?]*$",
        re.IGNORECASE,
    ),
    # "new project: X" with no verb at all. The connector is *required*
    # here, unlike above: without a verb, a bare "new project deadline
    # is friday" is an ordinary sentence about a project, and reading
    # it as an instruction would start a project called "deadline is
    # friday". The colon (or "called", or "name it") is the user being
    # explicit.
    re.compile(
        r"^(?:a\s+)?new\s+projects?\s*"
        rf"(?:{_PROJECT_NAME_CONNECTOR})"
        r"(?P<name>[^.;:!?\n]{1,60}?)[\s.!?]*$",
        re.IGNORECASE,
    ),
)

#: Words that are part of *asking* for a project, never part of its
#: name. A capture whose name is nothing but these -- "create a project
#: and name", where the user trailed off -- is not a name at all, so the
#: line falls through to being filed rather than starting a project
#: called "and name". A real name only has to contain one other word:
#: "Call Logs" survives, "name it" does not.
_PROJECT_CREATE_FILLER = frozenset(
    {
        "called", "named", "titled", "labeled", "labelled",
        "name", "call", "title", "label", "dub",
        "and", "then", "for", "to", "about", "up",
        "it", "this", "them", "the", "a", "an",
    }
)


_RE_LAST_WORKED = re.compile(
    r"^(?:when\s+(?:was|is)\s+)?(?:the\s+)?last\s+time\s+i\s+"
    r"(?:worked|touched|looked)\s+(?:on|at)\s+(.+?)[\s?.!]*$",
    re.IGNORECASE,
)

_RE_YESTERDAY = re.compile(
    r"^what\s+(?:did\s+i\s+(?:work\s+on|do|touch|write)"
    r"|was\s+i\s+working\s+on)\s+yesterday[\s?.!]*$",
    re.IGNORECASE,
)

# "when did I create X" (verb first) and "when was X created" (verb last).
_RE_CREATED_ACTIVE = re.compile(
    r"^when\s+did\s+i\s+(?:creat\w*|make|made|file|add|write)\s+(.+?)"
    r"[\s?.!]*$",
    re.IGNORECASE,
)
_RE_CREATED_PASSIVE = re.compile(
    r"^when\s+(?:was|were)\s+(.+?)\s+"
    r"(?:created|made|filed|added|written)[\s?.!]*$",
    re.IGNORECASE,
)

# Topic search. One prefix per accepted opener; the captured tail is
# then stripped of connectors ("about the ...") and noise nouns.
_TOPIC_SEARCH_PREFIXES = (
    re.compile(r"^do\s+i\s+have\s+(?:any(?:thing)?\s+)?(.+)$", re.IGNORECASE),
    re.compile(r"^what\s+did\s+i\s+write\s+(.+)$", re.IGNORECASE),
    re.compile(r"^find\s+my\s+notes\s*(.*)$", re.IGNORECASE),
    re.compile(r"^show\s+me\s+(?:the\s+)?notes\s*(.*)$", re.IGNORECASE),
    re.compile(r"^anything\s+(?:about|on)\s+(.+)$", re.IGNORECASE),
    re.compile(r"^what\s+was\s+i\s+doing\s+with\s+(.+)$", re.IGNORECASE),
)

# Project roll-up: an opener plus the literal word "project" somewhere
# in the tail, which is what separates "tell me about the Batman
# project" (open the view) from "tell me about the grapple" (a search).
_PROJECT_STATUS_PREFIXES = (
    re.compile(r"^show\s+me\s+(?:the\s+|my\s+)?(.+)$", re.IGNORECASE),
    re.compile(
        r"^tell\s+me\s+(?:everything\s+|all\s+)?about\s+(?:the\s+|my\s+)?(.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^what(?:'s|s|\s+is)\s+the\s+status\s+of\s+(?:the\s+|my\s+)?(.+)$",
        re.IGNORECASE,
    ),
    re.compile(r"^how(?:'s|s|\s+is)\s+(?:the\s+|my\s+)?(.+)$", re.IGNORECASE),
    re.compile(
        r"^what(?:'s|s|\s+is)\s+going\s+on\s+with\s+(?:the\s+|my\s+)?(.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^how\s+(?:are\s+things|is\s+it\s+going)\s+(?:on|with|in)\s+"
        r"(?:the\s+|my\s+)?(.+)$",
        re.IGNORECASE,
    ),
)
_RE_HAS_PROJECT_WORD = re.compile(r"\bprojects?\b", re.IGNORECASE)

_RE_TOPIC_CONNECTOR = re.compile(
    r"^(?:about|on|regarding|with|for|any|anything|in)\s+", re.IGNORECASE
)
_LEADING_NOISE = ("the ", "my ", "a ", "an ", "that ", "this ", "some ")
_TRAILING_NOISE = (
    "notes",
    "note",
    "entries",
    "entry",
    "memories",
    "memory",
    "projects",
    "project",
    "tasks",
    "task",
    "items",
    "item",
    "files",
    "file",
    "stuff",
    "thing",
)


#: Words that survive :func:`_clean_entity` but name nothing. A project
#: is only ever resolved (or created) from a real name, so "add a task to
#: this project" must not open, and must certainly not create, a project
#: called "this".
_VAGUE_ENTITIES = frozenset(
    {
        "this", "that", "these", "those", "it", "its", "one", "ones",
        "the", "my", "mine", "a", "an", "some", "any", "another",
        "new", "same", "other", "current", "here", "there",
    }
    # A bare category noun is not a name either: "tell me about this
    # project" cleans down to "project", which must not resolve to a
    # project called "project".
    | set(_TRAILING_NOISE)
)


def _project_entity(phrase: str) -> str:
    """:func:`_clean_entity`, but empty for a name that names nothing.

    Every project shape resolves its name through this, so a vague
    reference falls through to the next rule (and ultimately to search)
    instead of resolving -- or creating -- a project named "this".
    """
    name = _clean_entity(phrase)
    return "" if name.strip().lower() in _VAGUE_ENTITIES else name


def _clean_entity(phrase: str) -> str:
    """Reduce "the batman project" to "batman".

    Strips surrounding punctuation, leading articles/possessives, and
    trailing category nouns, repeatedly, but never all the way to an
    empty string -- if stripping would empty it, the original phrase
    stands.
    """
    original = (phrase or "").strip()
    text = original.strip(" \t?!.,;:\"'\u201c\u201d")
    changed = True
    while changed and text:
        changed = False
        lowered = text.lower()
        for noise in _LEADING_NOISE:
            if lowered.startswith(noise):
                text = text[len(noise):].strip()
                changed = True
                break
        lowered = text.lower()
        for noise in _TRAILING_NOISE:
            if lowered.endswith(" " + noise) or lowered == noise:
                if lowered == noise:
                    # Nothing but a category noun -- keep it, there is
                    # no real topic underneath.
                    return text
                text = text[: -len(noise)].strip()
                changed = True
                break
    return text or original


_RE_CONTRACTED_OPENER = re.compile(
    r"^(what|who|when|where|why|how)(?:'s|s)\s+", re.IGNORECASE
)


def _expand_contraction(text: str) -> str:
    """Turn a leading "what's" into "what is" for the fallback check.

    :func:`core.assistant.classify_input` decides question-vs-capture on
    the first whitespace token, so "what's on my plate" reads as a
    statement to it. Expanding the contraction before asking keeps that
    function (and its tests) untouched while letting an obviously
    interrogative line fall back to a search instead of a note.
    """
    return _RE_CONTRACTED_OPENER.sub(r"\1 is ", text or "", count=1)


def _entity_payload(phrase: str) -> dict:
    """Payload for the shapes whose subject is an entry or project name.

    Carries both readings of the captured phrase: ``topic`` is the
    cleaned entity, ``raw`` the phrase as typed. "when did I create Cave
    notes" cleans to "Cave" (the trailing category noun is stripped),
    which is right for "when did I create that note" and wrong for a
    title that genuinely ends in "notes" -- so the lookup tries the
    unabridged phrase first and falls back to the cleaned one.
    """
    text = (phrase or "").strip()
    return {"topic": _clean_entity(text), "raw": text.strip(" 	?!.,;:")}


def _entity_candidates(payload: dict) -> list[str]:
    """Ordered, de-duplicated lookup keys from an entity payload."""
    keys = []
    for value in (payload.get("raw"), payload.get("topic")):
        value = (value or "").strip()
        if value and value not in keys:
            keys.append(value)
    return keys


def _clean_topic(tail: str) -> str:
    """Strip connectors ("about the ...") then noise, for search topics."""
    text = (tail or "").strip().strip(" \t?!.,;:")
    while True:
        stripped = _RE_TOPIC_CONNECTOR.sub("", text, count=1).strip()
        if stripped == text:
            break
        text = stripped
    return _clean_entity(text)


# ---------------------------------------------------------------------------
# capture-time project assignment
# ---------------------------------------------------------------------------

#: A capture that ends by naming a project files itself there:
#: "remember I need to test the sensor to the ESP32 project".
#:
#: The determiner ("the" / "my") is *required*, and something has to sit
#: between it and the word "project". Both constraints are there to stop
#: false positives: without the determiner, "remember to finish the
#: project" would file a project called "finish", and without the gap,
#: "add this to the project" would file one called nothing at all. The
#: name cannot span a line break or sentence punctuation either, so the
#: phrase can only ever be read off the tail of the capture.
_RE_PROJECT_ASSIGNMENT = re.compile(
    # Two shapes, because dropping the article changes what a name is
    # allowed to look like. With an article ("... to the bday project")
    # the name may be several words. Without one ("... to bday project",
    # just as common when typing quickly) it has to be a single word --
    # otherwise the connector matches far too early and "remember that I
    # need to test the sensor to the ESP32 project" reads as a project
    # called "test the sensor to the ESP32". A bare demonstrative
    # ("to this project") is caught by _project_entity, not here.
    r"\s+(?:to|in|for|under)\s+"
    r"(?:(?:the|my|a|an)\s+(?P<name>[^.;:!?\n]{1,60}?)"
    r"|(?P<bare>[^\s.;:!?\n]{1,40}))"
    r"\s+projects?\s*[.!?]*$",
    re.IGNORECASE,
)

#: Strips the capture verb off the front, to judge whether anything of
#: substance would survive removing the project phrase.
_RE_CAPTURE_LEAD = re.compile(
    r"^remember\b\s*(?:that\s+|to\s+)?", re.IGNORECASE
)

#: Filing verbs that say *where* a note goes rather than what it says.
#: "add buy solder to the Zeyad project" is an instruction with a note
#: inside it, and the note is "buy solder" -- keeping the verb titled the
#: entry "add buy solder", which is what this closes.
#:
#: The five verbs are exactly the ones _RE_PROJECT_ACTION already reads
#: this way, and they are kept in step with it on purpose: "add a task to
#: my X project: buy solder" and "add buy solder to the X project" are
#: one sentence said two ways, so they must not disagree about the title.
#: An optional "a task" / "a note" unit goes with them, but only when the
#: noun is really there -- "add a new sensor" keeps its article.
#:
#: Two verbs are deliberately absent. ``remember`` is stored verbatim
#: (two tests pin the whole spoken line, typo included), and ``note``
#: reads as a noun at least as often as a verb -- "note the sensor drift"
#: is pinned as content, not as an instruction.
#:
#: Only ever applied to a capture that named its project outright: that
#: trailing phrase is what makes the leading verb an instruction. A bare
#: "add milk" keeps its verb, since nothing there says it was addressed
#: to Alfred rather than written down.
_RE_CAPTURE_FILING_VERB = re.compile(
    r"^(?:please\s+|also\s+|and\s+)?(?:add|put|file|stick|log)"
    r"(?:\s+(?:(?:a|an|this)\s+)?(?:task|note|item|reminder|todo))?"
    r"(?:\s+|$)",
    re.IGNORECASE,
)


def parse_project_assignment(text: str) -> tuple[str, Optional[str]]:
    """Split a trailing "... to the <name> project" off a capture.

    Returns ``(content, project_name)``. ``project_name`` is ``None``
    whenever the pattern is absent -- and then ``content`` is ``text``
    unchanged, so every capture that does not end this way behaves
    exactly as it did before.

    The phrase is dropped from the saved note when it can be: it is an
    instruction about where the note belongs, not part of the note. A
    leading filing verb goes with it for the same reason -- "add buy
    solder to the Zeyad project" files "buy solder", not "add buy
    solder". When stripping would leave nothing of substance ("add to
    the Zeyad project", or a bare "remember"), the whole line stays in
    the content and only the link is acted on -- a linked note with a
    clumsy sentence beats an empty one.
    """
    raw = (text or "").strip()
    match = _RE_PROJECT_ASSIGNMENT.search(raw)
    if match is None:
        return raw, None

    name = _project_entity(match.group("name") or match.group("bare") or "")
    if not name:
        return raw, None

    stripped = (raw[: match.start()] + raw[match.end():]).strip()
    # The leading filing verb goes with it: it addressed Alfred, it is
    # not part of what was said. See _RE_CAPTURE_FILING_VERB for why
    # that is safe here and nowhere else.
    stripped = _RE_CAPTURE_FILING_VERB.sub("", stripped, count=1).strip()
    if not _RE_CAPTURE_LEAD.sub("", stripped).strip():
        return raw, name
    return stripped, name


# ---------------------------------------------------------------------------
# classification (pure -- no database, no Qt)
# ---------------------------------------------------------------------------

def classify(
    text: str, session_context: Optional[SessionContext] = None
) -> Classification:
    """Route ``text`` to an outcome and a weight, without touching the DB.

    Rule order matters and is fixed: the explicit "remember" prefix wins
    outright, then the no-lookup shapes (capabilities, view actions),
    then follow-ups, then each substantial shape, then the single-fact
    profile question, and finally the plain capture-or-search
    fallback. Project *existence* is deliberately not checked here --
    that is a database question, resolved during the lookup -- which
    keeps this function pure and cheap to test.
    """
    raw = (text or "").strip()
    if not raw:
        return Classification(OUTCOME_CAPTURE, WEIGHT_NONE, text=raw)

    lowered = raw.lower()
    ctx = session_context

    # 1. Explicit capture prefix.
    if lowered == "remember" or lowered.startswith("remember"):
        return Classification(OUTCOME_CAPTURE, WEIGHT_NONE, text=raw)

    # 2. Fixed capability answer -- no lookup of any kind.
    if _RE_CAPABILITIES.match(raw):
        return Classification(
            OUTCOME_DIRECT_ANSWER, WEIGHT_NONE, KIND_CAPABILITIES, text=raw
        )

    # 3. View switches -- no lookup, just navigation.
    match = _RE_ACTION.match(raw)
    if match:
        key = re.sub(r"\s+", " ", match.group(1).strip().lower())
        target = _ACTION_TARGETS.get(key)
        if target:
            return Classification(
                OUTCOME_ACTION,
                WEIGHT_NONE,
                KIND_VIEW,
                text=raw,
                payload={"view_id": target},
            )

    # 4. Project write: "add a task to my ESP32 project: test the
    #    sensor". Resolved before every question shape because it is an
    #    instruction, not a question -- and trivial weight because the
    #    work is a resolve plus two inserts on the owning connection,
    #    which is also what keeps the write off the worker thread.
    match = _RE_PROJECT_ACTION.match(raw)
    if match:
        body = (match.group("body") or "").strip()
        name = _project_entity(match.group("name"))
        if body and name:
            said = (match.group("what") or "").lower()
            return Classification(
                OUTCOME_ACTION,
                WEIGHT_TRIVIAL,
                KIND_PROJECT_ACTION,
                text=raw,
                payload={
                    "name": name,
                    "raw": (match.group("name") or "").strip(),
                    "body": body,
                    "entry_type": _PROJECT_ACTION_TYPES.get(said, "task"),
                },
            )

    # 5. Project creation: "start a new project called Batcave".
    #    Otherwise a project only ever comes into being as a side
    #    effect -- rule 4's write, a capture that names one, or a topic
    #    keyword -- so this is the shape for asking outright, with
    #    nothing to file under it yet. Trivial weight: get-or-create is
    #    one indexed read and at most one insert, which is also what
    #    keeps the write on the connection the UI owns.
    for pattern in _PROJECT_CREATE_PATTERNS:
        match = pattern.match(raw)
        if match is None:
            continue
        name = _project_entity(match.group("name"))
        # A vague or filler-only name falls through to the next pattern
        # and ultimately to capture, rather than starting a project
        # called "this" or "name it".
        if not name or all(
            word in _PROJECT_CREATE_FILLER for word in name.lower().split()
        ):
            continue
        return Classification(
            OUTCOME_ACTION,
            WEIGHT_TRIVIAL,
            KIND_PROJECT_CREATE,
            text=raw,
            payload={
                "name": name,
                "raw": (match.group("name") or "").strip(),
            },
        )

    # 6. Conversational completion: "I finished the meeting". A write,
    #    like rule 4, but substantial weight -- finding the task means
    #    reading both open buckets and matching terms against every
    #    title and body, which is a real lookup and should say so.
    #    Placed before every question shape because it is a statement
    #    about work already on file; left after rule 4 so an explicit
    #    "add a task to ..." instruction still wins.
    for pattern in _TASK_COMPLETE_PATTERNS:
        match = pattern.match(raw)
        if match is None:
            continue
        what = (match.group("what") or "").strip()
        terms = extract_query_terms(what)
        if not terms:
            continue
        return Classification(
            OUTCOME_SUBSTANTIAL_LOOKUP,
            WEIGHT_SUBSTANTIAL,
            KIND_TASK_COMPLETE,
            text=raw,
            payload={"what": what, "terms": terms},
        )

    # 7. "Open my ESP32 project" -- navigation to a named project. One
    #    indexed row, so trivial weight: Alfred confirms and switches,
    #    with no thinking state and no search. Reached only after rule 3,
    #    which owns the plain "open my projects" view switch.
    match = _RE_PROJECT_OPEN.match(raw)
    if match:
        name = _project_entity(match.group("name"))
        if name:
            return Classification(
                OUTCOME_LOOKUP_AND_SHOW,
                WEIGHT_TRIVIAL,
                KIND_PROJECT_OPEN,
                text=raw,
                payload={
                    "name": name,
                    "raw": (match.group("name") or "").strip(),
                },
            )

    # 8. Pronoun follow-up. Resolving a reference we already have is
    #    cheap by construction, so this is always trivial weight. Only
    #    recognized when there *is* something to refer back to.
    if ctx is not None and ctx.has_reference():
        for pattern in _CONTEXT_PATTERNS:
            if pattern.match(raw):
                return Classification(
                    OUTCOME_TRIVIAL_LOOKUP,
                    WEIGHT_TRIVIAL,
                    KIND_CONTEXT_REUSE,
                    text=raw,
                )

    # 9. Task buckets.
    for pattern in _TASK_PATTERNS:
        match = pattern.match(raw)
        if match:
            # The day word is folded back to its canonical
            # spelling: the patterns accept "tommorow" and friends
            # so a typo still asks the question the user meant,
            # while every branch downstream keeps comparing against
            # "today"/"tomorrow".
            bucket = canonical_day_word(match.group(1))
            return Classification(
                OUTCOME_SUBSTANTIAL_LOOKUP,
                WEIGHT_SUBSTANTIAL,
                KIND_TASKS,
                text=raw,
                payload={"bucket": bucket},
            )

    # 10. Project task lists ("what's left on X", "what tasks are on
    #    X", "show me the tasks for X") -- verbal only, never opens a
    #    view. All three phrasings share one handler.
    for pattern in _PROJECT_TASK_PATTERNS:
        match = pattern.match(raw)
        if match:
            return Classification(
                OUTCOME_SUBSTANTIAL_LOOKUP,
                WEIGHT_SUBSTANTIAL,
                KIND_PENDING_ON,
                text=raw,
                payload=_entity_payload(match.group(1)),
            )

    # 11. "When was the last time I worked on X".
    match = _RE_LAST_WORKED.match(raw)
    if match:
        return Classification(
            OUTCOME_SUBSTANTIAL_LOOKUP,
            WEIGHT_SUBSTANTIAL,
            KIND_LAST_WORKED,
            text=raw,
            payload=_entity_payload(match.group(1)),
        )

    # 12. "What did I work on yesterday".
    if _RE_YESTERDAY.match(raw):
        return Classification(
            OUTCOME_SUBSTANTIAL_LOOKUP,
            WEIGHT_SUBSTANTIAL,
            KIND_YESTERDAY,
            text=raw,
        )

    # 13. Creation dates, either word order.
    match = _RE_CREATED_ACTIVE.match(raw) or _RE_CREATED_PASSIVE.match(raw)
    if match:
        return Classification(
            OUTCOME_SUBSTANTIAL_LOOKUP,
            WEIGHT_SUBSTANTIAL,
            KIND_CREATED_AT,
            text=raw,
            payload=_entity_payload(match.group(1)),
        )

    # 14. Topic search. Checked before the project roll-up so that
    #     "show me the notes on the Batman project" searches notes
    #     rather than opening the Projects view.
    for pattern in _TOPIC_SEARCH_PREFIXES:
        match = pattern.match(raw)
        if match:
            topic = _clean_topic(match.group(1))
            if topic:
                return Classification(
                    OUTCOME_SUBSTANTIAL_LOOKUP,
                    WEIGHT_SUBSTANTIAL,
                    KIND_TOPIC_SEARCH,
                    text=raw,
                    payload={"topic": topic},
                )

    # 15. Project roll-up -- answer first, then open the Projects view.
    #     "tell me everything about X" reads as a project question, but
    #     whether X *is* a project is a database question. So the shape
    #     is accepted either way and the lookup decides: a real project
    #     gets the roll-up plus the view, anything else degrades to a
    #     plain search with no view switch. Saying the word "project"
    #     makes the request strict -- then an unknown name is answered
    #     with "no project by that name" rather than searched for.
    vague_project_question = False
    for pattern in _PROJECT_STATUS_PREFIXES:
        match = pattern.match(raw)
        if match:
            tail = _RE_PROGRESS_TAIL.sub("", match.group(1).strip(" 	?!."))
            name = _project_entity(tail)
            if not name:
                # The shape is a question, but the name is a bare
                # reference ("tell me about this project"). Nothing to
                # resolve -- remembered below so it degrades to a search
                # instead of being filed as a note.
                vague_project_question = True
            if name:
                return Classification(
                    OUTCOME_LOOKUP_AND_SHOW,
                    WEIGHT_SUBSTANTIAL,
                    KIND_PROJECT_STATUS,
                    text=raw,
                    payload={
                        "name": name,
                        "raw": tail.strip(),
                        "strict": bool(_RE_HAS_PROJECT_WORD.search(tail)),
                    },
                )

    # 16. Single stored fact: "what's my <field>". One row, one regex --
    #     trivial by construction, so it never shows thinking.
    match = _RE_PROFILE_FIELD.match(raw)
    if match:
        return Classification(
            OUTCOME_TRIVIAL_LOOKUP,
            WEIGHT_TRIVIAL,
            KIND_PROFILE_FIELD,
            text=raw,
            payload={"field": re.sub(r"\s+", " ", match.group(1).strip())},
        )

    # 17. Nothing recognized: fall back to the plain split. Question
    #     shapes become a plain FTS5 search; anything else is a capture.
    if vague_project_question or (
        classify_input(_expand_contraction(raw)) == INTENT_QUESTION
    ):
        return Classification(
            OUTCOME_SUBSTANTIAL_LOOKUP,
            WEIGHT_SUBSTANTIAL,
            KIND_FALLBACK_SEARCH,
            text=raw,
        )
    return Classification(OUTCOME_CAPTURE, WEIGHT_NONE, text=raw)


# ---------------------------------------------------------------------------
# lookups (Qt-free -- safe to run on a worker thread)
# ---------------------------------------------------------------------------

def _project_task_state(
    conn: sqlite3.Connection, project_id: int
) -> tuple[list, list[str], int]:
    """Return ``(memories, open_task_titles, completed_task_count)``.

    Composed from the existing project and task helpers rather than a
    new join: the project's memory ids are intersected with the "all"
    task bucket, which is already ordered the way the Tasks view shows
    it.
    """
    memories = list_project_memories(conn, project_id)
    ids = {m.id for m in memories}
    open_titles: list[str] = []
    completed = 0
    for mem, _due, completed_at in list_tasks(conn, "all"):
        if mem.id not in ids:
            continue
        if completed_at:
            completed += 1
        else:
            open_titles.append(mem.title)
    return memories, open_titles, completed


def _project_status_answer(
    conn: sqlite3.Connection, project, open_view: bool
) -> AssistantAnswer:
    memories, open_titles, completed = _project_task_state(conn, project.id)
    text = R.build_project_status(
        project.name,
        len(memories),
        {"open": len(open_titles), "done": completed},
        open_titles,
    )
    return AssistantAnswer(
        text=text,
        results=memories[:MAX_QUESTION_RESULTS],
        kind=KIND_PROJECT_STATUS,
        open_project_id=project.id if open_view else None,
        project_id=project.id,
        topic=project.name,
    )


def _search_answer(
    conn: sqlite3.Connection,
    topic: str,
    kind: str,
    display: Optional[str] = None,
) -> AssistantAnswer:
    """Search for ``topic`` and summarize the hits.

    ``display`` overrides how the topic is named in the sentence -- the
    unrecognized-phrasing fallback quotes the words it actually
    searched for, so the user can see what Alfred took from the
    question rather than being told it understood it.
    """
    terms = extract_query_terms(topic) or topic.strip()
    if not terms:
        return AssistantAnswer(text=ASSISTANT_DECLINE, kind=kind)
    results = search(conn, terms, include_deleted=False)
    capped = results[:MAX_QUESTION_RESULTS]
    return AssistantAnswer(
        text=R.build_search_summary(
            display or topic, len(results), [m.title for m in capped]
        ),
        results=capped,
        kind=kind,
        memory_id=capped[0].id if capped else None,
        topic=topic,
    )


def _task_completion_answer(
    conn: sqlite3.Connection, payload: dict
) -> AssistantAnswer:
    """Complete the one open task the user says they finished.

    Matching is the same substring-over-significant-terms rule the rest
    of this module uses: the phrase is reduced to terms by
    :func:`core.assistant.extract_query_terms` (so "I finished the
    meeting" searches for ``meeting``), and a task matches when *every*
    term appears in its title or content. Requiring all of them keeps
    "the sensor wiring" from matching a task about a sensor and another
    about wiring.

    Only open tasks are searched -- Today plus Upcoming, which between
    them are every incomplete task. Completing something twice is not a
    thing the user can ask for, and an already-finished task is not
    evidence about which one they mean.

    Three outcomes, and only one of them writes:

    * exactly one match -- complete it and name it back;
    * more than one -- name the candidates and ask. Completing the wrong
      task is a silent data change the user has no reason to go looking
      for, so a guess is worse than a question;
    * none -- say so. Nothing is created: the user reported finishing
      work, not filing it.
    """
    terms = [t for t in (payload.get("terms") or "").split() if t]
    if not terms:  # pragma: no cover - classify rejects empty terms
        return AssistantAnswer(
            text=R.build_task_completion_none(),
            kind=KIND_TASK_COMPLETE,
            weight=WEIGHT_SUBSTANTIAL,
        )

    rows = list_tasks(conn, "today") + list_tasks(conn, "upcoming")
    matches = []
    for memory, _due, _done in rows:
        haystack = " ".join((memory.title, memory.content)).lower()
        if all(term in haystack for term in terms):
            matches.append(memory)

    if not matches:
        return AssistantAnswer(
            text=R.build_task_completion_none(),
            kind=KIND_TASK_COMPLETE,
            weight=WEIGHT_SUBSTANTIAL,
        )

    if len(matches) > 1:
        return AssistantAnswer(
            text=R.build_task_completion_ambiguous(
                [m.title for m in matches]
            ),
            results=matches[:MAX_QUESTION_RESULTS],
            kind=KIND_TASK_COMPLETE,
            weight=WEIGHT_SUBSTANTIAL,
        )

    target = matches[0]
    try:
        complete_task(conn, target.id)
    except sqlite3.Error:
        return AssistantAnswer(
            text=ASSISTANT_ERROR,
            kind=KIND_TASK_COMPLETE,
            weight=WEIGHT_SUBSTANTIAL,
        )
    return AssistantAnswer(
        text=R.build_task_completed(target.title),
        kind=KIND_TASK_COMPLETE,
        weight=WEIGHT_SUBSTANTIAL,
        memory_id=target.id,
        topic=target.title,
    )


def execute(
    conn: Optional[sqlite3.Connection],
    classification: Classification,
    context: Optional[SessionContext] = None,
) -> AssistantAnswer:
    """Run the lookup for ``classification`` and build the reply.

    ``conn`` may be ``None`` for weight-``none`` classifications, which
    by definition perform no query. Called either inline on the UI
    thread (none/trivial) or on a worker thread with its own connection
    (substantial) -- which is why nothing here touches a widget.
    """
    cls = classification
    kind = cls.kind
    payload = cls.payload
    ctx = context or SessionContext()

    if kind == KIND_CAPABILITIES:
        return AssistantAnswer(text=ASSISTANT_CAPABILITIES, kind=kind)

    if kind == KIND_VIEW:
        return AssistantAnswer(
            text=ASSISTANT_ACTION_CONFIRM,
            kind=kind,
            view_id=payload.get("view_id"),
        )

    if conn is None:  # pragma: no cover - defensive
        return AssistantAnswer(text=ASSISTANT_DECLINE, kind=kind)

    if kind == KIND_PROFILE_FIELD:
        field_name = payload.get("field", "")
        value = find_profile_fact(conn, field_name)
        text = (
            R.build_profile_fact(field_name, value)
            if value
            else ASSISTANT_UNKNOWN_FIELD
        )
        return AssistantAnswer(
            text=text, kind=kind, weight=WEIGHT_TRIVIAL, topic=field_name
        )

    if kind == KIND_CONTEXT_REUSE:
        return _context_answer(conn, ctx)

    if kind == KIND_TASKS:
        bucket = payload.get("bucket")
        if bucket == "today":
            rows = list_tasks(conn, "today")
            label = ASSISTANT_TASK_BUCKET_TODAY
        elif bucket == "tomorrow":
            # "due tomorrow" wants tomorrow alone. The ``today`` bucket
            # is now "due on or before the reference date", so pointing
            # it at tomorrow would drag today's and every overdue task
            # in with it; ``due_on`` is the exact-date query.
            rows = list_tasks(
                conn, "due_on", reference_date=date.today() + timedelta(days=1)
            )
            label = ASSISTANT_TASK_BUCKET_TOMORROW
        else:
            # Unqualified "my tasks" means everything still open. Today
            # (dated, due now or overdue) plus Upcoming (dated later, or
            # never dated) partition the incomplete tasks exactly once,
            # so adding them cannot double-count.
            rows = list_tasks(conn, "today") + list_tasks(conn, "upcoming")
            label = ASSISTANT_TASK_BUCKET_OPEN
        memories = [mem for mem, _due, _done in rows]
        return AssistantAnswer(
            text=R.build_task_list_summary(label, [m.title for m in memories]),
            results=memories[:MAX_QUESTION_RESULTS],
            kind=kind,
            weight=WEIGHT_SUBSTANTIAL,
        )

    if kind == KIND_TASK_COMPLETE:
        return _task_completion_answer(conn, payload)

    if kind == KIND_PENDING_ON:
        project = None
        for candidate in _entity_candidates(payload):
            project = find_project_by_name(conn, candidate)
            if project is not None:
                break
        if project is None:
            return AssistantAnswer(
                text=ASSISTANT_PROJECT_UNKNOWN,
                kind=kind,
                weight=WEIGHT_SUBSTANTIAL,
            )
        memories, open_titles, _done = _project_task_state(conn, project.id)
        by_title = {m.title: m for m in memories}
        return AssistantAnswer(
            text=R.build_pending_items_summary(project.name, open_titles),
            results=[
                by_title[t]
                for t in open_titles[:MAX_QUESTION_RESULTS]
                if t in by_title
            ],
            kind=kind,
            weight=WEIGHT_SUBSTANTIAL,
            project_id=project.id,
            topic=project.name,
        )

    if kind == KIND_LAST_WORKED:
        # A project name will not appear in any memory's text, so an
        # FTS search for it finds nothing. Check the project table
        # first and answer from its newest entry.
        for candidate in _entity_candidates(payload):
            project = find_project_by_name(conn, candidate)
            if project is None:
                continue
            memories, _open, _done = _project_task_state(conn, project.id)
            if not memories:
                continue
            newest = max(memories, key=lambda m: m.updated_at or "")
            return AssistantAnswer(
                text=R.build_last_worked_on(project.name, newest.updated_at),
                results=[newest],
                kind=kind,
                weight=WEIGHT_SUBSTANTIAL,
                project_id=project.id,
                memory_id=newest.id,
                topic=project.name,
            )
        answer = None
        for candidate in _entity_candidates(payload):
            answer = _search_answer(conn, candidate, kind)
            if answer.results:
                break
        if answer is None:  # pragma: no cover - payload always has one
            answer = _search_answer(conn, "", kind)
        if answer.results:
            newest = max(answer.results, key=lambda m: m.updated_at or "")
            answer.text = R.build_last_worked_on(
                newest.title, newest.updated_at
            )
            answer.results = [newest]
            answer.memory_id = newest.id
        answer.weight = WEIGHT_SUBSTANTIAL
        return answer

    if kind == KIND_YESTERDAY:
        memories = list_memories_updated_on(
            conn, date.today() - timedelta(days=1)
        )
        return AssistantAnswer(
            text=R.build_yesterday_summary([m.title for m in memories]),
            results=memories[:MAX_QUESTION_RESULTS],
            kind=kind,
            weight=WEIGHT_SUBSTANTIAL,
            memory_id=memories[0].id if memories else None,
        )

    if kind == KIND_CREATED_AT:
        topic = payload.get("topic", "")
        mem = None
        for candidate in _entity_candidates(payload):
            mem = find_entry_by_title_or_text(conn, candidate)
            if mem is not None:
                topic = candidate
                break
        if mem is None:
            return AssistantAnswer(
                text=ASSISTANT_CREATED_UNKNOWN,
                kind=kind,
                weight=WEIGHT_SUBSTANTIAL,
            )
        return AssistantAnswer(
            text=R.build_created_at(mem.title, mem.created_at),
            results=[mem],
            kind=kind,
            weight=WEIGHT_SUBSTANTIAL,
            memory_id=mem.id,
            topic=topic,
        )

    if kind == KIND_PROJECT_ACTION:
        # A write, so this is the one branch that changes the database.
        # The composition lives in core.projects.add_entry_to_project --
        # resolve-or-create, insert, link -- so nothing here builds SQL
        # or duplicates the project logic.
        try:
            project, memory_id, created = add_entry_to_project(
                conn,
                payload.get("name", ""),
                payload.get("body", ""),
                payload.get("entry_type", "task"),
            )
        except (sqlite3.Error, ValueError):
            return AssistantAnswer(
                text=ASSISTANT_ERROR, kind=kind, weight=WEIGHT_TRIVIAL
            )
        mem = get_memory(conn, memory_id)
        return AssistantAnswer(
            text=R.build_project_added(
                project.name, mem.title if mem else "", created
            ),
            results=[mem] if mem else [],
            kind=kind,
            weight=WEIGHT_TRIVIAL,
            # Deliberately no ``open_project_id``: filing something is
            # not a request to be taken anywhere. The user is talking to
            # the bubble, quite possibly over another application, and
            # the confirmation names both the project and the entry --
            # so raising the main window would interrupt to show what
            # has already been said. ``project_id`` still travels, which
            # refreshes the project in place if it happens to be on
            # screen behind the bubble.
            refresh_project_id=project.id,
            project_id=project.id,
            memory_id=memory_id,
            topic=project.name,
        )

    if kind == KIND_PROJECT_CREATE:
        # The other write branch, and the smaller one: a project and
        # nothing in it. get_or_create_project is the same resolve-or-
        # create the capture path and the project write already use, so
        # a project started by asking and a project started by filing
        # something under it can never end up as two rows. A name that
        # resolves to an existing project reports that instead of
        # starting a near-duplicate; the reply names the project it
        # landed on, so the resolution is visible either way.
        try:
            project, created = get_or_create_project(
                conn, payload.get("name", "")
            )
        except (sqlite3.Error, ValueError):
            return AssistantAnswer(
                text=ASSISTANT_ERROR, kind=kind, weight=WEIGHT_TRIVIAL
            )
        return AssistantAnswer(
            text=R.build_project_created(project.name, created),
            kind=kind,
            weight=WEIGHT_TRIVIAL,
            # No ``open_project_id``, for the same reason as the write
            # above: an empty project is nothing to look at, and being
            # taken to it is not what was asked for. Saying the name
            # back is the confirmation. "Open my Batcave project" is the
            # separate sentence for wanting to see it, and it still
            # does.
            refresh_project_id=project.id,
            project_id=project.id,
            topic=project.name,
        )

    if kind == KIND_PROJECT_OPEN:
        # Pure navigation: resolve the name, confirm, switch. Never
        # creates a project -- the user asked to see one, not to start
        # one -- and never falls back to a search.
        project = None
        for candidate in _entity_candidates(
            {"raw": payload.get("raw"), "topic": payload.get("name")}
        ):
            project = find_project_by_name(conn, candidate)
            if project is not None:
                break
        if project is None:
            return AssistantAnswer(
                text=R.build_project_unknown(payload.get("name", "")),
                kind=kind,
                weight=WEIGHT_TRIVIAL,
            )
        return AssistantAnswer(
            text=ASSISTANT_ACTION_CONFIRM,
            kind=kind,
            weight=WEIGHT_TRIVIAL,
            open_project_id=project.id,
            project_id=project.id,
            topic=project.name,
        )

    if kind == KIND_PROJECT_STATUS:
        project = None
        for candidate in _entity_candidates(
            {"raw": payload.get("raw"), "topic": payload.get("name")}
        ):
            project = find_project_by_name(conn, candidate)
            if project is not None:
                break
        if project is None:
            if payload.get("strict"):
                # The user said "project", so an unknown name is a
                # flat no -- and the view stays where it is. The name is
                # quoted back so the user can see what was looked for.
                return AssistantAnswer(
                    text=R.build_project_unknown(payload.get("name", "")),
                    kind=kind,
                    weight=WEIGHT_SUBSTANTIAL,
                )
            # "tell me about X" where X is not a project: answer it as
            # the search it actually is. No view switch, because
            # ``open_project_id`` stays unset.
            answer = _search_answer(
                conn, payload.get("name", ""), KIND_TOPIC_SEARCH
            )
            answer.weight = WEIGHT_SUBSTANTIAL
            return answer
        answer = _project_status_answer(conn, project, True)
        answer.weight = WEIGHT_SUBSTANTIAL
        return answer

    # KIND_TOPIC_SEARCH and KIND_FALLBACK_SEARCH.
    topic = payload.get("topic")
    display = None
    if not topic:
        # Unrecognized phrasing: search the stripped question and quote
        # the terms back, rather than pretending to have parsed it.
        topic = extract_query_terms(cls.text) or cls.text
        display = "“{0}”".format(topic)
    answer = _search_answer(
        conn, topic, kind or KIND_FALLBACK_SEARCH, display=display
    )
    answer.weight = WEIGHT_SUBSTANTIAL
    return answer


def _context_answer(
    conn: sqlite3.Connection, ctx: SessionContext
) -> AssistantAnswer:
    """Re-answer about whatever was last shown ("what about it?").

    Verbal only: a follow-up never re-opens a view, and it is always
    trivial weight because the reference is already resolved.
    """
    if ctx.last_shown_project_id is not None:
        project = get_project(conn, ctx.last_shown_project_id)
        if project is not None:
            answer = _project_status_answer(conn, project, False)
            answer.weight = WEIGHT_TRIVIAL
            answer.kind = KIND_CONTEXT_REUSE
            return answer
    if ctx.last_shown_memory_id is not None:
        mem = get_memory(conn, ctx.last_shown_memory_id)
        if mem is not None:
            return AssistantAnswer(
                text=R.build_context_memory(mem.title, mem.updated_at),
                results=[mem],
                kind=KIND_CONTEXT_REUSE,
                weight=WEIGHT_TRIVIAL,
                memory_id=mem.id,
            )
    return AssistantAnswer(
        text=ASSISTANT_CONTEXT_NONE,
        kind=KIND_CONTEXT_REUSE,
        weight=WEIGHT_TRIVIAL,
    )


# ---------------------------------------------------------------------------
# background worker
# ---------------------------------------------------------------------------

def connection_path(conn: sqlite3.Connection) -> Optional[str]:
    """Return the file backing ``conn``, or ``None`` for in-memory DBs.

    ``PRAGMA database_list`` reports an empty file for ``:memory:``,
    which is the signal to run the lookup synchronously instead of
    handing a path to a worker that cannot reach the same data.
    """
    try:
        for row in conn.execute("PRAGMA database_list").fetchall():
            if row[1] == "main":
                return row[2] or None
    except sqlite3.Error:  # pragma: no cover - defensive
        return None
    return None


class _LookupSignals(QObject):
    """Signal carrier for the worker.

    ``QRunnable`` is not a ``QObject``, so the signals live here. This
    object is created on the main thread, which is what makes the
    connections queued and the delivery main-thread-safe.
    """

    done = Signal(object)
    failed = Signal(str)


class _LookupRunnable(QRunnable):
    """Runs one substantial lookup on its own SQLite connection."""

    def __init__(
        self,
        db_path: str,
        classification: Classification,
        context: SessionContext,
        signals: _LookupSignals,
    ) -> None:
        super().__init__()
        self._db_path = db_path
        self._classification = classification
        self._context = context
        self.signals = signals

    def run(self) -> None:  # pragma: no cover - exercised via the controller
        conn = None
        answer = None
        try:
            # A second, short-lived connection: SQLite objects cannot
            # cross threads, and this one is opened and closed entirely
            # inside this worker.
            conn = get_connection(self._db_path)
            answer = execute(conn, self._classification, self._context)
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            self.signals.failed.emit(str(exc))
            return
        finally:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
        self.signals.done.emit(answer)


# ---------------------------------------------------------------------------
# controller
# ---------------------------------------------------------------------------

class AssistantController(QObject):
    """Routes one submitted line to an answer, a capture, or a view.

    Signals, in the order the popup consumes them:

    ``thinking_started``
        A substantial lookup has begun. Emitted immediately on
        submission -- there is no gate in front of it -- at most once per
        submission, and *never* for none- or trivial-weight work.
    ``answer_ready(AssistantAnswer)``
        The reply is ready to render.
    ``captured(int)``
        The input was a "remember ..." capture; carries the new row id.
    ``failed(str)``
        The lookup raised. ``answer_ready`` still fires with a polite
        message, so the popup has something to show either way.
    """

    #: Every submission, whatever its weight. This is the *pose* cue, not
    #: the bubble's placeholder line: Alfred visibly thinks whenever he is
    #: asked something, because a butler who answers without reacting reads
    #: as a text box. ``thinking_started`` stays substantial-only so the
    #: words "Lemme check, sir." keep meaning that there is something to
    #: check -- the two cues are deliberately not the same signal.
    request_started = Signal()
    thinking_started = Signal()
    answer_ready = Signal(object)
    captured = Signal(int)
    failed = Signal(str)

    def __init__(
        self,
        conn: sqlite3.Connection,
        context: Optional[SessionContext] = None,
        threaded: bool = True,
        parent: Optional[QObject] = None,
        min_hold_ms: int = THINKING_MIN_HOLD_MS,
        settings_provider: Optional[Callable[[], dict]] = None,
    ) -> None:
        super().__init__(parent)
        self.conn = conn
        self.context = context if context is not None else SessionContext()
        self._threaded = threaded
        self._db_path = connection_path(conn)

        #: Zero-arg callable returning the live settings dict, or None.
        #: A *callable* rather than the dict itself because
        #: ``MainWindow._on_settings_saved`` rebinds ``self.settings`` to
        #: a new dict when the user saves -- a reference captured once at
        #: construction would go stale the first time Settings is used.
        #: ``None`` means "no settings available", under which keyword
        #: auto-linking is simply off (the headless/test default).
        self._settings_provider = settings_provider

        #: Settable so a test can take the hold out of the way when it
        #: is asserting routing rather than timing. Zero disables it
        #: entirely, and the answer is then published the moment the
        #: lookup returns.
        self.min_hold_ms = int(min_hold_ms)

        # One half of the reveal. Started alongside the lookup, never
        # after it, so the minimum and the real work overlap instead of
        # stacking.
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._on_hold_elapsed)
        self._held_answer: Optional[AssistantAnswer] = None
        self._held_failure: Optional[str] = None

        self._request_id = 0
        self._inflight: Optional[_LookupSignals] = None

        # Test/introspection counters. ``thinking_engaged_count`` is the
        # honest measure of the two-axis rule: it only ever increments
        # for substantial work, so a slow trivial lookup cannot show
        # thinking even in principle.
        self.thinking_engaged_count = 0
        self.thinking_shown = False

    # ----- public API -----

    def submit(self, text: str) -> Classification:
        """Classify ``text``, then capture, navigate, or look it up."""
        cls = classify(text, self.context)
        self._abandon_hold()
        self._request_id += 1
        request_id = self._request_id
        self.thinking_shown = False
        # Before any routing decision: he was asked something, so he
        # reacts. Emitted for captures too -- "remember that ..." is still
        # a thing said to him that he then acts on.
        self.request_started.emit()

        if cls.outcome == OUTCOME_CAPTURE:
            # A capture may name the project it belongs in. The note is
            # filed either way; the phrase only ever adds a link.
            content, project_name = parse_project_assignment(text)
            new_id = int(_core_ask(self.conn, content).get("memory_id") or 0)
            if new_id:
                # Both post-steps read the *stored* text, not the raw
                # line: the project phrase has already been stripped out
                # of it, so "... to the Friday project" cannot be read
                # as a due date.
                self._apply_parsed_due_date(new_id, content)
                if project_name:
                    self._assign_project(new_id, project_name)
                else:
                    # Keyword rules are the fallback, never an override:
                    # an explicit "to the X project" is the user saying
                    # it outright, and outranks a substring guess.
                    self._apply_topic_keywords(new_id, content)
            self.context.last_shown_memory_id = None
            self.captured.emit(new_id)
            return cls

        if not cls.needs_lookup:
            self._deliver(execute(None, cls, self.context), request_id)
            return cls

        if cls.weight == WEIGHT_TRIVIAL:
            # One row, one regex: run it inline on the owning
            # connection. No gate is armed, so however long this takes,
            # the user never sees a thinking state for it.
            self._finish_inline(cls, request_id)
            return cls

        # Substantial: say so immediately, start the minimum-visibility
        # hold, then do the work off the UI thread.
        self._engage_thinking()
        self._start_hold()
        if self._threaded and self._db_path:
            signals = _LookupSignals()
            signals.done.connect(
                lambda answer, rid=request_id: self._deliver(answer, rid)
            )
            signals.failed.connect(
                lambda message, rid=request_id: self._on_failed(message, rid)
            )
            self._inflight = signals
            QThreadPool.globalInstance().start(
                _LookupRunnable(
                    self._db_path, cls, replace(self.context), signals
                )
            )
        else:
            # No file to open a second connection against (in-memory
            # DB) or threading disabled for a test: run it here. The
            # answer is still held for the minimum, and the event loop
            # is free for all but the query itself -- so the thinking
            # state is shown for this path too.
            self._finish_inline(cls, request_id)
        return cls

    def reset_context(self) -> None:
        """Forget what the last exchange was about."""
        self.context.clear()

    # ----- internals -----

    def _apply_parsed_due_date(self, memory_id: int, content: str) -> None:
        """Promote a just-captured note to a dated task, if it named a day.

        Runs on every capture, prefix or no prefix: "I have a meeting
        tomorrow at 6pm" is a task whether or not the user thought to say
        "remember". When :func:`core.date_parsing.parse_relative_date`
        finds nothing, this is a no-op and the capture stays an inbox
        item exactly as before.

        Nothing is stripped from the content. The date phrase is part of
        what the user wrote, and the stored text should read back as they
        said it; the parsed date is *additional* structure, not a
        replacement for the sentence. Note that only the date survives --
        the schema has no due *time*, so the "at 6pm" above remains
        ordinary text in the body and is not scheduled.

        A failure here costs the promotion and nothing else: the note is
        already committed, and losing a capture over a due date would be
        the worse trade (same rule as :meth:`_assign_project`).
        """
        due = parse_relative_date(content)
        if due is None:
            return
        try:
            convert_to_task(self.conn, memory_id, due_date=due.isoformat())
        except sqlite3.Error:
            pass  # the note is safe; only the promotion was lost

    def _topic_keywords(self) -> dict:
        """Return the configured ``{project: [keyword, ...]}`` rules.

        Defensive about shape because this dict is user-editable JSON on
        disk: anything that isn't a name mapped to a list of strings is
        skipped rather than raised on. No provider (or no rules) yields
        an empty dict, which makes the whole feature a no-op.
        """
        if self._settings_provider is None:
            return {}
        try:
            settings = self._settings_provider() or {}
        except Exception:  # pragma: no cover - a provider should not raise
            return {}
        raw = settings.get("topic_keywords") if isinstance(settings, dict) else None
        if not isinstance(raw, dict):
            return {}
        cleaned: dict = {}
        for name, keywords in raw.items():
            if not isinstance(name, str) or not name.strip():
                continue
            if isinstance(keywords, str):
                keywords = [keywords]
            if not isinstance(keywords, (list, tuple)):
                continue
            terms = [
                k.strip().lower()
                for k in keywords
                if isinstance(k, str) and k.strip()
            ]
            if terms:
                cleaned[name.strip()] = terms
        return cleaned

    def _match_topic_keywords(self, content: str) -> str:
        """Return the project whose keywords ``content`` mentions, or "".

        Plain case-insensitive substring matching, nothing cleverer -- no
        stemming, no fuzzy distance, no semantics. The first configured
        group with a hit wins, in the order the settings file lists them,
        so a capture that mentions two groups' keywords lands in one
        project rather than being split or duplicated.
        """
        haystack = (content or "").lower()
        if not haystack:
            return ""
        for name, keywords in self._topic_keywords().items():
            if any(keyword in haystack for keyword in keywords):
                return name
        return ""

    def _apply_topic_keywords(self, memory_id: int, content: str) -> None:
        """File a capture under a project its text matched by keyword."""
        name = self._match_topic_keywords(content)
        if name:
            self._assign_project(memory_id, name)

    def _assign_project(self, memory_id: int, project_name: str) -> None:
        """Link a just-captured note to the project it named.

        The note row is already committed by the time this runs, so a
        name the database won't take costs the link and nothing else --
        losing the capture over a failed link would be the worse trade.
        """
        try:
            project, _created = get_or_create_project(self.conn, project_name)
            set_memory_project(self.conn, memory_id, project.id)
        except (sqlite3.Error, ValueError):
            pass  # the note is safe; only the link was lost

    def _finish_inline(self, cls: Classification, request_id: int) -> None:
        try:
            answer = execute(self.conn, cls, self.context)
        except Exception as exc:  # noqa: BLE001 - surfaced, never raised
            self._on_failed(str(exc), request_id)
            return
        self._deliver(answer, request_id)

    def _engage_thinking(self) -> None:
        """Show the thinking state. Substantial work only, no gate."""
        self.thinking_engaged_count += 1
        self.thinking_shown = True
        self.thinking_started.emit()

    def _start_hold(self) -> None:
        """Start the minimum-visibility window for the answer."""
        if self.min_hold_ms > 0:
            self._hold_timer.start(self.min_hold_ms)

    def _abandon_hold(self) -> None:
        """Drop any held answer: a newer submission supersedes it."""
        self._hold_timer.stop()
        self._held_answer = None
        self._held_failure = None

    def _on_hold_elapsed(self) -> None:
        """The minimum is up. Publish the answer if it already landed.

        Nothing to do when it hasn't: the lookup is still running, and
        :meth:`_settle` will publish it the moment it returns.
        """
        answer = self._held_answer
        failure = self._held_failure
        self._held_answer = None
        self._held_failure = None
        if answer is not None:
            self._publish(answer, failure)

    def _settle(
        self,
        answer: AssistantAnswer,
        failure: Optional[str],
        request_id: int,
    ) -> None:
        """The lookup is done. Publish it, or park it until the hold is.

        This is the join between the two independent completions --
        worker-finished and minimum-elapsed -- and the reason neither one
        has to wait on a sleep. If the timer is still running the answer
        is parked here and published by :meth:`_on_hold_elapsed`;
        otherwise the minimum has already passed and it goes out now.
        The worker has finished either way, so parking costs it nothing
        and the event loop keeps turning throughout.
        """
        if request_id != self._request_id:
            return  # a newer submission superseded this one
        if self._hold_timer.isActive():
            self._held_answer = answer
            self._held_failure = failure
            return
        self._publish(answer, failure)

    def _publish(
        self, answer: AssistantAnswer, failure: Optional[str]
    ) -> None:
        """Emit one settled result, in the order the popup expects."""
        self._hold_timer.stop()
        self._inflight = None
        if failure is not None:
            self.failed.emit(failure)
        self._remember(answer)
        self.answer_ready.emit(answer)

    def _deliver(self, answer: AssistantAnswer, request_id: int) -> None:
        self._settle(answer, None, request_id)

    def _on_failed(self, message: str, request_id: int) -> None:
        # The popup always gets something renderable, so the failure
        # goes out alongside a polite answer rather than instead of one.
        self._settle(
            AssistantAnswer(text=ASSISTANT_ERROR, kind=KIND_FALLBACK_SEARCH),
            message,
            request_id,
        )

    def _remember(self, answer: AssistantAnswer) -> None:
        """Record what this answer was about, for the next follow-up."""
        if answer.project_id is not None:
            self.context.last_shown_project_id = answer.project_id
            self.context.last_shown_memory_id = None
        elif answer.memory_id is not None:
            self.context.last_shown_memory_id = answer.memory_id
            self.context.last_shown_project_id = None
        if answer.topic:
            self.context.last_topic = answer.topic
