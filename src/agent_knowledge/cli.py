"""CLI entry point — admin and inspection commands for agent-knowledge.

EP-00005: groups replace sessions; capture-only scope. The legacy `akw review`
LLM-synthesis flow has been removed — synthesis is human work performed in the
memory folder with whatever tools the curator chooses.
"""

from __future__ import annotations

import json as json_mod
import os
import re
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


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "project"


def _read_env_project(working_dir: str | None) -> str | None:
    if not working_dir:
        return None
    env_path = Path(working_dir) / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("AKW_PROJECT="):
            continue
        value = line.split("=", 1)[1].strip().strip("\"'")
        return value or None
    return None


def _is_path_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _display_path(path: str | None) -> str:
    if not path:
        return ""
    expanded = Path(path).expanduser()
    try:
        home = Path.home().resolve()
        resolved = expanded.resolve()
        if resolved == home:
            return "~"
        return f"~/{resolved.relative_to(home)}"
    except (OSError, ValueError):
        return path


def _ensure_project_entity_page(config, conn, project: dict) -> None:
    """Create a project entity page when auto-registering a project."""
    slug = _slugify(project["name"])
    rel_path = f"2_knowledges/entities/projects/{slug}.md"
    full_path = config.memory_dir / rel_path
    if full_path.exists():
        return
    content = (
        "---\n"
        f"summary: Project entity for {project['name']}\n"
        "tags: [project]\n"
        f"project_id: {project['id']}\n"
        f"path: {project.get('path', '')}\n"
        f"created_at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        "---\n\n"
        f"# {project['name']}\n\n"
        f"- Project ID: `{project['id']}`\n"
        f"- Path: `{project.get('path', '')}`\n"
    )
    memory.create_page(config.memory_dir, rel_path, content)
    storage.create_memory_edit(
        conn,
        rel_path,
        "knowledge",
        "create",
        f"Created project entity for {project['name']}",
    )


def _project_session_folder_name(project: dict) -> str:
    metadata = project.get("metadata") or {}
    if isinstance(metadata, dict) and metadata.get("session_folder"):
        return _slugify(str(metadata["session_folder"]))
    return _slugify(project.get("name") or project.get("id") or "project")


def _project_session_folder_path(project: dict) -> str:
    return f"{paths.SESSIONS_DIR}/{_project_session_folder_name(project)}"


def _ensure_project_session_folder(config, project: dict, create: bool) -> str:
    rel_path = _project_session_folder_path(project)
    full_path = config.memory_dir / rel_path
    if full_path.exists():
        if not full_path.is_dir():
            click.echo(f"Project session path exists but is not a directory: {rel_path}", err=True)
            raise SystemExit(1)
        return rel_path
    if create:
        full_path.mkdir(parents=True, exist_ok=True)
        return rel_path

    click.echo(
        "\n".join([
            f"Project session folder missing: {rel_path}",
            f"Default folder name: {_project_session_folder_name(project)}",
            "Create it, then start again:",
            f"  mkdir -p {rel_path}",
            "Or let akw create it:",
            "  akw session start --create-project-folder",
        ]),
        err=True,
    )
    raise SystemExit(1)


def _resolve_project(config, conn, project: str | None, working_dir: str | None) -> dict:
    """Resolve or create the active project for a session."""
    wd = working_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    explicit = _read_env_project(wd) or project
    projects = storage.list_projects(conn)

    if explicit:
        for p in projects:
            if p["id"] == explicit or p["name"] == explicit:
                return p

    wd_path = Path(wd)
    matches = [
        p for p in projects
        if p.get("path") and _is_path_within(wd_path, Path(p["path"]))
    ]
    if matches:
        return max(matches, key=lambda p: len(str(Path(p["path"]).resolve())))

    name = explicit or wd_path.resolve().name
    project_obj = storage.create_project(conn, name, _display_path(str(wd_path.resolve())))
    _ensure_project_entity_page(config, conn, project_obj)
    return project_obj


def _session_summary_payload(config, row: dict, include_content: bool = True) -> dict:
    payload = {
        "session_id": row["id"],
        "path": row.get("draft_path"),
        "title": row.get("title"),
        "summary": row.get("summary"),
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "ended_at": row.get("ended_at"),
        "metadata": row.get("metadata") or {},
    }
    if include_content and row.get("draft_path"):
        try:
            payload["content"] = memory.read_page(config.memory_dir, row["draft_path"])
        except FileNotFoundError:
            payload["content"] = None
    return payload


def _parse_simple_frontmatter(content: str) -> dict:
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    out: dict = {}
    for line in parts[1].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip().strip("\"'")
    return out


def _curated_project_sessions_dirs(project: dict) -> list[str]:
    candidates = [
        f"2_knowledges/entities/projects/{project['id']}/sessions",
        f"2_knowledges/entities/projects/{_slugify(project['name'])}/sessions",
    ]
    seen: set[str] = set()
    out: list[str] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
    return out


def _session_ids_from_frontmatter_value(value: str | None) -> list[str]:
    if not value:
        return []
    return re.findall(r"[0-9a-fA-F]{12,}", value)


def _session_payload_from_file(
    config,
    project: dict,
    md_file: Path,
    source: str,
    include_content: bool = True,
) -> dict:
    rel_path = str(md_file.relative_to(config.memory_dir))
    content = md_file.read_text(encoding="utf-8")
    fm = _parse_simple_frontmatter(content)
    title = _first_heading(content) or md_file.stem
    updated_at = datetime.fromtimestamp(
        md_file.stat().st_mtime,
        tz=timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "session_id": fm.get("session_id") or md_file.stem,
        "session_ids": _session_ids_from_frontmatter_value(fm.get("session_ids")),
        "path": rel_path,
        "title": title,
        "summary": fm.get("summary", ""),
        "created_at": fm.get("created_at") or updated_at,
        "started_at": fm.get("started_at"),
        "ended_at": fm.get("ended_at") or fm.get("created_at") or updated_at,
        "metadata": {
            "project_id": fm.get("project_id") or project["id"],
            "project_name": fm.get("project_name") or project["name"],
            "source": source,
        },
    }
    if include_content:
        payload["content"] = content
    return payload


def _draft_project_session_payloads(config, project: dict, include_content: bool = True) -> list[dict]:
    session_folder = _project_session_folder_path(project)
    full_dir = config.memory_dir / session_folder
    if not full_dir.exists() or not full_dir.is_dir():
        return []
    return [
        _session_payload_from_file(config, project, md_file, "draft", include_content)
        for md_file in sorted(full_dir.rglob("*.md"))
    ]


def _curated_session_payloads(config, project: dict, include_content: bool = True) -> list[dict]:
    payloads: list[dict] = []
    for rel_dir in _curated_project_sessions_dirs(project):
        full_dir = config.memory_dir / rel_dir
        if not full_dir.exists() or not full_dir.is_dir():
            continue
        payloads.extend(
            _session_payload_from_file(config, project, md_file, "knowledge", include_content)
            for md_file in sorted(full_dir.rglob("*.md"))
        )
    return payloads


def _recent_session_payloads(
    config,
    conn,
    project: dict,
    limit: int,
    exclude_session_id: str | None = None,
) -> list[dict]:
    draft_rows = storage.list_recent_session_summaries(
        conn,
        project_id=project["id"],
        limit=limit,
        exclude_session_id=exclude_session_id,
    )
    row_payloads = [
        _session_summary_payload(config, row, include_content=True)
        for row in draft_rows
    ]
    row_payloads = [
        payload for payload in row_payloads
        if payload.get("content") is not None
    ]
    for payload in row_payloads:
        payload.setdefault("metadata", {})
        payload["metadata"].setdefault("source", "draft")

    payloads = row_payloads + _draft_project_session_payloads(config, project, include_content=True)
    payloads.extend(_curated_session_payloads(config, project, include_content=True))
    by_path: dict[str, dict] = {}
    for payload in payloads:
        path = payload.get("path")
        if path:
            by_path[path] = payload
    payloads = list(by_path.values())

    merged_ids: dict[str, str] = {}
    for payload in payloads:
        for sid in payload.get("session_ids") or []:
            merged_ids[sid] = payload.get("path", "")

    payloads = [
        p for p in payloads
        if (
            (not exclude_session_id or p.get("session_id") != exclude_session_id)
            and (
                p.get("session_id") not in merged_ids
                or merged_ids[p.get("session_id")] == p.get("path")
            )
        )
    ]
    payloads.sort(
        key=lambda p: p.get("ended_at") or p.get("created_at") or p.get("started_at") or "",
        reverse=True,
    )
    return payloads[:limit]


def _yaml_scalar(value: str | None) -> str:
    return json_mod.dumps(value or "")


def _build_session_summary_page(
    content: str,
    *,
    one_line_summary: str,
    project: dict,
    session: dict,
    ended_at: str,
    draft_created_at: str,
) -> str:
    return (
        "---\n"
        f"summary: {_yaml_scalar(one_line_summary)}\n"
        "tags: [session]\n"
        f"project_id: {_yaml_scalar(project['id'])}\n"
        f"project_name: {_yaml_scalar(project['name'])}\n"
        f"agent: {_yaml_scalar(session.get('agent'))}\n"
        f"session_id: {_yaml_scalar(session['id'])}\n"
        f"started_at: {_yaml_scalar(session.get('started_at'))}\n"
        f"ended_at: {_yaml_scalar(ended_at)}\n"
        f"working_dir: {_yaml_scalar(session.get('working_dir'))}\n"
        f"created_at: {_yaml_scalar(draft_created_at)}\n"
        "---\n\n"
        f"{content.strip()}\n"
    )


def _active_session_id_from_env() -> str | None:
    return os.environ.get("AKW_SESSION_ID") or os.environ.get("AKW_GROUP_ID")


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


# --- session lifecycle subgroup (used by hooks and scripts) ---

@main.group("session")
def session_group():
    """Session lifecycle and summary commands."""


main.add_command(session_group)


@main.group()
def group():
    """Deprecated legacy alias for session lifecycle commands."""
    pass


main.add_command(group)


def _session_start_impl(
    group_id: str | None,
    project: str | None,
    agent: str,
    working_dir: str | None,
    json_out: bool,
    create_project_folder: bool,
) -> None:
    config = load_config()
    memory.ensure_memory_dirs(config.memory_dir)
    conn = storage.connect(config.sessions_db)

    wd = working_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    project_obj = _resolve_project(config, conn, project, wd)
    session_folder = _ensure_project_session_folder(config, project_obj, create_project_folder)
    display_wd = _display_path(wd)

    md: dict = {"agent": agent, "project_id": project_obj["id"], "project_name": project_obj["name"]}
    if display_wd:
        md["working_dir"] = display_wd
    md["session_folder"] = session_folder

    result = storage.start_session(
        conn,
        session_id=group_id,
        project_id=project_obj["id"],
        project_name=project_obj["name"],
        agent=agent,
        working_dir=display_wd,
        metadata=md,
    )
    if json_out:
        payload: dict = {
            "session_id": result["id"],
            "group_id": result["id"],
            "started_at": result["started_at"],
            "segment_start_at": result["started_at"],
            "project": {
                "id": project_obj["id"],
                "name": project_obj["name"],
                "path": _display_path(project_obj.get("path")),
                "session_folder": session_folder,
            },
            "latest_summaries": _recent_session_payloads(
                config,
                conn,
                project_obj,
                limit=5,
                exclude_session_id=result["id"],
            ),
        }
        _emit_json(payload)
    else:
        click.echo(result["id"])
    conn.close()


@session_group.command("start")
@click.option("--session-id", "--group-id", "-g", "group_id", default=None, help="Session ID to use (omit for new session).")
@click.option("--project", "-p", default=None, help="Project name or ID.")
@click.option("--agent", "-a", default="claude", help="Agent name.")
@click.option("--working-dir", default=None, help="Working directory path metadata.")
@click.option("--create-project-folder", is_flag=True, help="Create 1_drafts/sessions/<project> if missing.")
@click.option("--json", "json_out", is_flag=True, help="Emit session metadata and latest project summaries as JSON.")
def session_start(
    group_id: str | None,
    project: str | None,
    agent: str,
    working_dir: str | None,
    create_project_folder: bool,
    json_out: bool,
):
    """Start a new session. Prints session_id to stdout for hook capture."""
    _session_start_impl(group_id, project, agent, working_dir, json_out, create_project_folder)


@group.command("start")
@click.option("--group-id", "-g", default=None, help="Deprecated alias for --session-id.")
@click.option("--project", "-p", default=None, help="Project name or ID.")
@click.option("--agent", "-a", default="claude", help="Agent name.")
@click.option("--working-dir", default=None, help="Working directory path metadata.")
@click.option("--create-project-folder", is_flag=True, help="Create 1_drafts/sessions/<project> if missing.")
@click.option("--json", "json_out", is_flag=True, help="Emit session metadata and latest project summaries as JSON.")
def group_start(
    group_id: str | None,
    project: str | None,
    agent: str,
    working_dir: str | None,
    create_project_folder: bool,
    json_out: bool,
):
    """Deprecated alias for `akw session start`."""
    _session_start_impl(group_id, project, agent, working_dir, json_out, create_project_folder)


@group.command("end")
@click.option("--group-id", "-g", "group_id", default=None, help="Group ID to end (default: most recent open).")
def group_end(group_id: str | None):
    """Deprecated alias guard. Use `akw session close` with a summary."""
    click.echo(
        "Session summary required. Run `akw session close --content-file <summary.md>` before ending.",
        err=True,
    )
    raise SystemExit(1)


def _session_close_impl(
    session_id: str | None,
    content: str | None,
    content_file: str | None,
    summary: str | None,
    json_out: bool,
) -> None:
    if content is None and content_file is None:
        click.echo("Provide --content or --content-file.", err=True)
        raise SystemExit(2)
    if content_file:
        content = Path(content_file).read_text(encoding="utf-8")
    assert content is not None

    config = load_config()
    memory.ensure_memory_dirs(config.memory_dir)
    conn = storage.connect(config.sessions_db)

    sid = session_id or _active_session_id_from_env()
    session = storage.get_open_session(conn, sid) if sid else storage.get_open_session(conn, latest=True)
    if session is None:
        click.echo("No open session to close.", err=True)
        conn.close()
        raise SystemExit(1)

    project = storage.get_project(conn, session["project_id"])
    if project is None:
        project = {
            "id": session["project_id"],
            "name": session["project_name"],
            "path": session.get("working_dir") or "",
        }
    session_folder = _ensure_project_session_folder(config, project, create=True)

    redacted, findings = sanitizer.redact(content)
    title = _first_heading(redacted) or "Session Summary"
    one_line = summary or next(
        (
            line.strip()
            for line in redacted.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ),
        title,
    )
    one_line = one_line[:200]
    ended_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    created_at = ended_at
    draft_path = (
        f"{session_folder}/"
        f"{session['id'][:8]}-{paths.compact_iso(ended_at)}.md"
    )
    page_content = _build_session_summary_page(
        redacted,
        one_line_summary=one_line,
        project=project,
        session=session,
        ended_at=ended_at,
        draft_created_at=created_at,
    )

    try:
        memory.create_page(config.memory_dir, draft_path, page_content)
    except FileExistsError:
        click.echo(f"Session summary already exists: {draft_path}", err=True)
        conn.close()
        raise SystemExit(1)

    storage.create_memory_edit(
        conn,
        draft_path,
        "draft",
        "create",
        one_line,
        group_id=session["id"],
    )
    storage.upsert_draft_state(
        conn,
        draft_path=draft_path,
        group_id=session["id"],
        segment_start_at=session["started_at"],
        segment_end_at=ended_at,
    )
    closed = storage.close_session(
        conn,
        session["id"],
        draft_path=draft_path,
        title=title,
        summary=one_line,
        ended_at=ended_at,
        metadata={"redactions": findings} if findings else {},
    )
    assert closed is not None

    payload = _session_summary_payload(config, closed, include_content=True)
    if json_out:
        _emit_json(payload)
    else:
        click.echo(f"Closed session: {session['id']}")
        click.echo(f"Summary: {draft_path}")
    conn.close()


@session_group.command("close")
@click.option("--session-id", default=None, help="Session ID to close (default: active/latest open).")
@click.option("--content", "content", default=None, help="Full markdown summary content.")
@click.option("--content-file", "content_file", type=click.Path(exists=True, dir_okay=False), default=None, help="Read summary content from a file.")
@click.option("--summary", "summary", default=None, help="One-line summary for frontmatter and recent lists.")
@click.option("--json", "json_out", is_flag=True, help="Emit created draft metadata as JSON.")
def session_close(
    session_id: str | None,
    content: str | None,
    content_file: str | None,
    summary: str | None,
    json_out: bool,
):
    """Close the active session by writing one durable summary draft."""
    _session_close_impl(session_id, content, content_file, summary, json_out)


@group.command("close")
@click.option("--session-id", "--group-id", "session_id", default=None, help="Session/group ID to close (default: active/latest open).")
@click.option("--content", "content", default=None, help="Full markdown summary content.")
@click.option("--content-file", "content_file", type=click.Path(exists=True, dir_okay=False), default=None, help="Read summary content from a file.")
@click.option("--summary", "summary", default=None, help="One-line summary for frontmatter and recent lists.")
@click.option("--json", "json_out", is_flag=True, help="Emit created draft metadata as JSON.")
def group_close(
    session_id: str | None,
    content: str | None,
    content_file: str | None,
    summary: str | None,
    json_out: bool,
):
    """Deprecated alias for `akw session close`."""
    _session_close_impl(session_id, content, content_file, summary, json_out)


def _session_status_impl(json_out: bool) -> None:
    config = load_config()
    if not config.sessions_db.exists():
        if json_out:
            _emit_json({"error": "Not initialized. Run 'akw init' first."})
        else:
            click.echo("Not initialized. Run 'akw init' first.")
        return

    conn = storage.connect(config.sessions_db)
    env_session_id = _active_session_id_from_env()
    session = storage.get_open_session(conn, env_session_id) if env_session_id else None
    if session is None:
        session = storage.get_open_session(conn, latest=True)
    if session is None:
        if json_out:
            _emit_json({"session_id": None, "group_id": None, "segment_start_at": None, "segment_turn_count": 0})
        else:
            click.echo("No active session.")
        conn.close()
        return

    if json_out:
        _emit_json({
            "session_id": session["id"],
            "group_id": session["id"],
            "segment_start_at": session["started_at"],
            "segment_turn_count": 0,
            "agent": session.get("agent"),
            "project_id": session.get("project_id"),
            "project_name": session.get("project_name"),
            "latest_at": session["started_at"],
        })
        conn.close()
        return

    click.echo(f"Session:         {session['id']}")
    click.echo(f"Agent:           {session.get('agent', '?')}")
    click.echo(f"Project:         {session.get('project_name') or session.get('project_id')}")
    click.echo(f"Started:         {session['started_at'][:19]}")
    click.echo("Summary:         not saved")
    conn.close()


@session_group.command("status")
@click.option("--json", "json_out", is_flag=True, help="Emit status as JSON.")
def session_status(json_out: bool):
    """Show the most recent open session."""
    _session_status_impl(json_out)


@group.command("status")
@click.option("--json", "json_out", is_flag=True, help="Emit status as JSON.")
def group_status(json_out: bool):
    """Deprecated alias for `akw session status`."""
    _session_status_impl(json_out)


def _session_recent_impl(project: str | None, working_dir: str | None, limit: int, json_out: bool) -> None:
    config = load_config()
    memory.ensure_memory_dirs(config.memory_dir)
    conn = storage.connect(config.sessions_db)
    wd = working_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    project_obj = _resolve_project(config, conn, project, wd)
    env_session_id = _active_session_id_from_env()
    current = storage.get_open_session(conn, env_session_id) if env_session_id else None
    payload = _recent_session_payloads(
        config,
        conn,
        project_obj,
        limit=limit,
        exclude_session_id=current["id"] if current else None,
    )

    if json_out:
        _emit_json(payload)
    else:
        if not payload:
            click.echo("No recent session summaries.")
        for item in payload:
            click.echo(f"{item['ended_at']}  {item['title']}  {item['path']}")
            if item.get("content"):
                click.echo(item["content"])
    conn.close()


@session_group.command("recent")
@click.option("--project", "-p", default=None, help="Project name or ID. Defaults to resolved current project.")
@click.option("--working-dir", default=None, help="Working directory for project resolution.")
@click.option("--limit", default=5, show_default=True, help="Maximum summaries to return.")
@click.option("--json", "json_out", is_flag=True, help="Emit recent summaries as JSON.")
def session_recent(project: str | None, working_dir: str | None, limit: int, json_out: bool):
    """Show recent closed session summaries for the current project."""
    _session_recent_impl(project, working_dir, limit, json_out)


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
    """Deprecated no-op. Raw prompt capture is disabled."""
    click.echo("Deprecated: raw prompt capture is disabled. Use `akw session close` at session end.", err=True)


@group.command("turn")
@click.option("--batch-size", "-b", default=10, help="Flush after this many buffered turns.")
def group_turn(batch_size: int):
    """Deprecated no-op. Raw turn capture is disabled."""
    click.echo("Deprecated: raw turn capture is disabled. Use `akw session close` at session end.", err=True)


@group.command("flush")
def group_flush():
    """Deprecated no-op. Raw turn buffers are disabled."""
    click.echo("Deprecated: raw turn buffers are disabled. Use `akw session close` at session end.", err=True)


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
