"""CLI entry point — admin and inspection commands for agent-knowledge.

EP-00005: groups replace sessions; capture-only scope. The legacy `akw review`
LLM-synthesis flow has been removed — synthesis is human work performed in the
memory folder with whatever tools the curator chooses.
"""

from __future__ import annotations

import json as json_mod
import os
import sys
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import click

from agent_knowledge import __version__
from agent_knowledge.core.config import load_config
from agent_knowledge.core import storage, memory, search, paths, sanitizer


# --- Top-level group ---

@click.group()
@click.version_option(__version__, "-V", "--version", prog_name="akw")
def main():
    """Agent Knowledge — persistent memory for AI agents."""
    pass


@main.command("guide")
def guide_cmd():
    """Print the akw command catalog and trigger phrases.

    Single source of truth for "what can akw do?" — the same content the
    SessionStart hook injects into Claude Code as a system reminder.
    Reference this from a global CLAUDE.md so agents in fresh repos learn
    the CLI surface on first contact.
    """
    text = resources.files("agent_knowledge").joinpath("akw_instructions.md").read_text(encoding="utf-8")
    click.echo(text)


# --- JSON / shared helpers ---

_INTELLIGENCES_TIER_LABELS = ("skill", "agent")


def _emit_json(payload) -> None:
    """Emit a structured payload as pretty-printed JSON to stdout."""
    click.echo(json_mod.dumps(payload, indent=2, default=str))


def _build_page(title: str, content: str, tags: list[str] | None, summary: str) -> str:
    """Build a markdown page with optional frontmatter (mirror of server-side helper)."""
    parts: list[str] = []
    if tags or summary:
        parts.append("---")
        if tags:
            parts.append(f"tags: [{', '.join(tags)}]")
        if summary:
            parts.append(f"summary: {summary}")
        parts.append("---")
        parts.append("")
    parts.append(f"# {title}")
    parts.append("")
    parts.append(content)
    return "\n".join(parts)


def _first_heading(content: str) -> str | None:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def _pending_counts(conn) -> dict:
    """Compute the `pending` payload for `group start --json` (parity with MCP)."""
    return {
        "unarchived_session_drafts": storage.count_unarchived_session_drafts(conn),
        "incomplete_segments": (
            len(storage.get_orphaned_groups(conn))
            + len(storage.get_closed_no_draft_segments(conn))
        ),
    }


def _get_recommended_context(duckdb_conn, memory_dir: Path, project: dict | None) -> list[dict]:
    """Resolve matching skills + recent knowledge for a project (parity with MCP)."""
    if not project:
        return []

    candidate_paths: list[str] = []
    for tag in project.get("tags", []) or []:
        skill_results = search.search(duckdb_conn, tag, tier="skill")
        candidate_paths.extend(r["path"] for r in skill_results)

    knowledge = search.get_index(duckdb_conn, tier="knowledge")
    candidate_paths.extend(r["path"] for r in knowledge[:5])

    seen: set[str] = set()
    out: list[dict] = []
    for p in candidate_paths:
        if p in seen:
            continue
        seen.add(p)
        try:
            content = memory.read_page(memory_dir, p)
            out.append({"path": p, "content": content})
        except FileNotFoundError:
            continue
    return out


def _match_segment_for_draft_path(conn, group_id: str, draft_path: str) -> dict | None:
    """Find the segment whose canonical draft path matches `draft_path`."""
    for seg in storage.get_group_segments(conn, group_id):
        seg_start = seg.get("segment_start_at")
        if not seg_start:
            continue
        if paths.session_draft_path(group_id, seg_start) == draft_path:
            return seg
    return None


# --- init / status / search / reindex ---

@main.command()
def init():
    """Initialize data directory, folder structure, and run migrations."""
    config = load_config()

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.db_dir.mkdir(parents=True, exist_ok=True)
    click.echo(f"Data directory: {config.data_dir}")

    memory.ensure_memory_dirs(config.memory_dir)
    click.echo(f"Memory directory: {config.memory_dir}")

    conn = storage.connect(config.sessions_db)
    click.echo(f"Database: {config.sessions_db}")
    conn.close()

    click.echo("Initialized.")


@main.command()
def status():
    """Show system stats and pending review counts."""
    config = load_config()
    if not config.sessions_db.exists():
        click.echo("Not initialized. Run 'akw init' first.")
        return

    conn = storage.connect(config.sessions_db)

    projects = storage.list_projects(conn)
    all_groups = storage.list_groups(conn)
    open_groups = storage.get_open_groups(conn)
    orphans = storage.get_orphaned_groups(conn)
    closed_no_draft = storage.get_closed_no_draft_segments(conn)
    incomplete_total = len(orphans) + len(closed_no_draft)
    unarchived_today = storage.count_unarchived_session_drafts(conn, exclude_today=False)
    unarchived_pending = storage.count_unarchived_session_drafts(conn, exclude_today=True)

    click.echo(f"Data directory:  {config.data_dir}")
    click.echo(f"Projects:        {len(projects)}")
    click.echo(f"Groups:          {len(all_groups)} ({len(open_groups)} open, {len(orphans)} orphaned)")
    click.echo(f"Session drafts:  {unarchived_today} unarchived ({unarchived_pending} from prior days)")
    click.echo(f"Incomplete:      {incomplete_total} segment(s) ({len(orphans)} orphan, {len(closed_no_draft)} closed-no-draft)")

    for label, subdir in [
        ("Knowledge", "2_knowledges"),
        ("Skills", "3_intelligences/skills"),
        ("Agents", "3_intelligences/agents"),
    ]:
        pages = memory.list_pages(config.memory_dir, subdir)
        click.echo(f"{label} pages:    {len(pages)}")

    if config.memory_dir.exists():
        duckdb_conn = search.connect(config.search_db)
        count = search.sync_from_files(duckdb_conn, config.memory_dir)
        click.echo(f"Search index:    {count} pages indexed")
        duckdb_conn.close()
    else:
        click.echo("Search index:    not built")

    if incomplete_total:
        click.echo("")
        click.echo(f"Run 'akw recover' to write stub drafts for the {incomplete_total} incomplete segment(s).")

    conn.close()


def _run_search(
    query: str,
    tier: str | None,
    domain: str | None = None,
    json_out: bool = False,
) -> None:
    """Shared search runner — used by `akw search` and the skill/agent CLI wrappers.

    When `tier` is omitted, intelligences tiers (`skill`, `agent`) are filtered out
    of the default ranking — matching the MCP `memory_search` contract. Pass
    `tier="skill"` / `tier="agent"` explicitly (via `akw skill search` /
    `akw agent search`) to scope INTO those tiers.
    """
    config = load_config()
    if not config.memory_dir.exists():
        if json_out:
            _emit_json([])
        else:
            click.echo("Memory folder not initialized. Run 'akw init' first.")
        return

    duckdb_conn = search.connect(config.search_db)
    search.sync_from_files(duckdb_conn, config.memory_dir)

    results = search.search(duckdb_conn, query, tier, domain_filter=domain)
    if tier is None:
        results = [r for r in results if r["tier"] not in _INTELLIGENCES_TIER_LABELS]

    if json_out:
        _emit_json(results)
        duckdb_conn.close()
        return

    if not results:
        click.echo("No results found.")
        duckdb_conn.close()
        return

    for r in results:
        click.echo(f"  [{r['tier']}] {r['path']}")
        if r.get("summary"):
            click.echo(f"    {r['summary'][:100]}")

    click.echo(f"\n{len(results)} results.")
    duckdb_conn.close()


@main.command("search")
@click.argument("query")
@click.option("--tier", "-t", default=None, help="Filter by tier: knowledge, skill, agent, session_draft, session_archived.")
@click.option("--json", "json_out", is_flag=True, help="Emit results as JSON (parity with MCP memory_search).")
def search_cmd(query: str, tier: str | None, json_out: bool):
    """Search memory from the terminal."""
    _run_search(query, tier, json_out=json_out)


# --- skill / agent discovery (EP-00009) ---

@main.group()
def skill():
    """Skill bundle discovery commands."""


main.add_command(skill)


@skill.command("search")
@click.argument("query")
@click.option("--domain", "-d", default=None, help="Limit to a single domain (e.g. engineering, design).")
@click.option("--json", "json_out", is_flag=True, help="Emit results as JSON (parity with MCP skill_search).")
def skill_search_cmd(query: str, domain: str | None, json_out: bool):
    """Search skill bundles by query."""
    _run_search(query, tier="skill", domain=domain, json_out=json_out)


@skill.command("show")
@click.argument("skill_arg")
@click.option("--json", "json_out", is_flag=True, help="Emit content + manifest as JSON (parity with MCP skill_get).")
def skill_show_cmd(skill_arg: str, json_out: bool):
    """Print SKILL.md content + bundle manifest. Accepts full path or <domain>/<slug>."""
    config = load_config()
    canonical = paths.resolve_skill_path(skill_arg)
    parsed = paths.parse_skill_path(canonical)
    if parsed is None:
        click.echo(f"Not a valid skill path: {skill_arg}", err=True)
        raise SystemExit(1)
    domain, slug = parsed

    full = config.memory_dir / canonical
    if not full.exists():
        click.echo(f"Skill not found: {canonical}", err=True)
        raise SystemExit(1)

    content = full.read_text(encoding="utf-8")
    bundle_dir = config.memory_dir / paths.skill_bundle_dir(canonical)
    resources = memory.list_bundle_companions(config.memory_dir, bundle_dir, "resources")
    scripts = memory.list_bundle_companions(config.memory_dir, bundle_dir, "scripts")
    tests = memory.list_bundle_companions(config.memory_dir, bundle_dir, "tests")

    if json_out:
        _emit_json({
            "path": canonical,
            "domain": domain,
            "slug": slug,
            "title": _first_heading(content) or slug,
            "content": content,
            "resources": resources,
            "scripts": scripts,
            "tests": tests,
        })
        return

    click.echo(f"# {domain}/{slug}\n")
    click.echo(content)
    for label, items in (("resources", resources), ("scripts", scripts), ("tests", tests)):
        if items:
            click.echo(f"\n## {label}/")
            for p in items:
                click.echo(f"  {p}")


@main.group()
def agent():
    """Agent persona discovery commands."""


main.add_command(agent)


@agent.command("search")
@click.argument("query")
@click.option("--domain", "-d", default=None, help="Limit to a single domain (e.g. engineering, design).")
@click.option("--json", "json_out", is_flag=True, help="Emit results as JSON (parity with MCP agent_search).")
def agent_search_cmd(query: str, domain: str | None, json_out: bool):
    """Search agent personas by query."""
    _run_search(query, tier="agent", domain=domain, json_out=json_out)


@agent.command("show")
@click.argument("agent_arg")
@click.option("--json", "json_out", is_flag=True, help="Emit content as JSON (parity with MCP agent_get).")
def agent_show_cmd(agent_arg: str, json_out: bool):
    """Print agent persona file. Accepts full path or <domain>/<slug>."""
    config = load_config()
    canonical = paths.resolve_agent_path(agent_arg)
    parsed = paths.parse_agent_path(canonical)
    if parsed is None:
        click.echo(f"Not a valid agent path: {agent_arg}", err=True)
        raise SystemExit(1)
    domain, slug = parsed

    full = config.memory_dir / canonical
    if not full.exists():
        click.echo(f"Agent not found: {canonical}", err=True)
        raise SystemExit(1)

    content = full.read_text(encoding="utf-8")

    if json_out:
        _emit_json({
            "path": canonical,
            "domain": domain,
            "slug": slug,
            "title": _first_heading(content) or slug,
            "content": content,
        })
        return

    click.echo(f"# {domain}/{slug}\n")
    click.echo(content)


@main.command()
@click.option("--force", is_flag=True, help="Drift-recover draft_state from frontmatter even if the table is non-empty.")
def reindex(force: bool):
    """Rebuild DuckDB search index + reconcile draft_state with on-disk drafts.

    Two roles per Decision C of EP-00005:
    - Drift recovery: rebuild draft_state from frontmatter when the table is
      missing or known-stale. Requires `--force` if the table is non-empty.
    - Reconciliation: pick up file moves the curator made by hand (e.g.
      `git mv` into `1_drafts/_archived/`). Always safe.
    """
    config = load_config()
    if not config.memory_dir.exists():
        click.echo("Not initialized. Run 'akw init' first.")
        return

    duckdb_conn = search.connect(config.search_db)
    count = search.sync_from_files(duckdb_conn, config.memory_dir)
    click.echo(f"Search index: {count} pages.")
    duckdb_conn.close()

    conn = storage.connect(config.sessions_db)
    try:
        result = storage.reindex_draft_state(conn, config.memory_dir, force=force)
    finally:
        conn.close()

    if result["had_existing_rows"] and not force and result["rebuilt"] == 0:
        click.echo(
            f"draft_state: {result['reconciled']} reconciled (file moves). "
            f"Run with --force to rebuild from frontmatter."
        )
    else:
        click.echo(
            f"draft_state: {result['rebuilt']} rebuilt, {result['reconciled']} reconciled."
        )


# --- groups subcommand (replaces sessions listing) ---

@main.command()
@click.option("--project", "-p", default=None, help="Filter by project name or ID.")
def groups(project: str | None):
    """List groups with start metadata + latest activity."""
    config = load_config()
    if not config.sessions_db.exists():
        click.echo("Not initialized. Run 'akw init' first.")
        return

    conn = storage.connect(config.sessions_db)

    project_id = None
    if project:
        for p in storage.list_projects(conn):
            if p["id"] == project or p["name"] == project:
                project_id = p["id"]
                break
        if project_id is None:
            click.echo(f"Project not found: {project}")
            conn.close()
            return

    filter_md = {"project_id": project_id} if project_id else None
    results = storage.list_groups(conn, filter_metadata=filter_md)
    if not results:
        click.echo("No groups found.")
        conn.close()
        return

    for g in results:
        gid_short = g["group_id"][:12] if g["group_id"] else "(none)"
        agent = (g.get("metadata") or {}).get("agent", "?")
        started = (g.get("started_at") or "")[:16]
        latest = (g.get("latest_at") or "")[:16]
        latest_kind = g.get("latest_kind") or "?"
        click.echo(f"[{latest_kind:<10}] {gid_short}... | started {started} | last {latest} | {agent}")

    conn.close()


# --- group lifecycle subgroup (used by hooks and scripts) ---

@main.group()
def group():
    """Group lifecycle commands (used by hooks and scripts)."""
    pass


main.add_command(group)


@group.command("start")
@click.option("--group-id", "-g", default=None, help="Group ID to continue (omit for new group).")
@click.option("--project", "-p", default=None, help="Project name or ID.")
@click.option("--agent", "-a", default="claude", help="Agent name.")
@click.option("--working-dir", default=None, help="Working directory path metadata.")
@click.option("--json", "json_out", is_flag=True, help="Emit group_id, segment_start_at, pending counts, and recommended_context as JSON (parity with MCP group_start).")
def group_start(
    group_id: str | None,
    project: str | None,
    agent: str,
    working_dir: str | None,
    json_out: bool,
):
    """Start a new group (or continue one). Prints group_id to stdout for hook capture."""
    config = load_config()
    conn = storage.connect(config.sessions_db)

    project_id: str | None = None
    project_obj: dict | None = None
    if project:
        for p in storage.list_projects(conn):
            if p["id"] == project or p["name"] == project:
                project_id = p["id"]
                project_obj = p
                break
        if project_id is None:
            path = working_dir or os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
            new_project = storage.create_project(conn, project, path)
            project_id = new_project["id"]
            project_obj = new_project

    md: dict = {"agent": agent}
    if project_id:
        md["project_id"] = project_id
    if working_dir:
        md["working_dir"] = working_dir

    result = storage.start_group(
        conn, group_id=group_id, agent=agent, metadata=md,
    )

    if json_out:
        duckdb_conn = search.connect(config.search_db)
        if config.memory_dir.exists():
            search.sync_from_files(duckdb_conn, config.memory_dir)
        payload: dict = {
            "group_id": result["group_id"],
            "segment_start_at": result["segment_start_at"],
            "pending": _pending_counts(conn),
            "recommended_context": _get_recommended_context(duckdb_conn, config.memory_dir, project_obj),
        }
        if result.get("idle_closed_segment"):
            payload["idle_closed_segment"] = result["idle_closed_segment"]
        duckdb_conn.close()
        _emit_json(payload)
    else:
        click.echo(result["group_id"])
    conn.close()


@group.command("end")
@click.option("--group-id", "-g", "group_id", default=None, help="Group ID to end (default: most recent open).")
def group_end(group_id: str | None):
    """End the current segment of a group. Idempotent."""
    config = load_config()
    conn = storage.connect(config.sessions_db)

    gid = group_id
    if gid is None:
        open_groups = storage.get_open_groups(conn)
        if not open_groups:
            click.echo("No open group to end.", err=True)
            conn.close()
            return
        gid = max(open_groups, key=lambda g: g["latest_at"])["group_id"]

    result = storage.end_group(conn, gid, kind="end")
    if result is None:
        click.echo(f"Group has no turns: {gid}", err=True)
    else:
        click.echo(gid)
    conn.close()


@group.command("status")
@click.option("--json", "json_out", is_flag=True, help="Emit status as JSON (parity with MCP group_status).")
def group_status(json_out: bool):
    """Show the most recent open group + its segment."""
    config = load_config()
    if not config.sessions_db.exists():
        if json_out:
            _emit_json({"error": "Not initialized. Run 'akw init' first."})
        else:
            click.echo("Not initialized. Run 'akw init' first.")
        return

    conn = storage.connect(config.sessions_db)
    open_groups = storage.get_open_groups(conn)
    if not open_groups:
        if json_out:
            _emit_json({"group_id": None, "segment_start_at": None, "segment_turn_count": 0})
        else:
            click.echo("No active group.")
        conn.close()
        return

    chosen = max(open_groups, key=lambda g: g["latest_at"])
    gid = chosen["group_id"]
    md = chosen.get("start_marker_metadata") or {}
    seg_turns = storage.get_current_segment_turns(conn, gid)
    turn_count = sum(1 for t in seg_turns if t["kind"] == "turn")
    seg_start = next((t["created_at"] for t in seg_turns if t["kind"] == "start"), None)

    if json_out:
        _emit_json({
            "group_id": gid,
            "segment_start_at": seg_start,
            "segment_turn_count": turn_count,
            "agent": md.get("agent"),
            "project_id": md.get("project_id"),
            "latest_at": chosen["latest_at"],
        })
        conn.close()
        return

    click.echo(f"Group:           {gid}")
    click.echo(f"Agent:           {md.get('agent', '?')}")
    click.echo(f"Project:         {md.get('project_id') or '(none)'}")
    click.echo(f"Segment start:   {seg_start[:19] if seg_start else '(none)'}")
    click.echo(f"Latest activity: {chosen['latest_at'][:19]}")
    click.echo(f"Turns:           {turn_count}")
    conn.close()


@group.command("list")
@click.option("--recent", "-r", is_flag=True, help="Show only recent groups (last 10).")
def group_list(recent: bool):
    """List groups for continuation lookup."""
    config = load_config()
    if not config.sessions_db.exists():
        click.echo("Not initialized. Run 'akw init' first.")
        return

    conn = storage.connect(config.sessions_db)
    results = storage.list_groups(conn)
    if recent:
        results = results[-10:]

    if not results:
        click.echo("No groups found.")
        conn.close()
        return

    for g in results:
        gid_short = g["group_id"][:12]
        md = g.get("metadata") or {}
        agent = md.get("agent", "?")
        started = (g.get("started_at") or "")[:16]
        latest_kind = g.get("latest_kind") or "?"
        click.echo(f"[{latest_kind:<10}] {gid_short}... | {started} | {agent}")
    conn.close()


@group.command("context")
def group_context():
    """Print recent group summaries to stderr (used by SessionStart hook)."""
    config = load_config()
    if not config.sessions_db.exists():
        return

    conn = storage.connect(config.sessions_db)
    open_groups = storage.get_open_groups(conn)
    current_id = max(open_groups, key=lambda g: g["latest_at"])["group_id"] if open_groups else ""
    _print_group_context(conn, current_id, config.memory_dir)
    conn.close()


@group.command("prompt")
def group_prompt():
    """Save user prompt from UserPromptSubmit hook. Paired with next Stop response."""
    config = load_config()
    try:
        hook_data = json_mod.load(sys.stdin)
    except (json_mod.JSONDecodeError, ValueError):
        return

    prompt = hook_data.get("prompt", "")
    if not prompt:
        return

    if len(prompt) > 200_000:
        prompt = prompt[:200_000] + "..."

    prompt_file = config.data_dir / "pending_prompt.txt"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt)


@group.command("turn")
@click.option("--batch-size", "-b", default=10, help="Flush after this many buffered turns.")
def group_turn(batch_size: int):
    """Buffer a turn from Stop hook. Pairs with pending user prompt. Flushes every N turns."""
    config = load_config()
    conn = storage.connect(config.sessions_db)

    open_groups = storage.get_open_groups(conn)
    if not open_groups:
        conn.close()
        return
    gid = max(open_groups, key=lambda g: g["latest_at"])["group_id"]

    try:
        hook_data = json_mod.load(sys.stdin)
    except (json_mod.JSONDecodeError, ValueError):
        conn.close()
        return

    assistant_msg = hook_data.get("last_assistant_message", "")
    if not assistant_msg:
        conn.close()
        return

    if len(assistant_msg) > 200_000:
        assistant_msg = assistant_msg[:200_000] + "..."

    prompt_file = config.data_dir / "pending_prompt.txt"
    user_prompt = ""
    if prompt_file.exists():
        user_prompt = prompt_file.read_text()
        prompt_file.unlink(missing_ok=True)

    buffer_file = config.data_dir / "turn_buffer.jsonl"
    turn_entry = json_mod.dumps({
        "request": user_prompt or "(no prompt captured)",
        "response": assistant_msg,
    })
    with open(buffer_file, "a") as f:
        f.write(turn_entry + "\n")

    with open(buffer_file) as f:
        lines = f.readlines()

    if len(lines) >= batch_size:
        _flush_turn_buffer(conn, gid, buffer_file)

    conn.close()


@group.command("flush")
def group_flush():
    """Flush any buffered turns to the database. Called by SessionEnd hook."""
    config = load_config()
    conn = storage.connect(config.sessions_db)

    open_groups = storage.get_open_groups(conn)
    if not open_groups:
        conn.close()
        return
    gid = max(open_groups, key=lambda g: g["latest_at"])["group_id"]

    buffer_file = config.data_dir / "turn_buffer.jsonl"
    if buffer_file.exists():
        _flush_turn_buffer(conn, gid, buffer_file)

    conn.close()


@group.command("turns")
@click.argument("group_id")
@click.option("--segment-start", default=None, help="Segment start timestamp (ISO). Default: current segment.")
def group_turns(group_id: str, segment_start: str | None):
    """Print raw turns for a group's segment (used by `akw recover` follow-ups)."""
    config = load_config()
    conn = storage.connect(config.sessions_db)

    if segment_start:
        rows = storage.get_segment_turns(conn, group_id, segment_start)
    else:
        rows = storage.get_current_segment_turns(conn, group_id)

    if not rows:
        click.echo("No turns found.")
        conn.close()
        return

    for t in rows:
        kind = t["kind"]
        ts = (t["created_at"] or "")[:19]
        if kind == "turn":
            req = (t["request"] or "")[:120]
            click.echo(f"[turn       ] {ts} | {req}")
        else:
            click.echo(f"[{kind:<10}] {ts}")
    conn.close()


def _flush_turn_buffer(conn, group_id: str, buffer_file: Path) -> None:
    """Read buffered turns from file, write to DB, and clear the buffer."""
    from agent_knowledge.core import sanitizer

    if not buffer_file.exists():
        return

    turns = []
    with open(buffer_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    turn = json_mod.loads(line)
                    req, _ = sanitizer.redact(turn.get("request", ""))
                    resp, _ = sanitizer.redact(turn.get("response", ""))
                    turns.append({"request": req, "response": resp})
                except (json_mod.JSONDecodeError, ValueError):
                    continue

    if turns:
        storage.create_turns(conn, group_id, turns)

    buffer_file.unlink(missing_ok=True)


# --- archive (Phase 4) ---

@main.command()
@click.argument("draft_path")
def archive(draft_path: str):
    """Move a session draft into 1_drafts/_archived/ as a flat-file `sessions__*.md`.

    Records the move in memory_edits so subsequent `akw status` reflects archival.
    """
    config = load_config()
    sessions_prefix = paths.SESSIONS_DIR + "/"
    if not draft_path.startswith(sessions_prefix):
        click.echo(f"Only {sessions_prefix} paths can be archived. Got: {draft_path}", err=True)
        raise SystemExit(1)

    full_src = config.memory_dir / draft_path
    if not full_src.exists():
        click.echo(f"Draft not found: {draft_path}", err=True)
        raise SystemExit(1)

    target = paths.archived_session_path(draft_path)

    conn = storage.connect(config.sessions_db)
    try:
        memory.move_page(config.memory_dir, draft_path, target)
        storage.create_memory_edit(
            conn, target, "draft", "update",
            f"Archived from {draft_path}",
        )
        # Phase 2: update draft_state row (id stable, draft_path mutates).
        storage.archive_draft_state(conn, draft_path, target)
        click.echo(f"Archived: {draft_path} → {target}")
    except FileExistsError:
        click.echo(f"Target already exists: {target}", err=True)
        raise SystemExit(1)
    finally:
        conn.close()


# --- recover (Phase 4.5) ---

_STUB_BODY = """# Session segment recovered without summary

This segment ended without a clean session-end summary. The agent likely crashed,
disconnected, or was killed before writing the draft. The raw turns are preserved
in the database.

**Inspect raw turns:** `akw group turns {group_id} --segment-start {segment_start_at}`

**Next steps (curator's choice):**
- Read the raw turns and replace this body with a real summary, then archive.
- If the segment isn't worth preserving, `akw archive {draft_path}` and move on.
"""


@main.command()
@click.option("--dry-run", is_flag=True, help="Print what would happen without writing.")
def recover(dry_run: bool):
    """Recover incomplete segments: write idle_close markers for orphans + stub drafts."""
    config = load_config()
    if not config.sessions_db.exists():
        click.echo("Not initialized. Run 'akw init' first.")
        return

    conn = storage.connect(config.sessions_db)

    # Pass 1: orphan groups → write idle_close markers.
    orphans = storage.get_orphaned_groups(conn)
    closed_count = 0
    for orphan in orphans:
        gid = orphan["group_id"]
        if dry_run:
            click.echo(f"[dry-run] would write idle_close for orphan group {gid}")
        else:
            storage.end_group(conn, gid, kind="idle_close")
        closed_count += 1

    # Pass 2: closed-no-draft segments → stub drafts.
    # (Re-query so freshly-closed orphans appear.)
    closed_no_draft = (
        storage.get_closed_no_draft_segments(conn) if not dry_run else
        # In dry-run mode the orphans above weren't actually closed, so simulate:
        storage.get_closed_no_draft_segments(conn) + [
            {
                "group_id": o["group_id"],
                "segment_start_at": (o.get("start_marker_metadata") or {}).get("segment_start_at") or o["latest_at"],
                "segment_end_at": o["latest_at"],
                "end_kind": "idle_close",
                "turn_count": 0,
                "start_marker_metadata": o.get("start_marker_metadata") or {},
            } for o in orphans
        ]
    )

    stubs_written = 0
    for seg in closed_no_draft:
        gid = seg["group_id"]
        seg_start = seg["segment_start_at"]
        seg_end = seg["segment_end_at"]
        end_kind = seg["end_kind"] or "closed_no_draft"
        recovery_kind = "idle_close" if end_kind == "idle_close" else "closed_no_draft"
        draft_path = paths.session_draft_path(gid, seg_start)

        body = _STUB_BODY.format(
            group_id=gid,
            segment_start_at=seg_start,
            draft_path=draft_path,
        )
        frontmatter = (
            "---\n"
            f"group_id: {gid}\n"
            f"segment_start_at: {seg_start}\n"
            f"segment_end_at: {seg_end}\n"
            f"source_metadata: {json_mod.dumps(seg.get('start_marker_metadata') or {})}\n"
            f"created_at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"recovery_kind: {recovery_kind}\n"
            f"turn_count: {seg.get('turn_count', 0)}\n"
            "---\n\n"
        )
        page_content = frontmatter + body

        if dry_run:
            click.echo(f"[dry-run] would write stub: {draft_path}")
            stubs_written += 1
            continue

        try:
            memory.create_page(config.memory_dir, draft_path, page_content)
            storage.create_memory_edit(
                conn, draft_path, "draft", "create",
                f"Recovery stub ({recovery_kind})",
                group_id=gid,
            )
            # Phase 2: write a draft_state row so pending counts pick this up.
            storage.upsert_draft_state(
                conn,
                draft_path=draft_path,
                group_id=gid,
                segment_start_at=seg_start,
                segment_end_at=seg_end,
            )
            click.echo(f"Wrote stub: {draft_path}")
            stubs_written += 1
        except FileExistsError:
            # Already exists — skip silently (idempotency).
            pass

    if dry_run:
        click.echo(f"\n[dry-run] would close {closed_count} orphan(s); would write {stubs_written} stub draft(s).")
    else:
        click.echo(f"\nClosed {closed_count} orphan(s); wrote {stubs_written} stub draft(s).")
    conn.close()


# --- memory subcommands (parity with MCP memory_* tools) ---

@main.group("memory")
def memory_group():
    """Memory page commands (read, create, update, rm, ls, history)."""


main.add_command(memory_group)


@memory_group.command("read")
@click.argument("path")
@click.option("--json", "json_out", is_flag=True, help="Emit {path, content} as JSON (parity with MCP memory_read).")
def memory_read_cmd(path: str, json_out: bool):
    """Read a memory page by relative path."""
    config = load_config()
    try:
        content = memory.read_page(config.memory_dir, path)
    except FileNotFoundError:
        click.echo(f"Page not found: {path}", err=True)
        raise SystemExit(1)

    if json_out:
        _emit_json({"path": path, "content": content})
    else:
        click.echo(content)


@memory_group.command("create")
@click.option("--path", "path", required=True, help="Page path. Must be under 1_drafts/ or an agent-writable carve-out.")
@click.option("--title", required=True, help="Page title.")
@click.option("--content", "content", default=None, help="Inline content (or use --content-file).")
@click.option("--content-file", "content_file", type=click.Path(exists=True, dir_okay=False), default=None, help="Read content from a file instead of --content.")
@click.option("--tags", default=None, help="Comma-separated tag list.")
@click.option("--summary", default="", help="Short index summary.")
@click.option("--group-id", "group_id", default=None, help="Bind the page to an originating group (used for session drafts).")
def memory_create_cmd(
    path: str,
    title: str,
    content: str | None,
    content_file: str | None,
    tags: str | None,
    summary: str,
    group_id: str | None,
):
    """Create a new memory page in 1_drafts/. Curated tiers are rejected."""
    if content is None and content_file is None:
        click.echo("Provide --content or --content-file.", err=True)
        raise SystemExit(2)
    if content_file:
        content = Path(content_file).read_text(encoding="utf-8")
    assert content is not None

    rejection = paths.reject_curated_write(path)
    if rejection:
        click.echo(rejection, err=True)
        raise SystemExit(1)

    config = load_config()
    redacted, _ = sanitizer.redact(content)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    page_content = _build_page(title, redacted, tag_list, summary)

    try:
        memory.create_page(config.memory_dir, path, page_content)
    except FileExistsError:
        click.echo(f"Page already exists: {path}", err=True)
        raise SystemExit(1)

    conn = storage.connect(config.sessions_db)
    try:
        tier = memory.get_tier(path) or "draft"
        storage.create_memory_edit(
            conn, path, tier, "create",
            summary or f"Created {title}",
            group_id=group_id,
        )
        if path.startswith(paths.SESSIONS_DIR + "/") and group_id:
            seg = _match_segment_for_draft_path(conn, group_id, path)
            if seg is not None:
                storage.upsert_draft_state(
                    conn,
                    draft_path=path,
                    group_id=group_id,
                    segment_start_at=seg["segment_start_at"],
                    segment_end_at=seg["segment_end_at"] or seg["segment_start_at"],
                )
    finally:
        conn.close()

    click.echo(f"Created: {path}")


@memory_group.command("update")
@click.argument("path")
@click.option("--content", "content", default=None, help="New full body of the page (replaces existing content, including any frontmatter). Pair with --content-file to load from disk.")
@click.option("--content-file", "content_file", type=click.Path(exists=True, dir_okay=False), default=None, help="Read replacement body from a file instead of --content.")
@click.option("--summary", default="", help="Short edit summary recorded in audit history (not written to the page).")
def memory_update_cmd(path: str, content: str | None, content_file: str | None, summary: str):
    """Update an existing memory page (curator-side; curated tiers rejected).

    --content / --content-file replaces the entire file body — there is no
    merge with existing frontmatter. To preserve title/tags/summary lines,
    include them in the new content.
    """
    if content is None and content_file is None:
        click.echo("Provide --content or --content-file.", err=True)
        raise SystemExit(2)
    if content_file:
        content = Path(content_file).read_text(encoding="utf-8")
    assert content is not None

    rejection = paths.reject_curated_write(path)
    if rejection:
        click.echo(rejection, err=True)
        raise SystemExit(1)

    config = load_config()
    redacted, _ = sanitizer.redact(content)
    try:
        memory.update_page(config.memory_dir, path, redacted)
    except FileNotFoundError:
        click.echo(f"Page not found: {path}", err=True)
        raise SystemExit(1)

    conn = storage.connect(config.sessions_db)
    try:
        tier = memory.get_tier(path) or "draft"
        storage.create_memory_edit(
            conn, path, tier, "update",
            summary or "Updated page",
        )
    finally:
        conn.close()

    click.echo(f"Updated: {path}")


@memory_group.command("rm")
@click.argument("path")
@click.option("--reason", default="", help="Why the page is being removed (recorded in audit log).")
def memory_rm_cmd(path: str, reason: str):
    """Delete a memory page (or archive if it's an agent-writable carve-out)."""
    if path.startswith(paths.DRAFTS_PREFIX):
        click.echo(
            "Drafts cannot be deleted via this command. The curator removes drafts via the file system.",
            err=True,
        )
        raise SystemExit(1)

    config = load_config()
    conn = storage.connect(config.sessions_db)
    duckdb_conn = search.connect(config.search_db)

    try:
        if paths.is_archive_redirected_path(path):
            target = paths.archived_knowledge_path(path)
            try:
                memory.move_page(config.memory_dir, path, target)
            except FileNotFoundError:
                click.echo(f"Page not found: {path}", err=True)
                raise SystemExit(1)
            except FileExistsError:
                click.echo(f"Archive target already exists for {path}", err=True)
                raise SystemExit(1)
            tier = memory.get_tier(path) or "knowledge"
            storage.create_memory_edit(
                conn, target, tier, "archive",
                reason or f"Archived from {path}",
            )
            search.sync_from_files(duckdb_conn, config.memory_dir)
            click.echo(f"Archived: {path} → {target}")
            return

        try:
            memory.delete_page(config.memory_dir, path)
        except FileNotFoundError:
            click.echo(f"Page not found: {path}", err=True)
            raise SystemExit(1)

        tier = memory.get_tier(path) or "draft"
        storage.create_memory_edit(conn, path, tier, "delete", reason or "Deleted page")
        if tier in ("knowledge", "skill", "agent"):
            search.sync_from_files(duckdb_conn, config.memory_dir)
        click.echo(f"Deleted: {path}")
    finally:
        duckdb_conn.close()
        conn.close()


@memory_group.command("ls")
@click.option("--tier", "-t", default=None, help="Filter by tier (knowledge, skill, agent, session_draft, ...).")
@click.option("--json", "json_out", is_flag=True, help="Emit catalog as JSON (parity with MCP memory_index).")
def memory_ls_cmd(tier: str | None, json_out: bool):
    """List indexed pages (parity with MCP memory_index)."""
    config = load_config()
    if not config.memory_dir.exists():
        click.echo("Memory folder not initialized. Run 'akw init' first.", err=True)
        raise SystemExit(1)

    duckdb_conn = search.connect(config.search_db)
    search.sync_from_files(duckdb_conn, config.memory_dir)
    rows = search.get_index(duckdb_conn, tier)
    duckdb_conn.close()

    if json_out:
        _emit_json(rows)
        return

    if not rows:
        click.echo("No pages indexed.")
        return
    for r in rows:
        click.echo(f"  [{r['tier']}] {r['path']}")
    click.echo(f"\n{len(rows)} pages.")


@memory_group.command("history")
@click.option("--page-path", "page_path", default=None, help="Limit to a single page path.")
@click.option("--limit", default=20, help="Max rows to return.")
@click.option("--json", "json_out", is_flag=True, help="Emit history as JSON (parity with MCP memory_history).")
def memory_history_cmd(page_path: str | None, limit: int, json_out: bool):
    """Show recent edit history (parity with MCP memory_history)."""
    config = load_config()
    if not config.sessions_db.exists():
        click.echo("Not initialized. Run 'akw init' first.", err=True)
        raise SystemExit(1)

    conn = storage.connect(config.sessions_db)
    rows = storage.get_memory_history(conn, limit, page_path)
    conn.close()

    if json_out:
        _emit_json(rows)
        return

    if not rows:
        click.echo("No edits recorded.")
        return
    for r in rows:
        ts = (r.get("created_at") or "")[:19]
        kind = r.get("edit_kind") or r.get("kind") or "?"
        click.echo(f"[{kind:<8}] {ts} | {r.get('page_path', '?')}")


# --- project subcommands (parity with MCP project_* tools) ---

@main.group("project")
def project_group():
    """Project management commands."""


main.add_command(project_group)


@project_group.command("new")
@click.option("--name", required=True, help="Project name.")
@click.option("--path", "path_arg", required=True, help="Absolute path to the project root.")
@click.option("--tags", default=None, help="Comma-separated domain tags (e.g. 'python,web').")
def project_new_cmd(name: str, path_arg: str, tags: str | None):
    """Register a project (parity with MCP project_create)."""
    config = load_config()
    conn = storage.connect(config.sessions_db)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    result = storage.create_project(conn, name, path_arg, tag_list)
    conn.close()
    click.echo(result["id"])


@project_group.command("ls")
@click.option("--json", "json_out", is_flag=True, help="Emit projects as JSON (parity with MCP project_list).")
def project_ls_cmd(json_out: bool):
    """List registered projects (parity with MCP project_list)."""
    config = load_config()
    if not config.sessions_db.exists():
        click.echo("Not initialized. Run 'akw init' first.", err=True)
        raise SystemExit(1)

    conn = storage.connect(config.sessions_db)
    projects = storage.list_projects(conn)
    conn.close()

    if json_out:
        _emit_json(projects)
        return

    if not projects:
        click.echo("No projects registered.")
        return
    for p in projects:
        tags = ",".join(p.get("tags") or [])
        click.echo(f"  {p['id'][:8]}  {p['name']:<24}  {p.get('path', '')}  [{tags}]")


# --- maintain subcommands (parity with MCP maintain_* tools) ---

@main.group("maintain")
def maintain_group():
    """Maintenance commands (stats, purge)."""


main.add_command(maintain_group)


@maintain_group.command("stats")
@click.option("--stale-days", default=90, help="Pages older than this are reported as stale (default: 90).")
@click.option("--json", "json_out", is_flag=True, help="Emit stats as JSON (parity with MCP maintain_get_stats).")
def maintain_stats_cmd(stale_days: int, json_out: bool):
    """Structural stats: page counts per tier, group stats, stale pages."""
    config = load_config()
    if not config.sessions_db.exists():
        click.echo("Not initialized. Run 'akw init' first.", err=True)
        raise SystemExit(1)

    stale_cutoff = (
        datetime.now(timezone.utc) - __import__("datetime").timedelta(days=stale_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    knowledge_pages = memory.list_pages(config.memory_dir, "2_knowledges")
    skill_pages = memory.list_pages(config.memory_dir, "3_intelligences/skills")
    agent_pages = memory.list_pages(config.memory_dir, "3_intelligences/agents")
    draft_pages = memory.list_pages(config.memory_dir, "1_drafts")

    stale: list[dict] = []
    for pages, tier in [
        (knowledge_pages, "knowledge"),
        (skill_pages, "skill"),
        (agent_pages, "agent"),
    ]:
        for page_path in pages:
            full_path = config.memory_dir / page_path
            if full_path.exists():
                mtime = datetime.fromtimestamp(full_path.stat().st_mtime, tz=timezone.utc)
                if mtime.strftime("%Y-%m-%dT%H:%M:%SZ") < stale_cutoff:
                    stale.append({"path": page_path, "tier": tier, "last_modified": mtime.isoformat()})

    conn = storage.connect(config.sessions_db)
    all_groups = storage.list_groups(conn)
    open_groups = storage.get_open_groups(conn)
    orphans = storage.get_orphaned_groups(conn)
    closed_no_draft = storage.get_closed_no_draft_segments(conn)
    conn.close()

    payload = {
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

    if json_out:
        _emit_json(payload)
        return

    click.echo(f"Pages:")
    for k, v in payload["pages"].items():
        click.echo(f"  {k:<10} {v}")
    click.echo(f"Groups:")
    for k, v in payload["groups"].items():
        click.echo(f"  {k:<28} {v}")
    if stale:
        click.echo(f"Stale pages ({len(stale)} older than {stale_days} days):")
        for s in stale[:10]:
            click.echo(f"  [{s['tier']}] {s['path']}")
        if len(stale) > 10:
            click.echo(f"  ... and {len(stale) - 10} more.")


@maintain_group.command("purge")
@click.option("--older-than-days", "older_than_days", default=365, help="Purge archived drafts older than N days (default: 365).")
def maintain_purge_cmd(older_than_days: int):
    """Delete archived session drafts older than the retention boundary."""
    config = load_config()
    archive_dir = config.memory_dir / paths.ARCHIVED_DIR
    if not archive_dir.exists():
        click.echo(f"Archive directory does not exist: {archive_dir}")
        return

    cutoff = (
        datetime.now(timezone.utc) - __import__("datetime").timedelta(days=older_than_days)
    ).timestamp()
    purged = 0
    for p in archive_dir.glob(f"{paths.ARCHIVED_SESSION_PREFIX}*.md"):
        if p.stat().st_mtime < cutoff:
            p.unlink()
            purged += 1

    click.echo(f"Purged {purged} archived draft(s) older than {older_than_days} days.")


# --- Helpers ---

def _print_group_context(conn, exclude_group_id: str, memory_dir: Path) -> None:
    """Print recent group summaries to stderr (Claude Code displays hook stderr)."""
    recent = storage.list_groups(conn)[-6:]  # last 6 groups
    recent = [g for g in recent if g["group_id"] != exclude_group_id]
    if not recent:
        return

    lines = ["[agent-knowledge] recent groups:"]
    for g in recent[-5:]:
        gid_short = g["group_id"][:12]
        md = g.get("metadata") or {}
        agent = md.get("agent", "?")
        date = (g.get("started_at") or "")[:16]
        latest_kind = g.get("latest_kind") or "?"
        turns = storage.get_group_turns(conn, g["group_id"])
        turn_count = sum(1 for t in turns if t["kind"] == "turn")

        summary = ""
        last_turn = next(
            (t for t in reversed(turns) if t["kind"] == "turn" and t["request"]),
            None,
        )
        if last_turn:
            summary = f" — {last_turn['request'][:80]}"

        lines.append(f"  [{latest_kind:<10}] {date} | {agent} | {turn_count} turns | {gid_short}...{summary}")

        # Show session draft if it exists
        draft_path = storage.get_segment_draft_path(conn, g["group_id"])
        if draft_path:
            try:
                content = memory.read_page(memory_dir, draft_path)
                content_lines = [
                    line.strip() for line in content.split("\n")
                    if line.strip() and not line.startswith("---")
                    and not line.startswith("tags:") and not line.startswith("summary:")
                    and not line.startswith("group_id:") and not line.startswith("segment_")
                ]
                for cl in content_lines[:2]:
                    lines.append(f"    {cl[:100]}")
            except FileNotFoundError:
                pass

    # Pending counts
    orphans = storage.get_orphaned_groups(conn)
    closed_no_draft = storage.get_closed_no_draft_segments(conn)
    incomplete = len(orphans) + len(closed_no_draft)
    if incomplete:
        lines.append(f"  ({incomplete} incomplete segment(s) — run `akw recover`)")

    click.echo("\n".join(lines), err=True)
