"""End-to-end recovery flow: orphans + closed-no-draft → stub drafts."""

from click.testing import CliRunner

from agent_knowledge.core import storage
from agent_knowledge import cli


def _make_stale(conn, group_id: str, ts: str = "2026-01-01T00:00:00Z") -> None:
    conn.execute("UPDATE turns SET created_at = ? WHERE group_id = ?", (ts, group_id))
    conn.commit()


class TestRecoverDryRun:
    def test_dry_run_reports_orphans(self, tmp_db):
        r = storage.start_group(tmp_db, agent="claude")
        storage.create_turns(tmp_db, r["group_id"], [{"request": "q", "response": "a"}])
        _make_stale(tmp_db, r["group_id"])

        # Pre-condition: 1 orphan exists
        assert len(storage.get_orphaned_groups(tmp_db)) == 1


class TestRecoverHappyPath:
    def _setup_isolated_config(self, tmp_path, monkeypatch):
        """Point load_config at an isolated data_dir and pre-create memory dirs."""
        data_dir = tmp_path / "data"
        monkeypatch.setenv("AKW_DATA_DIR", str(data_dir))

        from agent_knowledge.core.config import load_config
        config = load_config()
        config.db_dir.mkdir(parents=True, exist_ok=True)
        memory_dir = config.memory_dir
        memory_dir.mkdir(parents=True, exist_ok=True)
        (memory_dir / "1_drafts" / "sessions").mkdir(parents=True, exist_ok=True)
        (memory_dir / "1_drafts" / "_archived").mkdir(parents=True, exist_ok=True)
        return config

    def test_recover_writes_idle_close_and_stub(self, tmp_path, monkeypatch):
        config = self._setup_isolated_config(tmp_path, monkeypatch)

        # Build orphan
        conn = storage.connect(config.sessions_db)
        r = storage.start_group(conn, agent="claude", metadata={"agent": "claude"})
        storage.create_turns(conn, r["group_id"], [{"request": "old", "response": "old"}])
        _make_stale(conn, r["group_id"])
        conn.close()

        # Run recover
        runner = CliRunner()
        result = runner.invoke(cli.main, ["recover"])
        assert result.exit_code == 0, result.output
        assert "wrote" in result.output.lower()

        # Verify a stub draft was written
        stubs = list((config.memory_dir / "1_drafts" / "sessions").glob("*.md"))
        assert len(stubs) == 1, f"Expected 1 stub, got {len(stubs)}: {result.output}"
        content = stubs[0].read_text()
        assert "recovery_kind: idle_close" in content
        assert "Session segment recovered without summary" in content

    def test_recover_idempotent(self, tmp_path, monkeypatch):
        config = self._setup_isolated_config(tmp_path, monkeypatch)

        conn = storage.connect(config.sessions_db)
        r = storage.start_group(conn, agent="claude", metadata={"agent": "claude"})
        storage.create_turns(conn, r["group_id"], [{"request": "q", "response": "a"}])
        _make_stale(conn, r["group_id"])
        conn.close()

        runner = CliRunner()
        first = runner.invoke(cli.main, ["recover"])
        second = runner.invoke(cli.main, ["recover"])
        assert first.exit_code == 0
        assert second.exit_code == 0

        stubs = list((config.memory_dir / "1_drafts" / "sessions").glob("*.md"))
        assert len(stubs) == 1
