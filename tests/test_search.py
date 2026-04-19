"""Tests for DuckDB search module."""

from agent_knowledge.core import memory, search


class TestSyncAndSearch:
    def test_sync_indexes_knowledge_and_skills(self, tmp_memory, tmp_search):
        memory.ensure_memory_dirs(tmp_memory)
        memory.create_page(tmp_memory, "knowledge/concepts/auth.md", "# Auth Patterns\nMutex locks for token refresh.")
        memory.create_page(tmp_memory, "skills/python-coding/skills.md", "# Python Skills\nUse type hints everywhere.")
        memory.create_page(tmp_memory, "drafts/sessions/2026-04-19.md", "# Session Draft\nShould not be indexed.")

        count = search.sync_from_files(tmp_search, tmp_memory)
        assert count == 2

    def test_search_returns_results(self, tmp_memory, tmp_search):
        memory.ensure_memory_dirs(tmp_memory)
        memory.create_page(tmp_memory, "knowledge/concepts/auth.md", "# Auth Patterns\nMutex locks for token refresh.")
        memory.create_page(tmp_memory, "knowledge/concepts/logging.md", "# Logging\nStructured logging with JSON.")
        search.sync_from_files(tmp_search, tmp_memory)

        results = search.search(tmp_search, "mutex token")
        assert len(results) >= 1
        assert results[0]["path"] == "knowledge/concepts/auth.md"

    def test_search_filter_by_tier(self, tmp_memory, tmp_search):
        memory.ensure_memory_dirs(tmp_memory)
        memory.create_page(tmp_memory, "knowledge/concepts/patterns.md", "# Patterns\nCoding patterns.")
        memory.create_page(tmp_memory, "skills/python-coding/skills.md", "# Skills\nCoding skills.")
        search.sync_from_files(tmp_search, tmp_memory)

        results = search.search(tmp_search, "coding", tier="skill")
        assert all(r["tier"] == "skill" for r in results)

    def test_search_empty_query(self, tmp_search):
        assert search.search(tmp_search, "") == []
        assert search.search(tmp_search, "   ") == []


class TestIndex:
    def test_get_index(self, tmp_memory, tmp_search):
        memory.ensure_memory_dirs(tmp_memory)
        memory.create_page(tmp_memory, "knowledge/concepts/auth.md", "# Auth\nContent.")
        memory.create_page(tmp_memory, "skills/python-coding/skills.md", "# Python\nContent.")
        search.sync_from_files(tmp_search, tmp_memory)

        index = search.get_index(tmp_search)
        assert len(index) == 2

        knowledge_only = search.get_index(tmp_search, tier="knowledge")
        assert len(knowledge_only) == 1
        assert knowledge_only[0]["tier"] == "knowledge"


class TestExtractors:
    def test_extract_title_from_heading(self):
        assert search._extract_title("# My Title\nContent", "fallback") == "My Title"

    def test_extract_title_fallback(self):
        assert search._extract_title("No heading here", "fallback") == "fallback"

    def test_extract_summary(self):
        summary = search._extract_summary("# Title\n\nThis is the summary line.\nMore content.")
        assert summary == "This is the summary line."

    def test_extract_tags(self):
        content = "---\ntags: [python, web]\n---\n# Content"
        tags = search._extract_tags(content)
        assert "python" in tags
        assert "web" in tags
