"""Project CRUD and the memory<->project link table.

The model is intentionally simple: a memory belongs to AT MOST one
project, and a project can hold many memories. Links live in
``memory_projects`` with a composite PK; ``ON DELETE CASCADE`` on the
project FK is what makes "delete a project" safe -- the links go away
without the memories themselves being touched.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from core.db import now as _now
from core.inbox import derive_title
from core.memory import Memory, _memories_from_rows, create_memory


@dataclass
class Project:
    """A named container for memories.

    ``memory_count`` is populated by ``list_projects`` (so the sidebar
    can show "Project Name  (3)" without an N+1). For ``get_project``,
    which only ever returns a single project, the count stays at 0 --
    callers that need a count for a single id can compute it
    themselves or call ``list_projects`` and pick the row out.
    """
    id: int
    name: str
    description: str
    created_at: str
    memory_count: int = 0


def _row_to_project(row: sqlite3.Row, memory_count: int = 0) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        created_at=row["created_at"],
        memory_count=memory_count,
    )


def create_project(
    conn: sqlite3.Connection, name: str, description: str = ""
) -> int:
    """Insert a new project and return its id."""
    cur = conn.execute(
        "INSERT INTO projects(name, description, created_at) VALUES (?, ?, ?)",
        (name, description, _now()),
    )
    conn.commit()
    return cur.lastrowid


def get_project(conn: sqlite3.Connection, project_id: int) -> Project | None:
    """Return a single project by id, or None if not found."""
    row = conn.execute(
        "SELECT id, name, description, created_at FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_project(row)


def list_projects(conn: sqlite3.Connection) -> list[Project]:
    """Return every project, ordered by name, with a live memory count.

    The count is the number of non-deleted memories linked via
    ``memory_projects``. The query is a single round-trip (no N+1):
    a LEFT JOIN on the link table filtered by ``is_deleted = 0`` plus
    a GROUP BY. Projects with no links still show up (with count 0)
    thanks to the LEFT JOIN.
    """
    rows = conn.execute(
        "SELECT p.id, p.name, p.description, p.created_at, "
        "       COUNT(m.id) AS memory_count "
        "FROM projects p "
        "LEFT JOIN memory_projects mp ON mp.project_id = p.id "
        "LEFT JOIN memories m "
        "  ON m.id = mp.memory_id AND m.is_deleted = 0 "
        "GROUP BY p.id "
        "ORDER BY p.name COLLATE NOCASE"
    ).fetchall()
    return [_row_to_project(r, memory_count=r["memory_count"]) for r in rows]


def find_project_by_name(
    conn: sqlite3.Connection, fragment: str
) -> Optional[Project]:
    """Find one project by a case-insensitive name fragment.

    Used by the butler layer, where the user says "the batman project"
    and means the one called "Batman". Substring matching only -- no
    fuzzy or semantic matching. An exact (case-insensitive) name always
    wins over a substring hit; otherwise the shortest matching name
    wins, on the reasoning that "bat" should resolve to "Bats" rather
    than "Batcave Renovations" when both exist.
    """
    needle = (fragment or "").strip()
    if not needle:
        return None

    row = conn.execute(
        "SELECT * FROM projects WHERE name = ? COLLATE NOCASE LIMIT 1",
        (needle,),
    ).fetchone()
    if row is not None:
        return _row_to_project(row)

    like = f"%{needle}%"
    row = conn.execute(
        "SELECT * FROM projects WHERE name LIKE ? COLLATE NOCASE "
        "ORDER BY LENGTH(name), name LIMIT 1",
        (like,),
    ).fetchone()
    return _row_to_project(row) if row is not None else None


def get_or_create_project(
    conn: sqlite3.Connection, name: str
) -> tuple[Project, bool]:
    """Resolve ``name`` to a project, creating one only if none matches.

    Returns ``(project, created)``. Resolution goes through
    :func:`find_project_by_name` rather than an exact-match query of its
    own, so the name that answers "what's left on the ESP32 project"
    resolves a capture to that same project. If the two disagreed, a
    capture could quietly create a second project the question layer
    would never look at. Creation is the fallback, not the default.

    Only used by the butler layer's capture path so far; the JSON
    importer keeps its own cached variant, which is warranted there
    because it resolves a name per imported row.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("project name must not be empty")

    existing = find_project_by_name(conn, cleaned)
    if existing is not None:
        return existing, False

    new_id = create_project(conn, cleaned)
    created = get_project(conn, new_id)
    if created is None:  # pragma: no cover - the row was just inserted
        raise sqlite3.Error("project disappeared immediately after insert")
    return created, True


def rename_project(
    conn: sqlite3.Connection,
    project_id: int,
    name: str | None = None,
    description: str | None = None,
) -> None:
    """Update only the provided fields. No-op if ``project_id`` is gone.

    Both kwargs are optional; passing neither is a valid (no-op) call
    (kept as a no-op rather than raising so callers can use a
    consistent "I have these values, please apply what I passed"
    pattern without conditionals).
    """
    if name is None and description is None:
        return

    row = conn.execute(
        "SELECT name, description FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if row is None:
        return

    new_name = name if name is not None else row["name"]
    new_description = description if description is not None else row["description"]
    conn.execute(
        "UPDATE projects SET name = ?, description = ? WHERE id = ?",
        (new_name, new_description, project_id),
    )
    conn.commit()


def delete_project(conn: sqlite3.Connection, project_id: int) -> None:
    """Hard-delete a project. The memories it linked are left alone.

    ``ON DELETE CASCADE`` on ``memory_projects.project_id`` removes
    the link rows. The memories themselves have no FK to ``projects``,
    so they stay -- they simply become unassigned. (A test asserts this
    explicitly.)
    """
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()


def list_project_memories(
    conn: sqlite3.Connection, project_id: int
) -> list[Memory]:
    """Return the project's memories, pinned-first then most-recently-updated.

    Mirrors ``core.memory.list_memories``'s ordering so a project's
    view feels identical to All Memories from the user's perspective.
    Soft-deleted memories are excluded by the WHERE clause -- there's
    no point showing trash here.
    """
    rows = conn.execute(
        "SELECT m.* FROM memories m "
        "JOIN memory_projects mp ON mp.memory_id = m.id "
        "WHERE mp.project_id = ? AND m.is_deleted = 0 "
        "ORDER BY m.is_pinned DESC, m.updated_at DESC",
        (project_id,),
    ).fetchall()
    return _memories_from_rows(conn, rows)


def get_memory_project(
    conn: sqlite3.Connection, memory_id: int
) -> Project | None:
    """Return the project ``memory_id`` is assigned to, or None.

    A memory belongs to at most one project in this model, so this is
    a single-row lookup (LIMIT 1, but the composite PK on
    ``memory_projects`` plus the absence of any other write path
    means there's only ever one row to find).
    """
    row = conn.execute(
        "SELECT p.id, p.name, p.description, p.created_at "
        "FROM projects p "
        "JOIN memory_projects mp ON mp.project_id = p.id "
        "WHERE mp.memory_id = ?",
        (memory_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_project(row)


def set_memory_project(
    conn: sqlite3.Connection,
    memory_id: int,
    project_id: int | None,
) -> None:
    """Assign ``memory_id`` to ``project_id``, replacing any prior link.

    Passing ``None`` unassigns. Safe to call repeatedly (idempotent):
    the DELETE+INSERT pattern always converges to a single row (or
    zero rows, when project_id is None). The memory row itself is
    never touched.
    """
    conn.execute(
        "DELETE FROM memory_projects WHERE memory_id = ?", (memory_id,)
    )
    if project_id is not None:
        conn.execute(
            "INSERT INTO memory_projects(memory_id, project_id) VALUES (?, ?)",
            (memory_id, project_id),
        )
    conn.commit()


def add_entry_to_project(
    conn: sqlite3.Connection,
    project_name: str,
    content: str,
    entry_type: str = "task",
) -> tuple["Project", int, bool]:
    """File ``content`` as a new ``entry_type`` row inside a project.

    Returns ``(project, memory_id, project_was_created)``. This is the
    write behind "add a task to my ESP32 project: test the sensor" -- one
    place that composes the three existing steps (resolve-or-create the
    project, create the memory, link it) so no caller has to reimplement
    the sequence or reach for SQL of its own.

    The project is resolved with :func:`get_or_create_project`, so an
    existing name is reused and only a genuinely new one is inserted.
    Titles come from :func:`core.inbox.derive_title`, the same derivation
    a quick capture uses, which keeps a task added by voice-shaped
    request indistinguishable from one typed into the editor.

    Raises ``ValueError`` if either the project name or the content is
    blank -- an empty task is never what the user meant.
    """
    body = (content or "").strip()
    if not body:
        raise ValueError("content must not be empty")
    project, created = get_or_create_project(conn, project_name)
    memory_id = create_memory(
        conn, title=derive_title(body), content=body, type=entry_type
    )
    set_memory_project(conn, memory_id, project.id)
    return project, memory_id, created
