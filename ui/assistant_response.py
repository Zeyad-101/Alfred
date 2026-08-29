"""Pure response templates for the butler interaction layer.

Every function here is a plain string builder: no Qt, no database, no
I/O. Callers pass already-fetched data in and get a finished sentence
out, which makes the grammar (list joining, singular/plural, date
formatting) unit-testable on its own.

Nothing in this module *understands* anything. The wording is
hand-written in :mod:`ui.strings` and filled with real values; the only
"intelligence" is picking the right template and getting the English
right for 0, 1, 2, and 3+ items.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional, Sequence

from ui.strings import (
    ASSISTANT_PROJECT_ADDED,
    ASSISTANT_PROJECT_ADDED_NEW,
    ASSISTANT_PROJECT_EXISTS,
    ASSISTANT_PROJECT_STARTED,
    ASSISTANT_PROJECT_UNKNOWN,
    ASSISTANT_PROJECT_UNKNOWN_NAMED,
    ASSISTANT_CONTEXT_MEMORY,
    ASSISTANT_CREATED_AT,
    ASSISTANT_LAST_WORKED,
    ASSISTANT_LIST_AND,
    ASSISTANT_LIST_MORE,
    ASSISTANT_NO_MATCH,
    ASSISTANT_NOUN_ENTRIES,
    ASSISTANT_NOUN_ENTRY,
    ASSISTANT_NOUN_MEMORIES,
    ASSISTANT_NOUN_MEMORY,
    ASSISTANT_NOUN_OPEN_TASK,
    ASSISTANT_NOUN_OPEN_TASKS,
    ASSISTANT_NOUN_TASK,
    ASSISTANT_NOUN_TASKS,
    ASSISTANT_PENDING_NONE,
    ASSISTANT_PENDING_SUMMARY,
    ASSISTANT_PROFILE_FACT,
    ASSISTANT_PROJECT_ALL_DONE,
    ASSISTANT_PROJECT_OUTSTANDING,
    ASSISTANT_PROJECT_STATUS,
    ASSISTANT_PROJECT_STATUS_EMPTY,
    ASSISTANT_SEARCH_SUMMARY,
    ASSISTANT_TASK_DONE,
    ASSISTANT_TASK_DONE_AMBIGUOUS,
    ASSISTANT_TASK_DONE_NONE,
    ASSISTANT_TASKS_NONE,
    ASSISTANT_TASKS_SUMMARY,
    ASSISTANT_YESTERDAY,
    ASSISTANT_YESTERDAY_NONE,
)

#: Default cap on how many titles a summary sentence will name.
MAX_TITLES = 3


# ---------------------------------------------------------------------------
# grammar helpers
# ---------------------------------------------------------------------------

def pluralize(count: int, singular: str, plural: str) -> str:
    """Return ``"1 task"`` / ``"2 tasks"`` -- count plus the right noun."""
    return f"{count} {singular if count == 1 else plural}"


def join_list(items: Iterable[str], max_items: Optional[int] = None) -> str:
    """Join ``items`` the way a person would write them.

    One item stays bare, two are joined with "and", three or more take
    the serial comma. When ``max_items`` is set and there are more, the
    overflow becomes a final "N more" element so the sentence still
    reads as a list::

        []              -> ""
        [A]             -> "A"
        [A, B]          -> "A and B"
        [A, B, C]       -> "A, B, and C"
        [A..E], max 3   -> "A, B, C, and 2 more"
    """
    cleaned = [str(i).strip() for i in items if str(i).strip()]
    if not cleaned:
        return ""

    if max_items is not None and len(cleaned) > max_items:
        overflow = len(cleaned) - max_items
        cleaned = cleaned[:max_items]
        cleaned.append(ASSISTANT_LIST_MORE.format(count=overflow))

    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} {ASSISTANT_LIST_AND} {cleaned[1]}"
    return f"{', '.join(cleaned[:-1])}, {ASSISTANT_LIST_AND} {cleaned[-1]}"


def format_when(value: object) -> str:
    """Format a stored ISO timestamp as "5 March 2026", in local time.

    Timestamps are written as UTC (``core.db.now``); the user thinks in
    local calendar days, so tz-aware values are converted before the
    date is taken. Anything unparseable falls back to the leading date
    portion of the raw string so we never raise inside a template.
    """
    if value is None:
        return ""
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return f"{parsed.day} {parsed.strftime('%B %Y')}"


# ---------------------------------------------------------------------------
# single-fact answers
# ---------------------------------------------------------------------------

def build_profile_fact(field: str, value: str) -> str:
    """"Your name is Bruce, sir." -- a stored single fact, read back."""
    return ASSISTANT_PROFILE_FACT.format(
        field=field.strip(), value=str(value).strip().rstrip(".")
    )


def build_created_at(title: str, created_at: object) -> str:
    return ASSISTANT_CREATED_AT.format(title=title, when=format_when(created_at))


def build_last_worked_on(title: str, updated_at: object) -> str:
    return ASSISTANT_LAST_WORKED.format(title=title, when=format_when(updated_at))


def build_context_memory(title: str, updated_at: object) -> str:
    return ASSISTANT_CONTEXT_MEMORY.format(
        title=title, when=format_when(updated_at)
    )


# ---------------------------------------------------------------------------
# list answers
# ---------------------------------------------------------------------------

def build_search_summary(
    topic: str, count: int, titles: Sequence[str], max_titles: int = MAX_TITLES
) -> str:
    """Summarize a topic search: how many, and which ones by name."""
    if count <= 0:
        return ASSISTANT_NO_MATCH.format(topic=topic)
    return ASSISTANT_SEARCH_SUMMARY.format(
        count=pluralize(count, ASSISTANT_NOUN_ENTRY, ASSISTANT_NOUN_ENTRIES),
        topic=topic,
        titles=join_list(titles, max_items=max_titles),
    )


def build_task_list_summary(
    bucket_label: str, tasks: Sequence[str], max_titles: int = MAX_TITLES
) -> str:
    """Summarize a task bucket ("due today", "still open", ...)."""
    if not tasks:
        return ASSISTANT_TASKS_NONE.format(bucket_label=bucket_label)
    return ASSISTANT_TASKS_SUMMARY.format(
        count=pluralize(len(tasks), ASSISTANT_NOUN_TASK, ASSISTANT_NOUN_TASKS),
        bucket_label=bucket_label,
        titles=join_list(tasks, max_items=max_titles),
    )


def build_task_completed(title: str) -> str:
    """Confirm one task was marked complete, naming it back.

    Naming it is the point: the user said "I finished the meeting" and
    Alfred picked a row out of the open tasks on their behalf. Echoing
    the title is how they see *which* row, and catch it immediately if
    it was the wrong one.
    """
    return ASSISTANT_TASK_DONE.format(title=(title or "").strip())


def build_task_completion_ambiguous(
    titles: Sequence[str], max_titles: int = MAX_TITLES
) -> str:
    """Two or more open tasks match: name them and ask which.

    Deliberately does not complete anything. Guessing would be a silent
    write to the wrong row, and completion is the one operation the user
    has no reason to go back and check.
    """
    return ASSISTANT_TASK_DONE_AMBIGUOUS.format(
        titles=join_list(titles, max_items=max_titles)
    )


def build_task_completion_none() -> str:
    """Nothing matched. A decline, not a capture -- the user was telling
    Alfred about work that is already on file, so inventing a new task
    from the sentence would be the wrong reading of it."""
    return ASSISTANT_TASK_DONE_NONE


def build_pending_items_summary(
    topic: str, pending_titles: Sequence[str], max_titles: int = MAX_TITLES
) -> str:
    """Answer "what's left on X" -- outstanding work, named."""
    if not pending_titles:
        return ASSISTANT_PENDING_NONE.format(topic=topic)
    return ASSISTANT_PENDING_SUMMARY.format(
        count=pluralize(
            len(pending_titles), ASSISTANT_NOUN_TASK, ASSISTANT_NOUN_TASKS
        ),
        topic=topic,
        titles=join_list(pending_titles, max_items=max_titles),
    )


def build_yesterday_summary(
    titles: Sequence[str], max_titles: int = MAX_TITLES
) -> str:
    if not titles:
        return ASSISTANT_YESTERDAY_NONE
    return ASSISTANT_YESTERDAY.format(
        titles=join_list(titles, max_items=max_titles)
    )


def build_project_status(
    project_name: str,
    memory_count: int,
    task_counts: dict,
    pending_task_titles: Sequence[str],
    max_titles: int = MAX_TITLES,
) -> str:
    """Full project summary: size, open tasks, and what's outstanding.

    ``task_counts`` is a mapping with an ``"open"`` key (and optionally
    ``"done"``); only the open count is spoken, since that's the part
    that tells the user whether the project needs them.
    """
    open_count = int(task_counts.get("open", 0) or 0)
    if memory_count == 0 and open_count == 0:
        return ASSISTANT_PROJECT_STATUS_EMPTY.format(project=project_name)

    sentence = ASSISTANT_PROJECT_STATUS.format(
        project=project_name,
        memories=pluralize(
            memory_count, ASSISTANT_NOUN_MEMORY, ASSISTANT_NOUN_MEMORIES
        ),
        tasks=pluralize(
            open_count, ASSISTANT_NOUN_OPEN_TASK, ASSISTANT_NOUN_OPEN_TASKS
        ),
    )
    if pending_task_titles:
        sentence += ASSISTANT_PROJECT_OUTSTANDING.format(
            titles=join_list(pending_task_titles, max_items=max_titles)
        )
    else:
        sentence += ASSISTANT_PROJECT_ALL_DONE
    return sentence


def build_project_unknown(project_name: str) -> str:
    """Decline a question about a project that isn't on file.

    Names the project back so the user can see which spelling Alfred
    looked for -- the same courtesy the search fallback extends by
    quoting the terms it used.
    """
    name = (project_name or "").strip()
    if not name:
        return ASSISTANT_PROJECT_UNKNOWN
    return ASSISTANT_PROJECT_UNKNOWN_NAMED.format(project=name)


def build_project_added(
    project_name: str, title: str, project_created: bool
) -> str:
    """Confirm a write into a project, saying so when it is brand new."""
    template = (
        ASSISTANT_PROJECT_ADDED_NEW if project_created else ASSISTANT_PROJECT_ADDED
    )
    return template.format(project=project_name, title=title)


def build_project_created(project_name: str, project_created: bool) -> str:
    """Confirm a project the user asked for outright, new or not.

    ``project_created`` comes straight from
    :func:`core.projects.get_or_create_project`, so a name that
    resolved to an existing project reports that instead of claiming a
    second one was started. Either way the *resolved* name is what gets
    said back, which is how the user sees that "the bat project" landed
    on "Batcave" rather than starting something new.
    """
    template = (
        ASSISTANT_PROJECT_STARTED if project_created else ASSISTANT_PROJECT_EXISTS
    )
    return template.format(project=project_name)
