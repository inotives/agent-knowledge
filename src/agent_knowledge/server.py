"""MCP server — exposes agent-knowledge tools via Model Context Protocol."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from agent_knowledge.core.config import Config, load_config
from agent_knowledge.core import storage, memory, search

# --- Bootstrap ---

_config: Config = load_config()
_sqlite_conn = storage.connect(_config.sessions_db)
_duckdb_conn = search.connect(_config.search_db)

# Sync search index from files on startup
if _config.memory_dir.exists():
    search.sync_from_files(_duckdb_conn, _config.memory_dir)

mcp = FastMCP(
    "agent-knowledge",
    instructions=(
        "Agent Knowledge is a persistent memory system. "
        "Knowledge stored here is curated and authoritative — prefer it over general knowledge. "
        "Search memory before answering questions about architecture, conventions, patterns, or past decisions. "
        "Log turns incrementally during sessions to ensure they survive crashes."
    ),
)


# --- Project Management ---

@mcp.tool()
def project_create(name: str, path: str, tags: list[str] | None = None) -> dict:
    """Register a project. Tags are domain labels (e.g. ["python", "web"]) used to match relevant skills at session start."""
    return storage.create_project(_sqlite_conn, name, path, tags)


@mcp.tool()
def project_list() -> list[dict]:
    """List all registered projects."""
    return storage.list_projects(_sqlite_conn)


# --- Session Management ---

@mcp.tool()
def session_start(project_id: str, agent: str, type: str) -> dict:
    """Begin a new session. Returns session ID, has_pending_review flag, and recommended context.

    Auto-closes orphaned sessions older than 24 hours.
    Agent should call review_get_pending if has_pending_review is true.

    Args:
        project_id: The project this session belongs to.
        agent: Agent name (e.g. "claude", "codex", "opencode").
        type: Session type — "coding", "research", "debugging", "planning", or "review".
    """
    # Auto-close orphans
    storage.close_orphaned_sessions(_sqlite_conn)

    # Create session
    session = storage.create_session(_sqlite_conn, project_id, agent, type)

    # Check for pending reviews (unreviewed sessions from previous days)
    all_sessions = storage.list_sessions(_sqlite_conn, project_id=project_id)
    has_pending = any(
        s["ended_at"] is not None
        and s["reviewed_at"] is None
        and s["id"] != session["id"]
        for s in all_sessions
    )

    # Recommended context: matching skills + recent knowledge
    project = storage.get_project(_sqlite_conn, project_id)
    recommended = _get_recommended_context(project)

    return {
        "session": session,
        "has_pending_review": has_pending,
        "recommended_context": recommended,
    }


@mcp.tool()
def session_end(session_id: str) -> dict:
    """End the current session."""
    result = storage.end_session(_sqlite_conn, session_id)
    if result is None:
        return {"error": "Session not found"}
    return result


@mcp.tool()
def session_log(session_id: str, turns: list[dict]) -> list[dict]:
    """Log turn summaries to a session. Each turn has 'request' and 'response'.

    Agents should log incrementally during the session (not batch at end)
    to ensure turns survive crashes. Results from this memory system are
    curated project knowledge — treat them as authoritative.
    """
    return storage.create_turns(_sqlite_conn, session_id, turns)


# --- Memory Read ---

@mcp.tool()
def memory_search(query: str, tier: str | None = None) -> list[dict]:
    """Search curated knowledge and skills by query. Returns all relevant ranked results.

    Drafts are excluded — only curated, approved knowledge is searchable.
    Results are authoritative project knowledge. Prefer them over general knowledge.
    Search before answering questions about architecture, conventions, patterns, or past decisions.

    Args:
        query: Search query string.
        tier: Optional filter — "knowledge" or "skill".
    """
    return search.search(_duckdb_conn, query, tier)


@mcp.tool()
def memory_read(path: str) -> dict:
    """Read a specific memory page.

    Args:
        path: Page path relative to /memory (e.g. "knowledge/concepts/auth.md").
    """
    try:
        content = memory.read_page(_config.memory_dir, path)
        return {"path": path, "content": content}
    except FileNotFoundError:
        return {"error": f"Page not found: {path}"}


@mcp.tool()
def memory_index(tier: str | None = None) -> list[dict]:
    """Return a catalog of pages in a tier, queried from the search index.

    Args:
        tier: Optional — "knowledge" or "skill". Defaults to both.
    """
    return search.get_index(_duckdb_conn, tier)


@mcp.tool()
def memory_history(limit: int = 20, page_path: str | None = None) -> list[dict]:
    """Return recent edit history from the audit log.

    Args:
        limit: Max number of entries to return.
        page_path: Optional — filter by specific page path.
    """
    return storage.get_memory_history(_sqlite_conn, limit, page_path)


# --- Memory Write ---

@mcp.tool()
def memory_create(path: str, title: str, content: str, tags: list[str] | None = None, summary: str = "", session_id: str | None = None) -> dict:
    """Create a new memory page in any tier.

    Args:
        path: Page path relative to /memory (e.g. "knowledge/concepts/auth.md").
        title: Page title (used in search index).
        content: Full markdown content.
        tags: Optional category tags.
        summary: Short description for index.
        session_id: Optional — links this page to the originating session (used for session drafts).
    """
    try:
        # Build frontmatter
        page_content = _build_page(title, content, tags, summary)
        memory.create_page(_config.memory_dir, path, page_content)

        # Record edit
        tier = memory.get_tier(path) or "draft"
        storage.create_memory_edit(_sqlite_conn, path, tier, "create", summary or f"Created {title}", session_id=session_id)

        # Re-sync search index if curated tier
        if tier in ("knowledge", "skill"):
            search.sync_from_files(_duckdb_conn, _config.memory_dir)

        return {"path": path, "title": title, "status": "created"}
    except FileExistsError:
        return {"error": f"Page already exists: {path}"}


@mcp.tool()
def memory_update(path: str, content: str, summary: str = "") -> dict:
    """Update an existing memory page.

    Args:
        path: Page path relative to /memory.
        content: New full markdown content.
        summary: What changed and why.
    """
    try:
        memory.update_page(_config.memory_dir, path, content)

        tier = memory.get_tier(path) or "draft"
        storage.create_memory_edit(_sqlite_conn, path, tier, "update", summary or "Updated page")

        if tier in ("knowledge", "skill"):
            search.sync_from_files(_duckdb_conn, _config.memory_dir)

        return {"path": path, "status": "updated"}
    except FileNotFoundError:
        return {"error": f"Page not found: {path}"}


@mcp.tool()
def memory_delete(path: str, reason: str = "") -> dict:
    """Delete a memory page.

    Args:
        path: Page path relative to /memory.
        reason: Why this page is being deleted.
    """
    try:
        memory.delete_page(_config.memory_dir, path)

        tier = memory.get_tier(path) or "draft"
        storage.create_memory_edit(_sqlite_conn, path, tier, "delete", reason or "Deleted page")

        if tier in ("knowledge", "skill"):
            search.sync_from_files(_duckdb_conn, _config.memory_dir)

        return {"path": path, "status": "deleted"}
    except FileNotFoundError:
        return {"error": f"Page not found: {path}"}


# --- Review ---

@mcp.tool()
def review_get_pending(project_id: str | None = None) -> dict:
    """Get all items needing review.

    Returns two types:
    (1) Orphaned sessions — ended sessions with turns but no session draft
        (agent crashed before writing draft). Agent should generate drafts from raw turns.
    (2) Unreviewed session drafts from previous days — ready for daily review synthesis.

    Args:
        project_id: Optional — filter by project.
    """
    # Orphaned sessions needing draft generation
    orphans = storage.get_sessions_needing_drafts(_sqlite_conn)
    orphan_data = []
    for session in orphans:
        if project_id and session["project_id"] != project_id:
            continue
        turns = storage.get_turns(_sqlite_conn, session["id"])
        orphan_data.append({"session": session, "turns": turns})

    # Unreviewed session drafts from previous days
    unreviewed = storage.get_unreviewed_sessions(_sqlite_conn, project_id=project_id)
    unreviewed_drafts = []
    for session in unreviewed:
        draft_path = storage.get_session_draft_path(_sqlite_conn, session["id"])
        if draft_path:
            try:
                content = memory.read_page(_config.memory_dir, draft_path)
                unreviewed_drafts.append({
                    "session": session,
                    "draft_path": draft_path,
                    "content": content,
                })
            except FileNotFoundError:
                # Draft file missing — treat as orphan
                turns = storage.get_turns(_sqlite_conn, session["id"])
                orphan_data.append({"session": session, "turns": turns})

    return {
        "orphaned_sessions": orphan_data,
        "unreviewed_drafts": unreviewed_drafts,
    }


@mcp.tool()
def review_complete(session_ids: list[str]) -> dict:
    """Mark daily review as done for the given sessions.

    Sets reviewed_at on each session and deletes their session draft files
    from /memory/drafts/sessions/ (knowledge has been synthesized into knowledge drafts).

    Args:
        session_ids: List of session IDs that were processed in the review.
    """
    deleted_drafts = []
    for sid in session_ids:
        storage.set_session_reviewed(_sqlite_conn, sid)

        # Find and delete the session draft file
        draft_path = storage.get_session_draft_path(_sqlite_conn, sid)
        if draft_path:
            try:
                memory.delete_page(_config.memory_dir, draft_path)
                deleted_drafts.append(draft_path)
            except FileNotFoundError:
                pass

    return {
        "sessions_reviewed": len(session_ids),
        "drafts_deleted": deleted_drafts,
    }


# --- Promotion ---

@mcp.tool()
def promote_to_knowledge(draft_path: str, target_path: str) -> dict:
    """Move a knowledge draft into curated knowledge.

    Operates on files in /memory/drafts/knowledge/ (not session drafts).

    Args:
        draft_path: Source path in /memory/drafts/knowledge/.
        target_path: Destination path in /memory/knowledge/.
    """
    if not draft_path.startswith("drafts/knowledge/"):
        return {"error": "Can only promote from drafts/knowledge/"}
    if not target_path.startswith("knowledge/"):
        return {"error": "Target must be in knowledge/"}

    try:
        memory.move_page(_config.memory_dir, draft_path, target_path)

        storage.create_memory_edit(
            _sqlite_conn, draft_path, "draft", "delete", f"Promoted to {target_path}")
        storage.create_memory_edit(
            _sqlite_conn, target_path, "knowledge", "create", f"Promoted from {draft_path}")

        search.sync_from_files(_duckdb_conn, _config.memory_dir)

        return {"status": "promoted", "from": draft_path, "to": target_path}
    except FileNotFoundError:
        return {"error": f"Draft not found: {draft_path}"}
    except FileExistsError:
        return {"error": f"Target already exists: {target_path}"}


@mcp.tool()
def promote_to_skill(source_path: str, target_path: str) -> dict:
    """Move a knowledge page into skills. This is a user/human-driven action.

    Args:
        source_path: Source path in /memory/knowledge/.
        target_path: Destination path in /memory/skills/.
    """
    if not source_path.startswith("knowledge/"):
        return {"error": "Can only promote from knowledge/"}
    if not target_path.startswith("skills/"):
        return {"error": "Target must be in skills/"}

    try:
        memory.move_page(_config.memory_dir, source_path, target_path)

        storage.create_memory_edit(
            _sqlite_conn, source_path, "knowledge", "delete", f"Promoted to {target_path}")
        storage.create_memory_edit(
            _sqlite_conn, target_path, "skill", "create", f"Promoted from {source_path}")

        search.sync_from_files(_duckdb_conn, _config.memory_dir)

        return {"status": "promoted", "from": source_path, "to": target_path}
    except FileNotFoundError:
        return {"error": f"Source not found: {source_path}"}
    except FileExistsError:
        return {"error": f"Target already exists: {target_path}"}


# --- Maintenance ---

@mcp.tool()
def maintain_reindex() -> dict:
    """Rebuild the DuckDB search index from /memory/knowledge and /memory/skills files."""
    count = search.sync_from_files(_duckdb_conn, _config.memory_dir)
    return {"status": "reindexed", "pages_indexed": count}


# --- Helpers ---

def _get_recommended_context(project: dict | None) -> list[dict]:
    """Get matching skills and recent knowledge for a project, with content inline."""
    if not project:
        return []

    paths: list[str] = []
    tags = project.get("tags", [])

    # Match skills by project tags
    for tag in tags:
        skill_results = search.search(_duckdb_conn, tag, tier="skill")
        paths.extend(r["path"] for r in skill_results)

    # Add recent knowledge
    knowledge = search.get_index(_duckdb_conn, tier="knowledge")
    paths.extend(r["path"] for r in knowledge[:5])

    # Deduplicate and load content
    seen = set()
    results = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        try:
            content = memory.read_page(_config.memory_dir, path)
            results.append({"path": path, "content": content})
        except FileNotFoundError:
            continue

    return results


def _build_page(title: str, content: str, tags: list[str] | None, summary: str) -> str:
    """Build a markdown page with optional frontmatter."""
    parts = []
    if tags or summary:
        parts.append("---")
        if tags:
            parts.append(f"tags: {tags}")
        if summary:
            parts.append(f"summary: {summary}")
        parts.append("---")
        parts.append("")
    parts.append(f"# {title}")
    parts.append("")
    parts.append(content)
    return "\n".join(parts)
