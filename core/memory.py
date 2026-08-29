"""Memory CRUD and tag operations for Alfred."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from typing import Optional, Union

import sqlite3

from core.db import now as _now


@dataclass
class Memory:
    id: int
    title: str
    content: str
    type: str
    created_at: str
    updated_at: str
    is_pinned: bool
    is_deleted: bool
    deleted_at: Optional[str]
    tags: list[str] = field(default_factory=list)


@dataclass
class MemoryVersion:
    """A snapshot of a memory's title+content taken before an update.

    The ``id`` is monotonically increasing within a single memory
    (later snapshots get higher ids), so ``ORDER BY id DESC`` gives
    newest-first.
    """
    id: int
    memory_id: int
    title: str
    content: str
    saved_at: str


def _row_to_memory(row: sqlite3.Row, tags: Optional[list[str]] = None) -> Memory:
    return Memory(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        type=row["type"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        is_pinned=bool(row["is_pinned"]),
        is_deleted=bool(row["is_deleted"]),
        deleted_at=row["deleted_at"],
        tags=list(tags) if tags else [],
    )


def _row_to_version(row: sqlite3.Row) -> MemoryVersion:
    return MemoryVersion(
        id=row["id"],
        memory_id=row["memory_id"],
        title=row["title"],
        content=row["content"],
        saved_at=row["saved_at"],
    )


def _ensure_tag(conn: sqlite3.Connection, name: str) -> int:
    """Return the id for ``name``, creating the tag row if it does not exist.

    ``tags.name`` is UNIQUE COLLATE NOCASE, so 'Electronics' and 'electronics'
    collide -- the second insert is silently ignored via ``INSERT OR IGNORE``
    and we fall back to a SELECT to get the id of the existing row.

    NB: we cannot use ``cur.lastrowid`` to detect a fresh insert. After
    ``INSERT OR IGNORE`` against a pre-existing row, ``lastrowid`` still
    returns the rowid of the most recent *successful* insert on the
    connection (i.e. a stale value from an earlier call), not 0/None --
    so a truthy check would happily return the wrong id. Instead we look
    at ``cur.rowcount``: SQLite sets it to 1 for an insert that actually
    happened and 0 for one that was ignored.
    """
    cur = conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
    if cur.rowcount:
        return cur.lastrowid
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    if row is None:
        # Should be unreachable: the row was either just inserted or it
        # was already there, so a SELECT by name must find it.
        raise RuntimeError(f"_ensure_tag: tag row for {name!r} vanished")
    return row["id"]


def _add_tags_no_commit(
    conn: sqlite3.Connection, memory_id: int, tag_names: list[str]
) -> None:
    for name in tag_names:
        tag_id = _ensure_tag(conn, name)
        conn.execute(
            "INSERT OR IGNORE INTO memory_tags(memory_id, tag_id) VALUES (?, ?)",
            (memory_id, tag_id),
        )


def _memories_from_rows(
    conn: sqlite3.Connection, rows: list[sqlite3.Row]
) -> list[Memory]:
    """Hydrate a list of memory rows with their tags, in a single batch query."""
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    tag_rows = conn.execute(
        f"SELECT mt.memory_id, t.name FROM memory_tags mt "
        f"JOIN tags t ON t.id = mt.tag_id "
        f"WHERE mt.memory_id IN ({placeholders}) "
        f"ORDER BY t.name",
        ids,
    ).fetchall()
    tags_by_mid: dict[int, list[str]] = {}
    for tr in tag_rows:
        tags_by_mid.setdefault(tr["memory_id"], []).append(tr["name"])
    return [_row_to_memory(r, tags_by_mid.get(r["id"], [])) for r in rows]


# ---------- public API ----------


def create_memory(
    conn: sqlite3.Connection,
    title: str,
    content: str,
    type: str = "note",
    tags: Optional[list[str]] = None,
) -> int:
    """Insert a new memory and return its id."""
    ts = _now()
    cur = conn.execute(
        "INSERT INTO memories(title, content, type, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (title, content, type, ts, ts),
    )
    memory_id = cur.lastrowid
    if tags:
        _add_tags_no_commit(conn, memory_id, tags)
    conn.commit()
    return memory_id


def get_memory(conn: sqlite3.Connection, memory_id: int) -> Optional[Memory]:
    """Fetch a single memory (with its tags) by id, or None if not found."""
    row = conn.execute(
        "SELECT * FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_memory(row, get_tags(conn, memory_id))


def update_memory(
    conn: sqlite3.Connection,
    memory_id: int,
    title: Optional[str] = None,
    content: Optional[str] = None,
) -> None:
    """Update only the fields that are provided; bumps ``updated_at``.

    If the title or content actually changes from what is currently in
    the DB, the pre-update title+content is snapshotted into
    ``memory_versions`` first (in the same transaction), so the history
    dialog has something to restore from later. If neither field
    changes -- including the all-``None`` case -- the call is a no-op
    and no version row is created.
    """
    # Read the current title/content so we can both detect a no-op
    # and snapshot the pre-update state. This is one extra SELECT per
    # update, which is fine: a write to the same row already has to
    # touch the row, and FTS keeps a copy in sync too.
    current = conn.execute(
        "SELECT title, content FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    if current is None:
        return  # memory was deleted out from under us; nothing to do

    new_title = title if title is not None else current["title"]
    new_content = content if content is not None else current["content"]
    if new_title == current["title"] and new_content == current["content"]:
        # Nothing actually changing; skip both the version row and the
        # updated_at bump.
        return

    # Snapshot the PRE-update state in the same transaction.
    conn.execute(
        "INSERT INTO memory_versions(memory_id, title, content, saved_at) "
        "VALUES (?, ?, ?, ?)",
        (memory_id, current["title"], current["content"], _now()),
    )

    updates: list[str] = []
    params: list[object] = []
    if title is not None:
        updates.append("title = ?")
        params.append(title)
    if content is not None:
        updates.append("content = ?")
        params.append(content)
    updates.append("updated_at = ?")
    params.append(_now())
    params.append(memory_id)
    conn.execute(
        f"UPDATE memories SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    conn.commit()


def delete_memory(conn: sqlite3.Connection, memory_id: int) -> None:
    """Soft delete: set is_deleted=1 and stamp deleted_at. The row stays."""
    conn.execute(
        "UPDATE memories SET is_deleted = 1, deleted_at = ? WHERE id = ?",
        (_now(), memory_id),
    )
    conn.commit()


def restore_memory(conn: sqlite3.Connection, memory_id: int) -> None:
    """Undo a soft delete: is_deleted=0, deleted_at=NULL."""
    conn.execute(
        "UPDATE memories SET is_deleted = 0, deleted_at = NULL WHERE id = ?",
        (memory_id,),
    )
    conn.commit()


def permanently_delete_memory(conn: sqlite3.Connection, memory_id: int) -> None:
    """Hard delete: actually remove the row (and via cascade, its tag links)."""
    conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    conn.commit()


def list_trash(conn: sqlite3.Connection) -> list[Memory]:
    """Return all soft-deleted memories, newest-deleted first.

    Type is intentionally not filtered -- trash is a single unified
    holding area regardless of whether the deleted item was a note, an
    inbox capture, or a task. Newest-first ordering means the most
    recently deleted item (i.e. the one the user is most likely to want
    to restore) is at the top of the list.
    """
    rows = conn.execute(
        "SELECT * FROM memories "
        "WHERE is_deleted = 1 "
        "ORDER BY deleted_at DESC"
    ).fetchall()
    return _memories_from_rows(conn, rows)


def empty_trash(conn: sqlite3.Connection) -> int:
    """Permanently delete every soft-deleted memory. Returns the count.

    A single ``DELETE`` is preferred over a per-row
    ``permanently_delete_memory`` loop: one round-trip, one transaction,
    and the FK ``ON DELETE CASCADE`` on memory_tags / memory_versions /
    task_details cleans up child rows the same way either way.
    """
    cur = conn.execute("DELETE FROM memories WHERE is_deleted = 1")
    conn.commit()
    return cur.rowcount


def toggle_pin(conn: sqlite3.Connection, memory_id: int) -> bool:
    """Flip is_pinned and return the new state. False if the memory is gone."""
    existing = conn.execute(
        "SELECT id FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    if existing is None:
        return False
    conn.execute(
        "UPDATE memories SET is_pinned = 1 - is_pinned, updated_at = ? WHERE id = ?",
        (_now(), memory_id),
    )
    row = conn.execute(
        "SELECT is_pinned FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    conn.commit()
    return bool(row["is_pinned"])


def list_memories(
    conn: sqlite3.Connection,
    include_deleted: bool = False,
    pinned_only: bool = False,
) -> list[Memory]:
    """List memories, newest-updated first, pinned ones ahead of unpinned."""
    sql = "SELECT * FROM memories WHERE 1=1"
    params: list[object] = []
    if not include_deleted:
        sql += " AND is_deleted = 0"
    if pinned_only:
        sql += " AND is_pinned = 1"
    sql += " ORDER BY is_pinned DESC, updated_at DESC"
    rows = conn.execute(sql, params).fetchall()
    return _memories_from_rows(conn, rows)


def list_memories_updated_on(
    conn: sqlite3.Connection,
    day: Union[str, "_date"],
    include_deleted: bool = False,
) -> list[Memory]:
    """List memories whose ``updated_at`` falls on the given calendar day.

    ``day`` is a ``date`` or a ``YYYY-MM-DD`` string. Timestamps are
    stored as UTC ISO strings (``core.db.now``), but "yesterday" means
    yesterday *where the user is*, so the comparison converts to
    localtime before taking the date part. That does mean an entry
    written at 01:00 UTC counts as the previous day in western
    timezones -- which is exactly what the user meant by it.
    """
    key = day.isoformat() if hasattr(day, "isoformat") else str(day)
    sql = (
        "SELECT * FROM memories "
        "WHERE date(updated_at, 'localtime') = ?"
    )
    if not include_deleted:
        sql += " AND is_deleted = 0"
    sql += " ORDER BY is_pinned DESC, updated_at DESC"
    rows = conn.execute(sql, (key,)).fetchall()
    return _memories_from_rows(conn, rows)


def add_tags(
    conn: sqlite3.Connection, memory_id: int, tag_names: list[str]
) -> None:
    """Attach tags to a memory. Creates tags that don't exist; case-insensitive."""
    _add_tags_no_commit(conn, memory_id, tag_names)
    conn.commit()


def get_tags(conn: sqlite3.Connection, memory_id: int) -> list[str]:
    """Return the tag names attached to ``memory_id``, sorted alphabetically."""
    rows = conn.execute(
        "SELECT t.name FROM tags t "
        "JOIN memory_tags mt ON mt.tag_id = t.id "
        "WHERE mt.memory_id = ? "
        "ORDER BY t.name",
        (memory_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def set_tags(
    conn: sqlite3.Connection, memory_id: int, tag_names: list[str]
) -> None:
    """Replace all tags on a memory. Pass ``[]`` to clear."""
    conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (memory_id,))
    for name in tag_names:
        tag_id = _ensure_tag(conn, name)
        conn.execute(
            "INSERT OR IGNORE INTO memory_tags(memory_id, tag_id) VALUES (?, ?)",
            (memory_id, tag_id),
        )
    conn.commit()


# ---------- version history ----------


def list_versions(conn: sqlite3.Connection, memory_id: int) -> list[MemoryVersion]:
    """Return all version snapshots for ``memory_id``, newest first.

    Newest first means highest ``id`` first, which is correct because
    ``memory_versions.id`` is autoincrement and we always insert a new
    row in chronological order.
    """
    rows = conn.execute(
        "SELECT id, memory_id, title, content, saved_at "
        "FROM memory_versions WHERE memory_id = ? ORDER BY id DESC",
        (memory_id,),
    ).fetchall()
    return [_row_to_version(r) for r in rows]


def get_version(
    conn: sqlite3.Connection, version_id: int
) -> Optional[MemoryVersion]:
    """Fetch a single version by id, or None if it doesn't exist."""
    row = conn.execute(
        "SELECT id, memory_id, title, content, saved_at "
        "FROM memory_versions WHERE id = ?",
        (version_id,),
    ).fetchone()
    return _row_to_version(row) if row is not None else None


def restore_version(
    conn: sqlite3.Connection, memory_id: int, version_id: int
) -> None:
    """Overwrite the memory's title/content with an older version's.

    Snapshots the CURRENT state as a new version first, so the state
    being restored away from is never silently lost -- it shows up as
    the newest entry in the history list after the restore. A no-op
    (target version identical to current) skips both the snapshot and
    the overwrite.

    The function is a no-op if the version doesn't exist or doesn't
    belong to this memory, or if the memory itself is gone.
    """
    version = get_version(conn, version_id)
    if version is None or version.memory_id != memory_id:
        return

    current = conn.execute(
        "SELECT title, content FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    if current is None:
        return

    if (
        current["title"] == version.title
        and current["content"] == version.content
    ):
        # Already at this version; don't pollute history with a
        # duplicate snapshot.
        return

    # Snapshot current state BEFORE overwriting. The snapshot uses the
    # current row's title/content, not the version's.
    conn.execute(
        "INSERT INTO memory_versions(memory_id, title, content, saved_at) "
        "VALUES (?, ?, ?, ?)",
        (memory_id, current["title"], current["content"], _now()),
    )

    # Overwrite the memory with the version's title/content.
    conn.execute(
        "UPDATE memories SET title = ?, content = ?, updated_at = ? "
        "WHERE id = ?",
        (version.title, version.content, _now(), memory_id),
    )
    conn.commit()
