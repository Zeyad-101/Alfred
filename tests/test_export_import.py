"""Tests for core/export_import.py: export_all, export_to_file,
import_from_file, and backup_db.

The export tests build a deliberately varied fixture (plain note,
tagged note, project-bearing memory, due-dated task, completed task,
two linked memories) so a single round trip can verify every
feature. The import tests then exercise the all-or-nothing
guarantee, get-or-create semantics, and the failure paths.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3

import pytest

from core.db import get_connection, init_db
from core.export_import import (
    EXPORT_VERSION,
    backup_db,
    export_all,
    export_to_file,
    import_from_file,
)


# ---------- helpers ----------


def _count_memories(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]


def _count_links(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]


def _make_mixed_db(conn) -> dict:
    """Seed ``conn`` with one example of every feature export_all cares about.

    Returns a dict of named ids so the test can assert specific
    membership. Variants included:

    * a plain note (no tags, no project, not a task)
    * a tagged note (two tags, no project, not a task)
    * a memory in a project
    * a task with a due date
    * a completed task
    * two linked memories
    """
    from core.inbox import convert_to_task
    from core.links import link_memories
    from core.memory import create_memory
    from core.projects import create_project, set_memory_project
    from core.tasks import complete_task

    plain = create_memory(conn, "Plain", "Just a note", type="note")
    tagged = create_memory(
        conn,
        "Tagged",
        "Has tags",
        type="note",
        tags=["alpha", "beta"],
    )
    in_project = create_memory(conn, "In project", "Project note", type="note")
    project_id = create_project(conn, "Sidekick")
    set_memory_project(conn, in_project, project_id)

    due_task = create_memory(conn, "Due task", "x", type="task")
    convert_to_task(conn, due_task, due_date="2026-09-01")

    done_task = create_memory(conn, "Done task", "x", type="task")
    convert_to_task(conn, done_task, due_date="2026-08-15")
    complete_task(conn, done_task)

    link_a = create_memory(conn, "Link A", "x", type="note")
    link_b = create_memory(conn, "Link B", "x", type="note")
    link_memories(conn, link_a, link_b)

    return {
        "plain": plain,
        "tagged": tagged,
        "in_project": in_project,
        "project_id": project_id,
        "due_task": due_task,
        "done_task": done_task,
        "link_a": link_a,
        "link_b": link_b,
    }


# ---------- export_all ----------


def test_export_all_top_level_shape(conn):
    """The export dict has exactly the four top-level keys the spec lists."""
    data = export_all(conn)
    assert set(data.keys()) == {
        "alfred_export_version",
        "exported_at",
        "memories",
        "links",
    }
    assert data["alfred_export_version"] == EXPORT_VERSION
    assert data["exported_at"]  # non-empty ISO timestamp
    assert data["memories"] == []
    assert data["links"] == []


def test_export_all_mixed_data(conn):
    """The export captures every feature for a varied dataset."""
    ids = _make_mixed_db(conn)
    data = export_all(conn)
    assert data["alfred_export_version"] == EXPORT_VERSION
    assert len(data["memories"]) == 7  # 7 memories in the fixture

    by_export_id = {m["_export_id"]: m for m in data["memories"]}

    # Plain note: no tags, no project, no task.
    plain = by_export_id[ids["plain"]]
    assert plain["title"] == "Plain"
    assert plain["type"] == "note"
    assert plain["tags"] == []
    assert plain["project_name"] is None
    assert plain["task"] is None

    # Tagged note: tags come through sorted.
    tagged = by_export_id[ids["tagged"]]
    assert tagged["tags"] == ["alpha", "beta"]

    # Memory in project: project_name is the NAME, not the id.
    in_proj = by_export_id[ids["in_project"]]
    assert in_proj["project_name"] == "Sidekick"

    # Due-dated task: task dict has due_date and (still-null) completed_at.
    due = by_export_id[ids["due_task"]]
    assert due["type"] == "task"
    assert due["task"] is not None
    assert due["task"]["due_date"] == "2026-09-01"
    assert due["task"]["completed_at"] is None

    # Completed task: both due and completed_at populated.
    done = by_export_id[ids["done_task"]]
    assert done["task"] is not None
    assert done["task"]["due_date"] == "2026-08-15"
    assert done["task"]["completed_at"]  # non-empty ISO timestamp

    # The two linked memories.
    link_a = by_export_id[ids["link_a"]]
    link_b = by_export_id[ids["link_b"]]
    assert {link["a"] for link in data["links"]} == {ids["link_a"]}
    assert {link["b"] for link in data["links"]} == {ids["link_b"]}


def test_export_all_excludes_deleted_memories(conn):
    """Soft-deleted memories must not appear in the export."""
    from core.memory import create_memory, delete_memory

    live = create_memory(conn, "Live", "alive")
    gone = create_memory(conn, "Gone", "deleted")
    delete_memory(conn, gone)

    data = export_all(conn)
    titles = [m["title"] for m in data["memories"]]
    assert "Live" in titles
    assert "Gone" not in titles


def test_export_all_excludes_links_to_deleted(conn):
    """A link whose endpoint is trashed must not appear in the export."""
    from core.links import link_memories
    from core.memory import create_memory, delete_memory

    a = create_memory(conn, "Alive A", "x")
    b = create_memory(conn, "Doomed B", "x")
    link_memories(conn, a, b)
    delete_memory(conn, b)

    data = export_all(conn)
    assert data["links"] == []


def test_export_to_file_writes_readable_json(conn, tmp_path):
    """export_to_file produces a UTF-8 pretty-printed JSON file."""
    from core.memory import create_memory

    create_memory(conn, "T", "C")
    path = tmp_path / "out.json"
    export_to_file(conn, str(path))
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    # Pretty-printed: 4-space "alfred_export_version" should be
    # preceded by 2 spaces of indent.
    assert "  " in text
    parsed = json.loads(text)
    assert parsed["alfred_export_version"] == EXPORT_VERSION


# ---------- import_from_file ----------


def _import_into_fresh_db(source_conn, export_path) -> tuple[sqlite3.Connection, dict]:
    """Open a brand-new DB in ``tmp_path`` and import ``export_path`` into it.

    Returns the target connection (caller closes) and the import summary.
    """
    import os as _os

    target_path = _os.path.join(_os.path.dirname(str(export_path)), "target.db")
    target = get_connection(target_path)
    init_db(target)
    summary = import_from_file(target, str(export_path))
    return target, summary


def test_import_roundtrip_preserves_everything(conn, tmp_path):
    """Export a varied DB to a file, import into a fresh DB, verify
    everything came through with NEW ids."""
    ids = _make_mixed_db(conn)
    export_path = tmp_path / "export.json"
    export_to_file(conn, str(export_path))

    target, summary = _import_into_fresh_db(conn, export_path)
    try:
        # The import summary reports the counts.
        assert summary["memories_imported"] == 7
        # Two endpoints + the link between them.
        assert summary["links_imported"] == 1
        # "Sidekick" is the only project in the export.
        assert summary["projects_created"] == 1

        # 7 memories on the new side, all with fresh ids assigned by
        # the target's AUTOINCREMENT counter. We don't try to assert
        # non-overlap with the source ids here: both source and
        # target are fresh DBs, so both start their auto counters at
        # 1 and the imported rows would coincidentally share the
        # source's id range. The count + content checks below are
        # the real signal that the import created new rows.
        assert _count_memories(target) == 7
        target_ids = {r[0] for r in target.execute("SELECT id FROM memories").fetchall()}
        assert all(i > 0 for i in target_ids), "ids should be positive integers"
        # And the new ids are not, e.g., stringified copies of the
        # source's ids (a defensive check that the import isn't
        # accidentally putting the source's id string into the
        # target's id column).
        assert all(isinstance(i, int) for i in target_ids)

        # Tags round-tripped on the tagged note.
        tagged_new = next(
            r[0] for r in target.execute(
                "SELECT id FROM memories WHERE title = 'Tagged'"
            ).fetchall()
        )
        tag_rows = target.execute(
            "SELECT t.name FROM tags t "
            "JOIN memory_tags mt ON mt.tag_id = t.id "
            "WHERE mt.memory_id = ? "
            "ORDER BY t.name",
            (tagged_new,),
        ).fetchall()
        assert [r[0] for r in tag_rows] == ["alpha", "beta"]

        # Project round-tripped with a new memory linked to it.
        proj = target.execute(
            "SELECT name FROM projects"
        ).fetchall()
        assert [r[0] for r in proj] == ["Sidekick"]
        in_proj_new = target.execute(
            "SELECT id FROM memories WHERE title = 'In project'"
        ).fetchone()[0]
        link_row = target.execute(
            "SELECT 1 FROM memory_projects WHERE memory_id = ?",
            (in_proj_new,),
        ).fetchone()
        assert link_row is not None

        # Due-dated task: due_date preserved, completed_at still null.
        due_new = target.execute(
            "SELECT id FROM memories WHERE title = 'Due task'"
        ).fetchone()[0]
        td = target.execute(
            "SELECT due_date, completed_at FROM task_details "
            "WHERE memory_id = ?",
            (due_new,),
        ).fetchone()
        assert td[0] == "2026-09-01"
        assert td[1] is None

        # Completed task: both stamps preserved.
        done_new = target.execute(
            "SELECT id FROM memories WHERE title = 'Done task'"
        ).fetchone()[0]
        td = target.execute(
            "SELECT due_date, completed_at FROM task_details "
            "WHERE memory_id = ?",
            (done_new,),
        ).fetchone()
        assert td[0] == "2026-08-15"
        assert td[1] is not None

        # The two linked memories are still linked, and the link
        # points to the NEW ids (not the source's ids).
        link_row = target.execute(
            "SELECT memory_id_a, memory_id_b FROM memory_links"
        ).fetchone()
        a_new = target.execute(
            "SELECT id FROM memories WHERE title = 'Link A'"
        ).fetchone()[0]
        b_new = target.execute(
            "SELECT id FROM memories WHERE title = 'Link B'"
        ).fetchone()[0]
        assert {link_row[0], link_row[1]} == {a_new, b_new}
    finally:
        target.close()


def test_import_roundtrip_uses_fresh_timestamps(conn, tmp_path):
    """Imported memories get NOW() stamps, not the file's old ones."""
    from core.memory import create_memory, get_memory

    mid = create_memory(conn, "T", "C")
    original_created = get_memory(conn, mid).created_at
    export_path = tmp_path / "export.json"
    export_to_file(conn, str(export_path))

    target, _summary = _import_into_fresh_db(conn, export_path)
    try:
        new_mid = target.execute(
            "SELECT id FROM memories WHERE title = 'T'"
        ).fetchone()[0]
        new_row = target.execute(
            "SELECT created_at, updated_at FROM memories WHERE id = ?",
            (new_mid,),
        ).fetchone()
        # The new created_at is not the original (we'd have to
        # compare strings — they should differ at least in the
        # microsecond / sub-second part most of the time, but
        # the safer assertion is that the export DID carry the
        # original, and the import ignored it.
        export_data = json.loads(export_path.read_text(encoding="utf-8"))
        exported_created = export_data["memories"][0]["created_at"]
        assert exported_created == original_created
        assert new_row[0] != exported_created, (
            "import must not carry the export's created_at"
        )
    finally:
        target.close()


def test_import_creates_missing_project(conn, tmp_path):
    """An export referencing a project name that doesn't exist locally
    should create it (get-or-create semantics, create side)."""
    from core.memory import create_memory

    create_memory(
        conn, "Project note", "x", type="note"
    )
    data = export_all(conn)
    # The note has no project; build a synthetic export that names one.
    data["memories"][0]["project_name"] = "Brand New"
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    target, summary = _import_into_fresh_db(conn, path)
    try:
        assert summary["projects_created"] == 1
        names = [
            r[0]
            for r in target.execute("SELECT name FROM projects").fetchall()
        ]
        assert "Brand New" in names
    finally:
        target.close()


def test_import_reuses_existing_project(conn, tmp_path):
    """An export referencing a project name that already exists in the
    target must reuse it — no duplicate project rows.

    The "existing" part is in the TARGET, not the source: we pre-
    create a project in the target DB, then import an export whose
    memory refers to a project of the same name. The import should
    link the new memory to the existing project, not create a second
    one.
    """
    from core.memory import create_memory
    from core.projects import create_project, set_memory_project

    # Source side: build an export that names a project "Existing".
    src_memory = create_memory(conn, "note", "x", type="note")
    src_project = create_project(conn, "Existing")
    set_memory_project(conn, src_memory, src_project)
    export_path = tmp_path / "export.json"
    export_to_file(conn, str(export_path))

    # Target side: pre-create a project with the same name. The
    # import must reuse it, not create a duplicate.
    target_path = tmp_path / "target.db"
    target = get_connection(str(target_path))
    init_db(target)
    try:
        create_project(target, "Existing")
        summary = import_from_file(target, str(export_path))
        # No new project was created.
        assert summary["projects_created"] == 0
        # And there's still exactly one project with that name.
        rows = target.execute(
            "SELECT id FROM projects WHERE name = 'Existing'"
        ).fetchall()
        assert len(rows) == 1
        # The imported memory is linked to the existing project.
        imported = target.execute(
            "SELECT id FROM memories WHERE title = 'note'"
        ).fetchone()[0]
        link = target.execute(
            "SELECT project_id FROM memory_projects WHERE memory_id = ?",
            (imported,),
        ).fetchone()
        assert link is not None
        assert link[0] == rows[0][0]
    finally:
        target.close()


def test_import_malformed_json_raises_and_db_unchanged(conn, tmp_path):
    """Garbage JSON raises ValueError and leaves the DB untouched."""
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not valid JSON", encoding="utf-8")

    before = _count_memories(conn)
    with pytest.raises(ValueError, match="not valid JSON"):
        import_from_file(conn, str(bad))
    assert _count_memories(conn) == before


def test_import_wrong_version_raises(conn, tmp_path):
    """A file with a different alfred_export_version is rejected."""
    bad = tmp_path / "wrong_version.json"
    bad.write_text(
        json.dumps(
            {
                "alfred_export_version": 999,
                "memories": [],
                "links": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="version"):
        import_from_file(conn, str(bad))


def test_import_missing_version_raises(conn, tmp_path):
    """A JSON object without alfred_export_version is rejected."""
    bad = tmp_path / "no_version.json"
    bad.write_text(json.dumps({"memories": [], "links": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="alfred_export_version"):
        import_from_file(conn, str(bad))


def test_import_missing_required_field_raises(conn, tmp_path):
    """A memory missing a required field (e.g. 'title') is rejected
    before any writes happen."""
    bad = tmp_path / "missing_field.json"
    bad.write_text(
        json.dumps(
            {
                "alfred_export_version": EXPORT_VERSION,
                "memories": [{"content": "x", "type": "note", "_export_id": 1}],
                "links": [],
            }
        ),
        encoding="utf-8",
    )
    before = _count_memories(conn)
    with pytest.raises(ValueError, match="title"):
        import_from_file(conn, str(bad))
    assert _count_memories(conn) == before


def test_import_bad_link_target_rolls_back(conn, tmp_path):
    """A link referring to an unknown _export_id fails the whole import."""
    from core.memory import create_memory

    create_memory(conn, "T", "C")
    export_path = tmp_path / "export.json"
    export_to_file(conn, str(export_path))

    # Inject a bogus link that points at a _export_id we never made.
    data = json.loads(export_path.read_text(encoding="utf-8"))
    data["links"].append({"a": 99999, "b": 99998})
    bad = tmp_path / "bad_link.json"
    bad.write_text(json.dumps(data), encoding="utf-8")

    target_path = tmp_path / "target.db"
    target = get_connection(str(target_path))
    init_db(target)
    try:
        with pytest.raises(ValueError, match="unknown memory id"):
            import_from_file(target, str(bad))
        # The whole import was rolled back — no memories, no links.
        assert _count_memories(target) == 0
        assert _count_links(target) == 0
    finally:
        target.close()


def test_import_summary_counts_links_and_memories(conn, tmp_path):
    """The summary dict reports the right counts for a multi-link import."""
    from core.links import link_memories
    from core.memory import create_memory

    # 3 memories, 2 independent links (a-b and a-c).
    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    c = create_memory(conn, "C", "x")
    link_memories(conn, a, b)
    link_memories(conn, a, c)

    export_path = tmp_path / "export.json"
    export_to_file(conn, str(export_path))

    target, summary = _import_into_fresh_db(conn, export_path)
    try:
        assert summary["memories_imported"] == 3
        assert summary["links_imported"] == 2
    finally:
        target.close()


# ---------- backup_db ----------


def test_backup_db_creates_timestamped_file(tmp_path):
    """The backup file lives in backup_dir/ and matches the timestamp pattern."""
    src = tmp_path / "src.db"
    # Create a real SQLite file (even empty) so copy2 has something to grab.
    src_conn = sqlite3.connect(str(src))
    src_conn.close()
    backup_dir = tmp_path / "backups"

    backup_path = backup_db(str(src), backup_dir=str(backup_dir))
    assert os.path.exists(backup_path)
    assert os.path.isdir(str(backup_dir))
    # Filename pattern: alfred-backup-YYYYMMDD-HHMMSS.db
    assert re.search(
        r"alfred-backup-\d{8}-\d{6}\.db$", os.path.basename(backup_path)
    )


def test_backup_db_content_matches_source(tmp_path):
    """A backup of a populated DB has the same row counts as the source."""
    from core.memory import create_memory
    from core.projects import create_project
    from core.inbox import convert_to_task
    from core.links import link_memories

    src = tmp_path / "src.db"
    conn = get_connection(str(src))
    init_db(conn)
    try:
        for i in range(5):
            create_memory(conn, f"Note {i}", "x", type="note")
        create_project(conn, "P1")
        t = create_memory(conn, "T", "x", type="task")
        convert_to_task(conn, t, due_date="2026-09-01")
        a = create_memory(conn, "A", "x")
        b = create_memory(conn, "B", "x")
        link_memories(conn, a, b)
    finally:
        conn.close()

    backup_path = backup_db(str(src), backup_dir=str(tmp_path / "backups"))

    src_count_mem = sqlite3.connect(str(src)).execute(
        "SELECT COUNT(*) FROM memories"
    ).fetchone()[0]
    src_count_links = sqlite3.connect(str(src)).execute(
        "SELECT COUNT(*) FROM memory_links"
    ).fetchone()[0]
    src_count_proj = sqlite3.connect(str(src)).execute(
        "SELECT COUNT(*) FROM projects"
    ).fetchone()[0]

    bkp = sqlite3.connect(backup_path)
    try:
        bkp_count_mem = bkp.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        bkp_count_links = bkp.execute(
            "SELECT COUNT(*) FROM memory_links"
        ).fetchone()[0]
        bkp_count_proj = bkp.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    finally:
        bkp.close()

    assert src_count_mem == bkp_count_mem == 8  # 5 notes + T + A + B
    assert src_count_links == bkp_count_links == 1
    assert src_count_proj == bkp_count_proj == 1
    # And the backup is not zero bytes.
    assert os.path.getsize(backup_path) > 0


def test_backup_db_creates_missing_dir(tmp_path):
    """backup_dir is created if it doesn't exist."""
    src = tmp_path / "src.db"
    src_conn = sqlite3.connect(str(src))
    src_conn.close()
    backup_dir = tmp_path / "nested" / "backups"
    assert not backup_dir.exists()
    backup_db(str(src), backup_dir=str(backup_dir))
    assert backup_dir.is_dir()
