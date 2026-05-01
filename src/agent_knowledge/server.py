"""MCP server — exposes agent-knowledge tools via Model Context Protocol.

EP-00005 capture-only scope: this server captures turns, manages group lifecycle,
and surfaces pending counts. It does not synthesize, propose, or promote — those
flows are human work performed against the memory folder with whatever tools the
curator chooses (typically Claude Code, Obsidian, manual edit).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mcp.server.fastmcp import FastMCP

from agent_knowledge.core.config import Config, load_config
from agent_knowledge.core import storage, memory, search, sanitizer, paths

# --- Bootstrap ---

_config: Config = load_config()
_sqlite_conn = storage.connect(_config.sessions_db)
_duckdb_conn = search.connect(_config.search_db)

# Sync search index from files on startup
if _config.memory_dir.exists():
    search.sync_from_files(_duckdb_conn, _config.memory_dir)

# Active-group tracking (per server process): only the id is cached; storage is the truth.
_active_group_id: str | None = None
_client_agent: str = "unknown"


mcp = FastMCP(
    "agent-knowledge",
    instructions=(
        "Agent Knowledge captures conversation activity into session summaries. "
        "Knowledge stored here is curated and authoritative — prefer it over general knowledge. "
        "Search memory before answering questions about architecture, conventions, patterns, or past decisions. "
        "Log turns incrementally during a group to ensure they survive crashes.\n\n"
        "GROUP LIFECYCLE: A group is one logical unit of work (one Claude session, one barebone "
        "conversation, or one barebone task). A group may have multiple segments over time "
        "(continuation reuses the same group_id and starts a new segment). Each segment is a "
        "start→end pair on the `turns` table.\n\n"
        "WRAP-UP: When the user signals the end of a session (says bye, exit, done, or you've "
        "completed the main task), you MUST:\n"
        "1. Call group_status to confirm the active group_id and segment.\n"
        "2. Summarize what was accomplished, decisions made, and patterns learned.\n"
        "3. Write a session draft via memory_create(path='1_drafts/sessions/<group_first_8>-<segment_compact_iso>.md', "
        "title='Session: <topic>', content=summary, group_id=<active_group_id>).\n"
        "4. Call group_end (no args needed — closes the active segment).\n"
        "Do NOT include secrets, API keys, tokens, or credentials in the draft.\n\n"
        "PENDING COUNTS: group_start returns a `pending` field. If non-zero, surface to the user — "
        "for example: \"You have N session summaries and M incomplete segments waiting. Open the "
        "memory folder if you want to review summaries; run `akw recover` for incomplete ones.\" "
        "Do NOT auto-process pending items. The user opts in.\n\n"
        "MEMORY LAYOUT: The deployed memory folder is a numbered three-tier wiki. Numeric "
        "prefixes encode promotion order (1 → 2 → 3); `0_configs/` is the wiki contract, "
        "not a tier:\n"
        "- `0_configs/`        templates + rules (curator-only)\n"
        "- `1_drafts/`         Tier 1 — agent-writable drafts (see DRAFT STAGING below)\n"
        "- `2_knowledges/`     Tier 2 — curated, durable knowledge (curator-only)\n"
        "- `3_intelligences/`  Tier 3 — skills + agent personas (curator-only)\n\n"
        "DRAFT STAGING: Inside `1_drafts/`, the nested numeric prefix on each subfolder "
        "signals the *promotion target* — where a draft lands once curated:\n"
        "- `1_drafts/sessions/`       session summaries (one per segment) — written via the WRAP-UP flow\n"
        "- `1_drafts/2_knowledges/`   drafts targeting Tier 2 knowledge pages\n"
        "- `1_drafts/2_notes/`        ad-hoc notes (will promote to `2_knowledges/notes/`)\n"
        "- `1_drafts/2_researches/`   research outputs (will promote to `2_knowledges/researches/`)\n"
        "- `1_drafts/3_skills/`       drafts targeting Tier 3 skills\n"
        "Pick the staging folder that matches the *promotion target* of the draft you're writing.\n\n"
        "TIER WRITE BOUNDARY: memory_create and memory_update REJECT writes to `2_knowledges/`, "
        "`3_intelligences/`, `0_configs/`, and `1_drafts/_archived/`. Curated tiers are written "
        "by humans only, via their editor on the file system. Synthesis from drafts "
        "to knowledge is a human activity outside this MCP — see "
        "`0_configs/rules/knowledge-management.md` in the deployed memory folder for conventions.\n\n"
        "WRITE CARVE-OUTS: A small set of paths inside curated tiers are agent-writable:\n"
        "- `2_knowledges/preferences/` — user preferences (e.g. tooling, conventions, "
        "stated likes/dislikes). Use `memory_create` / `memory_update` directly.\n"
        "Deletes of carve-out pages do NOT unlink — they archive to `<tier>/_archived/...`. "
        "Call `memory_delete` and the server moves the file for you.\n\n"
        "DRAFT POLICY: Agents create and append to `1_drafts/`. Agents must NEVER delete "
        "drafts and must NEVER write to `1_drafts/_archived/` — archiving is a curator action via "
        "`akw archive` or manual move."
    ),
)


# --- Helpers ---

def _ensure_group(metadata: dict | None = None) -> dict:
    """Get or create an active group. Adopts the most recent open group if cached id is invalid.

    Returns the group_status-shaped dict for the active group's current segment.
    """
    global _active_group_id

    cached_id = _active_group_id
    if cached_id is not None:
        latest = storage._latest_turn(_sqlite_conn, cached_id)
        if latest is not None and latest["kind"] not in ("end", "idle_close"):
            start = storage._latest_start_turn(_sqlite_conn, cached_id)
            return {
                "group_id": cached_id,
                "segment_start_at": start["created_at"] if start else None,
            }
        _active_group_id = None

    open_groups = storage.get_open_groups(_sqlite_conn)
    if open_groups:
        # Most recent open group wins.
        chosen = max(open_groups, key=lambda g: g["latest_at"])
        chosen_id: str = chosen["group_id"]
        _active_group_id = chosen_id
        start = storage._latest_start_turn(_sqlite_conn, chosen_id)
        return {
            "group_id": chosen_id,
            "segment_start_at": start["created_at"] if start else None,
        }

    # No open group — start a fresh one.
    md = dict(metadata or {})
    md.setdefault("agent", _client_agent)
    result = storage.start_group(_sqlite_conn, agent=_client_agent, metadata=md)
    _active_group_id = result["group_id"]
    return {
        "group_id": result["group_id"],
        "segment_start_at": result["segment_start_at"],
    }


def _pending_counts() -> dict:
    """Compute the `pending` payload returned by group_start.

    `unarchived_session_drafts` comes from indexed draft_state SQL (Phase 2).
    `incomplete_segments` is orphans + closed-no-draft (still scan-based; cheap at
    this scale, may move to draft_state in a follow-up).
    """
    unarchived = storage.count_unarchived_session_drafts(_sqlite_conn)
    orphan_count = len(storage.get_orphaned_groups(_sqlite_conn))
    closed_no_draft_count = len(storage.get_closed_no_draft_segments(_sqlite_conn))
    return {
        "unarchived_session_drafts": unarchived,
        "incomplete_segments": orphan_count + closed_no_draft_count,
    }


def _get_recommended_context(project: dict | None) -> list[dict]:
    """Get matching skills and recent knowledge for a project, with content inline."""
    if not project:
        return []

    paths: list[str] = []
    tags = project.get("tags", [])

    for tag in tags:
        skill_results = search.search(_duckdb_conn, tag, tier="skill")
        paths.extend(r["path"] for r in skill_results)

    knowledge = search.get_index(_duckdb_conn, tier="knowledge")
    paths.extend(r["path"] for r in knowledge[:5])

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


def _reject_curated_path(path: str) -> dict | None:
    """Tier write boundary (EP-00008). Wraps `paths.reject_curated_write` as a tool result."""
    reason = paths.reject_curated_write(path)
    return {"error": reason} if reason else None


# --- MCP Prompts ---

@mcp.prompt()
def group_bootstrap() -> str:
    """Start a new group with the Agent Knowledge system.

    A group may already be active (created by a SessionStart hook). Calling group_start
    upgrades it with caller-provided metadata; logging turns auto-creates one if needed.
    """
    return (
        "Start an Agent Knowledge group:\n"
        "1. Call group_start (all params optional — auto-group may already exist).\n"
        "2. If pending counts are non-zero, surface them to the user. Do NOT auto-process.\n"
        "3. Read recommended_context from the response.\n"
        "4. Begin work, log turns incrementally with group_log.\n"
    )


@mcp.prompt()
def group_wrapup() -> str:
    """End the current group's segment with a session draft.

    1. Summarize the current segment's turns — what was asked, decided, learned.
    2. Write a session draft via memory_create:
       - path: 1_drafts/sessions/<group_first_8>-<segment_compact_iso>.md
       - Pass group_id to bind the draft to the segment.
    3. Call group_end (no args needed — closes the active segment).

    Do NOT include secrets in the draft.
    """
    return (
        "End your Agent Knowledge group's segment:\n"
        "1. Summarize the segment's turns (what was asked, decided, learned).\n"
        "2. Write a session draft: memory_create(path='1_drafts/sessions/<group_first_8>-<segment_compact_iso>.md', group_id=...).\n"
        "3. Call group_end.\n"
        "Do NOT include secrets or credentials in the draft.\n"
    )


# --- Project Management ---

@mcp.tool()
def project_create(name: str, path: str, tags: list[str] | None = None) -> dict:
    """Register a project. Tags are domain labels (e.g. ["python", "web"])."""
    return storage.create_project(_sqlite_conn, name, path, tags)


@mcp.tool()
def project_list() -> list[dict]:
    """List all registered projects."""
    return storage.list_projects(_sqlite_conn)


# --- Group Management ---

@mcp.tool()
def group_start(
    group_id: str | None = None,
    agent: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Begin or continue a group. Returns group_id, pending counts, recommended context.

    All parameters are optional. If a `group_id` is provided and the group's latest
    turn is open and stale (>30 min idle), an `idle_close` is written for the stale
    segment before the new `start` (continuation-by-implicit-resumption).

    Args:
        group_id: Optional — pass to continue a known group; omit to start a new one.
        agent: Optional agent label (e.g. "claude", "codex"); auto-detected if omitted.
        metadata: Optional dict (e.g. {project_id, working_dir, conversation_id}).
    """
    global _active_group_id

    agent_name = agent or _client_agent
    md = dict(metadata or {})
    md.setdefault("agent", agent_name)

    result = storage.start_group(
        _sqlite_conn,
        group_id=group_id,
        agent=agent_name,
        metadata=md,
    )
    _active_group_id = result["group_id"]

    # Pull project metadata for recommended_context (if metadata.project_id resolves).
    project = None
    project_id = md.get("project_id")
    if project_id:
        project = storage.get_project(_sqlite_conn, project_id)
        if project is None:
            for p in storage.list_projects(_sqlite_conn):
                if p["name"] == project_id:
                    project = p
                    break

    payload: dict = {
        "group_id": result["group_id"],
        "segment_start_at": result["segment_start_at"],
        "pending": _pending_counts(),
        "recommended_context": _get_recommended_context(project),
    }
    if result.get("idle_closed_segment"):
        payload["idle_closed_segment"] = result["idle_closed_segment"]
    return payload


@mcp.tool()
def group_status() -> dict:
    """Get the current active group + segment metadata."""
    active = _ensure_group()
    gid = active["group_id"]
    seg_start = active.get("segment_start_at")

    segment_turn_count = 0
    if seg_start:
        segment_turn_count = sum(
            1 for t in storage.get_current_segment_turns(_sqlite_conn, gid)
            if t["kind"] == "turn"
        )

    return {
        "group_id": gid,
        "segment_start_at": seg_start,
        "segment_turn_count": segment_turn_count,
    }


@mcp.tool()
def group_end(group_id: str | None = None) -> dict:
    """End the current segment. Idempotent. Returns a summarization hint.

    The hint instructs the agent to write a session draft at the computed path. The
    draft path embeds the segment_start_at so multiple segments of the same group
    each get their own draft (continuation produces N drafts, never overwrites).

    Args:
        group_id: Optional — if omitted, ends the active group's current segment.
    """
    global _active_group_id

    gid = group_id or _active_group_id
    if gid is None:
        return {"error": "No active group to end"}

    result = storage.end_group(_sqlite_conn, gid, kind="end")
    if result is None:
        return {"error": f"Group has no turns: {gid}"}

    if _active_group_id == gid:
        _active_group_id = None

    seg_start = result["segment_start_at"]
    draft_path = paths.session_draft_path(gid, seg_start) if seg_start else None
    hint = (
        f"Segment {seg_start} → {result['segment_end_at']} closed. "
        f"Summarize this segment's turns and write a session draft via "
        f"memory_create(path='{draft_path}', group_id='{gid}', title=..., content=summary)."
    ) if draft_path else "Segment closed."

    return {
        "group_id": gid,
        "segment_start_at": seg_start,
        "segment_end_at": result["segment_end_at"],
        "draft_path": draft_path,
        "summarization_hint": hint,
    }


@mcp.tool()
def group_log(
    group_id: str | None = None,
    turns: list[dict] | None = None,
) -> dict:
    """Append `kind='turn'` rows to a group. Each turn has 'request' and 'response'.

    If `group_id` is omitted, uses the active group (auto-creating if needed). Auto-handles
    idle-close-on-stale: if the group's latest turn is open and >30 min old, writes an
    `idle_close` for the stale segment and a fresh `start` before the requested turns.

    Content is scanned for secrets — any detected patterns are automatically redacted.
    """
    if not turns:
        return {"error": "No turns provided"}

    gid = group_id
    if gid is None:
        active = _ensure_group()
        gid = active["group_id"]

    sanitized_turns = []
    for turn in turns:
        req, _ = sanitizer.redact(turn.get("request", ""))
        resp, _ = sanitizer.redact(turn.get("response", ""))
        sanitized_turns.append({**turn, "request": req, "response": resp})

    result = storage.create_turns(_sqlite_conn, gid, sanitized_turns)

    payload: dict = {
        "group_id": gid,
        "segment_start_at": result["segment_start_at"],
        "turns": result["turns"],
    }
    if result.get("idle_closed_segment"):
        payload["idle_closed_segment"] = result["idle_closed_segment"]
        # Hint the agent that a stale segment just closed.
        idle = result["idle_closed_segment"]
        payload["summarization_hint"] = (
            f"Idle-close fired: prior segment {idle.get('segment_start_at')} "
            f"→ {idle.get('created_at')} closed. To summarize it, fetch its turns via "
            f"`get_segment_turns` (CLI: `akw group turns {gid} --segment-start {idle.get('segment_start_at')}`) "
            f"and write a session draft via memory_create."
        )
    return payload


# --- Memory Read ---

@mcp.tool()
def memory_search(query: str, tier: str | None = None) -> list[dict]:
    """Search drafts and curated knowledge by query (BM25).

    Tiers: `knowledge`, `session_draft`, `session_archived`, `knowledge_draft`,
    `note_draft`, `research_draft`, `skill_draft`. Pass `tier` to scope; omit
    for an all-tier search.

    Skills (`3_intelligences/skills/`) and agent personas (`3_intelligences/agents/`)
    are intentionally NOT in this index — they have dedicated discovery tools.
    """
    return search.search(_duckdb_conn, query, tier)


@mcp.tool()
def memory_read(path: str) -> dict:
    """Read a specific memory page."""
    try:
        content = memory.read_page(_config.memory_dir, path)
        return {"path": path, "content": content}
    except FileNotFoundError:
        return {"error": f"Page not found: {path}"}


@mcp.tool()
def memory_index(tier: str | None = None) -> list[dict]:
    """Return a catalog of pages in a tier, queried from the search index."""
    return search.get_index(_duckdb_conn, tier)


@mcp.tool()
def memory_history(limit: int = 20, page_path: str | None = None) -> list[dict]:
    """Return recent edit history from the audit log."""
    return storage.get_memory_history(_sqlite_conn, limit, page_path)


# --- Memory Write ---

@mcp.tool()
def memory_create(
    path: str,
    title: str,
    content: str,
    tags: list[str] | None = None,
    summary: str = "",
    group_id: str | None = None,
) -> dict:
    """Create a new memory page in `1_drafts/sessions/`. Curated tiers are rejected.

    For session drafts, also writes a `draft_state` row so `pending` counts stay
    O(1). The segment match is by computed canonical draft path:
    `1_drafts/sessions/<group_first_8>-<segment_compact_iso>.md`.

    Args:
        path: Page path. Must NOT begin with `2_knowledges/`, `3_intelligences/`, `0_configs/`, or `1_drafts/_archived/`.
        title: Page title.
        content: Full markdown content (sanitized for secrets).
        tags: Optional category tags.
        summary: Short description for the index.
        group_id: Optional — links the page to the originating group (used for session drafts).
    """
    rejection = _reject_curated_path(path)
    if rejection:
        return rejection

    try:
        content, _ = sanitizer.redact(content)
        page_content = _build_page(title, content, tags, summary)
        memory.create_page(_config.memory_dir, path, page_content)

        tier = memory.get_tier(path) or "draft"
        storage.create_memory_edit(
            _sqlite_conn, path, tier, "create",
            summary or f"Created {title}",
            group_id=group_id,
        )

        # Phase 2: write a draft_state row for session drafts so pending counts
        # use indexed SQL.
        if path.startswith(paths.SESSIONS_DIR + "/") and group_id:
            seg = _match_segment_for_draft_path(group_id, path)
            if seg is not None:
                storage.upsert_draft_state(
                    _sqlite_conn,
                    draft_path=path,
                    group_id=group_id,
                    segment_start_at=seg["segment_start_at"],
                    segment_end_at=seg["segment_end_at"] or seg["segment_start_at"],
                )

        # Curated-tier writes never reach this point thanks to _reject_curated_path,
        # so no search-index resync is needed (drafts aren't indexed).
        return {"path": path, "title": title, "status": "created"}
    except FileExistsError:
        return {"error": f"Page already exists: {path}"}


def _match_segment_for_draft_path(group_id: str, draft_path: str) -> dict | None:
    """Find the segment whose canonical draft path matches `draft_path`.

    Returns the segment dict (or None if no match). Linear scan over segments —
    fine while groups have a handful of segments.
    """
    segments = storage.get_group_segments(_sqlite_conn, group_id)
    for seg in segments:
        seg_start = seg.get("segment_start_at")
        if not seg_start:
            continue
        if paths.session_draft_path(group_id, seg_start) == draft_path:
            return seg
    return None


@mcp.tool()
def memory_update(path: str, content: str, summary: str = "") -> dict:
    """Update an existing memory page. Curated tiers are rejected."""
    rejection = _reject_curated_path(path)
    if rejection:
        return rejection

    try:
        content, _ = sanitizer.redact(content)
        memory.update_page(_config.memory_dir, path, content)

        tier = memory.get_tier(path) or "draft"
        storage.create_memory_edit(
            _sqlite_conn, path, tier, "update",
            summary or "Updated page",
        )
        return {"path": path, "status": "updated"}
    except FileNotFoundError:
        return {"error": f"Page not found: {path}"}


@mcp.tool()
def memory_delete(path: str, reason: str = "") -> dict:
    """Delete a memory page.

    Drafts (paths under `1_drafts/`) cannot be deleted via this tool — use the
    curator file-system flow instead. Pages in agent-writable curated carve-outs
    (e.g. `2_knowledges/preferences/`) are *archived*, not unlinked: the file
    moves to `<tier>/_archived/<original-rel-path>` so the audit trail survives.
    """
    if path.startswith(paths.DRAFTS_PREFIX):
        return {"error": "Drafts cannot be deleted by agents. Curator removes drafts via the file system."}

    try:
        if paths.is_archive_redirected_path(path):
            target = paths.archived_knowledge_path(path)
            memory.move_page(_config.memory_dir, path, target)
            tier = memory.get_tier(path) or "knowledge"
            storage.create_memory_edit(
                _sqlite_conn, target, tier, "archive",
                reason or f"Archived from {path}",
            )
            search.sync_from_files(_duckdb_conn, _config.memory_dir)
            return {"path": target, "status": "archived", "from": path}

        memory.delete_page(_config.memory_dir, path)
        tier = memory.get_tier(path) or "draft"
        storage.create_memory_edit(_sqlite_conn, path, tier, "delete", reason or "Deleted page")

        if tier in ("knowledge", "skill", "agent"):
            search.sync_from_files(_duckdb_conn, _config.memory_dir)

        return {"path": path, "status": "deleted"}
    except FileNotFoundError:
        return {"error": f"Page not found: {path}"}
    except FileExistsError:
        return {"error": f"Archive target already exists for {path}"}


# --- Maintenance ---

@mcp.tool()
def maintain_reindex() -> dict:
    """Rebuild the DuckDB search index from /memory/knowledge and /memory/skills files."""
    count = search.sync_from_files(_duckdb_conn, _config.memory_dir)
    return {"status": "reindexed", "pages_indexed": count}


@mcp.tool()
def maintain_get_stats(stale_days: int = 90) -> dict:
    """Return structural stats for the memory system.

    Reports orphaned pages, stale pages, page counts per tier, group stats.
    """
    stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    knowledge_pages = memory.list_pages(_config.memory_dir, "2_knowledges")
    skill_pages = memory.list_pages(_config.memory_dir, "3_intelligences/skills")
    agent_pages = memory.list_pages(_config.memory_dir, "3_intelligences/agents")
    draft_pages = memory.list_pages(_config.memory_dir, "1_drafts")

    stale = []
    for pages, tier in [
        (knowledge_pages, "knowledge"),
        (skill_pages, "skill"),
        (agent_pages, "agent"),
    ]:
        for page_path in pages:
            full_path = _config.memory_dir / page_path
            if full_path.exists():
                mtime = datetime.fromtimestamp(full_path.stat().st_mtime, tz=timezone.utc)
                if mtime.strftime("%Y-%m-%dT%H:%M:%SZ") < stale_cutoff:
                    stale.append({"path": page_path, "tier": tier, "last_modified": mtime.isoformat()})

    all_groups = storage.list_groups(_sqlite_conn)
    open_groups = storage.get_open_groups(_sqlite_conn)
    orphans = storage.get_orphaned_groups(_sqlite_conn)
    closed_no_draft = storage.get_closed_no_draft_segments(_sqlite_conn)

    return {
        "pages": {
            "knowledge": len(knowledge_pages),
            "skills": len(skill_pages),
            "agents": len(agent_pages),
            "drafts": len(draft_pages),
        },
        "stale_pages": stale,
        "groups": {
            "total": len(all_groups),
            "open": len(open_groups),
            "orphaned": len(orphans),
            "closed_no_draft_segments": len(closed_no_draft),
        },
    }


@mcp.tool()
def maintain_purge(older_than_days: int = 365) -> dict:
    """Delete archived session drafts older than the retention boundary.

    Will hook into the `1_drafts/_archived/sessions__*.md` flat-file glob. Currently
    a no-op placeholder that returns 0 — purge automation doesn't exist yet.
    """
    return {
        "purged_drafts": [],
        "note": "maintain_purge is a no-op until Phase 4 archive flow lands",
        "older_than_days": older_than_days,
    }


def main():
    """Run the MCP server (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
