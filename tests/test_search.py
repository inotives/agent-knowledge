"""Tests for DuckDB search module."""

from agent_knowledge.core import memory, search


class TestSyncAndSearch:
    def test_sync_indexes_all_tiers(self, tmp_memory, tmp_search):
        memory.ensure_memory_dirs(tmp_memory)
        memory.create_page(tmp_memory, "2_knowledges/concepts/auth.md", "# Auth Patterns\nMutex locks for token refresh.")
        memory.create_page(tmp_memory, "1_drafts/sessions/2026-04-19.md", "# Session Draft\nDiscussion notes.")
        memory.create_page(tmp_memory, "1_drafts/_archived/sessions__2026-04-01-old.md", "# Old Session\nArchived draft.")
        memory.create_page(tmp_memory, "1_drafts/2_knowledges/concepts/draft.md", "# Knowledge Draft\nPromotes to 2_knowledges.")
        memory.create_page(tmp_memory, "1_drafts/2_notes/idea.md", "# Note Draft\nAd-hoc capture.")
        memory.create_page(tmp_memory, "1_drafts/2_researches/topic.md", "# Research Draft\nLiterature pull.")
        memory.create_page(tmp_memory, "1_drafts/3_skills/new-skill.md", "# Skill Draft\nPromotes to 3_intelligences/skills.")

        count = search.sync_from_files(tmp_search, tmp_memory)
        assert count == 7

    def test_sync_excludes_skills_and_agents(self, tmp_memory, tmp_search):
        memory.ensure_memory_dirs(tmp_memory)
        memory.create_page(tmp_memory, "3_intelligences/skills/python-coding/SKILL.md", "# Python Skills\nUse type hints.")
        memory.create_page(tmp_memory, "3_intelligences/agents/python/dev.md", "# Python Dev Persona\nThorough reviewer.")

        count = search.sync_from_files(tmp_search, tmp_memory)
        assert count == 0

    def test_sync_skips_archived_subfolders(self, tmp_memory, tmp_search):
        memory.ensure_memory_dirs(tmp_memory)
        memory.create_page(tmp_memory, "2_knowledges/preferences/live.md", "# Live\nactive preference.")
        memory.create_page(tmp_memory, "2_knowledges/_archived/preferences/old.md", "# Old\nretired preference.")

        count = search.sync_from_files(tmp_search, tmp_memory)
        assert count == 1

        results = search.search(tmp_search, "preference")
        paths_seen = {r["path"] for r in results}
        assert "2_knowledges/preferences/live.md" in paths_seen
        assert "2_knowledges/_archived/preferences/old.md" not in paths_seen

    def test_search_returns_results(self, tmp_memory, tmp_search):
        memory.ensure_memory_dirs(tmp_memory)
        memory.create_page(tmp_memory, "2_knowledges/concepts/auth.md", "# Auth Patterns\nMutex locks for token refresh.")
        memory.create_page(tmp_memory, "2_knowledges/concepts/logging.md", "# Logging\nStructured logging with JSON.")
        search.sync_from_files(tmp_search, tmp_memory)

        results = search.search(tmp_search, "mutex token")
        assert len(results) >= 1
        assert results[0]["path"] == "2_knowledges/concepts/auth.md"

    def test_search_filter_by_tier(self, tmp_memory, tmp_search):
        memory.ensure_memory_dirs(tmp_memory)
        memory.create_page(tmp_memory, "2_knowledges/concepts/patterns.md", "# Patterns\nDesign patterns library.")
        memory.create_page(tmp_memory, "1_drafts/2_researches/research-patterns.md", "# Research\nResearching patterns.")
        search.sync_from_files(tmp_search, tmp_memory)

        results = search.search(tmp_search, "patterns", tier="research_draft")
        assert all(r["tier"] == "research_draft" for r in results)
        assert len(results) >= 1

    def test_search_filter_session_archived(self, tmp_memory, tmp_search):
        memory.ensure_memory_dirs(tmp_memory)
        memory.create_page(tmp_memory, "1_drafts/sessions/live.md", "# Live\ndiscussing palindrome detection.")
        memory.create_page(tmp_memory, "1_drafts/_archived/sessions__archived.md", "# Old\nworking on quaternion math.")
        search.sync_from_files(tmp_search, tmp_memory)

        live = search.search(tmp_search, "palindrome", tier="session_draft")
        archived = search.search(tmp_search, "quaternion", tier="session_archived")
        assert len(live) == 1 and live[0]["path"] == "1_drafts/sessions/live.md"
        assert len(archived) == 1 and archived[0]["path"] == "1_drafts/_archived/sessions__archived.md"

    def test_search_empty_query(self, tmp_search):
        assert search.search(tmp_search, "") == []
        assert search.search(tmp_search, "   ") == []


class TestIndex:
    def test_get_index(self, tmp_memory, tmp_search):
        memory.ensure_memory_dirs(tmp_memory)
        memory.create_page(tmp_memory, "2_knowledges/concepts/auth.md", "# Auth\nContent.")
        memory.create_page(tmp_memory, "1_drafts/2_notes/idea.md", "# Idea\nQuick note.")
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
