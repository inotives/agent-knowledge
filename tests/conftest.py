"""Shared test fixtures."""

import pytest

from agent_knowledge.core import storage, search


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database with auto-migration."""
    db_path = tmp_path / "sessions.db"
    conn = storage.connect(db_path)
    yield conn
    conn.close()


@pytest.fixture
def tmp_memory(tmp_path):
    """Create a temporary memory directory."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    return memory_dir


@pytest.fixture
def tmp_search(tmp_path):
    """Create a temporary DuckDB search index."""
    db_path = tmp_path / "search.db"
    conn = search.connect(db_path)
    yield conn
    conn.close()
