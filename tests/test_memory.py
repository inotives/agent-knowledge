"""Tests for memory file operations module."""

import pytest

from agent_knowledge.core import memory


class TestEnsureDirs:
    def test_creates_all_dirs(self, tmp_memory):
        memory.ensure_memory_dirs(tmp_memory)
        assert (tmp_memory / "drafts" / "sessions").is_dir()
        assert (tmp_memory / "drafts" / "knowledge").is_dir()
        assert (tmp_memory / "drafts" / "reviews").is_dir()
        assert (tmp_memory / "knowledge" / "entities").is_dir()
        assert (tmp_memory / "knowledge" / "concepts").is_dir()
        assert (tmp_memory / "knowledge" / "sources").is_dir()
        assert (tmp_memory / "skills").is_dir()


class TestPages:
    def test_create_and_read(self, tmp_memory):
        memory.create_page(tmp_memory, "knowledge/concepts/auth.md", "# Auth\nPatterns here.")
        content = memory.read_page(tmp_memory, "knowledge/concepts/auth.md")
        assert "# Auth" in content

    def test_create_duplicate_raises(self, tmp_memory):
        memory.create_page(tmp_memory, "knowledge/test.md", "content")
        with pytest.raises(FileExistsError):
            memory.create_page(tmp_memory, "knowledge/test.md", "other")

    def test_read_nonexistent_raises(self, tmp_memory):
        with pytest.raises(FileNotFoundError):
            memory.read_page(tmp_memory, "nope.md")

    def test_update(self, tmp_memory):
        memory.create_page(tmp_memory, "knowledge/test.md", "v1")
        memory.update_page(tmp_memory, "knowledge/test.md", "v2")
        assert memory.read_page(tmp_memory, "knowledge/test.md") == "v2"

    def test_update_nonexistent_raises(self, tmp_memory):
        with pytest.raises(FileNotFoundError):
            memory.update_page(tmp_memory, "nope.md", "content")

    def test_delete(self, tmp_memory):
        memory.create_page(tmp_memory, "knowledge/test.md", "content")
        memory.delete_page(tmp_memory, "knowledge/test.md")
        with pytest.raises(FileNotFoundError):
            memory.read_page(tmp_memory, "knowledge/test.md")

    def test_delete_nonexistent_raises(self, tmp_memory):
        with pytest.raises(FileNotFoundError):
            memory.delete_page(tmp_memory, "nope.md")


class TestMovePage:
    def test_move(self, tmp_memory):
        memory.create_page(tmp_memory, "drafts/knowledge/draft.md", "content")
        memory.move_page(tmp_memory, "drafts/knowledge/draft.md", "knowledge/concepts/final.md")
        assert memory.read_page(tmp_memory, "knowledge/concepts/final.md") == "content"
        with pytest.raises(FileNotFoundError):
            memory.read_page(tmp_memory, "drafts/knowledge/draft.md")

    def test_move_to_existing_raises(self, tmp_memory):
        memory.create_page(tmp_memory, "knowledge/a.md", "a")
        memory.create_page(tmp_memory, "knowledge/b.md", "b")
        with pytest.raises(FileExistsError):
            memory.move_page(tmp_memory, "knowledge/a.md", "knowledge/b.md")


class TestListPages:
    def test_list(self, tmp_memory):
        memory.create_page(tmp_memory, "knowledge/a.md", "a")
        memory.create_page(tmp_memory, "knowledge/concepts/b.md", "b")
        pages = memory.list_pages(tmp_memory, "knowledge")
        assert len(pages) == 2
        assert "knowledge/a.md" in pages

    def test_list_empty(self, tmp_memory):
        assert memory.list_pages(tmp_memory, "skills") == []


class TestGetTier:
    def test_tiers(self):
        assert memory.get_tier("drafts/sessions/foo.md") == "draft"
        assert memory.get_tier("knowledge/concepts/bar.md") == "knowledge"
        assert memory.get_tier("skills/python/skills.md") == "skill"
        assert memory.get_tier("random/file.md") is None
