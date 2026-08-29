"""Full-text search across Alfred memories."""
from __future__ import annotations

import re
import sqlite3

from core.memory import list_memories, _memories_from_rows

# A "safe" token is one composed entirely of ASCII letters, digits, and
# underscores -- characters that FTS5 can match unquoted without any
# special interpretation. Any other character (apostrophe, hyphen,
# asterisk, etc.) would either be syntax to FTS5 or would need to be
# quoted, so we keep the safe/quoted split tight and explicit.
_PREFIX_SAFE = re.compile(r"^[A-Za-z0-9_]+$")


def _sanitize(query: str) -> str:
    """Build an FTS5 MATCH expression from a raw user query.

    Every whitespace-separated token is wrapped in double quotes so the
    query is safe regardless of what the user typed -- raw input like
    ``hello "world"`` would otherwise be parsed as FTS5 syntax and
    throw a runtime error. Wrapping makes each token a literal phrase.

    The LAST token additionally gets prefix matching: if it is composed
    solely of ASCII letters/digits/underscores, we emit it unquoted with
    a trailing ``*`` so the user gets prefix-as-they-type behavior on the
    token they are actively editing (typing ``Max`` finds
    ``Max98357A``). Earlier tokens stay exact-match -- they represent
    already-committed words the user has finished typing. If the last
    token contains characters that need escaping, it falls back to the
    quoted (no-prefix) form: FTS5 does not let you combine a phrase
    delimiter with a prefix wildcard in a useful way.
    """
    tokens = query.split()
    if not tokens:
        return ""

    parts: list[str] = []
    for token in tokens[:-1]:
        # Quote-wrap and double any internal double quotes.
        parts.append(f'"{token.replace(chr(34), chr(34) * 2)}"')

    last = tokens[-1]
    if _PREFIX_SAFE.match(last):
        # Unquoted + trailing * for prefix matching. The safe set
        # contains no double quotes, so no escaping is needed here.
        parts.append(last + "*")
    else:
        # Has special characters; quote it, no prefix.
        parts.append(f'"{last.replace(chr(34), chr(34) * 2)}"')

    return " ".join(parts)


def search(
    conn: sqlite3.Connection, query: str, include_deleted: bool = False
) -> list:
    """Search memories via FTS5.

    An empty / whitespace-only query short-circuits to ``list_memories``
    because FTS5 raises on an empty MATCH expression. Soft-deleted rows
    are excluded unless ``include_deleted`` is true.
    """
    if not query or not query.strip():
        return list_memories(conn, include_deleted=include_deleted)

    safe = _sanitize(query)
    sql = (
        "SELECT m.* FROM memories m "
        "JOIN memories_fts f ON m.id = f.rowid "
        "WHERE memories_fts MATCH ?"
    )
    params: list[object] = [safe]
    if not include_deleted:
        sql += " AND m.is_deleted = 0"
    # is_pinned first, then FTS5 relevance (rank), then most recently updated.
    sql += " ORDER BY m.is_pinned DESC, rank, m.updated_at DESC"
    rows = conn.execute(sql, params).fetchall()
    return _memories_from_rows(conn, rows)
