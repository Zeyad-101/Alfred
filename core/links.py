"""Symmetric, undirected links between two memories.

The model is intentionally tiny: a single link-table row means "these
two memories are related". There is one link type, no extra payload,
no direction. The ``CHECK (memory_id_a < memory_id_b)`` constraint
plus the canonical-order normalization in :func:`link_memories` mean
a pair (5, 12) is stored exactly one way -- the smaller id first --
so lookups in either direction are a single-row query.
"""
from __future__ import annotations

import sqlite3

from core.db import now as _now
from core.memory import Memory, _memories_from_rows


def _normalize(a: int, b: int) -> tuple[int, int]:
    """Return ``(min, max)`` for the link pair.

    Centralized so :func:`link_memories`, :func:`unlink_memories`, and
    :func:`are_linked` all agree on which side is "a" and which is
    "b". A self-link is a programming error in the link call, but
    it's also caught here so the callers can produce a clear
    :class:`ValueError` rather than relying on the CHECK constraint
    to fire with a generic SQLite message.
    """
    if a == b:
        raise ValueError("a memory cannot be linked to itself")
    return (a, b) if a < b else (b, a)


def link_memories(
    conn: sqlite3.Connection, memory_id_a: int, memory_id_b: int
) -> None:
    """Create a link between two memories. Idempotent.

    A second call with the same pair (in either order) is a no-op,
    not an error -- the editor's "+ Add Related" button guards
    against duplicates at the UI level, but a defensive idempotent
    write here means a re-entrant click (or a test that calls twice)
    can't crash the app.

    The link row is stored in canonical order (``(min, max)``) so
    the PK on ``(memory_id_a, memory_id_b)`` plus the CHECK
    constraint together guarantee one row per pair.
    """
    a, b = _normalize(memory_id_a, memory_id_b)
    conn.execute(
        "INSERT OR IGNORE INTO memory_links(memory_id_a, memory_id_b, created_at) "
        "VALUES (?, ?, ?)",
        (a, b, _now()),
    )
    conn.commit()


def unlink_memories(
    conn: sqlite3.Connection, memory_id_a: int, memory_id_b: int
) -> None:
    """Remove the link between two memories. No-op if no link exists.

    Symmetric in the same way as :func:`link_memories`: the argument
    order doesn't matter because we re-normalize before the DELETE.
    """
    a, b = _normalize(memory_id_a, memory_id_b)
    conn.execute(
        "DELETE FROM memory_links WHERE memory_id_a = ? AND memory_id_b = ?",
        (a, b),
    )
    conn.commit()


def list_linked_memories(
    conn: sqlite3.Connection, memory_id: int
) -> list[Memory]:
    """Return every memory linked to ``memory_id``, by title.

    The query joins both link-table positions via two separate
    predicates OR'd together -- there is no way to write a single
    predicate that captures both because of the ``a < b`` CHECK
    constraint, and that's fine: a UNION-style OR over a tiny
    table (one row per pair) is cheaper than any graph traversal
    would be.

    Soft-deleted linked memories are excluded: a link to a memory
    in the trash is not a useful relationship to surface, and the
    trash view is the place the user goes to deal with that
    memory anyway.
    """
    rows = conn.execute(
        "SELECT m.* FROM memories m "
        "WHERE m.is_deleted = 0 AND ("
        "  m.id IN (SELECT memory_id_b FROM memory_links WHERE memory_id_a = ?)"
        "  OR "
        "  m.id IN (SELECT memory_id_a FROM memory_links WHERE memory_id_b = ?)"
        ") "
        "ORDER BY m.title COLLATE NOCASE",
        (memory_id, memory_id),
    ).fetchall()
    return _memories_from_rows(conn, rows)


def are_linked(
    conn: sqlite3.Connection, memory_id_a: int, memory_id_b: int
) -> bool:
    """Return True iff the two memories are linked.

    Symmetric: the argument order does not matter because we
    normalize to canonical order before the single-row lookup.
    Returns False (not an error) for a self-link -- even though
    :func:`link_memories` would have rejected the call, callers
    that test ``are_linked(a, a)`` should not crash.
    """
    if memory_id_a == memory_id_b:
        return False
    a, b = _normalize(memory_id_a, memory_id_b)
    row = conn.execute(
        "SELECT 1 AS hit FROM memory_links "
        "WHERE memory_id_a = ? AND memory_id_b = ?",
        (a, b),
    ).fetchone()
    return row is not None
