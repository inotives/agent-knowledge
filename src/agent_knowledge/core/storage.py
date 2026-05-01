"""SQLite storage — CRUD for projects, groups (via marker turns), turns, memory_edits.

EP-00005: groups replace sessions. A group is a sequence of segments (start→end pairs)
sharing the same group_id. State lives entirely in `turns` via marker rows
(kind ∈ {'start','turn','end','idle_close'}). The `sessions` table is gone.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Idle threshold: if a group's latest turn is older than this, the next touch
# implicitly closes the stale segment and starts a new one (same group_id).
DEFAULT_IDLE_CLOSE_MINUTES = 30


def _uuid() -> str:
    return uuid4().hex


def _now_iso() -> str:
    """Microsecond-precision UTC ISO8601. Matches SQLite's strftime('%Y-%m-%dT%H:%M:%f', 'now')
    in shape (millisecond) but extended to microseconds for tiebreak headroom."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond:06d}Z"


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

# In-code migrations applied at every storage.connect via PRAGMA user_version.
# Each entry is one schema version; new entries append-only. Pre-release scope:
# no data preservation across schema-breaking changes (e.g. the EP-00005 cut).

_MIGRATIONS: list[str] = [
    # v1: EP-00005 baseline schema (Phase 1).
    """
    CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        path TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        metadata TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS turns (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('start', 'turn', 'end', 'idle_close')),
        request TEXT,
        response TEXT,
        metadata TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    );

    CREATE TABLE IF NOT EXISTS memory_edits (
        id TEXT PRIMARY KEY,
        group_id TEXT,
        page_path TEXT NOT NULL,
        tier TEXT NOT NULL CHECK (tier IN ('draft', 'knowledge', 'skill')),
        action TEXT NOT NULL CHECK (action IN ('create', 'update', 'delete')),
        summary TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    );

    CREATE INDEX IF NOT EXISTS idx_turns_group_created ON turns(group_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_turns_group_kind_created ON turns(group_id, kind, created_at);
    CREATE INDEX IF NOT EXISTS idx_memory_edits_group_id ON memory_edits(group_id);
    CREATE INDEX IF NOT EXISTS idx_memory_edits_page_path ON memory_edits(page_path);
    CREATE INDEX IF NOT EXISTS idx_memory_edits_tier ON memory_edits(tier);
    """,
    # v2: EP-00005 Phase 2 — draft_state table backs indexed pending counts.
    """
    CREATE TABLE IF NOT EXISTS draft_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        draft_path TEXT NOT NULL UNIQUE,
        group_id TEXT NOT NULL,
        segment_start_at TEXT NOT NULL,
        segment_end_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        archived_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_draft_state_pending ON draft_state(archived_at, segment_end_at);
    CREATE INDEX IF NOT EXISTS idx_draft_state_group ON draft_state(group_id);
    """,
    # v3: EP-00008 — relax memory_edits CHECK constraints to add `agent` / `config`
    # tiers and the `archive` action (carve-out delete redirects to archive).
    # SQLite can't ALTER CHECK constraints in place; recreate the table.
    """
    ALTER TABLE memory_edits RENAME TO _memory_edits_v2;

    CREATE TABLE memory_edits (
        id TEXT PRIMARY KEY,
        group_id TEXT,
        page_path TEXT NOT NULL,
        tier TEXT NOT NULL CHECK (tier IN ('draft', 'knowledge', 'skill', 'agent', 'config')),
        action TEXT NOT NULL CHECK (action IN ('create', 'update', 'delete', 'archive')),
        summary TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    );

    INSERT INTO memory_edits (id, group_id, page_path, tier, action, summary, created_at)
        SELECT id, group_id, page_path, tier, action, summary, created_at FROM _memory_edits_v2;

    DROP TABLE _memory_edits_v2;

    CREATE INDEX idx_memory_edits_group_id ON memory_edits(group_id);
    CREATE INDEX idx_memory_edits_page_path ON memory_edits(page_path);
    CREATE INDEX idx_memory_edits_tier ON memory_edits(tier);
    """,
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply pending migrations using PRAGMA user_version as the version tracker."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
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
    project = get_project(conn, project_id)
    assert project is not None  # just inserted
    return project


def get_project(conn: sqlite3.Connection, project_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_projects(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


# --- Groups (marker-turn API) ---

def _latest_turn(conn: sqlite3.Connection, group_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM turns WHERE group_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (group_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def _latest_start_turn(conn: sqlite3.Connection, group_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM turns WHERE group_id = ? AND kind = 'start' "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (group_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def _is_end_marker(turn: dict | None) -> bool:
    return turn is not None and turn["kind"] in ("end", "idle_close")


def _insert_marker(
    conn: sqlite3.Connection,
    group_id: str,
    kind: str,
    metadata: dict | None = None,
    created_at: str | None = None,
) -> dict:
    """Insert a single marker or turn row. Caller commits."""
    turn_id = _uuid()
    ts = created_at or _now_iso()
    conn.execute(
        "INSERT INTO turns (id, group_id, kind, request, response, metadata, created_at) "
        "VALUES (?, ?, ?, NULL, NULL, ?, ?)",
        (turn_id, group_id, kind, json.dumps(metadata or {}), ts),
    )
    return {
        "id": turn_id,
        "group_id": group_id,
        "kind": kind,
        "request": None,
        "response": None,
        "metadata": metadata or {},
        "created_at": ts,
    }


def _maybe_close_stale_segment(
    conn: sqlite3.Connection,
    group_id: str,
    idle_minutes: int = DEFAULT_IDLE_CLOSE_MINUTES,
) -> dict | None:
    """If the group's latest turn is open and older than idle_minutes, write idle_close.

    Returns the inserted idle_close marker (with `segment_start_at` field added) if one
    was written, else None. Caller commits.
    """
    latest = _latest_turn(conn, group_id)
    if latest is None or _is_end_marker(latest):
        return None

    cutoff = conn.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?) AS cutoff",
        (f"-{idle_minutes} minutes",),
    ).fetchone()["cutoff"]
    if latest["created_at"] >= cutoff:
        return None

    # Latest turn is older than the idle threshold — close the stale segment.
    # Use the latest turn's timestamp as the close time so the segment_end_at
    # reflects when activity actually stopped.
    marker = _insert_marker(
        conn,
        group_id,
        kind="idle_close",
        metadata={"reason": "idle_close_on_stale", "idle_minutes": idle_minutes},
        created_at=latest["created_at"],
    )
    start = _latest_start_turn(conn, group_id)
    marker["segment_start_at"] = start["created_at"] if start else None
    return marker


def start_group(
    conn: sqlite3.Connection,
    group_id: str | None = None,
    agent: str = "unknown",
    metadata: dict | None = None,
    idle_minutes: int = DEFAULT_IDLE_CLOSE_MINUTES,
) -> dict:
    """Begin a new segment for a group. Generates group_id if not provided.

    If `group_id` is provided and its latest turn is open and stale, writes an
    `idle_close` marker for the stale segment first, then the new `start`.

    Returns dict: {group_id, segment_start_at, marker, idle_closed_segment | None}
    where `idle_closed_segment` describes any segment that was implicitly closed.
    """
    gid = group_id or _uuid()
    md = dict(metadata or {})
    md.setdefault("agent", agent)

    idle_closed = _maybe_close_stale_segment(conn, gid, idle_minutes) if group_id else None
    marker = _insert_marker(conn, gid, kind="start", metadata=md)
    conn.commit()
    return {
        "group_id": gid,
        "segment_start_at": marker["created_at"],
        "marker": marker,
        "idle_closed_segment": idle_closed,
    }


def end_group(
    conn: sqlite3.Connection,
    group_id: str,
    kind: str = "end",
) -> dict | None:
    """Write an end marker for the group's current segment. Idempotent.

    If the group's latest turn is already an end marker (`end` or `idle_close`),
    no new marker is written and the existing closed segment is returned.

    Returns dict: {group_id, segment_start_at, segment_end_at, marker} or None
    if the group has no turns at all.
    """
    if kind not in ("end", "idle_close"):
        raise ValueError(f"end_group kind must be 'end' or 'idle_close', got {kind!r}")

    latest = _latest_turn(conn, group_id)
    if latest is None:
        return None

    start = _latest_start_turn(conn, group_id)

    if _is_end_marker(latest):
        # Idempotent: already closed. Return the existing pair.
        return {
            "group_id": group_id,
            "segment_start_at": start["created_at"] if start else None,
            "segment_end_at": latest["created_at"],
            "marker": latest,
        }

    marker = _insert_marker(conn, group_id, kind=kind)
    conn.commit()
    return {
        "group_id": group_id,
        "segment_start_at": start["created_at"] if start else None,
        "segment_end_at": marker["created_at"],
        "marker": marker,
    }


def create_turns(
    conn: sqlite3.Connection,
    group_id: str,
    turns: list[dict],
    idle_minutes: int = DEFAULT_IDLE_CLOSE_MINUTES,
) -> dict:
    """Append `kind='turn'` rows to a group. Auto-handles the idle-close-on-stale path:

    if the group's latest turn is open and older than `idle_minutes`, writes an
    `idle_close` for the stale segment and a fresh `start` marker before the new turns.

    Returns dict: {turns: [...], idle_closed_segment: dict | None, segment_start_at}
    """
    idle_closed = _maybe_close_stale_segment(conn, group_id, idle_minutes)
    if idle_closed is not None:
        # Open a new segment under the same group_id.
        _insert_marker(conn, group_id, kind="start")

    inserted = []
    for turn in turns:
        turn_id = _uuid()
        ts = _now_iso()
        conn.execute(
            "INSERT INTO turns (id, group_id, kind, request, response, metadata, created_at) "
            "VALUES (?, ?, 'turn', ?, ?, ?, ?)",
            (
                turn_id,
                group_id,
                turn.get("request", ""),
                turn.get("response", ""),
                json.dumps(turn.get("metadata", {})),
                ts,
            ),
        )
        inserted.append({
            "id": turn_id,
            "group_id": group_id,
            "kind": "turn",
            "request": turn.get("request", ""),
            "response": turn.get("response", ""),
            "metadata": turn.get("metadata", {}),
            "created_at": ts,
        })
    conn.commit()

    start = _latest_start_turn(conn, group_id)
    return {
        "turns": inserted,
        "idle_closed_segment": idle_closed,
        "segment_start_at": start["created_at"] if start else None,
    }


def get_group_turns(conn: sqlite3.Connection, group_id: str) -> list[dict]:
    """All turns for a group across all segments, including markers."""
    rows = conn.execute(
        "SELECT * FROM turns WHERE group_id = ? ORDER BY created_at ASC, rowid ASC",
        (group_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_current_segment_turns(conn: sqlite3.Connection, group_id: str) -> list[dict]:
    """Turns since the latest `start` marker (inclusive of the start marker)."""
    start = _latest_start_turn(conn, group_id)
    if start is None:
        return []
    rows = conn.execute(
        "SELECT * FROM turns WHERE group_id = ? AND created_at >= ? "
        "ORDER BY created_at ASC, rowid ASC",
        (group_id, start["created_at"]),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_segment_turns(
    conn: sqlite3.Connection,
    group_id: str,
    segment_start_at: str,
) -> list[dict]:
    """Turns for a specific segment, identified by its start marker timestamp.

    Returns turns from `segment_start_at` (inclusive) up to and including the
    next end marker for the same group_id.
    """
    end = conn.execute(
        "SELECT created_at FROM turns "
        "WHERE group_id = ? AND created_at > ? AND kind IN ('end','idle_close') "
        "ORDER BY created_at ASC, rowid ASC LIMIT 1",
        (group_id, segment_start_at),
    ).fetchone()

    if end is None:
        # Segment is still open.
        rows = conn.execute(
            "SELECT * FROM turns WHERE group_id = ? AND created_at >= ? "
            "ORDER BY created_at ASC, rowid ASC",
            (group_id, segment_start_at),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM turns WHERE group_id = ? "
            "AND created_at >= ? AND created_at <= ? "
            "ORDER BY created_at ASC, rowid ASC",
            (group_id, segment_start_at, end["created_at"]),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_group_segments(conn: sqlite3.Connection, group_id: str) -> list[dict]:
    """All segments for a group, ordered by start time.

    Each segment is {segment_start_at, segment_end_at, end_kind}. An open segment
    has `segment_end_at=None` and `end_kind=None`.
    """
    rows = conn.execute(
        "SELECT created_at, kind FROM turns WHERE group_id = ? "
        "AND kind IN ('start','end','idle_close') "
        "ORDER BY created_at ASC, rowid ASC",
        (group_id,),
    ).fetchall()

    segments: list[dict] = []
    current_start: str | None = None
    for row in rows:
        if row["kind"] == "start":
            if current_start is not None:
                # Two starts in a row without an end — treat the prior as still-open.
                segments.append({
                    "segment_start_at": current_start,
                    "segment_end_at": None,
                    "end_kind": None,
                })
            current_start = row["created_at"]
        else:
            # end or idle_close
            if current_start is not None:
                segments.append({
                    "segment_start_at": current_start,
                    "segment_end_at": row["created_at"],
                    "end_kind": row["kind"],
                })
                current_start = None
    if current_start is not None:
        segments.append({
            "segment_start_at": current_start,
            "segment_end_at": None,
            "end_kind": None,
        })
    return segments


def get_open_groups(conn: sqlite3.Connection) -> list[dict]:
    """Groups whose latest turn is not an end marker.

    Uses (created_at, rowid) as tiebreaker so multi-row writes within the same
    millisecond are ordered correctly.

    Returns a list of {group_id, latest_kind, latest_at, start_marker_metadata}.
    """
    rows = conn.execute(
        """SELECT t.group_id, t.kind AS latest_kind, t.created_at AS latest_at
           FROM turns t
           WHERE t.id = (
               SELECT id FROM turns t2
               WHERE t2.group_id = t.group_id
               ORDER BY t2.created_at DESC, t2.rowid DESC
               LIMIT 1
           )
           AND t.kind NOT IN ('end','idle_close')
           ORDER BY t.created_at ASC"""
    ).fetchall()
    results = []
    for row in rows:
        start = _latest_start_turn(conn, row["group_id"])
        results.append({
            "group_id": row["group_id"],
            "latest_kind": row["latest_kind"],
            "latest_at": row["latest_at"],
            "start_marker_metadata": start["metadata"] if start else {},
        })
    return results


def get_orphaned_groups(
    conn: sqlite3.Connection,
    older_than_hours: int = 24,
) -> list[dict]:
    """Open groups whose latest turn is older than the cutoff. Recovery candidates."""
    cutoff = conn.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?) AS cutoff",
        (f"-{older_than_hours} hours",),
    ).fetchone()["cutoff"]

    open_groups = get_open_groups(conn)
    return [g for g in open_groups if g["latest_at"] < cutoff]


def _expected_draft_path(group_id: str, segment_start_at: str) -> str:
    """Compute the canonical session-draft path for a segment.

    Thin wrapper over `paths.session_draft_path` — kept so callers in this
    module don't need to import paths directly.
    """
    from agent_knowledge.core.paths import session_draft_path
    return session_draft_path(group_id, segment_start_at)


def get_closed_no_draft_segments(conn: sqlite3.Connection) -> list[dict]:
    """Closed segments whose canonical session-draft path has no create entry in memory_edits.

    A segment is identified as needing a stub draft if there is no
    `action='create'` memory_edit row for the canonical draft path computed from
    its `(group_id, segment_start_at)`. Used by `akw recover`.
    """
    group_rows = conn.execute(
        "SELECT DISTINCT group_id FROM turns ORDER BY group_id"
    ).fetchall()

    results: list[dict] = []
    for grow in group_rows:
        gid = grow["group_id"]
        for seg in get_group_segments(conn, gid):
            if seg["segment_end_at"] is None:
                continue  # still open — handled by orphan recovery
            expected_path = _expected_draft_path(gid, seg["segment_start_at"])
            existing = conn.execute(
                "SELECT 1 FROM memory_edits "
                "WHERE page_path = ? AND action = 'create' LIMIT 1",
                (expected_path,),
            ).fetchone()
            if existing:
                continue
            turn_count_row = conn.execute(
                "SELECT COUNT(*) AS n FROM turns "
                "WHERE group_id = ? AND kind = 'turn' "
                "AND created_at >= ? AND created_at <= ?",
                (gid, seg["segment_start_at"], seg["segment_end_at"]),
            ).fetchone()
            start = conn.execute(
                "SELECT metadata FROM turns WHERE group_id = ? AND kind = 'start' "
                "AND created_at = ?",
                (gid, seg["segment_start_at"]),
            ).fetchone()
            results.append({
                "group_id": gid,
                "segment_start_at": seg["segment_start_at"],
                "segment_end_at": seg["segment_end_at"],
                "end_kind": seg["end_kind"],
                "turn_count": turn_count_row["n"] if turn_count_row else 0,
                "start_marker_metadata": json.loads(start["metadata"]) if start else {},
            })
    return results


def list_groups(
    conn: sqlite3.Connection,
    filter_metadata: dict | None = None,
) -> list[dict]:
    """Enumerate groups with start-marker metadata.

    `filter_metadata` is a dict of equality matches against the start marker's
    metadata JSON (e.g. {"agent": "claude"}). Uses json_extract — unindexed but
    acceptable for current scale.

    Returns a list of {group_id, started_at, latest_at, latest_kind, metadata}.
    """
    base_query = """
        SELECT
            t.group_id,
            (SELECT created_at FROM turns t2
             WHERE t2.group_id = t.group_id AND t2.kind = 'start'
             ORDER BY t2.created_at ASC, t2.rowid ASC LIMIT 1) AS started_at,
            (SELECT metadata FROM turns t2
             WHERE t2.group_id = t.group_id AND t2.kind = 'start'
             ORDER BY t2.created_at ASC, t2.rowid ASC LIMIT 1) AS metadata,
            (SELECT created_at FROM turns t3
             WHERE t3.group_id = t.group_id
             ORDER BY t3.created_at DESC, t3.rowid DESC LIMIT 1) AS latest_at,
            (SELECT kind FROM turns t4
             WHERE t4.group_id = t.group_id
             ORDER BY t4.created_at DESC, t4.rowid DESC LIMIT 1) AS latest_kind
        FROM turns t
        GROUP BY t.group_id
    """
    where_clauses: list[str] = []
    params: list = []
    if filter_metadata:
        for key, value in filter_metadata.items():
            where_clauses.append("json_extract(metadata, ?) = ?")
            params.extend([f"$.{key}", value])
    if where_clauses:
        base_query = (
            f"SELECT * FROM ({base_query}) WHERE " + " AND ".join(where_clauses)
        )
    base_query += " ORDER BY started_at ASC"
    rows = conn.execute(base_query, params).fetchall()

    results = []
    for row in rows:
        d = dict(row)
        if isinstance(d.get("metadata"), str):
            d["metadata"] = json.loads(d["metadata"])
        results.append(d)
    return results


# --- Memory Edits ---

def create_memory_edit(
    conn: sqlite3.Connection,
    page_path: str,
    tier: str,
    action: str,
    summary: str,
    group_id: str | None = None,
) -> dict:
    edit_id = _uuid()
    conn.execute(
        "INSERT INTO memory_edits (id, group_id, page_path, tier, action, summary) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (edit_id, group_id, page_path, tier, action, summary),
    )
    conn.commit()
    return {
        "id": edit_id,
        "group_id": group_id,
        "page_path": page_path,
        "tier": tier,
        "action": action,
        "summary": summary,
    }


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


def get_segment_draft_path(
    conn: sqlite3.Connection,
    group_id: str,
    segment_start_at: str | None = None,
) -> str | None:
    """Find the session-draft path written for a specific segment (or the most
    recent one, if `segment_start_at` is None)."""
    if segment_start_at:
        row = conn.execute(
            """SELECT page_path FROM memory_edits
            WHERE group_id = ?
            AND page_path LIKE '1_drafts/sessions/%'
            AND action = 'create'
            AND created_at >= ?
            ORDER BY created_at ASC LIMIT 1""",
            (group_id, segment_start_at),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT page_path FROM memory_edits
            WHERE group_id = ?
            AND page_path LIKE '1_drafts/sessions/%'
            AND action = 'create'
            ORDER BY created_at DESC LIMIT 1""",
            (group_id,),
        ).fetchone()
    return row["page_path"] if row else None


# --- Draft state (Phase 2) ---

def upsert_draft_state(
    conn: sqlite3.Connection,
    draft_path: str,
    group_id: str,
    segment_start_at: str,
    segment_end_at: str,
) -> dict:
    """Insert or refresh a draft_state row.

    On conflict by `draft_path`, updates segment_end_at and clears archived_at
    (re-creating a draft after archive deliberately resurrects it).
    """
    ts = _now_iso()
    conn.execute(
        """INSERT INTO draft_state (draft_path, group_id, segment_start_at, segment_end_at, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(draft_path) DO UPDATE SET
               segment_end_at = excluded.segment_end_at,
               archived_at = NULL""",
        (draft_path, group_id, segment_start_at, segment_end_at, ts),
    )
    conn.commit()
    return get_draft_state(conn, draft_path) or {}


def get_draft_state(conn: sqlite3.Connection, draft_path: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM draft_state WHERE draft_path = ?", (draft_path,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def archive_draft_state(
    conn: sqlite3.Connection,
    draft_path: str,
    new_path: str,
) -> dict | None:
    """Mark a draft_state row archived: set archived_at + update draft_path to the
    archived location. The id stays stable."""
    ts = _now_iso()
    conn.execute(
        "UPDATE draft_state SET archived_at = ?, draft_path = ? WHERE draft_path = ?",
        (ts, new_path, draft_path),
    )
    conn.commit()
    return get_draft_state(conn, new_path)


def count_unarchived_session_drafts(
    conn: sqlite3.Connection,
    exclude_today: bool = True,
) -> int:
    """Count session drafts that exist (in `1_drafts/sessions/`) and are not archived.

    `exclude_today=True` (default) drops drafts whose segment_end_at falls on
    today's UTC date, since today's draft is still in active review.
    """
    query = "SELECT COUNT(*) AS n FROM draft_state WHERE archived_at IS NULL"
    params: list = []
    if exclude_today:
        query += " AND date(segment_end_at) < date('now')"
    row = conn.execute(query, params).fetchone()
    return row["n"] if row else 0


def reindex_draft_state(
    conn: sqlite3.Connection,
    memory_dir,
    force: bool = False,
) -> dict:
    """Rebuild draft_state from on-disk frontmatter.

    Two roles, per Decision C:
    - Drift recovery (table missing or stale): walk `1_drafts/sessions/` and the
      archived flat-file glob (`1_drafts/_archived/sessions__*.md`) and INSERT
      rows for any frontmatter found. Requires `force=True` if the table is
      non-empty (guard against frontmatter drift silently overwriting canonical
      state).
    - Reconciliation (manual file moves): mark draft_state rows archived when
      their draft_path file is missing but the same basename exists in the
      flat-file archive.
    """
    from pathlib import Path
    from agent_knowledge.core import paths as _paths

    memory_dir = Path(memory_dir)
    sessions_dir = memory_dir / _paths.SESSIONS_DIR

    existing_count = conn.execute("SELECT COUNT(*) AS n FROM draft_state").fetchone()["n"]
    rebuilt = 0
    reconciled = 0

    rows = conn.execute(
        "SELECT id, draft_path FROM draft_state WHERE archived_at IS NULL"
    ).fetchall()
    for row in rows:
        relative = row["draft_path"]
        full = memory_dir / relative
        if full.exists():
            continue
        new_relative = _paths.archived_session_path(relative)
        if (memory_dir / new_relative).exists():
            conn.execute(
                "UPDATE draft_state SET archived_at = ?, draft_path = ? WHERE id = ?",
                (_now_iso(), new_relative, row["id"]),
            )
            reconciled += 1

    if existing_count == 0 or force:
        live_paths = list(sessions_dir.glob("*.md")) if sessions_dir.exists() else []
        archived_paths = list(memory_dir.glob(_paths.ARCHIVED_SESSION_GLOB))
        for path, archived in [(p, False) for p in live_paths] + [(p, True) for p in archived_paths]:
            fm = _parse_frontmatter(path.read_text())
            if not fm or "group_id" not in fm:
                continue
            relative = str(path.relative_to(memory_dir))
            conn.execute(
                """INSERT INTO draft_state
                    (draft_path, group_id, segment_start_at, segment_end_at, created_at, archived_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(draft_path) DO UPDATE SET
                       group_id = excluded.group_id,
                       segment_start_at = excluded.segment_start_at,
                       segment_end_at = excluded.segment_end_at,
                       archived_at = excluded.archived_at""",
                (
                    relative,
                    fm.get("group_id", ""),
                    fm.get("segment_start_at", ""),
                    fm.get("segment_end_at", ""),
                    fm.get("created_at", _now_iso()),
                    _now_iso() if archived else None,
                ),
            )
            rebuilt += 1
        conn.commit()

    return {"rebuilt": rebuilt, "reconciled": reconciled, "had_existing_rows": existing_count > 0}


def _parse_frontmatter(content: str) -> dict | None:
    """Pull simple `key: value` pairs out of a `---`-fenced YAML frontmatter block.

    Not a full YAML parser — handles flat scalars only, which is all draft
    frontmatter writes. Returns None if no frontmatter is present.
    """
    if not content.startswith("---"):
        return None
    rest = content[3:]
    end_idx = rest.find("\n---")
    if end_idx == -1:
        return None
    block = rest[:end_idx].strip()
    out: dict = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


# --- Helpers ---

def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("tags", "metadata"):
        if key in d and isinstance(d[key], str):
            d[key] = json.loads(d[key])
    return d
