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


# --- group + search --json shapes (parity with MCP) ---

def test_group_start_json_payload_shape(cli_env):
    runner, _ = cli_env
    result = runner.invoke(main, ["group", "start", "--agent", "test", "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert set(parsed.keys()) >= {"group_id", "segment_start_at", "pending", "recommended_context"}
    assert set(parsed["pending"]) == {"unarchived_session_drafts", "incomplete_segments"}
    assert isinstance(parsed["recommended_context"], list)


def test_group_status_json_when_active(cli_env):
    runner, _ = cli_env
    start = runner.invoke(main, ["group", "start", "--agent", "test", "--json"])
    assert start.exit_code == 0
    gid = json.loads(start.output)["group_id"]

    status = runner.invoke(main, ["group", "status", "--json"])
    assert status.exit_code == 0
    parsed = json.loads(status.output)
    assert parsed["group_id"] == gid
    assert "segment_start_at" in parsed
    assert "segment_turn_count" in parsed


def test_group_status_json_no_active_group(cli_env):
    runner, _ = cli_env
    result = runner.invoke(main, ["group", "status", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["group_id"] is None


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
