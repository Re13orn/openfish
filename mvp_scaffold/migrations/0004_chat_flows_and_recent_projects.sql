ALTER TABLE chat_context
    ADD COLUMN pending_flow_json TEXT;

CREATE TABLE IF NOT EXISTS user_project_activity (
    user_id INTEGER NOT NULL,
    project_key TEXT NOT NULL,
    is_pinned INTEGER NOT NULL DEFAULT 0,
    use_count INTEGER NOT NULL DEFAULT 1,
    last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, project_key),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_user_project_activity_recent
    ON user_project_activity(user_id, is_pinned DESC, last_used_at DESC, updated_at DESC);
