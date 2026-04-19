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
        db_url = f"sqlite:{config.sessions_db}"
        try:
            subprocess.run(
                ["dbmate", "--url", db_url, "--migrations-dir", str(migrations_dir), "--no-dump-schema", "up"],
                check=True,
            )
            click.echo(f"Database: {config.sessions_db}")
        except FileNotFoundError:
            click.echo("Warning: dbmate not found. Run 'uv sync' to install dev dependencies.", err=True)
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
