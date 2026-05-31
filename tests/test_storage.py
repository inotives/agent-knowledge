"""Tests for SQLite storage module — EP-00005 group-based API."""

from agent_knowledge.core import storage


class TestProjects:
    def test_create_and_get(self, tmp_db):
        project = storage.create_project(tmp_db, "test-project", "/tmp/test", tags=["python"])
        assert project["name"] == "test-project"
        assert project["path"] == "/tmp/test"
        assert project["tags"] == ["python"]

        fetched = storage.get_project(tmp_db, project["id"])
        assert fetched is not None
        assert fetched["id"] == project["id"]

    def test_list_projects(self, tmp_db):
        storage.create_project(tmp_db, "proj-a", "/a")
        storage.create_project(tmp_db, "proj-b", "/b")
        projects = storage.list_projects(tmp_db)
        assert len(projects) == 2

    def test_get_nonexistent(self, tmp_db):
        assert storage.get_project(tmp_db, "nonexistent") is None


class TestSessionSummaries:
    def test_start_and_close_session(self, tmp_db):
        session = storage.start_session(
            tmp_db,
            project_id="p1",
            project_name="Project One",
            agent="codex",
            working_dir="/tmp/project-one",
        )
        assert session["id"]
        assert session["ended_at"] is None

        open_session = storage.get_open_session(tmp_db, session["id"])
        assert open_session is not None
        assert open_session["project_name"] == "Project One"

        closed = storage.close_session(
            tmp_db,
            session["id"],
            draft_path="1_drafts/sessions/p1.md",
            title="Session Summary",
            summary="Did useful work",
            ended_at="2026-05-30T01:00:00Z",
        )
        assert closed is not None
        assert closed["ended_at"] == "2026-05-30T01:00:00Z"
        assert storage.get_open_session(tmp_db, session["id"]) is None

    def test_recent_summaries_are_project_scoped_and_exclude_current(self, tmp_db):
        older = storage.start_session(tmp_db, "p1", "Project One", "codex")
        storage.close_session(
            tmp_db, older["id"], "1_drafts/sessions/old.md", "Old", "Old summary",
            ended_at="2026-05-30T01:00:00Z",
        )
        newer = storage.start_session(tmp_db, "p1", "Project One", "codex")
        storage.close_session(
            tmp_db, newer["id"], "1_drafts/sessions/new.md", "New", "New summary",
            ended_at="2026-05-30T02:00:00Z",
        )
        other = storage.start_session(tmp_db, "p2", "Project Two", "codex")
        storage.close_session(
            tmp_db, other["id"], "1_drafts/sessions/other.md", "Other", "Other summary",
            ended_at="2026-05-30T03:00:00Z",
        )
        current = storage.start_session(tmp_db, "p1", "Project One", "codex")

        recent = storage.list_recent_session_summaries(
            tmp_db,
            project_id="p1",
            limit=5,
            exclude_session_id=current["id"],
        )
        assert [r["id"] for r in recent] == [newer["id"], older["id"]]


class TestGroupLifecycle:
    def test_start_and_end(self, tmp_db):
        result = storage.start_group(tmp_db, agent="claude", metadata={"project_id": "p1"})
        assert result["group_id"]
        assert result["segment_start_at"]

        ended = storage.end_group(tmp_db, result["group_id"])
        assert ended is not None
        assert ended["segment_end_at"] is not None
        assert ended["segment_start_at"] == result["segment_start_at"]

    def test_end_is_idempotent(self, tmp_db):
        result = storage.start_group(tmp_db, agent="claude")
        first = storage.end_group(tmp_db, result["group_id"])
        second = storage.end_group(tmp_db, result["group_id"])
        assert first is not None and second is not None
        assert first["segment_end_at"] == second["segment_end_at"]

    def test_continuation_reuses_group_id_new_segment(self, tmp_db):
        # First segment: start, turns, end
        r1 = storage.start_group(tmp_db, agent="claude")
        gid = r1["group_id"]
        storage.create_turns(tmp_db, gid, [{"request": "q", "response": "a"}])
        storage.end_group(tmp_db, gid)

        # Continuation
        r2 = storage.start_group(tmp_db, group_id=gid, agent="claude")
        assert r2["group_id"] == gid
        assert r2["segment_start_at"] != r1["segment_start_at"]

        segs = storage.get_group_segments(tmp_db, gid)
        assert len(segs) == 2
        assert segs[0]["segment_end_at"] is not None
        assert segs[1]["segment_end_at"] is None  # second segment still open

    def test_open_groups_excludes_ended(self, tmp_db):
        r = storage.start_group(tmp_db, agent="claude")
        assert len(storage.get_open_groups(tmp_db)) == 1
        storage.end_group(tmp_db, r["group_id"])
        assert len(storage.get_open_groups(tmp_db)) == 0


class TestIdleClose:
    def test_idle_close_on_stale_via_create_turns(self, tmp_db):
        r = storage.start_group(tmp_db, agent="claude")
        gid = r["group_id"]
        storage.create_turns(tmp_db, gid, [{"request": "old", "response": "old"}])

        # Make the segment stale.
        tmp_db.execute(
            "UPDATE turns SET created_at = '2026-01-01T00:00:00Z' WHERE group_id = ?",
            (gid,),
        )
        tmp_db.commit()

        result = storage.create_turns(tmp_db, gid, [{"request": "new", "response": "new"}])
        assert result["idle_closed_segment"] is not None
        # New segment was opened.
        segs = storage.get_group_segments(tmp_db, gid)
        assert len(segs) == 2
        assert segs[0]["end_kind"] == "idle_close"

    def test_no_idle_close_when_recent(self, tmp_db):
        r = storage.start_group(tmp_db, agent="claude")
        result = storage.create_turns(tmp_db, r["group_id"], [{"request": "q", "response": "a"}])
        assert result["idle_closed_segment"] is None


class TestOrphans:
    def test_orphan_detected_when_old_and_open(self, tmp_db):
        r = storage.start_group(tmp_db, agent="claude")
        storage.create_turns(tmp_db, r["group_id"], [{"request": "q", "response": "a"}])
        tmp_db.execute(
            "UPDATE turns SET created_at = '2026-01-01T00:00:00Z' WHERE group_id = ?",
            (r["group_id"],),
        )
        tmp_db.commit()

        orphans = storage.get_orphaned_groups(tmp_db, older_than_hours=24)
        assert len(orphans) == 1
        assert orphans[0]["group_id"] == r["group_id"]

    def test_recovery_writes_idle_close_then_segment_is_closed_no_draft(self, tmp_db):
        r = storage.start_group(tmp_db, agent="claude")
        storage.create_turns(tmp_db, r["group_id"], [{"request": "q", "response": "a"}])
        tmp_db.execute(
            "UPDATE turns SET created_at = '2026-01-01T00:00:00Z' WHERE group_id = ?",
            (r["group_id"],),
        )
        tmp_db.commit()

        storage.end_group(tmp_db, r["group_id"], kind="idle_close")
        assert len(storage.get_orphaned_groups(tmp_db)) == 0

        cnd = storage.get_closed_no_draft_segments(tmp_db)
        assert len(cnd) == 1
        assert cnd[0]["end_kind"] == "idle_close"


class TestSegmentQueries:
    def test_get_current_segment_turns(self, tmp_db):
        r = storage.start_group(tmp_db, agent="claude")
        storage.create_turns(tmp_db, r["group_id"], [
            {"request": "q1", "response": "a1"},
            {"request": "q2", "response": "a2"},
        ])
        rows = storage.get_current_segment_turns(tmp_db, r["group_id"])
        # Includes the start marker + 2 turns
        assert len(rows) == 3
        kinds = [row["kind"] for row in rows]
        assert kinds == ["start", "turn", "turn"]

    def test_get_segment_turns_specific_segment(self, tmp_db):
        # First segment
        r1 = storage.start_group(tmp_db, agent="claude")
        gid = r1["group_id"]
        storage.create_turns(tmp_db, gid, [{"request": "seg1", "response": "."}])
        storage.end_group(tmp_db, gid)

        # Second segment
        r2 = storage.start_group(tmp_db, group_id=gid, agent="claude")
        storage.create_turns(tmp_db, gid, [{"request": "seg2", "response": "."}])

        seg1_turns = storage.get_segment_turns(tmp_db, gid, r1["segment_start_at"])
        seg2_turns = storage.get_segment_turns(tmp_db, gid, r2["segment_start_at"])

        seg1_requests = [t["request"] for t in seg1_turns if t["kind"] == "turn"]
        seg2_requests = [t["request"] for t in seg2_turns if t["kind"] == "turn"]
        assert seg1_requests == ["seg1"]
        assert seg2_requests == ["seg2"]


class TestListGroups:
    def test_filter_by_agent(self, tmp_db):
        storage.start_group(tmp_db, agent="claude", metadata={"agent": "claude"})
        storage.start_group(tmp_db, agent="codex", metadata={"agent": "codex"})

        claude_only = storage.list_groups(tmp_db, filter_metadata={"agent": "claude"})
        assert len(claude_only) == 1
        assert claude_only[0]["metadata"]["agent"] == "claude"


class TestMemoryEdits:
    def test_create_and_history_with_group(self, tmp_db):
        r = storage.start_group(tmp_db, agent="claude")
        gid = r["group_id"]

        storage.create_memory_edit(
            tmp_db, "1_drafts/sessions/abc-20260420-1030.md", "draft", "create",
            "Created session draft", group_id=gid,
        )
        storage.create_memory_edit(
            tmp_db, "1_drafts/sessions/abc-20260420-1030.md", "draft", "update",
            "Edited session draft",
        )

        history = storage.get_memory_history(tmp_db)
        assert len(history) == 2

        filtered = storage.get_memory_history(
            tmp_db, page_path="1_drafts/sessions/abc-20260420-1030.md")
        assert len(filtered) == 2

    def test_history_limit(self, tmp_db):
        for i in range(5):
            storage.create_memory_edit(
                tmp_db, f"1_drafts/sessions/p{i}.md", "draft", "create", f"p{i}")
        history = storage.get_memory_history(tmp_db, limit=3)
        assert len(history) == 3
