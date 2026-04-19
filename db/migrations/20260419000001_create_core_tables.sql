-- migrate:up

CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    agent TEXT NOT NULL,
    type TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ended_at TEXT,
    reviewed_at TEXT,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    request TEXT NOT NULL,
    response TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE memory_edits (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    page_path TEXT NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('draft', 'knowledge', 'skill')),
    action TEXT NOT NULL CHECK (action IN ('create', 'update', 'delete')),
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_sessions_project_id ON sessions(project_id);
CREATE INDEX idx_sessions_ended_at ON sessions(ended_at);
CREATE INDEX idx_sessions_reviewed_at ON sessions(reviewed_at);
CREATE INDEX idx_turns_session_id ON turns(session_id);
CREATE INDEX idx_turns_created_at ON turns(created_at);
CREATE INDEX idx_memory_edits_session_id ON memory_edits(session_id);
CREATE INDEX idx_memory_edits_page_path ON memory_edits(page_path);
CREATE INDEX idx_memory_edits_tier ON memory_edits(tier);

-- migrate:down

DROP INDEX IF EXISTS idx_memory_edits_tier;
DROP INDEX IF EXISTS idx_memory_edits_page_path;
DROP INDEX IF EXISTS idx_memory_edits_session_id;
DROP INDEX IF EXISTS idx_turns_created_at;
DROP INDEX IF EXISTS idx_turns_session_id;
DROP INDEX IF EXISTS idx_sessions_reviewed_at;
DROP INDEX IF EXISTS idx_sessions_ended_at;
DROP INDEX IF EXISTS idx_sessions_project_id;
DROP TABLE IF EXISTS memory_edits;
DROP TABLE IF EXISTS turns;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS projects;
