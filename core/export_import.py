"""JSON export/import and manual DB backup for Alfred.

This module introduces no schema changes -- it just serializes the
contents of an existing Alfred DB to a JSON file (and back). The
on-disk format is versioned via the ``alfred_export_version`` field,
and the import rejects anything but the known-good version (1) with
a clear ``ValueError``. We do not attempt to guess at forward or
backward compatibility: a future Alfred that bumps the version will
ask the user to re-export from a compatible build rather than
silently mangle their data.

Export shape
------------
::

    {
      "alfred_export_version": 1,
      "exported_at": "<iso timestamp>",
      "memories": [
        {
          "title": ..., "content": ..., "type": ...,
          "created_at": ..., "updated_at": ...,
          "is_pinned": ...,
          "tags": [...],
          "project_name": <str or null>,
          "task": {"due_date": ..., "completed_at": ...} or null,
          "_export_id": <original memory id>
        },
        ...
      ],
      "links": [{"a": <_export_id>, "b": <_export_id>}, ...]
    }

The ``_export_id`` field is a temporary alias for the original
``memories.id`` so the ``links`` array can refer to the two endpoints
of each link. It is not persisted on import -- on the way in we build
a mapping from ``_export_id`` -> newly-allocated real ``memory_id``
and rewrite the link endpoints to the real ids.

Transactional safety
--------------------
The import is wrapped in a single ``BEGIN`` / ``COMMIT`` / ``ROLLBACK``
block. The public helpers in ``core`` (``create_memory``, ``set_tags``,
``link_memories``, ...) each call ``conn.commit()`` after writing, so
naively calling them in sequence would split the import into many
tiny transactions -- and a failure halfway through would leave
orphaned rows behind. To keep the import atomic, we pass the
high-level helpers a thin proxy (``_NoCommitConn``) whose ``commit``
and ``rollback`` are no-ops. The DML still lands on the real
connection and accumulates in one implicit transaction; our explicit
``COMMIT`` (or ``ROLLBACK`` on error) finalizes it. Python's
``sqlite3.Connection`` makes the ``commit`` attribute read-only, so
attribute assignment is not an option -- the proxy is the cleanest
way to intercept.

If the conn is already in an open transaction when the import starts,
the explicit ``BEGIN`` would error. In practice this never happens
in the app -- every UI handler commits its writes before returning --
so we don't add a runtime check.

Backup safety
-------------
``backup_db`` does a plain ``shutil.copy2`` of the DB file. That is
NOT crash-consistent if the source has uncommitted WAL data. A full
WAL checkpoint (``PRAGMA wal_checkpoint(TRUNCATE)``) would be, but
commit-then-copy is a deliberate simplification for a single-user
desktop app, and the caller is expected to commit
before invoking ``backup_db``. The module docstring of ``backup_db``
restates this.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime
from typing import Any, Optional

from core.db import now as _now
from core.paths import default_backup_dir
from core.inbox import convert_to_task
from core.links import link_memories
from core.memory import create_memory, set_tags
from core.projects import (
    create_project,
    get_memory_project,
    list_projects,
    set_memory_project,
)
from core.tasks import complete_task


# Bump this and refuse to import older files (and vice versa). The
# spec is explicit: don't guess at forward/backward compatibility.
EXPORT_VERSION = 1


class _NoCommitConn:
    """Pass-through wrapper around a sqlite3.Connection whose commit
    and rollback are no-ops.

    Used during ``import_from_file`` so the high-level helpers'
    post-write ``conn.commit()`` calls don't prematurely end our
    outer transaction. The proxy delegates every other attribute
    access to the wrapped connection, so DDL/DML, ``lastrowid``,
    ``row_factory``, etc. all work as if we were holding the real
    connection directly. The only intercepted methods are
    ``commit`` and ``rollback`` -- every other call (execute,
    executemany, executescript, close, etc.) is forwarded untouched.

    We do not intercept ``rollback`` because the high-level helpers
    don't call it during normal operation, but if a future refactor
    adds one, swallowing it here keeps the import's transaction
    boundary in our control.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def commit(self) -> None:
        # Intentionally a no-op. The real commit happens at the end
        # of import_from_file via an explicit ``COMMIT`` statement.
        return None

    def rollback(self) -> None:
        # Intentionally a no-op. The real rollback happens via an
        # explicit ``ROLLBACK`` statement in the import's except
        # branch. Suppressing accidental rollbacks here keeps the
        # import's transaction boundary under our control.
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


# ---------- export ----------


def export_all(conn: sqlite3.Connection) -> dict[str, Any]:
    """Build a JSON-serializable structure of every non-deleted memory.

    Soft-deleted memories are excluded -- they are not "on file" from
    the user's perspective, and including them would mean an export
    could resurrect trashed items on re-import, which would be
    surprising.

    The result is a plain ``dict`` ready for ``json.dump`` -- no
    datetime objects, no custom encoders needed.
    """
    memory_rows = conn.execute(
        "SELECT id, title, content, type, created_at, updated_at, "
        "       is_pinned "
        "FROM memories "
        "WHERE is_deleted = 0 "
        "ORDER BY id ASC"
    ).fetchall()

    # Pre-fetch tags for every memory in one round-trip rather than
    # one SELECT per row.
    if memory_rows:
        ids = [r["id"] for r in memory_rows]
        placeholders = ",".join("?" * len(ids))
        tag_rows = conn.execute(
            f"SELECT mt.memory_id, t.name "
            f"FROM memory_tags mt "
            f"JOIN tags t ON t.id = mt.tag_id "
            f"WHERE mt.memory_id IN ({placeholders}) "
            f"ORDER BY t.name",
            ids,
        ).fetchall()
        tags_by_mid: dict[int, list[str]] = {}
        for tr in tag_rows:
            tags_by_mid.setdefault(tr["memory_id"], []).append(tr["name"])
    else:
        tags_by_mid = {}

    # Pre-fetch task details for every task in one round-trip.
    if memory_rows:
        task_rows = conn.execute(
            "SELECT memory_id, due_date, completed_at "
            "FROM task_details "
            f"WHERE memory_id IN ({placeholders})",
            ids,
        ).fetchall()
        tasks_by_mid: dict[int, tuple[str | None, str | None]] = {
            r["memory_id"]: (r["due_date"], r["completed_at"])
            for r in task_rows
        }
    else:
        tasks_by_mid = {}

    memories_out: list[dict[str, Any]] = []
    for row in memory_rows:
        memory_id = row["id"]

        # Project name (or None). get_memory_project is a small,
        # cheap lookup, and projects are usually rare, so a per-row
        # call is fine here.
        project = get_memory_project(conn, memory_id)
        project_name = project.name if project is not None else None

        # Task details (or None). Tasks that exist without a
        # task_details row are still "type=task" -- we just don't
        # have a due_date or completed_at to put in the export.
        task_dict: dict[str, Any] | None = None
        if row["type"] == "task" and memory_id in tasks_by_mid:
            due, completed = tasks_by_mid[memory_id]
            task_dict = {"due_date": due, "completed_at": completed}

        memories_out.append(
            {
                "title": row["title"],
                "content": row["content"],
                "type": row["type"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "is_pinned": bool(row["is_pinned"]),
                "tags": list(tags_by_mid.get(memory_id, [])),
                "project_name": project_name,
                "task": task_dict,
                # Temporary alias used by the export's own ``links``
                # array. Not persisted on import -- the import builds
                # a _export_id -> real id mapping and rewrites
                # link endpoints through it.
                "_export_id": memory_id,
            }
        )

    # Only links where BOTH endpoints are non-deleted. A link to a
    # trashed memory is already invisible in the UI (the linked-list
    # query filters on is_deleted = 0), so exporting it would be
    # surprising.
    link_rows = conn.execute(
        "SELECT ml.memory_id_a, ml.memory_id_b "
        "FROM memory_links ml "
        "JOIN memories ma ON ma.id = ml.memory_id_a "
        "JOIN memories mb ON mb.id = ml.memory_id_b "
        "WHERE ma.is_deleted = 0 AND mb.is_deleted = 0"
    ).fetchall()
    links_out = [
        {"a": r["memory_id_a"], "b": r["memory_id_b"]} for r in link_rows
    ]

    return {
        "alfred_export_version": EXPORT_VERSION,
        "exported_at": _now(),
        "memories": memories_out,
        "links": links_out,
    }


def export_to_file(conn: sqlite3.Connection, filepath: str) -> None:
    """Write ``export_all(conn)`` to ``filepath`` as pretty-printed JSON.

    Uses ``indent=2`` so the file is human-readable -- a hand-edited
    file can be re-imported as long as the structure is preserved
    and the version is still 1.
    """
    data = export_all(conn)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------- import ----------


def _read_and_validate_export(filepath: str) -> dict[str, Any]:
    """Read and structurally validate the export file.

    Returns the parsed dict on success. Raises ``ValueError`` with a
    message identifying what's wrong on any failure. Crucially, this
    function does NOT touch the database -- it's the gate that
    prevents a half-done import from leaving orphaned rows.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise ValueError(
            f"Import failed: file not found at {filepath}."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Import failed: {filepath} is not valid JSON "
            f"({exc.msg} at line {exc.lineno} column {exc.colno})."
        ) from exc
    except OSError as exc:
        raise ValueError(
            f"Import failed: could not read {filepath} ({exc})."
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(
            f"Import failed: {filepath} is not an Alfred export "
            f"(expected a JSON object, got {type(data).__name__})."
        )

    version = data.get("alfred_export_version")
    if version is None:
        raise ValueError(
            f"Import failed: {filepath} is missing the "
            f"'alfred_export_version' field. This does not look like "
            f"an Alfred export."
        )
    if version != EXPORT_VERSION:
        raise ValueError(
            f"Import failed: {filepath} was written with "
            f"alfred_export_version={version!r}, but this build of "
            f"Alfred only accepts version {EXPORT_VERSION}. Re-export "
            f"from a compatible Alfred, or upgrade."
        )

    memories = data.get("memories", [])
    links = data.get("links", [])
    if not isinstance(memories, list):
        raise ValueError(
            f"Import failed: 'memories' field must be a list, got "
            f"{type(memories).__name__}."
        )
    if not isinstance(links, list):
        raise ValueError(
            f"Import failed: 'links' field must be a list, got "
            f"{type(links).__name__}."
        )

    for i, m in enumerate(memories):
        if not isinstance(m, dict):
            raise ValueError(
                f"Import failed: memories[{i}] is not a JSON object."
            )
        for required in ("title", "content", "type", "_export_id"):
            if required not in m:
                raise ValueError(
                    f"Import failed: memories[{i}] is missing the "
                    f"required field {required!r}."
                )
        if not isinstance(m["title"], str) or not isinstance(
            m["content"], str
        ) or not isinstance(m["type"], str):
            raise ValueError(
                f"Import failed: memories[{i}] has a non-string "
                f"title, content, or type."
            )
        if not isinstance(m["_export_id"], int):
            raise ValueError(
                f"Import failed: memories[{i}]._export_id must be an "
                f"integer."
            )
        project_name = m.get("project_name")
        if project_name is not None and not isinstance(project_name, str):
            raise ValueError(
                f"Import failed: memories[{i}].project_name must be "
                f"a string or null."
            )
        if "tags" in m and not isinstance(m["tags"], list):
            raise ValueError(
                f"Import failed: memories[{i}].tags must be a list."
            )
        if "task" in m and m["task"] is not None:
            task = m["task"]
            if not isinstance(task, dict):
                raise ValueError(
                    f"Import failed: memories[{i}].task must be an "
                    f"object or null."
                )
            for k in ("due_date", "completed_at"):
                if k in task and task[k] is not None and not isinstance(
                    task[k], str
                ):
                    raise ValueError(
                        f"Import failed: memories[{i}].task.{k} must "
                        f"be a string or null."
                    )

    for i, link in enumerate(links):
        if (
            not isinstance(link, dict)
            or "a" not in link
            or "b" not in link
            or not isinstance(link["a"], int)
            or not isinstance(link["b"], int)
        ):
            raise ValueError(
                f"Import failed: links[{i}] must be an object with "
                f"integer 'a' and 'b' fields."
            )

    return data


def _get_or_create_project_id(
    conn: sqlite3.Connection,
    projects_by_name: dict[str, int],
    name: str,
    projects_created: list[int],
) -> int:
    """Get the id of the project named ``name``, creating it if needed.

    ``projects_by_name`` is an in-memory cache shared across the
    import so we don't re-query for every memory. ``projects_created``
    is a single-element list used as a counter -- Python's scoping
    rules make this the least-clunky way to return "did we create
    a new one?" without changing the function's return type.
    """
    existing = projects_by_name.get(name)
    if existing is not None:
        return existing
    new_id = create_project(conn, name)
    projects_by_name[name] = new_id
    projects_created.append(1)
    return new_id


def import_from_file(conn: sqlite3.Connection, filepath: str) -> dict[str, int]:
    """Import memories / tags / projects / tasks / links from a JSON file.

    Returns a summary dict with::

        {
          "memories_imported":  <int>,
          "links_imported":     <int>,
          "projects_created":   <int>,
        }

    Raises ``ValueError`` (with a clear, human-readable message) for
    any malformed input. The whole import is one transaction -- if
    anything goes wrong partway through, ``ROLLBACK`` undoes every
    write so the DB is unchanged from before the call.
    """
    data = _read_and_validate_export(filepath)
    memories: list[dict[str, Any]] = data["memories"]
    links: list[dict[str, Any]] = data["links"]

    # The high-level helpers we delegate to (create_memory, set_tags,
    # create_project, set_memory_project, convert_to_task,
    # complete_task, link_memories) each call ``conn.commit()`` after
    # their write. We don't want each of those to commit, because
    # then a failure halfway through would leave half-imported rows
    # behind. The pattern: pass them a ``_NoCommitConn`` proxy that
    # swallows commit/rollback, bracket the work in an explicit
    # BEGIN/COMMIT/ROLLBACK on the real conn, and let the proxy
    # vanish when the import returns.
    tx = _NoCommitConn(conn)
    try:
        # Start a clean transaction. If the conn is already in one
        # (it shouldn't be in normal app flow), ``BEGIN`` would error;
        # the test fixture gives us a transaction-free conn, and the
        # UI commits after every prior handler, so this is safe.
        conn.execute("BEGIN")
        try:
            # Get-or-create lookup cache. Populated from the existing
            # project list at the start, then updated as we create
            # new projects mid-import so subsequent memories in the
            # same import can find them.
            projects_by_name: dict[str, int] = {
                p.name: p.id for p in list_projects(conn)
            }
            projects_created: list[int] = []

            id_map: dict[int, int] = {}
            for m in memories:
                # By design, created_at/updated_at from the
                # file are ignored. The new row gets a fresh ``now()``
                # stamp from create_memory. We do NOT carry forward
                # is_pinned either: it is part of the export
                # shape but is deliberately not restored on import,
                # and the imported entries land in a fresh context
                # where the user might not want a 50-item import to
                # bombard their pinned list. So we drop it.
                new_id = create_memory(
                    tx,
                    m["title"],
                    m["content"],
                    type=m["type"],
                )
                id_map[m["_export_id"]] = new_id

                tags = m.get("tags") or []
                if tags:
                    set_tags(tx, new_id, tags)

                project_name = m.get("project_name")
                if project_name:
                    pid = _get_or_create_project_id(
                        tx,
                        projects_by_name,
                        project_name,
                        projects_created,
                    )
                    set_memory_project(tx, new_id, pid)

                task = m.get("task")
                if task is not None:
                    # Flip type to 'task' and (optionally) set the
                    # due_date in one shot. The convert_to_task
                    # helper is the public entry point for this and
                    # also creates/updates the task_details row.
                    convert_to_task(
                        tx, new_id, due_date=task.get("due_date")
                    )
                    if task.get("completed_at"):
                        complete_task(tx, new_id)

            links_imported = 0
            for link in links:
                a = id_map.get(link["a"])
                b = id_map.get(link["b"])
                if a is None or b is None:
                    raise ValueError(
                        f"Import failed: link references unknown "
                        f"memory id {link['a'] if a is None else link['b']}."
                    )
                # link_memories raises ValueError on self-links; we
                # guard explicitly here so a self-link in the file
                # fails the whole import (a self-link in the export
                # would be a sign of a corrupt file, not data we want
                # to silently drop).
                if a == b:
                    raise ValueError(
                        f"Import failed: link refers to the same "
                        f"memory on both sides (id {a})."
                    )
                link_memories(tx, a, b)
                links_imported += 1

            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        # The proxy is __slots__-only and goes out of scope here;
        # nothing else to clean up. The real conn is the caller's
        # responsibility.
        pass

    return {
        "memories_imported": len(memories),
        "links_imported": links_imported,
        "projects_created": len(projects_created),
    }


# ---------- backup ----------


def backup_db(db_path: str, backup_dir: Optional[str] = None) -> str:
    """Copy ``db_path`` into ``backup_dir`` with a timestamped name.

    Returns the full path of the backup file. ``backup_dir`` is
    created if it does not already exist. ``shutil.copy2`` preserves
    the source's metadata (mtime, perms) on the copy.

    If ``backup_dir`` is None, the platform-aware backup
    directory from :func:`core.paths.default_backup_dir` is
    used -- ``<project>/data/backups`` in dev mode,
    ``%APPDATA%\\Alfred\\backups`` in a packaged install.

    Tradeoff: copying a live SQLite file is not strictly
    crash-consistent. If the source connection has uncommitted
    WAL data, the copy will be missing it. A truly safe backup
    would do a ``PRAGMA wal_checkpoint(TRUNCATE)`` first; we don't,
    because (a) commit-then-copy is a deliberate simplification
    for a single-user desktop app and (b) the
    default journal mode here is ``DELETE``, not ``WAL``, so
    commits do land in the main DB file and the risk is bounded
    to whatever the caller has in-flight. The caller is expected
    to commit any pending writes before invoking this.
    """
    if backup_dir is None:
        backup_dir = default_backup_dir()
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(backup_dir, f"alfred-backup-{timestamp}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path
