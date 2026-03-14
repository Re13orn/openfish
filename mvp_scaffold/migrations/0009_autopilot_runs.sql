CREATE TABLE IF NOT EXISTS autopilot_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    chat_id TEXT NOT NULL,
    created_by_user_id INTEGER NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    supervisor_session_id TEXT,
    worker_session_id TEXT,
    current_phase TEXT NOT NULL DEFAULT 'idle',
    cycle_count INTEGER NOT NULL DEFAULT 0,
    max_cycles INTEGER NOT NULL DEFAULT 100,
    no_progress_cycles INTEGER NOT NULL DEFAULT 0,
    same_instruction_cycles INTEGER NOT NULL DEFAULT 0,
    last_instruction_fingerprint TEXT,
    last_decision TEXT,
    last_worker_summary TEXT,
    last_supervisor_summary TEXT,
    paused_reason TEXT,
    stopped_by_user_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (stopped_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_autopilot_runs_project_created
    ON autopilot_runs(project_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_autopilot_runs_status_updated
    ON autopilot_runs(status, updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS autopilot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    cycle_no INTEGER NOT NULL,
    actor TEXT NOT NULL,
    event_type TEXT NOT NULL,
    summary TEXT,
    payload_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES autopilot_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_autopilot_events_run_created
    ON autopilot_events(run_id, created_at ASC, id ASC);
