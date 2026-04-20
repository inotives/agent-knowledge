"""CLI entry point — admin and inspection commands for agent-knowledge."""

from __future__ import annotations

from pathlib import Path

import click

from agent_knowledge.core.config import load_config
from agent_knowledge.core import storage, memory, search


@click.group()
def main():
    """Agent Knowledge — persistent memory for AI agents."""
    pass


@main.command()
def init():
    """Initialize data directory, folder structure, and run migrations."""
    config = load_config()

    # Create data directory
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.db_dir.mkdir(parents=True, exist_ok=True)
    click.echo(f"Data directory: {config.data_dir}")

    # Create memory folder structure
    memory.ensure_memory_dirs(config.memory_dir)
    click.echo(f"Memory directory: {config.memory_dir}")

    # Connect triggers auto-migration
    conn = storage.connect(config.sessions_db)
    click.echo(f"Database: {config.sessions_db}")
    conn.close()

    click.echo("Initialized.")


@main.command()
def status():
    """Show system stats."""
    config = load_config()

    if not config.sessions_db.exists():
        click.echo("Not initialized. Run 'akw init' first.")
        return

    conn = storage.connect(config.sessions_db)

    # Counts
    projects = storage.list_projects(conn)
    sessions = storage.list_sessions(conn)
    reviewed = [s for s in sessions if s["reviewed_at"] is not None]
    pending = [s for s in sessions if s["ended_at"] is not None and s["reviewed_at"] is None]

    click.echo(f"Data directory:  {config.data_dir}")
    click.echo(f"Projects:        {len(projects)}")
    click.echo(f"Sessions:        {len(sessions)} ({len(reviewed)} reviewed, {len(pending)} pending review)")

    # Memory page counts
    for tier, subdir in [("Drafts", "drafts"), ("Knowledge", "knowledge"), ("Skills", "skills")]:
        pages = memory.list_pages(config.memory_dir, subdir)
        click.echo(f"{tier} pages:    {len(pages)}")

    # Search index
    if config.search_db.exists():
        duckdb_conn = search.connect(config.search_db)
        index = search.get_index(duckdb_conn)
        click.echo(f"Search index:    {len(index)} pages indexed")
        duckdb_conn.close()
    else:
        click.echo("Search index:    not built")

    conn.close()


@main.command()
@click.option("--project", "-p", default=None, help="Filter by project name or ID.")
@click.option("--date", "-d", default=None, help="Filter by date (YYYY-MM-DD).")
def sessions(project: str | None, date: str | None):
    """List recent sessions with summaries."""
    config = load_config()
    if not config.sessions_db.exists():
        click.echo("Not initialized. Run 'akw init' first.")
        return

    conn = storage.connect(config.sessions_db)

    # Resolve project name to ID if needed
    project_id = None
    if project:
        projects = storage.list_projects(conn)
        for p in projects:
            if p["id"] == project or p["name"] == project:
                project_id = p["id"]
                break
        if project_id is None:
            click.echo(f"Project not found: {project}")
            conn.close()
            return

    results = storage.list_sessions(conn, project_id=project_id, date=date)
    if not results:
        click.echo("No sessions found.")
        conn.close()
        return

    for s in results:
        turns = storage.get_turns(conn, s["id"])
        status = "reviewed" if s["reviewed_at"] else ("ended" if s["ended_at"] else "active")
        click.echo(f"\n[{status}] {s['started_at'][:16]} | {s['agent']} | {s['type']}")
        click.echo(f"  ID: {s['id'][:12]}... | Turns: {len(turns)}")
        if turns:
            click.echo(f"  Last: {turns[-1]['request'][:80]}")

    conn.close()


@main.group()
def session():
    """Session lifecycle commands (used by hooks and scripts)."""
    pass


main.add_command(session)


@session.command("start")
@click.option("--project", "-p", default=None, help="Project name or ID.")
@click.option("--agent", "-a", default="claude", help="Agent name.")
@click.option("--type", "-t", "session_type", default="coding", help="Session type.")
@click.option("--continue", "continue_id", default=None, help="Session ID to continue.")
def session_start(project: str | None, agent: str, session_type: str, continue_id: str | None):
    """Start a new session. Closes any existing open sessions first. Prints session ID to stdout."""
    config = load_config()
    conn = storage.connect(config.sessions_db)

    # Resolve project name to ID, auto-create if not found
    project_id = None
    if project:
        projects = storage.list_projects(conn)
        for p in projects:
            if p["id"] == project or p["name"] == project:
                project_id = p["id"]
                break
        if project_id is None:
            # Auto-register project with the name and current dir as path
            import os
            path = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
            new_project = storage.create_project(conn, project, path)
            project_id = new_project["id"]

    # Close any existing open sessions (orphan cleanup)
    storage.close_orphaned_sessions(conn, older_than_hours=0)

    # Continuation
    if continue_id:
        session = storage.reopen_session(conn, continue_id)
        if session is None:
            click.echo(f"Session not found: {continue_id}", err=True)
            conn.close()
            raise SystemExit(1)
        if project_id or agent:
            storage.update_session_metadata(
                conn, session["id"],
                project_id=project_id, agent=agent, session_type=session_type,
            )
        click.echo(session["id"])
        conn.close()
        return

    # Create new session
    session = storage.create_session(conn, project_id, agent, session_type)
    click.echo(session["id"])
    conn.close()


@session.command("end")
@click.option("--session", "-s", "session_id", default=None, help="Session ID to end (default: most recent open).")
def session_end(session_id: str | None):
    """End a session. If no ID given, ends the most recent open session. Idempotent."""
    config = load_config()
    conn = storage.connect(config.sessions_db)

    if session_id is None:
        session = storage.get_most_recent_open_session(conn)
        if session is None:
            click.echo("No open session to end.", err=True)
            conn.close()
            return
        session_id = session["id"]

    result = storage.end_session(conn, session_id)
    if result:
        click.echo(f"{session_id}")
    conn.close()


@session.command("status")
def session_status():
    """Show the currently active session."""
    config = load_config()
    if not config.sessions_db.exists():
        click.echo("Not initialized. Run 'akw init' first.")
        return

    conn = storage.connect(config.sessions_db)
    session = storage.get_most_recent_open_session(conn)
    if session is None:
        click.echo("No active session.")
    else:
        turns = storage.get_turns(conn, session["id"])
        click.echo(f"Session: {session['id']}")
        click.echo(f"Agent:   {session['agent']}")
        click.echo(f"Type:    {session['type']}")
        click.echo(f"Started: {session['started_at'][:16]}")
        click.echo(f"Project: {session['project_id'] or '(none)'}")
        click.echo(f"Turns:   {len(turns)}")
    conn.close()


@session.command("context")
def session_context():
    """Print recent session context summary (used by hooks)."""
    config = load_config()
    if not config.sessions_db.exists():
        return

    conn = storage.connect(config.sessions_db)
    current = storage.get_most_recent_open_session(conn)
    exclude_id = current["id"] if current else ""
    _print_session_context(conn, exclude_id)
    conn.close()


@session.command("prompt")
def session_prompt():
    """Save user prompt from UserPromptSubmit hook. Paired with next Stop response."""
    import json as json_mod
    import sys

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


@session.command("turn")
@click.option("--batch-size", "-b", default=10, help="Flush after this many buffered turns.")
def session_turn(batch_size: int):
    """Buffer a turn from Stop hook. Pairs with pending user prompt. Flushes every N turns."""
    import json as json_mod
    import sys

    config = load_config()
    conn = storage.connect(config.sessions_db)

    session = storage.get_most_recent_open_session(conn)
    if session is None:
        conn.close()
        return

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

    # Read pending user prompt (saved by UserPromptSubmit hook)
    prompt_file = config.data_dir / "pending_prompt.txt"
    user_prompt = ""
    if prompt_file.exists():
        user_prompt = prompt_file.read_text()
        prompt_file.unlink(missing_ok=True)

    # Buffer to temp file
    buffer_file = config.data_dir / "turn_buffer.jsonl"
    turn_entry = json_mod.dumps({
        "request": user_prompt or "(no prompt captured)",
        "response": assistant_msg,
    })
    with open(buffer_file, "a") as f:
        f.write(turn_entry + "\n")

    # Count buffered turns
    with open(buffer_file) as f:
        lines = f.readlines()

    if len(lines) >= batch_size:
        _flush_turn_buffer(conn, session["id"], buffer_file)

    conn.close()


@session.command("flush")
def session_flush():
    """Flush any buffered turns to the database. Called by SessionEnd hook."""
    import json as json_mod

    config = load_config()
    conn = storage.connect(config.sessions_db)

    session = storage.get_most_recent_open_session(conn)
    if session is None:
        conn.close()
        return

    buffer_file = config.data_dir / "turn_buffer.jsonl"
    if buffer_file.exists():
        _flush_turn_buffer(conn, session["id"], buffer_file)

    conn.close()


def _flush_turn_buffer(conn, session_id: str, buffer_file: Path) -> None:
    """Read buffered turns from file, write to DB, and clear the buffer."""
    import json as json_mod
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
        storage.create_turns(conn, session_id, turns)

    # Clear buffer
    buffer_file.unlink(missing_ok=True)


@session.command("list")
@click.option("--recent", "-r", is_flag=True, help="Show only recent sessions (last 10).")
@click.option("--project", "-p", default=None, help="Filter by project name or ID.")
def session_list(recent: bool, project: str | None):
    """List sessions for continuation lookup."""
    config = load_config()
    if not config.sessions_db.exists():
        click.echo("Not initialized. Run 'akw init' first.")
        return

    conn = storage.connect(config.sessions_db)

    project_id = None
    if project:
        projects = storage.list_projects(conn)
        for p in projects:
            if p["id"] == project or p["name"] == project:
                project_id = p["id"]
                break

    results = storage.list_sessions(conn, project_id=project_id)
    if recent:
        results = results[:10]

    if not results:
        click.echo("No sessions found.")
        conn.close()
        return

    for s in results:
        turns = storage.get_turns(conn, s["id"])
        state = "reviewed" if s["reviewed_at"] else ("ended" if s["ended_at"] else "active")
        click.echo(f"[{state}] {s['started_at'][:16]} | {s['agent']} | {s['type']} | {s['id'][:12]}... | {len(turns)} turns")

    conn.close()


@main.command("search")
@click.argument("query")
@click.option("--tier", "-t", default=None, help="Filter by tier: knowledge or skill.")
def search_cmd(query: str, tier: str | None):
    """Search memory from the terminal."""
    config = load_config()
    if not config.search_db.exists():
        click.echo("Search index not built. Run 'akw init' first.")
        return

    duckdb_conn = search.connect(config.search_db)

    # Sync before searching
    if config.memory_dir.exists():
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
def review():
    """Run daily review — synthesize pending session drafts into knowledge drafts using LLM."""
    import os
    from agent_knowledge.core.config import load_config

    config = load_config()
    if not config.sessions_db.exists():
        click.echo("Not initialized. Run 'akw init' first.")
        return

    conn = storage.connect(config.sessions_db)

    # Gather pending items
    orphans = storage.get_sessions_needing_drafts(conn)
    unreviewed = storage.get_unreviewed_sessions(conn, exclude_today=False)

    if not orphans and not unreviewed:
        click.echo("No pending sessions to review.")
        conn.close()
        return

    # Check API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        click.echo("ANTHROPIC_API_KEY not set. Export it or set in .env file.", err=True)
        conn.close()
        return

    try:
        import anthropic
    except ImportError:
        click.echo("anthropic package not installed. Run: uv pip install agent-knowledge[llm]", err=True)
        conn.close()
        return
    client = anthropic.Anthropic(api_key=api_key)

    # Collect all session content for review
    session_contents = []
    session_ids = []

    for session in orphans:
        turns = storage.get_turns(conn, session["id"])
        if turns:
            turn_text = "\n".join(f"- Request: {t['request']}\n  Response: {t['response']}" for t in turns)
            session_contents.append(f"## Session: {session['agent']} ({session['type']}) — {session['started_at'][:10]}\n{turn_text}")
            session_ids.append(session["id"])

    for session in unreviewed:
        draft_path = storage.get_session_draft_path(conn, session["id"])
        if draft_path:
            try:
                content = memory.read_page(config.memory_dir, draft_path)
                session_contents.append(f"## Session Draft: {session['agent']} ({session['type']}) — {session['started_at'][:10]}\n{content}")
                session_ids.append(session["id"])
            except FileNotFoundError:
                turns = storage.get_turns(conn, session["id"])
                if turns:
                    turn_text = "\n".join(f"- Request: {t['request']}\n  Response: {t['response']}" for t in turns)
                    session_contents.append(f"## Session: {session['agent']} ({session['type']}) — {session['started_at'][:10]}\n{turn_text}")
                    session_ids.append(session["id"])

    if not session_contents:
        click.echo("No session content to review.")
        conn.close()
        return

    click.echo(f"Reviewing {len(session_contents)} sessions...")

    # Call LLM
    all_content = "\n\n".join(session_contents)
    prompt = f"""You are reviewing AI agent session logs to extract reusable knowledge.

Analyze the following session data and:
1. Identify key decisions, patterns, and learnings
2. Detect cross-session patterns if multiple sessions exist
3. Write knowledge draft pages in markdown format

For each knowledge draft, output it in this format:
---DRAFT---
title: <title>
tags: <comma-separated tags>
summary: <one-line summary>
---
<markdown content>
---END---

Focus on actionable, reusable knowledge. Skip trivial or one-off details.

Session data:
{all_content}"""

    try:
        response = client.messages.create(
            model=config.llm.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        llm_output = response.content[0].text
    except Exception as e:
        click.echo(f"LLM error: {e}", err=True)
        conn.close()
        return

    # Parse drafts from LLM output
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    drafts_written = 0

    parts = llm_output.split("---DRAFT---")
    for part in parts[1:]:  # Skip text before first draft
        if "---END---" not in part:
            continue
        draft_content = part.split("---END---")[0].strip()

        # Parse header
        lines = draft_content.split("\n")
        title = "Untitled"
        tags_str = ""
        summary_line = ""
        content_start = 0

        for i, line in enumerate(lines):
            if line.startswith("title:"):
                title = line.split(":", 1)[1].strip()
            elif line.startswith("tags:"):
                tags_str = line.split(":", 1)[1].strip()
            elif line.startswith("summary:"):
                summary_line = line.split(":", 1)[1].strip()
            elif line == "---":
                content_start = i + 1
                break

        md_content = "\n".join(lines[content_start:]).strip()
        if not md_content:
            continue

        # Write draft
        slug = title.lower().replace(" ", "-")[:50]
        draft_path = f"drafts/knowledge/{today}-{slug}.md"
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]

        try:
            from agent_knowledge.core import sanitizer
            md_content, _ = sanitizer.redact(md_content)

            page_content = ""
            if tags or summary_line:
                page_content += "---\n"
                if tags:
                    page_content += f"tags: {tags}\n"
                if summary_line:
                    page_content += f"summary: {summary_line}\n"
                page_content += "---\n\n"
            page_content += f"# {title}\n\n{md_content}"

            memory.create_page(config.memory_dir, draft_path, page_content)
            storage.create_memory_edit(conn, draft_path, "draft", "create", summary_line or f"Review draft: {title}")
            click.echo(f"  Draft: {draft_path}")
            drafts_written += 1
        except FileExistsError:
            click.echo(f"  Skipped (exists): {draft_path}")

    # Write review report
    report_path = f"drafts/reviews/{today}.md"
    report = f"# Daily Review — {today}\n\nSessions reviewed: {len(session_ids)}\nDrafts generated: {drafts_written}\n\n{llm_output}"
    try:
        memory.create_page(config.memory_dir, report_path, report)
        click.echo(f"  Report: {report_path}")
    except FileExistsError:
        click.echo(f"  Report exists: {report_path}")

    # Mark sessions as reviewed and clean up session drafts
    for sid in session_ids:
        storage.set_session_reviewed(conn, sid)
        draft_path = storage.get_session_draft_path(conn, sid)
        if draft_path:
            try:
                memory.delete_page(config.memory_dir, draft_path)
            except FileNotFoundError:
                pass

    click.echo(f"\nReview complete: {len(session_ids)} sessions reviewed, {drafts_written} drafts written.")
    conn.close()


@main.command()
@click.option("--older-than", default=365, help="Purge sessions older than N days (default: 365).")
def purge(older_than: int):
    """Delete reviewed sessions and turns older than retention period."""
    config = load_config()
    if not config.sessions_db.exists():
        click.echo("Not initialized. Run 'akw init' first.")
        return

    conn = storage.connect(config.sessions_db)

    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than)).strftime("%Y-%m-%dT%H:%M:%SZ")

    all_sessions = storage.list_sessions(conn)
    to_purge = [
        s for s in all_sessions
        if s["reviewed_at"] is not None and s["started_at"] < cutoff
    ]

    if not to_purge:
        click.echo(f"No reviewed sessions older than {older_than} days to purge.")
        conn.close()
        return

    purged_turns = 0
    for session in to_purge:
        turns = storage.get_turns(conn, session["id"])
        purged_turns += len(turns)

        draft_path = storage.get_session_draft_path(conn, session["id"])
        if draft_path:
            try:
                memory.delete_page(config.memory_dir, draft_path)
            except FileNotFoundError:
                pass

        conn.execute("DELETE FROM turns WHERE session_id = ?", (session["id"],))
        conn.execute("DELETE FROM memory_edits WHERE session_id = ?", (session["id"],))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session["id"],))

    conn.commit()
    click.echo(f"Purged {len(to_purge)} sessions, {purged_turns} turns (older than {older_than} days).")
    conn.close()


@main.command()
def reindex():
    """Rebuild DuckDB search index from /memory/knowledge and /memory/skills."""
    config = load_config()
    if not config.memory_dir.exists():
        click.echo("Not initialized. Run 'akw init' first.")
        return

    duckdb_conn = search.connect(config.search_db)
    count = search.sync_from_files(duckdb_conn, config.memory_dir)
    click.echo(f"Indexed {count} pages.")
    duckdb_conn.close()


def _print_session_context(conn, exclude_session_id: str) -> None:
    """Print recent session summaries to stderr (Claude Code displays hook stderr)."""
    import sys

    recent = storage.list_sessions(conn)[:6]  # last 6 sessions
    recent = [s for s in recent if s["id"] != exclude_session_id]
    if not recent:
        return

    lines = ["[agent-knowledge] recent sessions:"]
    for s in recent[:5]:
        turns = storage.get_turns(conn, s["id"])
        state = "reviewed" if s["reviewed_at"] else ("ended" if s["ended_at"] else "active")
        date = s["started_at"][:16]
        turn_count = len(turns)

        # Build a brief summary from last turn
        summary = ""
        if turns:
            last_req = turns[-1]["request"][:80]
            summary = f" — {last_req}"

        lines.append(f"  [{state}] {date} | {s['agent']} | {s['type']} | {turn_count} turns{summary}")

        # Show session draft if it exists
        draft_path = storage.get_session_draft_path(conn, s["id"])
        if draft_path:
            try:
                content = memory.read_page(
                    __import__("agent_knowledge.core.config", fromlist=["load_config"]).load_config().memory_dir,
                    draft_path,
                )
                # Show first 2 non-empty, non-frontmatter lines as highlight
                content_lines = [
                    l.strip() for l in content.split("\n")
                    if l.strip() and not l.startswith("---") and not l.startswith("tags:") and not l.startswith("summary:")
                ]
                for cl in content_lines[:2]:
                    lines.append(f"    {cl[:100]}")
            except FileNotFoundError:
                pass

    # Stats
    all_sessions = storage.list_sessions(conn)
    pending = sum(1 for s in all_sessions if s["ended_at"] and not s["reviewed_at"])
    if pending:
        lines.append(f"  ({pending} session(s) pending review)")

    click.echo("\n".join(lines))


