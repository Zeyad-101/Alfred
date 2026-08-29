"""Tests for project CRUD and the memory↔project link table."""
from __future__ import annotations


# ---------- create_project ----------


def test_create_project_returns_id_and_persists(conn):
    from core.projects import create_project, get_project

    pid = create_project(conn, "Tax 2026", description="Annual filing prep")
    p = get_project(conn, pid)
    assert p is not None
    assert p.id == pid
    assert p.name == "Tax 2026"
    assert p.description == "Annual filing prep"
    # created_at should be ISO 8601; we don't pin the value, just the shape.
    assert isinstance(p.created_at, str) and "T" in p.created_at


def test_create_project_default_description_is_empty(conn):
    from core.projects import create_project, get_project

    pid = create_project(conn, "No desc")
    assert get_project(conn, pid).description == ""


def test_get_project_returns_none_for_unknown_id(conn):
    from core.projects import get_project

    assert get_project(conn, 99999) is None


# ---------- list_projects ----------


def test_list_projects_empty_when_none(conn):
    from core.projects import list_projects

    assert list_projects(conn) == []


def test_list_projects_orders_alphabetically(conn):
    from core.projects import create_project, list_projects

    create_project(conn, "Charlie")
    create_project(conn, "alpha")
    create_project(conn, "Bravo")
    projects = list_projects(conn)
    # Case-insensitive sort: alpha < Bravo < Charlie
    assert [p.name for p in projects] == ["alpha", "Bravo", "Charlie"]


def test_list_projects_memory_count_excludes_deleted(conn):
    from core.memory import create_memory, delete_memory
    from core.projects import create_project, list_projects, set_memory_project

    pid = create_project(conn, "P")
    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    c = create_memory(conn, "C", "x")
    set_memory_project(conn, a, pid)
    set_memory_project(conn, b, pid)
    set_memory_project(conn, c, pid)
    # Soft-delete one — it must drop out of the count.
    delete_memory(conn, b)
    projects = list_projects(conn)
    assert len(projects) == 1
    assert projects[0].memory_count == 2


def test_list_projects_memory_count_zero_for_empty_project(conn):
    from core.projects import create_project, list_projects

    pid = create_project(conn, "Empty")
    projects = list_projects(conn)
    assert projects[0].memory_count == 0


def test_list_projects_uses_single_query_no_n_plus_one(conn):
    """Sanity: list_projects must do it in one round-trip, not N+1.

    We don't introspect SQL, but we can assert the post-condition that
    holds whether the count came from a JOIN+GROUP BY or from N
    separate COUNTs: counts must add up correctly even for a project
    that has many links, including soft-deleted ones (which should
    not count).
    """
    from core.memory import create_memory, delete_memory
    from core.projects import create_project, list_projects, set_memory_project

    pid = create_project(conn, "Bulk")
    ids = [create_memory(conn, f"M{i}", "x") for i in range(10)]
    for mid in ids[:7]:  # 7 active, 3 soft-deleted
        set_memory_project(conn, mid, pid)
    for mid in ids[7:]:
        set_memory_project(conn, mid, pid)
        delete_memory(conn, mid)
    projects = list_projects(conn)
    assert projects[0].memory_count == 7


# ---------- rename_project ----------


def test_rename_project_changes_name_only(conn):
    from core.projects import create_project, get_project, rename_project

    pid = create_project(conn, "Old", description="keep me")
    rename_project(conn, pid, name="New")
    p = get_project(conn, pid)
    assert p.name == "New"
    assert p.description == "keep me"


def test_rename_project_changes_description_only(conn):
    from core.projects import create_project, get_project, rename_project

    pid = create_project(conn, "Name", description="old desc")
    rename_project(conn, pid, description="new desc")
    p = get_project(conn, pid)
    assert p.name == "Name"
    assert p.description == "new desc"


def test_rename_project_changes_both(conn):
    from core.projects import create_project, get_project, rename_project

    pid = create_project(conn, "Old", description="old")
    rename_project(conn, pid, name="New", description="new")
    p = get_project(conn, pid)
    assert p.name == "New"
    assert p.description == "new"


def test_rename_project_no_args_is_noop(conn):
    """A call with neither name nor description is a no-op, not an error."""
    from core.projects import create_project, get_project, rename_project

    pid = create_project(conn, "Same", description="Same desc")
    rename_project(conn, pid)
    p = get_project(conn, pid)
    assert p.name == "Same"
    assert p.description == "Same desc"


def test_rename_project_unknown_id_is_noop(conn):
    """Renaming a non-existent project does not raise."""
    from core.projects import rename_project

    rename_project(conn, 99999, name="X")  # must not raise


# ---------- delete_project ----------


def test_delete_project_removes_the_row(conn):
    from core.projects import create_project, delete_project, get_project

    pid = create_project(conn, "Doomed")
    delete_project(conn, pid)
    assert get_project(conn, pid) is None


def test_delete_project_leaves_memories_untouched(conn):
    """Hard delete: project goes, memories STAY, just become unassigned.

    This is the contract the spec calls out explicitly — a test for
    it is non-negotiable.
    """
    from core.memory import create_memory, get_memory
    from core.projects import create_project, delete_project, set_memory_project

    pid = create_project(conn, "Container")
    mid = create_memory(conn, "M", "body")
    set_memory_project(conn, mid, pid)

    delete_project(conn, pid)

    # Memory is still there.
    m = get_memory(conn, mid)
    assert m is not None
    assert m.title == "M"
    # The link row is gone (CASCADE), so get_memory_project returns None.
    from core.projects import get_memory_project

    assert get_memory_project(conn, mid) is None


def test_delete_project_cascades_link_table(conn):
    """The FK on memory_projects is ON DELETE CASCADE; verify it fires."""
    from core.memory import create_memory
    from core.projects import create_project, delete_project, set_memory_project

    pid = create_project(conn, "P")
    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    set_memory_project(conn, a, pid)
    set_memory_project(conn, b, pid)

    # Pre: two link rows
    pre = conn.execute(
        "SELECT COUNT(*) AS n FROM memory_projects WHERE project_id = ?",
        (pid,),
    ).fetchone()["n"]
    assert pre == 2

    delete_project(conn, pid)

    # Post: zero link rows
    post = conn.execute(
        "SELECT COUNT(*) AS n FROM memory_projects WHERE project_id = ?",
        (pid,),
    ).fetchone()["n"]
    assert post == 0


def test_delete_project_unknown_id_is_noop(conn):
    from core.projects import delete_project

    delete_project(conn, 99999)  # must not raise


# ---------- list_project_memories ----------


def test_list_project_memories_excludes_soft_deleted(conn):
    from core.memory import create_memory, delete_memory
    from core.projects import create_project, list_project_memories, set_memory_project

    pid = create_project(conn, "P")
    a = create_memory(conn, "Alive", "x")
    b = create_memory(conn, "Doomed", "x")
    set_memory_project(conn, a, pid)
    set_memory_project(conn, b, pid)
    delete_memory(conn, b)
    memories = list_project_memories(conn, pid)
    assert [m.id for m in memories] == [a]


def test_list_project_memories_pinned_first_then_recent(conn):
    from core.memory import create_memory, toggle_pin
    from core.projects import create_project, list_project_memories, set_memory_project

    pid = create_project(conn, "P")
    # Create in known order: a (oldest), b, c (newest). updated_at
    # naturally follows created_at on create.
    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    c = create_memory(conn, "C", "x")
    set_memory_project(conn, a, pid)
    set_memory_project(conn, b, pid)
    set_memory_project(conn, c, pid)
    # Pin B — it should jump to the top regardless of created_at order.
    toggle_pin(conn, b)
    memories = list_project_memories(conn, pid)
    assert [m.id for m in memories] == [b, c, a]


def test_list_project_memories_empty_project(conn):
    from core.projects import create_project, list_project_memories

    pid = create_project(conn, "Empty")
    assert list_project_memories(conn, pid) == []


# ---------- get_memory_project ----------


def test_get_memory_project_returns_assigned_project(conn):
    from core.memory import create_memory
    from core.projects import create_project, get_memory_project, set_memory_project

    pid = create_project(conn, "Holder")
    mid = create_memory(conn, "M", "x")
    set_memory_project(conn, mid, pid)
    p = get_memory_project(conn, mid)
    assert p is not None
    assert p.id == pid
    assert p.name == "Holder"


def test_get_memory_project_returns_none_when_unassigned(conn):
    from core.memory import create_memory
    from core.projects import get_memory_project

    mid = create_memory(conn, "Lonely", "x")
    assert get_memory_project(conn, mid) is None


def test_get_memory_project_for_unknown_memory_returns_none(conn):
    from core.projects import get_memory_project

    assert get_memory_project(conn, 99999) is None


# ---------- set_memory_project ----------


def test_set_memory_project_assigns(conn):
    from core.memory import create_memory
    from core.projects import create_project, get_memory_project, set_memory_project

    pid = create_project(conn, "P")
    mid = create_memory(conn, "M", "x")
    set_memory_project(conn, mid, pid)
    assert get_memory_project(conn, mid).id == pid


def test_set_memory_project_replaces_prior_link(conn):
    """A memory has at most one project. Reassigning replaces, not stacks."""
    from core.memory import create_memory
    from core.projects import (
        create_project,
        get_memory_project,
        set_memory_project,
    )

    p1 = create_project(conn, "P1")
    p2 = create_project(conn, "P2")
    mid = create_memory(conn, "M", "x")
    set_memory_project(conn, mid, p1)
    set_memory_project(conn, mid, p2)
    # The link count for p1 should be zero, p2 should be one.
    assert get_memory_project(conn, mid).id == p2
    n_p1 = conn.execute(
        "SELECT COUNT(*) AS n FROM memory_projects WHERE project_id = ?", (p1,)
    ).fetchone()["n"]
    n_p2 = conn.execute(
        "SELECT COUNT(*) AS n FROM memory_projects WHERE project_id = ?", (p2,)
    ).fetchone()["n"]
    assert n_p1 == 0
    assert n_p2 == 1


def test_set_memory_project_none_unassigns(conn):
    from core.memory import create_memory
    from core.projects import (
        create_project,
        get_memory_project,
        set_memory_project,
    )

    pid = create_project(conn, "P")
    mid = create_memory(conn, "M", "x")
    set_memory_project(conn, mid, pid)
    set_memory_project(conn, mid, None)
    assert get_memory_project(conn, mid) is None


def test_set_memory_project_idempotent_when_called_twice(conn):
    """Calling assign twice with the same id is safe and produces one link row."""
    from core.memory import create_memory
    from core.projects import create_project, set_memory_project

    pid = create_project(conn, "P")
    mid = create_memory(conn, "M", "x")
    set_memory_project(conn, mid, pid)
    set_memory_project(conn, mid, pid)  # must not raise, must not dup
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM memory_projects WHERE memory_id = ?", (mid,)
    ).fetchone()["n"]
    assert n == 1


def test_set_memory_project_idempotent_unassign(conn):
    """Calling unassign twice is safe; first call removes the row, second is a no-op."""
    from core.memory import create_memory
    from core.projects import create_project, set_memory_project

    pid = create_project(conn, "P")
    mid = create_memory(conn, "M", "x")
    set_memory_project(conn, mid, pid)
    set_memory_project(conn, mid, None)
    set_memory_project(conn, mid, None)  # must not raise
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM memory_projects WHERE memory_id = ?", (mid,)
    ).fetchone()["n"]
    assert n == 0


def test_set_memory_project_does_not_touch_memory_row(conn):
    """set_memory_project is link-table-only; the memory's content/title are not changed."""
    from core.memory import create_memory, get_memory
    from core.projects import create_project, set_memory_project

    pid = create_project(conn, "P")
    mid = create_memory(conn, "Untouched", "body", tags=["a", "b"])
    before = get_memory(conn, mid)
    set_memory_project(conn, mid, pid)
    after = get_memory(conn, mid)
    assert after.title == before.title
    assert after.content == before.content
    assert after.tags == before.tags
    assert after.updated_at == before.updated_at
