"""Tests for skill / agent discovery (EP-00009).

`skill_search` / `agent_search` themselves are thin wrappers over
`search.search()` and tested in `test_search.py`. Here we cover the
bundle-manifest helper that backs `skill_get`, plus the CLI subcommands.
"""

from click.testing import CliRunner

from agent_knowledge import cli
from agent_knowledge.core import memory


class TestListBundleCompanions:
    def test_lists_recursive_files_relative_to_memory(self, tmp_memory):
        bundle = tmp_memory / "3_intelligences" / "skills" / "eng" / "foo"
        bundle.mkdir(parents=True)
        (bundle / "SKILL.md").write_text("# Foo\n")

        resources = bundle / "resources"
        resources.mkdir()
        (resources / "guide.md").write_text("g")
        nested = resources / "nested"
        nested.mkdir()
        (nested / "deep.md").write_text("d")

        files = memory.list_bundle_companions(tmp_memory, bundle, "resources")
        assert files == [
            "3_intelligences/skills/eng/foo/resources/guide.md",
            "3_intelligences/skills/eng/foo/resources/nested/deep.md",
        ]

    def test_missing_subdir_returns_empty_list(self, tmp_memory):
        bundle = tmp_memory / "3_intelligences" / "skills" / "eng" / "foo"
        bundle.mkdir(parents=True)
        assert memory.list_bundle_companions(tmp_memory, bundle, "resources") == []

    def test_subdir_is_a_file_returns_empty(self, tmp_memory):
        bundle = tmp_memory / "3_intelligences" / "skills" / "eng" / "foo"
        bundle.mkdir(parents=True)
        (bundle / "resources").write_text("not a dir")
        assert memory.list_bundle_companions(tmp_memory, bundle, "resources") == []


class TestSkillCli:
    def _setup_memory(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        monkeypatch.setenv("AKW_DATA_DIR", str(data_dir))
        from agent_knowledge.core.config import load_config
        config = load_config()
        config.memory_dir.mkdir(parents=True, exist_ok=True)
        bundle = config.memory_dir / "3_intelligences" / "skills" / "eng" / "foo"
        bundle.mkdir(parents=True)
        (bundle / "SKILL.md").write_text(
            "# Foo Skill\n\nAuthoritative recipe for foo. Token: aardvark-savanna.\n"
        )
        (bundle / "resources").mkdir()
        (bundle / "resources" / "guide.md").write_text("# Guide\n")
        (bundle / "scripts").mkdir()
        (bundle / "scripts" / "run.sh").write_text("#!/bin/sh\n")
        return config

    def test_skill_search_cli(self, tmp_path, monkeypatch):
        self._setup_memory(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["skill", "search", "aardvark"])
        assert result.exit_code == 0, result.output
        assert "3_intelligences/skills/eng/foo/SKILL.md" in result.output
        assert "[skill]" in result.output

    def test_skill_show_full_path(self, tmp_path, monkeypatch):
        self._setup_memory(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            cli.main,
            ["skill", "show", "3_intelligences/skills/eng/foo/SKILL.md"],
        )
        assert result.exit_code == 0, result.output
        assert "Foo Skill" in result.output
        assert "## resources/" in result.output
        assert "guide.md" in result.output
        assert "## scripts/" in result.output
        assert "run.sh" in result.output

    def test_skill_show_shorthand(self, tmp_path, monkeypatch):
        self._setup_memory(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["skill", "show", "eng/foo"])
        assert result.exit_code == 0, result.output
        assert "Foo Skill" in result.output

    def test_skill_show_missing(self, tmp_path, monkeypatch):
        self._setup_memory(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["skill", "show", "eng/nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestAgentCli:
    def _setup_memory(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        monkeypatch.setenv("AKW_DATA_DIR", str(data_dir))
        from agent_knowledge.core.config import load_config
        config = load_config()
        config.memory_dir.mkdir(parents=True, exist_ok=True)
        agents_dir = config.memory_dir / "3_intelligences" / "agents" / "eng"
        agents_dir.mkdir(parents=True)
        (agents_dir / "sre.md").write_text(
            "# SRE Persona\n\nIncident-focused engineer. Token: zinnia-bloom.\n"
        )
        return config

    def test_agent_search_cli(self, tmp_path, monkeypatch):
        self._setup_memory(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["agent", "search", "zinnia"])
        assert result.exit_code == 0, result.output
        assert "3_intelligences/agents/eng/sre.md" in result.output
        assert "[agent]" in result.output

    def test_agent_show_full_path(self, tmp_path, monkeypatch):
        self._setup_memory(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            cli.main,
            ["agent", "show", "3_intelligences/agents/eng/sre.md"],
        )
        assert result.exit_code == 0, result.output
        assert "SRE Persona" in result.output

    def test_agent_show_shorthand(self, tmp_path, monkeypatch):
        self._setup_memory(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli.main, ["agent", "show", "eng/sre"])
        assert result.exit_code == 0, result.output
        assert "SRE Persona" in result.output
