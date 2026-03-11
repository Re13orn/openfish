CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    telegram_chat_id TEXT NOT NULL,
    command_type TEXT NOT NULL CHECK(command_type IN ('ask', 'do')),
    request_text TEXT NOT NULL,
    minute_of_day INTEGER NOT NULL CHECK(minute_of_day >= 0 AND minute_of_day <= 1439),
    enabled INTEGER NOT NULL DEFAULT 1,
    last_triggered_on TEXT,
    last_task_id INTEGER,
    last_run_status TEXT,
    last_run_summary TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (last_task_id) REFERENCES tasks(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_project
    ON scheduled_tasks(project_id, enabled, minute_of_day);
