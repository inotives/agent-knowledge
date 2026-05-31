"""CLI smoke tests for the EP-00010 commands (memory/project/maintain + --json flags)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from agent_knowledge.cli import main


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Point AKW_DATA_DIR at a tmp dir and run `akw init` so commands can operate."""
    data_dir = tmp_path / "akw-data"
    monkeypatch.setenv("AKW_DATA_DIR", str(data_dir))
    runner = CliRunner()
    init = runner.invoke(main, ["init"])
    assert init.exit_code == 0, init.output
    return runner, data_dir


# --- memory subcommands ---

def test_memory_create_and_read_round_trip(cli_env):
    runner, _ = cli_env
    create = runner.invoke(
        main,
        [
            "memory", "create",
            "--path", "1_drafts/sessions/test-20260504-1200.md",
            "--title", "Test session",
            "--content", "Body of the test draft.",
            "--summary", "Smoke test",
        ],
    )
    assert create.exit_code == 0, create.output
    assert "Created" in create.output

    read = runner.invoke(main, ["memory", "read", "1_drafts/sessions/test-20260504-1200.md"])
    assert read.exit_code == 0
    assert "Body of the test draft." in read.output
    assert "# Test session" in read.output


def test_memory_create_rejects_curated_tier(cli_env):
    runner, _ = cli_env
    result = runner.invoke(
        main,
        [
            "memory", "create",
            "--path", "2_knowledges/architecture/foo.md",
            "--title", "Should reject",
            "--content", "x",
        ],
    )
    assert result.exit_code == 1
    assert "2_knowledges" in result.output


def test_memory_create_requires_content_source(cli_env):
    runner, _ = cli_env
    result = runner.invoke(main, ["memory", "create", "--path", "1_drafts/sessions/x.md", "--title", "x"])
    assert result.exit_code == 2
    assert "--content" in result.output


def test_memory_read_missing_returns_error(cli_env):
    runner, _ = cli_env
    result = runner.invoke(main, ["memory", "read", "1_drafts/sessions/does-not-exist.md"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_memory_update_round_trip(cli_env):
    runner, _ = cli_env
    runner.invoke(main, [
        "memory", "create",
        "--path", "1_drafts/sessions/up.md",
        "--title", "Up",
        "--content", "v1",
    ])
    update = runner.invoke(main, [
        "memory", "update",
        "1_drafts/sessions/up.md",
        "--content", "v2",
        "--summary", "bumped",
    ])
    assert update.exit_code == 0
    read = runner.invoke(main, ["memory", "read", "1_drafts/sessions/up.md"])
    assert "v2" in read.output


def test_memory_rm_blocks_drafts(cli_env):
    runner, _ = cli_env
    runner.invoke(main, [
        "memory", "create",
        "--path", "1_drafts/sessions/del.md",
        "--title", "Del",
        "--content", "x",
    ])
    result = runner.invoke(main, ["memory", "rm", "1_drafts/sessions/del.md"])
    assert result.exit_code == 1
    assert "Drafts cannot be deleted" in result.output


def test_memory_rm_archives_carve_out(cli_env):
    runner, data_dir = cli_env
    target = data_dir / "memory" / "2_knowledges" / "preferences" / "tooling.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Tooling preferences\n\nUse uv.\n")

    result = runner.invoke(main, ["memory", "rm", "2_knowledges/preferences/tooling.md", "--reason", "Outdated"])
    assert result.exit_code == 0
    assert "Archived" in result.output
    archived = data_dir / "memory" / "2_knowledges" / "_archived" / "preferences" / "tooling.md"
    assert archived.exists()
    assert not target.exists()


def test_memory_ls_json_returns_list(cli_env):
    runner, _ = cli_env
    runner.invoke(main, [
        "memory", "create",
        "--path", "1_drafts/sessions/ls.md",
        "--title", "Ls",
        "--content", "x",
    ])
    result = runner.invoke(main, ["memory", "ls", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert isinstance(parsed, list)


def test_memory_history_json(cli_env):
    runner, _ = cli_env
    runner.invoke(main, [
        "memory", "create",
        "--path", "1_drafts/sessions/h.md",
        "--title", "H",
        "--content", "x",
    ])
    result = runner.invoke(main, ["memory", "history", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert isinstance(parsed, list)
    assert any(r.get("page_path", "").endswith("h.md") for r in parsed)


# --- project subcommands ---

def test_project_new_and_ls(cli_env):
    runner, _ = cli_env
    new = runner.invoke(main, [
        "project", "new",
        "--name", "demo",
        "--path", "/tmp/demo",
        "--tags", "py,cli",
    ])
    assert new.exit_code == 0
    project_id = new.output.strip()
    assert len(project_id) > 0

    ls = runner.invoke(main, ["project", "ls", "--json"])
    assert ls.exit_code == 0
    parsed = json.loads(ls.output)
    assert any(p["id"] == project_id and p["name"] == "demo" for p in parsed)
    matched = next(p for p in parsed if p["id"] == project_id)
    assert matched.get("tags") == ["py", "cli"]


def test_project_ls_empty_returns_empty_json(cli_env):
    runner, _ = cli_env
    result = runner.invoke(main, ["project", "ls", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == []


# --- maintain subcommands ---

def test_maintain_stats_json_shape(cli_env):
    runner, _ = cli_env
    result = runner.invoke(main, ["maintain", "stats", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert set(parsed.keys()) == {"pages", "stale_pages", "groups"}
    assert set(parsed["pages"]) == {"knowledge", "skills", "agents", "drafts"}
    assert set(parsed["groups"]) == {"total", "open", "orphaned", "closed_no_draft_segments"}


def test_maintain_purge_no_archive_dir(cli_env):
    runner, data_dir = cli_env
    # Ensure the archive dir doesn't exist (init creates _archived/, so remove it).
    archived = data_dir / "memory" / "1_drafts" / "_archived"
    if archived.exists():
        for p in archived.iterdir():
            p.unlink()
        archived.rmdir()
    result = runner.invoke(main, ["maintain", "purge", "--older-than-days", "1"])
    assert result.exit_code == 0


# --- session + search --json shapes ---

def test_session_start_json_payload_shape(cli_env):
    runner, _ = cli_env
    result = runner.invoke(main, [
        "session", "start",
        "--agent", "test",
        "--working-dir", "/tmp/demo",
        "--create-project-folder",
        "--json",
    ])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert set(parsed.keys()) >= {"session_id", "group_id", "started_at", "project", "latest_summaries"}
    assert parsed["session_id"] == parsed["group_id"]
    assert parsed["project"]["name"] == "demo"
    assert parsed["project"]["session_folder"] == "1_drafts/sessions/demo"
    assert isinstance(parsed["latest_summaries"], list)


def test_session_start_stores_home_relative_project_path(cli_env, monkeypatch):
    runner, data_dir = cli_env
    home = data_dir / "home"
    project_dir = home / "work" / "demo"
    project_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    result = runner.invoke(main, [
        "session", "start",
        "--agent", "test",
        "--working-dir", str(project_dir),
        "--create-project-folder",
        "--json",
    ])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["project"]["path"] == "~/work/demo"

    close = runner.invoke(main, [
        "session", "close",
        "--session-id", parsed["session_id"],
        "--content", "# Session Summary\n\n## Requests And Prompts\n\nHome path test.",
        "--json",
    ])
    assert close.exit_code == 0, close.output
    closed = json.loads(close.output)
    assert 'working_dir: "~/work/demo"' in closed["content"]


def test_session_start_requires_project_folder_by_default(cli_env):
    runner, _ = cli_env
    result = runner.invoke(main, [
        "session", "start",
        "--agent", "test",
        "--working-dir", "/tmp/demo",
        "--json",
    ])
    assert result.exit_code == 1
    assert "Project session folder missing: 1_drafts/sessions/demo" in result.output
    assert "--create-project-folder" in result.output


def test_session_status_json_when_active(cli_env):
    runner, _ = cli_env
    start = runner.invoke(main, ["session", "start", "--agent", "test", "--create-project-folder", "--json"])
    assert start.exit_code == 0
    gid = json.loads(start.output)["group_id"]

    status = runner.invoke(main, ["session", "status", "--json"])
    assert status.exit_code == 0
    parsed = json.loads(status.output)
    assert parsed["group_id"] == gid
    assert parsed["session_id"] == gid
    assert "segment_start_at" in parsed
    assert "segment_turn_count" in parsed


def test_session_status_json_no_active_session(cli_env):
    runner, _ = cli_env
    result = runner.invoke(main, ["session", "status", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["group_id"] is None


def test_session_close_writes_summary_and_recent_returns_full_content(cli_env):
    runner, data_dir = cli_env
    start = runner.invoke(
        main,
        [
            "session", "start",
            "--agent", "test",
            "--project", "demo",
            "--working-dir", "/tmp/demo",
            "--create-project-folder",
            "--json",
        ],
    )
    assert start.exit_code == 0, start.output
    start_payload = json.loads(start.output)
    session_id = start_payload["session_id"]
    project_id = start_payload["project"]["id"]

    summary = """# Session Summary

## Requests And Prompts

Build the session workflow.

## Work Performed

Implemented close and recent commands.

## Discoveries And Insights

Recent summaries need full content.

## Completed Changes

Added storage and CLI support.

## Follow-Up And Next Steps

Run the full test suite.

## Additional Context

Project-scoped.
"""
    close = runner.invoke(
        main,
        ["session", "close", "--session-id", session_id, "--content", summary, "--json"],
    )
    assert close.exit_code == 0, close.output
    closed = json.loads(close.output)
    assert closed["session_id"] == session_id
    assert "Implemented close and recent commands." in closed["content"]
    assert (data_dir / "memory" / closed["path"]).exists()

    recent = runner.invoke(main, ["session", "recent", "--project", "demo", "--json"])
    assert recent.exit_code == 0, recent.output
    payload = json.loads(recent.output)
    assert len(payload) == 1
    assert payload[0]["session_id"] == session_id
    assert "Recent summaries need full content." in payload[0]["content"]

    curated_dir = data_dir / "memory" / "2_knowledges" / "entities" / "projects" / project_id / "sessions"
    curated_dir.mkdir(parents=True)
    curated = curated_dir / "curated-session.md"
    curated.write_text(
        """---
summary: Curated project session
tags: [session]
project_id: {project_id}
project_name: demo
session_id: curated-session
created_at: 2026-05-31T10:00:00Z
ended_at: 2026-05-31T10:00:00Z
---

# Curated Session

Curated session body.
""".format(project_id=project_id),
        encoding="utf-8",
    )

    with_curated = runner.invoke(main, ["session", "recent", "--project", "demo", "--json"])
    assert with_curated.exit_code == 0, with_curated.output
    merged = json.loads(with_curated.output)
    paths = {item["path"] for item in merged}
    assert closed["path"] in paths
    assert f"2_knowledges/entities/projects/{project_id}/sessions/curated-session.md" in paths
    curated_item = next(item for item in merged if item["session_id"] == "curated-session")
    assert "Curated session body." in curated_item["content"]
    assert curated_item["metadata"]["source"] == "knowledge"

    slug_curated_dir = data_dir / "memory" / "2_knowledges" / "entities" / "projects" / "demo" / "sessions"
    slug_curated_dir.mkdir(parents=True)
    slug_curated = slug_curated_dir / "slug-curated-session.md"
    slug_curated.write_text(
        """---
summary: Slug curated project session
tags: [session]
project_name: demo
session_id: slug-curated-session
created_at: 2026-05-31T11:00:00Z
ended_at: 2026-05-31T11:00:00Z
---

# Slug Curated Session

Slug curated session body.
""",
        encoding="utf-8",
    )
    with_slug_curated = runner.invoke(main, ["session", "recent", "--project", "demo", "--json"])
    assert with_slug_curated.exit_code == 0, with_slug_curated.output
    slug_merged = json.loads(with_slug_curated.output)
    slug_paths = {item["path"] for item in slug_merged}
    assert "2_knowledges/entities/projects/demo/sessions/slug-curated-session.md" in slug_paths


def test_session_recent_omits_missing_rows_covered_by_merged_draft(cli_env):
    runner, data_dir = cli_env
    start_a = runner.invoke(main, [
        "session", "start",
        "--project", "demo",
        "--working-dir", "/tmp/demo",
        "--create-project-folder",
        "--json",
    ])
    assert start_a.exit_code == 0, start_a.output
    session_a = json.loads(start_a.output)["session_id"]
    close_a = runner.invoke(main, [
        "session", "close",
        "--session-id", session_a,
        "--content", "# Session Summary\n\n## Requests And Prompts\n\nFirst.",
        "--json",
    ])
    assert close_a.exit_code == 0, close_a.output
    path_a = json.loads(close_a.output)["path"]

    start_b = runner.invoke(main, [
        "session", "start",
        "--project", "demo",
        "--working-dir", "/tmp/demo",
        "--json",
    ])
    assert start_b.exit_code == 0, start_b.output
    session_b = json.loads(start_b.output)["session_id"]
    close_b = runner.invoke(main, [
        "session", "close",
        "--session-id", session_b,
        "--content", "# Session Summary\n\n## Requests And Prompts\n\nMerged.",
        "--json",
    ])
    assert close_b.exit_code == 0, close_b.output
    payload_b = json.loads(close_b.output)
    path_b = payload_b["path"]

    file_a = data_dir / "memory" / path_a
    file_a.unlink()
    file_b = data_dir / "memory" / path_b
    content_b = file_b.read_text(encoding="utf-8")
    content_b = content_b.replace(
        f'session_id: "{session_b}"',
        f'session_ids: ["{session_a}", "{session_b}"]',
    )
    file_b.write_text(content_b, encoding="utf-8")

    recent = runner.invoke(main, ["session", "recent", "--project", "demo", "--json"])
    assert recent.exit_code == 0, recent.output
    payload = json.loads(recent.output)
    ids = [item["session_id"] for item in payload]
    paths = [item["path"] for item in payload]
    assert session_a not in ids
    assert path_a not in paths
    assert path_b in paths


def test_session_start_recent_excludes_current_open_session(cli_env):
    runner, _ = cli_env
    first = runner.invoke(main, [
        "session", "start",
        "--project", "demo",
        "--working-dir", "/tmp/demo",
        "--create-project-folder",
        "--json",
    ])
    first_id = json.loads(first.output)["session_id"]
    close = runner.invoke(main, [
        "session", "close",
        "--session-id", first_id,
        "--content", "# Session Summary\n\n## Requests And Prompts\n\nFirst saved session.",
        "--json",
    ])
    assert close.exit_code == 0, close.output

    second = runner.invoke(main, ["session", "start", "--project", "demo", "--working-dir", "/tmp/demo", "--json"])
    parsed = json.loads(second.output)
    assert parsed["session_id"] != first_id
    ids = [item["session_id"] for item in parsed["latest_summaries"]]
    assert first_id in ids
    assert parsed["session_id"] not in ids


def test_group_end_requires_session_summary(cli_env):
    runner, _ = cli_env
    result = runner.invoke(main, ["group", "end"])
    assert result.exit_code == 1
    assert "Session summary required" in result.output


def test_group_lifecycle_commands_remain_legacy_aliases(cli_env):
    runner, _ = cli_env
    start = runner.invoke(main, [
        "group", "start",
        "--project", "demo",
        "--working-dir", "/tmp/demo",
        "--create-project-folder",
        "--json",
    ])
    assert start.exit_code == 0, start.output
    session_id = json.loads(start.output)["session_id"]

    status = runner.invoke(main, ["group", "status", "--json"])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["session_id"] == session_id

    close = runner.invoke(
        main,
        [
            "group", "close",
            "--session-id", session_id,
            "--content", "# Session Summary\n\n## Requests And Prompts\n\nAlias path.",
            "--json",
        ],
    )
    assert close.exit_code == 0, close.output


def test_search_json_excludes_intelligences_by_default(cli_env):
    runner, data_dir = cli_env
    # Seed both a knowledge page and a skill so we can assert filtering.
    knowledge = data_dir / "memory" / "2_knowledges" / "topics" / "auth.md"
    knowledge.parent.mkdir(parents=True, exist_ok=True)
    knowledge.write_text("# Auth\n\nMiddleware notes for auth.\n")

    skill = data_dir / "memory" / "3_intelligences" / "skills" / "engineering" / "auth-coder" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("# Auth coder\n\nSkill for working on auth.\n")

    result = runner.invoke(main, ["search", "auth", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    tiers = {r["tier"] for r in parsed}
    assert "skill" not in tiers
    assert "agent" not in tiers


def test_skill_show_json_payload(cli_env):
    runner, data_dir = cli_env
    skill = data_dir / "memory" / "3_intelligences" / "skills" / "engineering" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("# Demo skill\n\nBody.\n")
    (skill.parent / "resources").mkdir()
    (skill.parent / "resources" / "ref.md").write_text("# Ref")

    result = runner.invoke(main, ["skill", "show", "engineering/demo", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["domain"] == "engineering"
    assert parsed["slug"] == "demo"
    assert parsed["title"] == "Demo skill"
    assert "Body." in parsed["content"]
    assert any(p.endswith("ref.md") for p in parsed["resources"])
    assert parsed["scripts"] == []


def test_agent_show_json_payload(cli_env):
    runner, data_dir = cli_env
    persona = data_dir / "memory" / "3_intelligences" / "agents" / "engineering" / "reviewer.md"
    persona.parent.mkdir(parents=True, exist_ok=True)
    persona.write_text("# Reviewer\n\nPersona body.\n")

    result = runner.invoke(main, ["agent", "show", "engineering/reviewer", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["domain"] == "engineering"
    assert parsed["slug"] == "reviewer"
    assert parsed["title"] == "Reviewer"
    assert "Persona body." in parsed["content"]
