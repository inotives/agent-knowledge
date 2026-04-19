"""Tests for SQLite storage module."""

from agent_knowledge.core import storage


class TestProjects:
    def test_create_and_get(self, tmp_db):
        project = storage.create_project(tmp_db, "test-project", "/tmp/test", tags=["python"])
        assert project["name"] == "test-project"
        assert project["path"] == "/tmp/test"
        assert project["tags"] == ["python"]

        fetched = storage.get_project(tmp_db, project["id"])
        assert fetched["id"] == project["id"]

    def test_list_projects(self, tmp_db):
        storage.create_project(tmp_db, "proj-a", "/a")
        storage.create_project(tmp_db, "proj-b", "/b")
        projects = storage.list_projects(tmp_db)
        assert len(projects) == 2

    def test_get_nonexistent(self, tmp_db):
        assert storage.get_project(tmp_db, "nonexistent") is None


class TestSessions:
    def test_create_and_end(self, tmp_db):
        project = storage.create_project(tmp_db, "proj", "/proj")
        session = storage.create_session(tmp_db, project["id"], "claude", "coding")
        assert session["agent"] == "claude"
        assert session["type"] == "coding"
        assert session["ended_at"] is None

        ended = storage.end_session(tmp_db, session["id"])
        assert ended["ended_at"] is not None

    def test_list_by_project(self, tmp_db):
        p1 = storage.create_project(tmp_db, "p1", "/p1")
        p2 = storage.create_project(tmp_db, "p2", "/p2")
        storage.create_session(tmp_db, p1["id"], "claude", "coding")
        storage.create_session(tmp_db, p2["id"], "codex", "research")

        sessions = storage.list_sessions(tmp_db, project_id=p1["id"])
        assert len(sessions) == 1
        assert sessions[0]["agent"] == "claude"

    def test_reviewed(self, tmp_db):
        project = storage.create_project(tmp_db, "proj", "/proj")
        session = storage.create_session(tmp_db, project["id"], "claude", "coding")
        assert session["reviewed_at"] is None

        storage.set_session_reviewed(tmp_db, session["id"])
        updated = storage.get_session(tmp_db, session["id"])
        assert updated["reviewed_at"] is not None


class TestTurns:
    def test_create_and_get(self, tmp_db):
        project = storage.create_project(tmp_db, "proj", "/proj")
        session = storage.create_session(tmp_db, project["id"], "claude", "coding")

        turns = storage.create_turns(tmp_db, session["id"], [
            {"request": "fix the bug", "response": "found null pointer, fixed"},
            {"request": "add tests", "response": "added 3 test cases"},
        ])
        assert len(turns) == 2

        fetched = storage.get_turns(tmp_db, session["id"])
        assert len(fetched) == 2
        assert fetched[0]["request"] == "fix the bug"
        assert fetched[1]["request"] == "add tests"


class TestMemoryEdits:
    def test_create_and_history(self, tmp_db):
        project = storage.create_project(tmp_db, "proj", "/proj")
        session = storage.create_session(tmp_db, project["id"], "claude", "coding")

        storage.create_memory_edit(
            tmp_db, "knowledge/concepts/auth.md", "knowledge", "create",
            "Added auth patterns page", session_id=session["id"],
        )
        storage.create_memory_edit(
            tmp_db, "knowledge/concepts/auth.md", "knowledge", "update",
            "Added mutex section",
        )

        history = storage.get_memory_history(tmp_db)
        assert len(history) == 2

        filtered = storage.get_memory_history(tmp_db, page_path="knowledge/concepts/auth.md")
        assert len(filtered) == 2

    def test_history_limit(self, tmp_db):
        for i in range(5):
            storage.create_memory_edit(tmp_db, f"knowledge/page{i}.md", "knowledge", "create", f"page {i}")
        history = storage.get_memory_history(tmp_db, limit=3)
        assert len(history) == 3
