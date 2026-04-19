"""Tests for review and promotion tools."""

from agent_knowledge.core import storage, memory


class TestGetUnreviewedSessions:
    def test_excludes_today(self, tmp_db):
        project = storage.create_project(tmp_db, "proj", "/proj")
        session = storage.create_session(tmp_db, project["id"], "claude", "coding")
        storage.end_session(tmp_db, session["id"])

        # Today's session should be excluded by default
        unreviewed = storage.get_unreviewed_sessions(tmp_db)
        assert len(unreviewed) == 0

        # Include today
        unreviewed = storage.get_unreviewed_sessions(tmp_db, exclude_today=False)
        assert len(unreviewed) == 1

    def test_excludes_reviewed(self, tmp_db):
        project = storage.create_project(tmp_db, "proj", "/proj")
        session = storage.create_session(tmp_db, project["id"], "claude", "coding")
        storage.end_session(tmp_db, session["id"])
        storage.set_session_reviewed(tmp_db, session["id"])

        unreviewed = storage.get_unreviewed_sessions(tmp_db, exclude_today=False)
        assert len(unreviewed) == 0

    def test_filter_by_project(self, tmp_db):
        p1 = storage.create_project(tmp_db, "p1", "/p1")
        p2 = storage.create_project(tmp_db, "p2", "/p2")
        s1 = storage.create_session(tmp_db, p1["id"], "claude", "coding")
        s2 = storage.create_session(tmp_db, p2["id"], "codex", "coding")
        storage.end_session(tmp_db, s1["id"])
        storage.end_session(tmp_db, s2["id"])

        unreviewed = storage.get_unreviewed_sessions(tmp_db, project_id=p1["id"], exclude_today=False)
        assert len(unreviewed) == 1
        assert unreviewed[0]["id"] == s1["id"]


class TestGetSessionsNeedingDrafts:
    def test_finds_sessions_without_drafts(self, tmp_db):
        project = storage.create_project(tmp_db, "proj", "/proj")
        session = storage.create_session(tmp_db, project["id"], "claude", "coding")
        storage.create_turns(tmp_db, session["id"], [{"request": "test", "response": "ok"}])
        storage.end_session(tmp_db, session["id"])

        needing = storage.get_sessions_needing_drafts(tmp_db)
        assert len(needing) == 1
        assert needing[0]["id"] == session["id"]

    def test_excludes_sessions_with_drafts(self, tmp_db):
        project = storage.create_project(tmp_db, "proj", "/proj")
        session = storage.create_session(tmp_db, project["id"], "claude", "coding")
        storage.create_turns(tmp_db, session["id"], [{"request": "test", "response": "ok"}])
        storage.end_session(tmp_db, session["id"])

        # Record a draft creation in memory_edits
        storage.create_memory_edit(
            tmp_db, "drafts/sessions/2026-04-19-test.md", "draft", "create",
            "Session draft", session_id=session["id"],
        )

        needing = storage.get_sessions_needing_drafts(tmp_db)
        assert len(needing) == 0

    def test_excludes_sessions_without_turns(self, tmp_db):
        project = storage.create_project(tmp_db, "proj", "/proj")
        session = storage.create_session(tmp_db, project["id"], "claude", "coding")
        storage.end_session(tmp_db, session["id"])

        needing = storage.get_sessions_needing_drafts(tmp_db)
        assert len(needing) == 0


class TestPromotion:
    def test_promote_to_knowledge(self, tmp_memory):
        memory.ensure_memory_dirs(tmp_memory)
        memory.create_page(tmp_memory, "drafts/knowledge/patterns.md", "# Patterns\nContent.")

        memory.move_page(tmp_memory, "drafts/knowledge/patterns.md", "knowledge/concepts/patterns.md")

        content = memory.read_page(tmp_memory, "knowledge/concepts/patterns.md")
        assert "# Patterns" in content

        pages = memory.list_pages(tmp_memory, "drafts/knowledge")
        assert len(pages) == 0

    def test_promote_to_skill(self, tmp_memory):
        memory.ensure_memory_dirs(tmp_memory)
        memory.create_page(tmp_memory, "knowledge/concepts/python-tips.md", "# Python Tips\nUse type hints.")

        memory.move_page(tmp_memory, "knowledge/concepts/python-tips.md", "skills/python-coding/skills.md")

        content = memory.read_page(tmp_memory, "skills/python-coding/skills.md")
        assert "# Python Tips" in content

        pages = memory.list_pages(tmp_memory, "knowledge/concepts")
        assert "knowledge/concepts/python-tips.md" not in pages
