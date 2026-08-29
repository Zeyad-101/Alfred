"""Database connection and schema management for Alfred."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core.paths import default_db_path


# The default on-disk location for the user's database. In dev
# mode this resolves to ``<project>/data/alfred.db``; in a
# packaged install it resolves to ``%APPDATA%\\Alfred\\alfred.db``
# (see ``core/paths.default_db_path``). The constant is evaluated
# at import time so callers that still use the bare name
# (``DEFAULT_DB_PATH``) get a stable string, but the path is
# computed from the platform-aware helper so a packaged build
# never lands its data next to the .exe.
DEFAULT_DB_PATH: str = default_db_path()


def now() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys enabled and Row factory set.

    The parent directory of ``db_path`` is created if it does not exist, so
    callers can pass a fresh path like ``data/alfred.db`` without first
    ensuring ``data/`` exists.
    """
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


# Schema is wrapped in IF NOT EXISTS so init_db is idempotent.
# The FTS5 triggers are the #1 bug source when done by hand -- we let SQLite
# keep memories_fts in sync automatically on every write to ``memories``.
SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT 'note',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_pinned INTEGER NOT NULL DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS memory_tags (
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (memory_id, tag_id)
);

CREATE TABLE IF NOT EXISTS memory_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    saved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_details (
    memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    due_date TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_projects (
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    PRIMARY KEY (memory_id, project_id)
);

CREATE TABLE IF NOT EXISTS memory_links (
    memory_id_a INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    memory_id_b INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (memory_id_a, memory_id_b),
    CHECK (memory_id_a < memory_id_b)
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    title, content, content='memories', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content) VALUES ('delete', old.id, old.title, old.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content) VALUES ('delete', old.id, old.title, old.content);
    INSERT INTO memories_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
END;
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Create the Alfred schema if it does not already exist."""
    conn.executescript(SCHEMA)
    conn.commit()
