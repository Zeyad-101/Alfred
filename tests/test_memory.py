"""Tests for memory CRUD and tag operations."""
from __future__ import annotations


def test_create_and_get(conn):
    from core.memory import create_memory, get_memory

    mid = create_memory(conn, "Shopping", "Buy milk", type="note", tags=["errand"])
    m = get_memory(conn, mid)

    assert m is not None
    assert m.id == mid
    assert m.title == "Shopping"
    assert m.content == "Buy milk"
    assert m.type == "note"
    assert m.is_pinned is False
    assert m.is_deleted is False
    assert m.deleted_at is None
    assert m.tags == ["errand"]
    # timestamps should be ISO 8601 strings
    assert isinstance(m.created_at, str) and "T" in m.created_at
    assert m.created_at == m.updated_at


def test_create_defaults(conn):
    from core.memory import create_memory, get_memory

    mid = create_memory(conn, "Title", "Content")
    m = get_memory(conn, mid)
    assert m.type == "note"
    assert m.tags == []


def test_update_title_only(conn):
    from core.memory import create_memory, get_memory, update_memory

    mid = create_memory(conn, "Old", "Content")
    update_memory(conn, mid, title="New")
    m = get_memory(conn, mid)
    assert m.title == "New"
    assert m.content == "Content"


def test_update_content_only(conn):
    from core.memory import create_memory, get_memory, update_memory

    mid = create_memory(conn, "Title", "Old")
    update_memory(conn, mid, content="New")
    m = get_memory(conn, mid)
    assert m.title == "Title"
    assert m.content == "New"


def test_update_both(conn):
    from core.memory import create_memory, get_memory, update_memory

    mid = create_memory(conn, "Old", "Old")
    update_memory(conn, mid, title="New", content="Newer")
    m = get_memory(conn, mid)
    assert m.title == "New"
    assert m.content == "Newer"


def test_update_no_args_does_nothing(conn):
    from core.memory import create_memory, get_memory, update_memory

    mid = create_memory(conn, "Title", "Content")
    update_memory(conn, mid)  # both None — should be a no-op, not an error
    m = get_memory(conn, mid)
    assert m.title == "Title"
    assert m.content == "Content"


def test_update_bumps_updated_at(conn):
    from core.memory import create_memory, get_memory, update_memory

    mid = create_memory(conn, "Title", "Content")
    original = get_memory(conn, mid).updated_at
    update_memory(conn, mid, title="New title")
    new = get_memory(conn, mid).updated_at
    assert new >= original


def test_soft_delete_and_restore(conn):
    from core.memory import (
        create_memory,
        delete_memory,
        get_memory,
        list_memories,
        restore_memory,
    )

    mid = create_memory(conn, "Note", "Content")
    delete_memory(conn, mid)
    m = get_memory(conn, mid)
    assert m.is_deleted is True
    assert m.deleted_at is not None
    assert list_memories(conn) == []  # excluded by default

    restore_memory(conn, mid)
    m = get_memory(conn, mid)
    assert m.is_deleted is False
    assert m.deleted_at is None
    assert len(list_memories(conn)) == 1


def test_list_include_deleted(conn):
    from core.memory import create_memory, delete_memory, list_memories

    create_memory(conn, "Alive", "x")
    mid2 = create_memory(conn, "Dead", "y")
    delete_memory(conn, mid2)
    assert len(list_memories(conn)) == 1
    assert len(list_memories(conn, include_deleted=True)) == 2


def test_permanent_delete(conn):
    from core.memory import (
        create_memory,
        get_memory,
        permanently_delete_memory,
    )

    mid = create_memory(conn, "Gone", "After me")
    permanently_delete_memory(conn, mid)
    assert get_memory(conn, mid) is None


def test_pin_toggle(conn):
    from core.memory import create_memory, get_memory, toggle_pin

    mid = create_memory(conn, "Title", "Content")
    assert get_memory(conn, mid).is_pinned is False

    new_state = toggle_pin(conn, mid)
    assert new_state is True
    assert get_memory(conn, mid).is_pinned is True

    new_state = toggle_pin(conn, mid)
    assert new_state is False
    assert get_memory(conn, mid).is_pinned is False


def test_list_orders_pinned_first(conn):
    from core.memory import create_memory, list_memories, toggle_pin

    create_memory(conn, "B", "second")
    a = create_memory(conn, "A", "first")
    toggle_pin(conn, a)
    memories = list_memories(conn)
    assert [m.title for m in memories] == ["A", "B"]


def test_add_and_get_tags(conn):
    from core.memory import add_tags, create_memory, get_tags

    mid = create_memory(conn, "Title", "Content")
    add_tags(conn, mid, ["python", "cli"])
    assert get_tags(conn, mid) == ["cli", "python"]  # sorted alphabetically


def test_set_tags_replaces(conn):
    from core.memory import create_memory, get_tags, set_tags

    mid = create_memory(conn, "Title", "Content")
    set_tags(conn, mid, ["a", "b", "c"])
    set_tags(conn, mid, ["x", "y"])
    assert get_tags(conn, mid) == ["x", "y"]


def test_set_tags_clears_when_empty(conn):
    from core.memory import add_tags, create_memory, get_tags, set_tags

    mid = create_memory(conn, "Title", "Content")
    add_tags(conn, mid, ["a", "b"])
    set_tags(conn, mid, [])
    assert get_tags(conn, mid) == []


def test_tags_case_insensitive(conn):
    from core.memory import add_tags, create_memory, get_tags

    mid = create_memory(conn, "Item", "Specs")
    add_tags(conn, mid, ["Electronics"])
    add_tags(conn, mid, ["electronics"])  # same tag, different case
    add_tags(conn, mid, ["ELECTRONICS"])  # same tag, different case
    tags = get_tags(conn, mid)
    # exactly one tag, and it's the spelling that was inserted first
    assert len(tags) == 1
    assert tags[0].lower() == "electronics"


def test_ensure_tag_returns_correct_id_across_inserts(conn):
    """Regression: ``_ensure_tag`` previously returned the lastrowid of the
    connection's *previous* successful insert when ``INSERT OR IGNORE`` was
    a no-op, so a tag that already existed could be re-resolved to the id
    of a *different* tag inserted earlier. Set tags on two memories that
    share a name, then re-ensure an existing tag — the id must match the
    row, not a stale one."""
    from core.memory import _ensure_tag, add_tags, create_memory

    a = create_memory(conn, "A", "x", tags=["alpha"])
    b = create_memory(conn, "B", "y", tags=["beta"])
    # Now ensure 'alpha' again. Pre-fix this returned the id of 'beta'
    # (the most recent successful insert on the connection).
    alpha_id = _ensure_tag(conn, "alpha")
    # And the actual row id for 'alpha' in the tags table:
    real = conn.execute("SELECT id FROM tags WHERE name = 'alpha'").fetchone()["id"]
    assert alpha_id == real
    # Add it to a third memory; the association must be correct, not the
    # id of some other tag.
    c = create_memory(conn, "C", "z", tags=["alpha"])
    from core.memory import get_tags

    assert get_tags(conn, c) == ["alpha"]


def test_tags_persist_with_memory(conn):
    from core.memory import create_memory, get_memory

    mid = create_memory(conn, "T", "C", tags=["alpha", "beta"])
    assert get_memory(conn, mid).tags == ["alpha", "beta"]


def test_get_memory_unknown_returns_none(conn):
    from core.memory import get_memory

    assert get_memory(conn, 99999) is None


# ---------- version history ----------


def test_create_memory_creates_no_version(conn):
    """create_memory must not produce a version row (nothing to version yet)."""
    from core.memory import create_memory, list_versions

    mid = create_memory(conn, "Title", "Content")
    assert list_versions(conn, mid) == []


def test_update_with_content_change_creates_one_version(conn):
    """An update that actually changes the content creates exactly one version
    that holds the PRE-update title+content (not the new values)."""
    from core.memory import create_memory, list_versions, update_memory

    mid = create_memory(conn, "Old title", "Old content")
    update_memory(conn, mid, title="New title", content="New content")

    versions = list_versions(conn, mid)
    assert len(versions) == 1
    v = versions[0]
    assert v.memory_id == mid
    assert v.title == "Old title"
    assert v.content == "Old content"
    # And not the new values
    assert v.title != "New title"
    assert v.content != "New content"


def test_update_with_title_only_change_creates_one_version(conn):
    """A title-only change still creates a version (carrying old title+content)."""
    from core.memory import create_memory, list_versions, update_memory

    mid = create_memory(conn, "Old", "Stays the same")
    update_memory(conn, mid, title="New")
    versions = list_versions(conn, mid)
    assert len(versions) == 1
    assert versions[0].title == "Old"
    assert versions[0].content == "Stays the same"


def test_update_with_content_only_change_creates_one_version(conn):
    """A content-only change still creates a version (carrying old title+content)."""
    from core.memory import create_memory, list_versions, update_memory

    mid = create_memory(conn, "Stays the same", "Old content")
    update_memory(conn, mid, content="New content")
    versions = list_versions(conn, mid)
    assert len(versions) == 1
    assert versions[0].title == "Stays the same"
    assert versions[0].content == "Old content"


def test_update_with_no_actual_change_creates_no_version(conn):
    """Calling update_memory with values that match the current row creates no
    version — and also does not bump updated_at."""
    from core.memory import create_memory, get_memory, list_versions, update_memory

    mid = create_memory(conn, "Title", "Content")
    before = get_memory(conn, mid).updated_at

    # All four "no real change" variants
    update_memory(conn, mid)  # both None
    update_memory(conn, mid, title="Title")  # same title
    update_memory(conn, mid, content="Content")  # same content
    update_memory(conn, mid, title="Title", content="Content")  # both same

    assert list_versions(conn, mid) == []
    # And updated_at was not bumped.
    assert get_memory(conn, mid).updated_at == before


def test_multiple_updates_create_versions_newest_first(conn):
    """Sequential edits create multiple versions, listed newest first."""
    from core.memory import create_memory, list_versions, update_memory

    mid = create_memory(conn, "v1", "v1 content")
    update_memory(conn, mid, title="v2", content="v2 content")
    update_memory(conn, mid, title="v3", content="v3 content")
    update_memory(conn, mid, title="v4", content="v4 content")

    versions = list_versions(conn, mid)
    assert len(versions) == 3
    # Newest first: the snapshot of v3 is at the top because v3 was the
    # pre-update state for the v4 update. Then v2 (snapshotted before v3),
    # then v1 (snapshotted before v2).
    assert versions[0].title == "v3"
    assert versions[0].content == "v3 content"
    assert versions[1].title == "v2"
    assert versions[1].content == "v2 content"
    assert versions[2].title == "v1"
    assert versions[2].content == "v1 content"


def test_restore_version_reverts_and_snapshots_current(conn):
    """restore_version must revert title+content AND snapshot the state being
    replaced, so nothing is silently lost."""
    from core.memory import (
        create_memory,
        get_memory,
        list_versions,
        restore_version,
        update_memory,
    )

    mid = create_memory(conn, "v1", "v1 content")
    update_memory(conn, mid, title="v2", content="v2 content")
    update_memory(conn, mid, title="v3", content="v3 content")

    # Pre-restore: versions = [v2, v1] (newest first); current = v3.
    pre = list_versions(conn, mid)
    assert len(pre) == 2
    assert pre[0].title == "v2"
    assert pre[1].title == "v1"

    # Restore the OLDEST version (v1).
    restore_version(conn, mid, pre[1].id)

    # Memory is now v1.
    m = get_memory(conn, mid)
    assert m.title == "v1"
    assert m.content == "v1 content"

    # Versions now: [v3-snapshot, v2, v1] — the snapshot of v3 was
    # taken FIRST (so v3 isn't lost), then the memory was overwritten
    # with v1's title/content.
    post = list_versions(conn, mid)
    assert len(post) == 3
    assert post[0].title == "v3"
    assert post[0].content == "v3 content"
    assert post[1].title == "v2"
    assert post[1].content == "v2 content"
    assert post[2].title == "v1"
    assert post[2].content == "v1 content"


def test_get_version_returns_correct_row(conn):
    from core.memory import create_memory, get_version, list_versions, update_memory

    mid = create_memory(conn, "v1", "v1 content")
    update_memory(conn, mid, title="v2", content="v2 content")
    versions = list_versions(conn, mid)
    assert len(versions) == 1

    fetched = get_version(conn, versions[0].id)
    assert fetched is not None
    assert fetched.id == versions[0].id
    assert fetched.memory_id == mid
    assert fetched.title == "v1"
    assert fetched.content == "v1 content"


def test_get_version_returns_none_for_unknown(conn):
    from core.memory import get_version

    assert get_version(conn, 99999) is None


def test_restore_unknown_version_is_a_noop(conn):
    """A bogus version_id must not crash and must not create a spurious snapshot."""
    from core.memory import create_memory, get_memory, list_versions, restore_version

    mid = create_memory(conn, "Title", "Content")
    restore_version(conn, mid, 99999)  # doesn't exist

    # Memory unchanged, no versions created.
    assert get_memory(conn, mid).title == "Title"
    assert list_versions(conn, mid) == []


def test_permanent_delete_cascades_to_versions(conn):
    """ON DELETE CASCADE: permanently removing a memory removes its versions."""
    from core.memory import (
        create_memory,
        list_versions,
        permanently_delete_memory,
        update_memory,
    )

    mid = create_memory(conn, "Title", "Content")
    update_memory(conn, mid, title="Title v2", content="Content v2")
    assert len(list_versions(conn, mid)) == 1

    permanently_delete_memory(conn, mid)
    # The version row had a FK to memories(id) with ON DELETE CASCADE,
    # so it should be gone too.
    assert list_versions(conn, mid) == []
