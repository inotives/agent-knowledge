"""SQLite storage — CRUD for projects, sessions, turns, memory_edits."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4


def _uuid() -> str:
    return uuid4().hex


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and foreign keys enabled."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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
    project_id: str,
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


# --- Helpers ---

def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("tags", "metadata"):
        if key in d and isinstance(d[key], str):
            d[key] = json.loads(d[key])
    return d
