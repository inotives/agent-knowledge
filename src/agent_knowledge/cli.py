"""CLI entry point — admin and inspection commands for agent-knowledge."""

from __future__ import annotations

import subprocess
import sys
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

    # Run dbmate migrations
    migrations_dir = _find_migrations_dir()
    if migrations_dir is None:
        click.echo("Warning: db/migrations/ not found, skipping migrations.", err=True)
    else:
        dbmate_bin = _find_dbmate()
        db_url = f"sqlite:{config.sessions_db}"
        try:
            subprocess.run(
                [dbmate_bin, "--url", db_url, "--migrations-dir", str(migrations_dir), "--no-dump-schema", "up"],
                check=True,
            )
            click.echo(f"Database: {config.sessions_db}")
        except FileNotFoundError:
            click.echo("Warning: dbmate not found.", err=True)
        except subprocess.CalledProcessError as e:
            click.echo(f"Migration error: {e}", err=True)
            sys.exit(1)

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

    import anthropic
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


def _find_dbmate() -> str:
    """Find the dbmate binary — check PATH, then look next to this Python executable."""
    import shutil
    found = shutil.which("dbmate")
    if found:
        return found
    # When installed via uv tool, dbmate is next to the Python binary
    bin_dir = Path(sys.executable).parent
    candidate = bin_dir / "dbmate"
    if candidate.exists():
        return str(candidate)
    return "dbmate"


def _find_migrations_dir() -> Path | None:
    """Find db/migrations/ relative to the package or cwd."""
    candidates = [
        Path.cwd() / "db" / "migrations",
        Path(__file__).parent.parent.parent / "db" / "migrations",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None
