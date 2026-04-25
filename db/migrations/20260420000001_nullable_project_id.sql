-- migrate:up
-- Make project_id nullable to support auto-created sessions without a project.
-- SQLite doesn't support ALTER COLUMN, so we recreate the table.

CREATE TABLE sessions_new (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    agent TEXT NOT NULL,
    type TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ended_at TEXT,
    reviewed_at TEXT,
    metadata TEXT NOT NULL DEFAULT '{}'
);

INSERT INTO sessions_new SELECT * FROM sessions;

DROP TABLE sessions;
ALTER TABLE sessions_new RENAME TO sessions;

CREATE INDEX idx_sessions_project_id ON sessions(project_id);
CREATE INDEX idx_sessions_ended_at ON sessions(ended_at);
CREATE INDEX idx_sessions_reviewed_at ON sessions(reviewed_at);

-- migrate:down

CREATE TABLE sessions_old (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    agent TEXT NOT NULL,
    type TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ended_at TEXT,
    reviewed_at TEXT,
    metadata TEXT NOT NULL DEFAULT '{}'
);

INSERT INTO sessions_old SELECT * FROM sessions WHERE project_id IS NOT NULL;

DROP TABLE sessions;
ALTER TABLE sessions_old RENAME TO sessions;

CREATE INDEX idx_sessions_project_id ON sessions(project_id);
CREATE INDEX idx_sessions_ended_at ON sessions(ended_at);
CREATE INDEX idx_sessions_reviewed_at ON sessions(reviewed_at);
