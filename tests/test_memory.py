"""Tests for memory file operations module."""

import pytest

from agent_knowledge.core import memory


class TestEnsureDirs:
    def test_creates_all_dirs(self, tmp_memory):
        memory.ensure_memory_dirs(tmp_memory)
        assert (tmp_memory / "0_configs" / "templates").is_dir()
        assert (tmp_memory / "0_configs" / "rules").is_dir()
        assert (tmp_memory / "1_drafts" / "sessions").is_dir()
        assert (tmp_memory / "1_drafts" / "_archived").is_dir()
        assert (tmp_memory / "1_drafts" / "2_knowledges").is_dir()
        assert (tmp_memory / "1_drafts" / "2_notes").is_dir()
        assert (tmp_memory / "1_drafts" / "2_researches").is_dir()
        assert (tmp_memory / "1_drafts" / "3_skills").is_dir()
        assert (tmp_memory / "1_drafts" / "reviews").is_dir()
        assert (tmp_memory / "2_knowledges").is_dir()
        assert (tmp_memory / "3_intelligences" / "skills").is_dir()
        assert (tmp_memory / "3_intelligences" / "agents").is_dir()


class TestPages:
    def test_create_and_read(self, tmp_memory):
        memory.create_page(tmp_memory, "2_knowledges/concepts/auth.md", "# Auth\nPatterns here.")
        content = memory.read_page(tmp_memory, "2_knowledges/concepts/auth.md")
        assert "# Auth" in content

    def test_create_duplicate_raises(self, tmp_memory):
        memory.create_page(tmp_memory, "2_knowledges/test.md", "content")
        with pytest.raises(FileExistsError):
            memory.create_page(tmp_memory, "2_knowledges/test.md", "other")

    def test_read_nonexistent_raises(self, tmp_memory):
        with pytest.raises(FileNotFoundError):
            memory.read_page(tmp_memory, "nope.md")

    def test_update(self, tmp_memory):
        memory.create_page(tmp_memory, "2_knowledges/test.md", "v1")
        memory.update_page(tmp_memory, "2_knowledges/test.md", "v2")
        assert memory.read_page(tmp_memory, "2_knowledges/test.md") == "v2"

    def test_update_nonexistent_raises(self, tmp_memory):
        with pytest.raises(FileNotFoundError):
            memory.update_page(tmp_memory, "nope.md", "content")

    def test_delete(self, tmp_memory):
        memory.create_page(tmp_memory, "2_knowledges/test.md", "content")
        memory.delete_page(tmp_memory, "2_knowledges/test.md")
        with pytest.raises(FileNotFoundError):
            memory.read_page(tmp_memory, "2_knowledges/test.md")

    def test_delete_nonexistent_raises(self, tmp_memory):
        with pytest.raises(FileNotFoundError):
            memory.delete_page(tmp_memory, "nope.md")


class TestMovePage:
    def test_move(self, tmp_memory):
        memory.create_page(tmp_memory, "1_drafts/knowledge/draft.md", "content")
        memory.move_page(tmp_memory, "1_drafts/knowledge/draft.md", "2_knowledges/concepts/final.md")
        assert memory.read_page(tmp_memory, "2_knowledges/concepts/final.md") == "content"
        with pytest.raises(FileNotFoundError):
            memory.read_page(tmp_memory, "1_drafts/knowledge/draft.md")

    def test_move_to_existing_raises(self, tmp_memory):
        memory.create_page(tmp_memory, "2_knowledges/a.md", "a")
        memory.create_page(tmp_memory, "2_knowledges/b.md", "b")
        with pytest.raises(FileExistsError):
            memory.move_page(tmp_memory, "2_knowledges/a.md", "2_knowledges/b.md")


class TestListPages:
    def test_list(self, tmp_memory):
        memory.create_page(tmp_memory, "2_knowledges/a.md", "a")
        memory.create_page(tmp_memory, "2_knowledges/concepts/b.md", "b")
        pages = memory.list_pages(tmp_memory, "2_knowledges")
        assert len(pages) == 2
        assert "2_knowledges/a.md" in pages

    def test_list_empty(self, tmp_memory):
        assert memory.list_pages(tmp_memory, "3_intelligences/skills") == []


class TestGetTier:
    def test_tiers(self):
        assert memory.get_tier("1_drafts/sessions/foo.md") == "draft"
        assert memory.get_tier("2_knowledges/concepts/bar.md") == "knowledge"
        assert memory.get_tier("3_intelligences/skills/python/SKILL.md") == "skill"
        assert memory.get_tier("3_intelligences/agents/python/dev.md") == "agent"
        assert memory.get_tier("0_configs/templates/foo.md") == "config"
        assert memory.get_tier("random/file.md") is None
