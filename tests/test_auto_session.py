"""Tests for auto-session, nullable project_id, session continuation, idempotent end."""

from agent_knowledge.core import storage


class TestNullableProjectId:
    def test_create_session_without_project(self, tmp_db):
        session = storage.create_session(tmp_db, None, "claude", "coding")
        assert session["project_id"] is None
        assert session["agent"] == "claude"

    def test_list_sessions_includes_null_project(self, tmp_db):
        storage.create_session(tmp_db, None, "claude", "coding")
        sessions = storage.list_sessions(tmp_db)
        assert len(sessions) == 1
        assert sessions[0]["project_id"] is None

    def test_turns_work_with_null_project_session(self, tmp_db):
        session = storage.create_session(tmp_db, None, "claude", "coding")
        turns = storage.create_turns(tmp_db, session["id"], [
            {"request": "hello", "response": "hi"},
        ])
        assert len(turns) == 1


class TestGetMostRecentOpenSession:
    def test_returns_none_when_no_sessions(self, tmp_db):
        assert storage.get_most_recent_open_session(tmp_db) is None

    def test_returns_open_session(self, tmp_db):
        session = storage.create_session(tmp_db, None, "claude", "coding")
        found = storage.get_most_recent_open_session(tmp_db)
        assert found["id"] == session["id"]

    def test_ignores_ended_sessions(self, tmp_db):
        session = storage.create_session(tmp_db, None, "claude", "coding")
        storage.end_session(tmp_db, session["id"])
        assert storage.get_most_recent_open_session(tmp_db) is None

    def test_returns_most_recent(self, tmp_db):
        storage.create_session(tmp_db, None, "claude", "coding")
        session2 = storage.create_session(tmp_db, None, "codex", "research")
        found = storage.get_most_recent_open_session(tmp_db)
        assert found["id"] == session2["id"]


class TestReopenSession:
    def test_reopen_ended_session(self, tmp_db):
        session = storage.create_session(tmp_db, None, "claude", "coding")
        storage.end_session(tmp_db, session["id"])
        reopened = storage.reopen_session(tmp_db, session["id"])
        assert reopened["ended_at"] is None

    def test_reopen_nonexistent(self, tmp_db):
        assert storage.reopen_session(tmp_db, "nonexistent") is None


class TestUpdateSessionMetadata:
    def test_backfill_project_id(self, tmp_db):
        project = storage.create_project(tmp_db, "proj", "/proj")
        session = storage.create_session(tmp_db, None, "claude", "coding")
        assert session["project_id"] is None

        updated = storage.update_session_metadata(tmp_db, session["id"], project_id=project["id"])
        assert updated["project_id"] == project["id"]

    def test_upgrade_agent_and_type(self, tmp_db):
        session = storage.create_session(tmp_db, None, "unknown", "coding")
        updated = storage.update_session_metadata(
            tmp_db, session["id"], agent="claude", session_type="debugging",
        )
        assert updated["agent"] == "claude"
        assert updated["type"] == "debugging"

    def test_no_op_when_nothing_provided(self, tmp_db):
        session = storage.create_session(tmp_db, None, "claude", "coding")
        updated = storage.update_session_metadata(tmp_db, session["id"])
        assert updated["id"] == session["id"]


class TestIdempotentSessionEnd:
    def test_end_already_ended_session(self, tmp_db):
        session = storage.create_session(tmp_db, None, "claude", "coding")
        first = storage.end_session(tmp_db, session["id"])
        assert first["ended_at"] is not None

        # Second call is a no-op — returns the session but doesn't change ended_at
        second = storage.end_session(tmp_db, session["id"])
        assert second["ended_at"] == first["ended_at"]

    def test_end_nonexistent_session(self, tmp_db):
        assert storage.end_session(tmp_db, "nonexistent") is None


class TestMigrationCompat:
    def test_fresh_db_gets_migrated(self, tmp_path):
        """A fresh database should get all tables created automatically."""
        db_path = tmp_path / "fresh.db"
        conn = storage.connect(db_path)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == len(storage._MIGRATIONS)

        # Tables should exist
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {r[0] for r in tables}
        assert {"projects", "sessions", "turns", "memory_edits"}.issubset(table_names)
        conn.close()
