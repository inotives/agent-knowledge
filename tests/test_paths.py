"""Tests for tier path constants and helpers (EP-00008)."""

import pytest

from agent_knowledge.core import paths


class TestSessionDraftPath:
    def test_canonical_form(self):
        gid = "abcdef0123456789"
        assert (
            paths.session_draft_path(gid, "2026-04-20T10:30:00Z")
            == "1_drafts/sessions/abcdef01-20260420-1030.md"
        )

    def test_uses_first_8_of_group_id(self):
        path = paths.session_draft_path("12345678-rest", "2026-04-20T10:30")
        assert path.startswith("1_drafts/sessions/12345678-")


class TestArchivedSessionPath:
    def test_flat_file_with_prefix(self):
        live = "1_drafts/sessions/abc-20260420-1030.md"
        assert paths.archived_session_path(live) == "1_drafts/_archived/sessions__abc-20260420-1030.md"

    def test_glob_matches_archived_path(self):
        archived = paths.archived_session_path("1_drafts/sessions/x.md")
        assert paths.is_archived_session_path(archived)
        assert not paths.is_archived_session_path("1_drafts/sessions/x.md")
        assert not paths.is_archived_session_path("1_drafts/_archived/notes__x.md")


class TestWriteBoundary:
    @pytest.mark.parametrize("prefix", [
        "2_knowledges/",
        "3_intelligences/",
        "0_configs/",
        "1_drafts/_archived/",
    ])
    def test_rejects_each_blocked_prefix(self, prefix):
        path = f"{prefix}some/file.md"
        reason = paths.reject_curated_write(path)
        assert reason is not None
        assert prefix in reason
        assert path in reason

    @pytest.mark.parametrize("path", [
        "1_drafts/sessions/foo.md",
        "1_drafts/notes/foo.md",
        "1_drafts/knowledge/foo.md",
        "random/path.md",
    ])
    def test_allows_non_blocked(self, path):
        assert paths.reject_curated_write(path) is None

    @pytest.mark.parametrize("path", [
        "2_knowledges/preferences/tooling.md",
        "2_knowledges/preferences/nested/deep.md",
    ])
    def test_carve_out_overrides_block(self, path):
        assert paths.reject_curated_write(path) is None
        assert paths.is_archive_redirected_path(path) is True

    def test_non_carve_out_knowledge_still_blocked(self):
        assert paths.reject_curated_write("2_knowledges/concepts/foo.md") is not None
        assert paths.is_archive_redirected_path("2_knowledges/concepts/foo.md") is False


class TestArchivedKnowledgePath:
    def test_preserves_subfolder_structure(self):
        assert (
            paths.archived_knowledge_path("2_knowledges/preferences/tooling.md")
            == "2_knowledges/_archived/preferences/tooling.md"
        )

    def test_works_for_other_curated_tiers(self):
        assert (
            paths.archived_knowledge_path("3_intelligences/skills/python/SKILL.md")
            == "3_intelligences/_archived/skills/python/SKILL.md"
        )

    def test_rejects_non_curated_path(self):
        with pytest.raises(ValueError):
            paths.archived_knowledge_path("1_drafts/sessions/foo.md")

    def test_rejects_already_archived(self):
        with pytest.raises(ValueError):
            paths.archived_knowledge_path("2_knowledges/_archived/preferences/foo.md")


class TestIndexedTiers:
    def test_covers_expected_tiers(self):
        labels = {label for label, _ in paths.INDEXED_TIERS}
        assert labels == {
            "knowledge",
            "session_draft",
            "knowledge_draft",
            "note_draft",
            "research_draft",
            "skill_draft",
        }

    def test_skills_and_agents_excluded(self):
        labels = {label for label, _ in paths.INDEXED_TIERS}
        assert "skill" not in labels
        assert "agent" not in labels

    def test_archived_session_tier_distinct(self):
        labels = {label for label, _ in paths.INDEXED_TIERS}
        assert paths.ARCHIVED_SESSION_TIER not in labels
        assert paths.ARCHIVED_SESSION_TIER in paths.VALID_TIERS

    def test_skill_and_agent_dirs_exposed(self):
        assert paths.SKILLS_DIR == "3_intelligences/skills"
        assert paths.AGENTS_DIR == "3_intelligences/agents"


class TestParseSkillPath:
    def test_canonical_skill_md_path(self):
        assert paths.parse_skill_path(
            "3_intelligences/skills/engineering/python-coding/SKILL.md"
        ) == ("engineering", "python-coding")

    def test_bundle_dir(self):
        assert paths.parse_skill_path(
            "3_intelligences/skills/engineering/python-coding"
        ) == ("engineering", "python-coding")

    def test_nested_slug(self):
        assert paths.parse_skill_path(
            "3_intelligences/skills/architecture/observability_design/SKILL.md"
        ) == ("architecture", "observability_design")

    @pytest.mark.parametrize("bad_path", [
        "3_intelligences/skills/SKILL.md",                # no domain
        "3_intelligences/skills/engineering",             # no slug
        "3_intelligences/agents/engineering/sre.md",      # wrong tier
        "2_knowledges/concepts/foo.md",                   # wrong tier
        "random/path.md",
    ])
    def test_malformed_returns_none(self, bad_path):
        assert paths.parse_skill_path(bad_path) is None


class TestParseAgentPath:
    def test_canonical(self):
        assert paths.parse_agent_path(
            "3_intelligences/agents/engineering/sre.md"
        ) == ("engineering", "sre")

    @pytest.mark.parametrize("bad_path", [
        "3_intelligences/agents/sre.md",                  # no domain
        "3_intelligences/agents/engineering/sre",         # missing .md
        "3_intelligences/skills/engineering/foo.md",      # wrong tier
        "random/path.md",
    ])
    def test_malformed_returns_none(self, bad_path):
        assert paths.parse_agent_path(bad_path) is None


class TestSkillBundleDir:
    def test_strips_skill_md(self):
        assert (
            paths.skill_bundle_dir("3_intelligences/skills/eng/foo/SKILL.md")
            == "3_intelligences/skills/eng/foo"
        )

    def test_bundle_dir_unchanged(self):
        assert (
            paths.skill_bundle_dir("3_intelligences/skills/eng/foo")
            == "3_intelligences/skills/eng/foo"
        )

    def test_strips_trailing_slash(self):
        assert (
            paths.skill_bundle_dir("3_intelligences/skills/eng/foo/")
            == "3_intelligences/skills/eng/foo"
        )


class TestResolveSkillPath:
    def test_full_path_pass_through(self):
        full = "3_intelligences/skills/eng/foo/SKILL.md"
        assert paths.resolve_skill_path(full) == full

    def test_bundle_dir_completes_to_skill_md(self):
        assert (
            paths.resolve_skill_path("3_intelligences/skills/eng/foo")
            == "3_intelligences/skills/eng/foo/SKILL.md"
        )

    def test_shorthand(self):
        assert (
            paths.resolve_skill_path("eng/foo")
            == "3_intelligences/skills/eng/foo/SKILL.md"
        )


class TestResolveAgentPath:
    def test_full_path_pass_through(self):
        full = "3_intelligences/agents/eng/sre.md"
        assert paths.resolve_agent_path(full) == full

    def test_full_path_without_md(self):
        assert (
            paths.resolve_agent_path("3_intelligences/agents/eng/sre")
            == "3_intelligences/agents/eng/sre.md"
        )

    def test_shorthand(self):
        assert (
            paths.resolve_agent_path("eng/sre")
            == "3_intelligences/agents/eng/sre.md"
        )


class TestIntelligencesTiers:
    def test_covers_skill_and_agent(self):
        labels = {label for label, _, _ in paths.INTELLIGENCES_TIERS}
        assert labels == {"skill", "agent"}

    def test_skill_walks_skill_md_only(self):
        skill = next(t for t in paths.INTELLIGENCES_TIERS if t[0] == "skill")
        assert skill == ("skill", paths.SKILLS_DIR, paths.SKILL_ENTRY_FILENAME)
