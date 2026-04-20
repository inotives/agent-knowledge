"""SQLite storage — CRUD for projects, sessions, turns, memory_edits."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4


def _uuid() -> str:
    return uuid4().hex


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode, foreign keys, and auto-migrate."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    return conn


# --- Migrations ---

_MIGRATIONS: list[str] = [
    # v1: core tables
    """
    CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        path TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        metadata TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        project_id TEXT REFERENCES projects(id),
        agent TEXT NOT NULL,
        type TEXT NOT NULL,
        started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        ended_at TEXT,
        reviewed_at TEXT,
        metadata TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS turns (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id),
        request TEXT NOT NULL,
        response TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        metadata TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS memory_edits (
        id TEXT PRIMARY KEY,
        session_id TEXT REFERENCES sessions(id),
        page_path TEXT NOT NULL,
        tier TEXT NOT NULL CHECK (tier IN ('draft', 'knowledge', 'skill')),
        action TEXT NOT NULL CHECK (action IN ('create', 'update', 'delete')),
        summary TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    );

    CREATE INDEX IF NOT EXISTS idx_sessions_project_id ON sessions(project_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_ended_at ON sessions(ended_at);
    CREATE INDEX IF NOT EXISTS idx_sessions_reviewed_at ON sessions(reviewed_at);
    CREATE INDEX IF NOT EXISTS idx_turns_session_id ON turns(session_id);
    CREATE INDEX IF NOT EXISTS idx_turns_created_at ON turns(created_at);
    CREATE INDEX IF NOT EXISTS idx_memory_edits_session_id ON memory_edits(session_id);
    CREATE INDEX IF NOT EXISTS idx_memory_edits_page_path ON memory_edits(page_path);
    CREATE INDEX IF NOT EXISTS idx_memory_edits_tier ON memory_edits(tier);
    """,
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply pending migrations using PRAGMA user_version as the version tracker."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]

    # Detect databases created by dbmate (tables exist but user_version=0)
    if current == 0:
        has_tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        ).fetchone()
        if has_tables:
            # Existing dbmate DB — mark as v1 and apply only future migrations.
            # Also make project_id nullable (dbmate schema had NOT NULL).
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions_new (
                    id TEXT PRIMARY KEY,
                    project_id TEXT REFERENCES projects(id),
                    agent TEXT NOT NULL,
                    type TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    ended_at TEXT,
                    reviewed_at TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                INSERT OR IGNORE INTO sessions_new SELECT * FROM sessions;
                DROP TABLE sessions;
                ALTER TABLE sessions_new RENAME TO sessions;
                CREATE INDEX IF NOT EXISTS idx_sessions_project_id ON sessions(project_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_ended_at ON sessions(ended_at);
                CREATE INDEX IF NOT EXISTS idx_sessions_reviewed_at ON sessions(reviewed_at);
            """)
            conn.execute(f"PRAGMA user_version = {len(_MIGRATIONS)}")
            conn.commit()
            return

    for i, sql in enumerate(_MIGRATIONS):
        version = i + 1
        if version <= current:
            continue
        conn.executescript(sql)
        conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()


# --- Projects ---

def create_project(
    conn: sqlite3.Connection,
    name: str,
    path: str,
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    project_id = _uuid()
    conn.execute(
        "INSERT INTO projects (id, name, path, tags, metadata) VALUES (?, ?, ?, ?, ?)",
        (project_id, name, path, json.dumps(tags or []), json.dumps(metadata or {})),
    )
    conn.commit()
    return get_project(conn, project_id)


def get_project(conn: sqlite3.Connection, project_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_projects(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


# --- Sessions ---

def create_session(
    conn: sqlite3.Connection,
    project_id: str | None,
    agent: str,
    session_type: str,
    metadata: dict | None = None,
) -> dict:
    session_id = _uuid()
    conn.execute(
        "INSERT INTO sessions (id, project_id, agent, type, metadata) VALUES (?, ?, ?, ?, ?)",
        (session_id, project_id, agent, session_type, json.dumps(metadata or {})),
    )
    conn.commit()
    return get_session(conn, session_id)


def get_session(conn: sqlite3.Connection, session_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return _row_to_dict(row) if row else None


def end_session(conn: sqlite3.Connection, session_id: str) -> dict | None:
    conn.execute(
        "UPDATE sessions SET ended_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ? AND ended_at IS NULL",
        (session_id,),
    )
    conn.commit()
    return get_session(conn, session_id)


def set_session_reviewed(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute(
        "UPDATE sessions SET reviewed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
        (session_id,),
    )
    conn.commit()


def list_sessions(
    conn: sqlite3.Connection,
    project_id: str | None = None,
    date: str | None = None,
) -> list[dict]:
    query = "SELECT * FROM sessions WHERE 1=1"
    params: list = []
    if project_id:
        query += " AND project_id = ?"
        params.append(project_id)
    if date:
        query += " AND started_at LIKE ?"
        params.append(f"{date}%")
    query += " ORDER BY started_at DESC"
    rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_orphaned_sessions(conn: sqlite3.Connection, older_than_hours: int = 24) -> list[dict]:
    """Find sessions with no ended_at older than N hours."""
    rows = conn.execute(
        """SELECT * FROM sessions
        WHERE ended_at IS NULL
        AND started_at < strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)""",
        (f"-{older_than_hours} hours",),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def close_orphaned_sessions(conn: sqlite3.Connection, older_than_hours: int = 24) -> list[dict]:
    """Auto-close orphaned sessions, setting ended_at to last turn timestamp."""
    orphans = get_orphaned_sessions(conn, older_than_hours)
    for orphan in orphans:
        last_turn = conn.execute(
            "SELECT created_at FROM turns WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (orphan["id"],),
        ).fetchone()
        ended_at = last_turn["created_at"] if last_turn else orphan["started_at"]
        conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ?",
            (ended_at, orphan["id"]),
        )
    conn.commit()
    return orphans


def get_most_recent_open_session(conn: sqlite3.Connection) -> dict | None:
    """Get the most recently created open session (no ended_at)."""
    row = conn.execute(
        "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC, rowid DESC LIMIT 1",
    ).fetchone()
    return _row_to_dict(row) if row else None


def reopen_session(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """Reopen a previously ended session (clear ended_at) for continuation."""
    conn.execute("UPDATE sessions SET ended_at = NULL WHERE id = ?", (session_id,))
    conn.commit()
    return get_session(conn, session_id)


def update_session_metadata(
    conn: sqlite3.Connection,
    session_id: str,
    project_id: str | None = None,
    agent: str | None = None,
    session_type: str | None = None,
) -> dict | None:
    """Backfill/upgrade session metadata fields. Only non-None values are applied."""
    updates = []
    params: list = []
    if project_id is not None:
        updates.append("project_id = ?")
        params.append(project_id)
    if agent is not None:
        updates.append("agent = ?")
        params.append(agent)
    if session_type is not None:
        updates.append("type = ?")
        params.append(session_type)
    if not updates:
        return get_session(conn, session_id)
    params.append(session_id)
    conn.execute(f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    return get_session(conn, session_id)


def get_unreviewed_sessions(
    conn: sqlite3.Connection,
    project_id: str | None = None,
    exclude_today: bool = True,
) -> list[dict]:
    """Get sessions that ended but haven't been reviewed yet.

    By default excludes today's sessions (still accumulating).
    """
    query = """SELECT * FROM sessions
        WHERE ended_at IS NOT NULL
        AND reviewed_at IS NULL
        AND type != 'review'"""
    params: list = []
    if exclude_today:
        query += " AND date(started_at) < date('now')"
    if project_id:
        query += " AND project_id = ?"
        params.append(project_id)
    query += " ORDER BY started_at ASC"
    rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_sessions_needing_drafts(conn: sqlite3.Connection) -> list[dict]:
    """Get ended sessions that have turns but no session draft in memory_edits.

    These are sessions where the agent crashed before writing a session draft.
    """
    rows = conn.execute(
        """SELECT s.* FROM sessions s
        WHERE s.ended_at IS NOT NULL
        AND s.reviewed_at IS NULL
        AND s.type != 'review'
        AND EXISTS (SELECT 1 FROM turns t WHERE t.session_id = s.id)
        AND NOT EXISTS (
            SELECT 1 FROM memory_edits me
            WHERE me.session_id = s.id
            AND me.page_path LIKE 'drafts/sessions/%'
            AND me.action = 'create'
        )
        ORDER BY s.started_at ASC""",
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# --- Turns ---

def create_turns(
    conn: sqlite3.Connection,
    session_id: str,
    turns: list[dict],
) -> list[dict]:
    results = []
    for turn in turns:
        turn_id = _uuid()
        conn.execute(
            "INSERT INTO turns (id, session_id, request, response, metadata) VALUES (?, ?, ?, ?, ?)",
            (turn_id, session_id, turn["request"], turn["response"], json.dumps(turn.get("metadata", {}))),
        )
        results.append({"id": turn_id, "session_id": session_id, **turn})
    conn.commit()
    return results


def get_turns(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM turns WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# --- Memory Edits ---

def create_memory_edit(
    conn: sqlite3.Connection,
    page_path: str,
    tier: str,
    action: str,
    summary: str,
    session_id: str | None = None,
) -> dict:
    edit_id = _uuid()
    conn.execute(
        "INSERT INTO memory_edits (id, session_id, page_path, tier, action, summary) VALUES (?, ?, ?, ?, ?, ?)",
        (edit_id, session_id, page_path, tier, action, summary),
    )
    conn.commit()
    return {"id": edit_id, "session_id": session_id, "page_path": page_path, "tier": tier, "action": action, "summary": summary}


def get_memory_history(
    conn: sqlite3.Connection,
    limit: int = 20,
    page_path: str | None = None,
) -> list[dict]:
    query = "SELECT * FROM memory_edits WHERE 1=1"
    params: list = []
    if page_path:
        query += " AND page_path = ?"
        params.append(page_path)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_session_draft_path(conn: sqlite3.Connection, session_id: str) -> str | None:
    """Find the session draft file path for a given session via memory_edits."""
    row = conn.execute(
        """SELECT page_path FROM memory_edits
        WHERE session_id = ?
        AND page_path LIKE 'drafts/sessions/%'
        AND action = 'create'
        ORDER BY created_at DESC LIMIT 1""",
        (session_id,),
    ).fetchone()
    return row["page_path"] if row else None


# --- Helpers ---

def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("tags", "metadata"):
        if key in d and isinstance(d[key], str):
            d[key] = json.loads(d[key])
    return d
