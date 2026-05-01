"""DuckDB search index — BM25 full-text search across all indexed tiers (EP-00008)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from agent_knowledge.core import paths


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB connection and ensure schema exists.

    Uses in-memory storage since the index is always rebuilt from files on
    startup. This avoids exclusive file locks that prevent concurrent sessions.
    """
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL fts")
    conn.execute("LOAD fts")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_pages (
            path TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            tier TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata TEXT DEFAULT '{}'
        )
    """)


def sync_from_files(conn: duckdb.DuckDBPyConnection, memory_dir: Path) -> int:
    """Rebuild the search index from every indexed tier (EP-00008 + EP-00009).

    Walks each `(tier, subdir)` in `paths.INDEXED_TIERS` recursively, skipping any
    `_archived/` subfolder so archived pages don't pollute live-tier results.
    Archived sessions are picked up via `ARCHIVED_SESSION_GLOB`. Skills and
    agents are walked separately under their dedicated tiers (`skill`, `agent`)
    via `paths.INTELLIGENCES_TIERS` — they share the index table but stay
    excluded from `memory_search` results unless explicitly requested.

    Returns the number of pages indexed.
    """
    conn.execute("DELETE FROM memory_pages")

    count = 0
    for tier_name, subdir in paths.INDEXED_TIERS:
        tier_dir = memory_dir / subdir
        if not tier_dir.exists():
            continue
        for md_file in tier_dir.rglob("*.md"):
            if "_archived" in md_file.relative_to(tier_dir).parts:
                continue
            count += _index_file(conn, memory_dir, md_file, tier_name)

    for md_file in memory_dir.glob(paths.ARCHIVED_SESSION_GLOB):
        count += _index_file(conn, memory_dir, md_file, paths.ARCHIVED_SESSION_TIER)

    count += _sync_intelligences(conn, memory_dir)

    _rebuild_fts(conn)
    return count


def _sync_intelligences(conn: duckdb.DuckDBPyConnection, memory_dir: Path) -> int:
    """Index skills (SKILL.md only) and agents (single-file personas) — EP-00009.

    Skills walker uses `rglob(SKILL_ENTRY_FILENAME)` so each bundle yields exactly
    one row regardless of how many resources/scripts it ships. Agents walker uses
    `rglob('*.md')` since personas are flat. Both skip `_archived/` subfolders.
    """
    count = 0
    for tier_name, subdir, walker_glob in paths.INTELLIGENCES_TIERS:
        tier_dir = memory_dir / subdir
        if not tier_dir.exists():
            continue
        for md_file in tier_dir.rglob(walker_glob):
            if "_archived" in md_file.relative_to(tier_dir).parts:
                continue
            count += _index_file(conn, memory_dir, md_file, tier_name)
    return count


def _index_file(
    conn: duckdb.DuckDBPyConnection,
    memory_dir: Path,
    md_file: Path,
    tier_name: str,
) -> int:
    content = md_file.read_text(encoding="utf-8")
    rel_path = str(md_file.relative_to(memory_dir))
    title = _extract_title(content, md_file.stem)
    summary = _extract_summary(content)
    tags = _extract_tags(content)
    updated_at = md_file.stat().st_mtime
    updated_at_str = datetime.fromtimestamp(updated_at, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn.execute(
        """INSERT OR REPLACE INTO memory_pages
        (path, title, content, summary, tags, tier, updated_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, '{}')""",
        (rel_path, title, content, summary, tags, tier_name, updated_at_str),
    )
    return 1


def search(
    conn: duckdb.DuckDBPyConnection,
    query: str,
    tier: str | None = None,
    domain_filter: str | None = None,
) -> list[dict]:
    """Search indexed pages using BM25. Returns ranked results.

    `domain_filter` (EP-00009) scopes results to a single domain segment within
    the tier — e.g. `tier='skill', domain_filter='engineering'` only matches
    `3_intelligences/skills/engineering/...`. Applied as a path-prefix predicate
    so BM25 ranks within the scoped subset.
    """
    if not query.strip():
        return []

    escaped = query.replace("'", "''")

    sql = """
        SELECT path, title, summary, tier,
               fts_main_memory_pages.match_bm25(path, ?) AS score
        FROM memory_pages
        WHERE score IS NOT NULL
    """
    params: list = [escaped]

    if tier:
        sql += " AND tier = ?"
        params.append(tier)

    if domain_filter and tier:
        prefix = _domain_prefix(tier, domain_filter)
        if prefix:
            sql += " AND path LIKE ?"
            params.append(f"{prefix}%")

    sql += " ORDER BY score DESC"

    rows = conn.execute(sql, params).fetchall()
    return [
        {"path": r[0], "title": r[1], "summary": r[2], "tier": r[3], "score": r[4]}
        for r in rows
    ]


def _domain_prefix(tier: str, domain: str) -> str | None:
    """Build the path prefix for a `(tier, domain)` filter.

    Only meaningful for skill/agent tiers — returns None otherwise so the
    domain predicate is silently dropped (general tiers don't have domains).
    """
    if tier == "skill":
        return f"{paths.SKILLS_DIR}/{domain}/"
    if tier == "agent":
        return f"{paths.AGENTS_DIR}/{domain}/"
    return None


def get_index(
    conn: duckdb.DuckDBPyConnection,
    tier: str | None = None,
) -> list[dict]:
    """Return a catalog of all indexed pages."""
    sql = "SELECT path, title, summary, tier FROM memory_pages"
    params: list = []
    if tier:
        sql += " WHERE tier = ?"
        params.append(tier)
    sql += " ORDER BY path"

    rows = conn.execute(sql, params).fetchall()
    return [
        {"path": r[0], "title": r[1], "summary": r[2], "tier": r[3]}
        for r in rows
    ]


def _rebuild_fts(conn: duckdb.DuckDBPyConnection) -> None:
    """Rebuild the FTS index."""
    conn.execute("PRAGMA create_fts_index('memory_pages', 'path', 'title', 'content', overwrite=1)")


def _extract_title(content: str, fallback: str) -> str:
    """Extract title from first # heading, or use filename."""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _extract_summary(content: str) -> str:
    """Extract first non-heading, non-empty paragraph as summary."""
    lines = content.splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
            return stripped[:200]
    return ""


def _extract_tags(content: str) -> str:
    """Extract tags from YAML frontmatter if present."""
    if not content.startswith("---"):
        return "[]"
    parts = content.split("---", 2)
    if len(parts) < 3:
        return "[]"
    frontmatter = parts[1]
    for line in frontmatter.splitlines():
        if line.strip().startswith("tags:"):
            tag_str = line.split(":", 1)[1].strip()
            tags = [t.strip().strip("\"'") for t in re.findall(r"[\w-]+", tag_str)]
            return str(tags)
    return "[]"
