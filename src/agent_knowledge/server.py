"""MCP server — exposes agent-knowledge tools via Model Context Protocol."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from agent_knowledge.core.config import Config, load_config
from agent_knowledge.core import storage, memory, search, sanitizer

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


# --- MCP Prompts ---

@mcp.prompt()
def session_bootstrap() -> str:
    """Start a new session with the Agent Knowledge system.

    Follow these steps:
    1. Call project_create if the project isn't registered yet, or project_list to find the project ID.
    2. Call session_start with the project ID, your agent name, and session type.
    3. If has_pending_review is true, call review_get_pending and process:
       - For orphaned sessions: generate session drafts from their raw turns using memory_create (path: drafts/sessions/..., include session_id param).
       - For unreviewed drafts: synthesize knowledge drafts using memory_create (path: drafts/knowledge/...).
       - Call review_complete with the processed session IDs.
    4. Read the recommended_context returned by session_start — this is curated knowledge relevant to your project.
    5. Begin your work. Log turns incrementally using session_log.
    """
    return (
        "Start a new Agent Knowledge session:\n"
        "1. Register or find your project (project_create / project_list)\n"
        "2. Call session_start — check has_pending_review flag\n"
        "3. If pending: call review_get_pending, process orphans + unreviewed drafts, call review_complete\n"
        "4. Read recommended_context from session_start response\n"
        "5. Begin work, log turns incrementally with session_log\n"
    )


@mcp.prompt()
def session_wrapup() -> str:
    """End the current session with proper review.

    Follow these steps:
    1. Summarize your session's turns — what was asked, decided, and learned.
    2. Write a session draft using memory_create:
       - path: drafts/sessions/YYYY-MM-DD-<topic>.md
       - Include the session_id param to link the draft to the session.
       - Content should capture key decisions, patterns discovered, and outcomes.
    3. Call session_end with your session ID.

    Do NOT include secrets, API keys, tokens, or credentials in the session draft.
    """
    return (
        "End your Agent Knowledge session:\n"
        "1. Summarize turns — what was asked, decided, learned\n"
        "2. Write session draft: memory_create(path='drafts/sessions/YYYY-MM-DD-topic.md', session_id=...)\n"
        "3. Call session_end\n"
        "Do NOT include secrets or credentials in the draft.\n"
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

    Content is scanned for secrets — any detected patterns are automatically redacted.
    """
    sanitized_turns = []
    for turn in turns:
        req, _ = sanitizer.redact(turn.get("request", ""))
        resp, _ = sanitizer.redact(turn.get("response", ""))
        sanitized_turns.append({**turn, "request": req, "response": resp})
    return storage.create_turns(_sqlite_conn, session_id, sanitized_turns)


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

    Content is scanned for secrets — any detected patterns are automatically redacted.
    """
    try:
        content, _ = sanitizer.redact(content)
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

    Content is scanned for secrets — any detected patterns are automatically redacted.
    """
    try:
        content, _ = sanitizer.redact(content)
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


@mcp.tool()
def maintain_get_stats(stale_days: int = 90) -> dict:
    """Return structural stats for the memory system.

    Reports orphaned pages, stale pages, page counts per tier, session stats.
    The calling agent interprets and acts on the report.

    Args:
        stale_days: Pages with no updates in this many days are considered stale.
    """
    from datetime import datetime, timezone, timedelta

    stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Page counts per tier
    knowledge_pages = memory.list_pages(_config.memory_dir, "knowledge")
    skill_pages = memory.list_pages(_config.memory_dir, "skills")
    draft_pages = memory.list_pages(_config.memory_dir, "drafts")

    # Stale pages (check file mtime)
    stale = []
    for pages, tier in [(knowledge_pages, "knowledge"), (skill_pages, "skills")]:
        for page_path in pages:
            full_path = _config.memory_dir / page_path
            if full_path.exists():
                mtime = datetime.fromtimestamp(full_path.stat().st_mtime, tz=timezone.utc)
                if mtime.strftime("%Y-%m-%dT%H:%M:%SZ") < stale_cutoff:
                    stale.append({"path": page_path, "tier": tier, "last_modified": mtime.isoformat()})

    # Session stats
    all_sessions = storage.list_sessions(_sqlite_conn)
    reviewed = [s for s in all_sessions if s["reviewed_at"] is not None]
    pending = [s for s in all_sessions if s["ended_at"] is not None and s["reviewed_at"] is None]
    orphans = [s for s in all_sessions if s["ended_at"] is None]

    return {
        "pages": {
            "knowledge": len(knowledge_pages),
            "skills": len(skill_pages),
            "drafts": len(draft_pages),
        },
        "stale_pages": stale,
        "sessions": {
            "total": len(all_sessions),
            "reviewed": len(reviewed),
            "pending_review": len(pending),
            "orphaned": len(orphans),
        },
    }


@mcp.tool()
def maintain_purge(older_than_days: int = 365) -> dict:
    """Delete reviewed sessions and turns older than retention period.

    Only purges sessions where reviewed_at is set. Also removes any remaining
    associated session draft files.

    Args:
        older_than_days: Sessions older than this many days will be purged.
    """
    from datetime import datetime, timezone, timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Find reviewed sessions older than cutoff
    all_sessions = storage.list_sessions(_sqlite_conn)
    to_purge = [
        s for s in all_sessions
        if s["reviewed_at"] is not None and s["started_at"] < cutoff
    ]

    purged_sessions = 0
    purged_turns = 0
    purged_drafts = []

    for session in to_purge:
        # Delete associated draft files
        draft_path = storage.get_session_draft_path(_sqlite_conn, session["id"])
        if draft_path:
            try:
                memory.delete_page(_config.memory_dir, draft_path)
                purged_drafts.append(draft_path)
            except FileNotFoundError:
                pass

        # Delete turns
        turns = storage.get_turns(_sqlite_conn, session["id"])
        purged_turns += len(turns)
        _sqlite_conn.execute("DELETE FROM turns WHERE session_id = ?", (session["id"],))

        # Delete memory_edits for this session
        _sqlite_conn.execute("DELETE FROM memory_edits WHERE session_id = ?", (session["id"],))

        # Delete session
        _sqlite_conn.execute("DELETE FROM sessions WHERE id = ?", (session["id"],))
        purged_sessions += 1

    _sqlite_conn.commit()

    return {
        "purged_sessions": purged_sessions,
        "purged_turns": purged_turns,
        "purged_drafts": purged_drafts,
        "cutoff_date": cutoff,
    }


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


def main():
    """Run the MCP server (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
