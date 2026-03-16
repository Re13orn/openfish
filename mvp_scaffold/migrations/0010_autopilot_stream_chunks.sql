CREATE TABLE IF NOT EXISTS autopilot_stream_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    cycle_no INTEGER NOT NULL,
    actor TEXT NOT NULL,
    channel TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES autopilot_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_autopilot_stream_chunks_run_created
    ON autopilot_stream_chunks(run_id, created_at ASC, id ASC);
