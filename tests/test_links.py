"""Tests for memory link CRUD and queries."""
from __future__ import annotations


# ---------- link_memories ----------


def test_link_memories_creates_a_row(conn):
    from core.links import link_memories
    from core.memory import create_memory

    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    link_memories(conn, a, b)
    rows = conn.execute("SELECT * FROM memory_links").fetchall()
    assert len(rows) == 1


def test_link_memories_normalizes_argument_order(conn):
    """link(5, 12) and link(12, 5) both store the row in canonical order
    (smaller id first) and produce exactly one row total."""
    from core.links import link_memories
    from core.memory import create_memory

    # Create them in reverse order so ``a`` ends up with the higher id.
    b = create_memory(conn, "B", "x")
    a = create_memory(conn, "A", "x")
    assert a > b  # sanity: a is the larger id here

    link_memories(conn, a, b)  # "wrong" order first
    link_memories(conn, b, a)  # "right" order second

    rows = conn.execute("SELECT * FROM memory_links").fetchall()
    assert len(rows) == 1
    # Smaller id stored first.
    assert rows[0]["memory_id_a"] == b
    assert rows[0]["memory_id_b"] == a


def test_link_memories_is_idempotent(conn):
    """Linking the same pair twice is a no-op, not a duplicate row."""
    from core.links import link_memories
    from core.memory import create_memory

    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    link_memories(conn, a, b)
    link_memories(conn, a, b)
    link_memories(conn, b, a)  # third call, reversed args
    n = conn.execute("SELECT COUNT(*) AS n FROM memory_links").fetchone()["n"]
    assert n == 1


def test_link_memories_self_link_raises(conn):
    """Linking a memory to itself is a programming error — must raise ValueError."""
    import pytest

    from core.links import link_memories
    from core.memory import create_memory

    a = create_memory(conn, "Loner", "x")
    with pytest.raises(ValueError):
        link_memories(conn, a, a)


def test_link_memories_self_link_does_not_create_row(conn):
    """A rejected self-link must not leave a row behind even on rollback."""
    from core.links import link_memories
    from core.memory import create_memory

    a = create_memory(conn, "Loner", "x")
    try:
        link_memories(conn, a, a)
    except ValueError:
        pass
    n = conn.execute("SELECT COUNT(*) AS n FROM memory_links").fetchone()["n"]
    assert n == 0


# ---------- unlink_memories ----------


def test_unlink_memories_removes_the_row(conn):
    from core.links import link_memories, unlink_memories
    from core.memory import create_memory

    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    link_memories(conn, a, b)
    unlink_memories(conn, a, b)
    n = conn.execute("SELECT COUNT(*) AS n FROM memory_links").fetchone()["n"]
    assert n == 0


def test_unlink_memories_argument_order_irrelevant(conn):
    """Unlink normalizes too — order doesn't matter."""
    from core.links import link_memories, unlink_memories
    from core.memory import create_memory

    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    link_memories(conn, a, b)
    unlink_memories(conn, b, a)  # reversed
    n = conn.execute("SELECT COUNT(*) AS n FROM memory_links").fetchone()["n"]
    assert n == 0


def test_unlink_memories_noop_when_not_linked(conn):
    """Unlinking a pair that was never linked must not raise."""
    from core.links import unlink_memories
    from core.memory import create_memory

    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    unlink_memories(conn, a, b)  # must not raise
    n = conn.execute("SELECT COUNT(*) AS n FROM memory_links").fetchone()["n"]
    assert n == 0


def test_unlink_memories_only_removes_the_specific_pair(conn):
    """A→B and C→D both exist; unlinking A→B leaves C→D intact."""
    from core.links import link_memories, unlink_memories
    from core.memory import create_memory

    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    c = create_memory(conn, "C", "x")
    d = create_memory(conn, "D", "x")
    link_memories(conn, a, b)
    link_memories(conn, c, d)
    unlink_memories(conn, a, b)
    rows = conn.execute("SELECT * FROM memory_links").fetchall()
    assert len(rows) == 1
    assert rows[0]["memory_id_a"] == c
    assert rows[0]["memory_id_b"] == d


def test_unlink_self_link_raises(conn):
    """A self-unlink is rejected the same way self-link is — both go
    through _normalize which checks for equality first."""
    import pytest

    from core.links import unlink_memories
    from core.memory import create_memory

    a = create_memory(conn, "A", "x")
    with pytest.raises(ValueError):
        unlink_memories(conn, a, a)


# ---------- list_linked_memories ----------


def test_list_linked_memories_returns_other_side(conn):
    """For a stored row (a, b), ``list_linked_memories(a)`` returns b
    and ``list_linked_memories(b)`` returns a. Both directions."""
    from core.links import link_memories, list_linked_memories
    from core.memory import create_memory

    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    link_memories(conn, a, b)
    from_a = list_linked_memories(conn, a)
    from_b = list_linked_memories(conn, b)
    assert [m.id for m in from_a] == [b]
    assert [m.id for m in from_b] == [a]


def test_list_linked_memories_returns_empty_when_none(conn):
    from core.links import list_linked_memories
    from core.memory import create_memory

    a = create_memory(conn, "Lonely", "x")
    assert list_linked_memories(conn, a) == []


def test_list_linked_memories_returns_multiple_in_title_order(conn):
    from core.links import link_memories, list_linked_memories
    from core.memory import create_memory

    hub = create_memory(conn, "Hub", "x")
    # Create in non-alphabetical order to prove the ORDER BY does work.
    z = create_memory(conn, "Zeta", "x")
    m = create_memory(conn, "Mu", "x")
    a = create_memory(conn, "Alpha", "x")
    link_memories(conn, hub, z)
    link_memories(conn, hub, m)
    link_memories(conn, hub, a)
    linked = list_linked_memories(conn, hub)
    assert [m.title for m in linked] == ["Alpha", "Mu", "Zeta"]


def test_list_linked_memories_excludes_soft_deleted(conn):
    """A link to a soft-deleted memory is not surfaced as a related entry."""
    from core.links import link_memories, list_linked_memories
    from core.memory import create_memory, delete_memory

    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    link_memories(conn, a, b)
    delete_memory(conn, b)
    assert list_linked_memories(conn, a) == []


def test_list_linked_memories_excludes_soft_deleted_in_both_positions(conn):
    """The exclusion must work whether the deleted memory is on the
    a-side or the b-side of the link row. The OR query hits both."""
    from core.links import link_memories, list_linked_memories
    from core.memory import create_memory, delete_memory

    # Create with a > b so (b, a) is the canonical stored row; deleting
    # ``a`` (the b-side of the stored row) must still be excluded.
    b = create_memory(conn, "B", "x")
    a = create_memory(conn, "A", "x")
    assert a > b
    link_memories(conn, a, b)
    delete_memory(conn, a)
    # b's related list must not surface a.
    assert list_linked_memories(conn, b) == []


# ---------- are_linked ----------


def test_are_linked_true_for_linked_pair(conn):
    from core.links import are_linked, link_memories
    from core.memory import create_memory

    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    link_memories(conn, a, b)
    assert are_linked(conn, a, b) is True


def test_are_linked_false_for_unlinked_pair(conn):
    from core.links import are_linked
    from core.memory import create_memory

    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    assert are_linked(conn, a, b) is False


def test_are_linked_is_symmetric(conn):
    """are_linked(a, b) == are_linked(b, a), regardless of which id is smaller."""
    from core.links import are_linked, link_memories
    from core.memory import create_memory

    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    link_memories(conn, a, b)
    assert are_linked(conn, a, b) == are_linked(conn, b, a)


def test_are_linked_self_returns_false_not_raises(conn):
    """A self-link is rejected at write time, but a self-query should
    not raise — it just returns False (a memory isn't "linked" to
    itself, and asking about it should be a no-op rather than a crash)."""
    from core.links import are_linked
    from core.memory import create_memory

    a = create_memory(conn, "A", "x")
    assert are_linked(conn, a, a) is False


# ---------- cascade on permanent delete ----------


def test_permanent_delete_cascades_links(conn):
    """Permanently deleting one side of a link removes the link row;
    the surviving side's list_linked_memories no longer mentions it."""
    from core.links import link_memories, list_linked_memories
    from core.memory import create_memory, permanently_delete_memory

    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    link_memories(conn, a, b)
    permanently_delete_memory(conn, a)
    # The link row was cascaded; b's related list is empty.
    assert list_linked_memories(conn, b) == []


def test_permanent_delete_cascades_both_sides(conn):
    """Deleting one side doesn't leave a half-link; the row is gone
    entirely (not (None, b) or (a, None))."""
    from core.links import link_memories
    from core.memory import create_memory, permanently_delete_memory

    a = create_memory(conn, "A", "x")
    b = create_memory(conn, "B", "x")
    link_memories(conn, a, b)
    permanently_delete_memory(conn, a)
    n = conn.execute("SELECT COUNT(*) AS n FROM memory_links").fetchone()["n"]
    assert n == 0
