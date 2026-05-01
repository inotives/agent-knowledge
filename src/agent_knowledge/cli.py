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
from pathlib import Path

import click

from agent_knowledge.core.config import load_config
from agent_knowledge.core import storage, memory, search, paths


# --- Top-level group ---

@click.group()
def main():
    """Agent Knowledge — persistent memory for AI agents."""
    pass


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


@main.command("search")
@click.argument("query")
@click.option("--tier", "-t", default=None, help="Filter by tier: knowledge, skill, agent, session_draft, session_archived.")
def search_cmd(query: str, tier: str | None):
    """Search memory from the terminal."""
    config = load_config()
    if not config.memory_dir.exists():
        click.echo("Memory folder not initialized. Run 'akw init' first.")
        return

    duckdb_conn = search.connect(config.search_db)
    search.sync_from_files(duckdb_conn, config.memory_dir)

    results = search.search(duckdb_conn, query, tier)
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
def group_start(
    group_id: str | None,
    project: str | None,
    agent: str,
    working_dir: str | None,
):
    """Start a new group (or continue one). Prints group_id to stdout for hook capture."""
    config = load_config()
    conn = storage.connect(config.sessions_db)

    project_id: str | None = None
    if project:
        for p in storage.list_projects(conn):
            if p["id"] == project or p["name"] == project:
                project_id = p["id"]
                break
        if project_id is None:
            path = working_dir or os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
            new_project = storage.create_project(conn, project, path)
            project_id = new_project["id"]

    md: dict = {"agent": agent}
    if project_id:
        md["project_id"] = project_id
    if working_dir:
        md["working_dir"] = working_dir

    result = storage.start_group(
        conn, group_id=group_id, agent=agent, metadata=md,
    )
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
def group_status():
    """Show the most recent open group + its segment."""
    config = load_config()
    if not config.sessions_db.exists():
        click.echo("Not initialized. Run 'akw init' first.")
        return

    conn = storage.connect(config.sessions_db)
    open_groups = storage.get_open_groups(conn)
    if not open_groups:
        click.echo("No active group.")
        conn.close()
        return

    chosen = max(open_groups, key=lambda g: g["latest_at"])
    gid = chosen["group_id"]
    md = chosen.get("start_marker_metadata") or {}
    seg_turns = storage.get_current_segment_turns(conn, gid)
    turn_count = sum(1 for t in seg_turns if t["kind"] == "turn")
    seg_start = next((t["created_at"] for t in seg_turns if t["kind"] == "start"), None)

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


# --- maintenance: purge ---

@main.command()
@click.option("--older-than", default=365, help="Purge archived drafts older than N days (default: 365).")
def purge(older_than: int):
    """Delete archived session drafts older than the retention boundary."""
    config = load_config()
    archive_dir = config.memory_dir / "drafts" / "archived" / "sessions"
    if not archive_dir.exists():
        click.echo(f"Archive directory does not exist: {archive_dir}")
        return

    cutoff = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=older_than)).timestamp()
    purged = 0
    for path in archive_dir.glob("**/*.md"):
        if path.stat().st_mtime < cutoff:
            path.unlink()
            purged += 1

    click.echo(f"Purged {purged} archived draft(s) older than {older_than} days.")


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
