-- Add interval-based scheduling support to scheduled_tasks.
-- schedule_type: 'daily' (fire at minute_of_day each day) | 'interval' (fire every interval_minutes)
-- interval_minutes: only used when schedule_type = 'interval'
-- last_triggered_at: ISO datetime of last trigger, used for interval gap calculation

ALTER TABLE scheduled_tasks ADD COLUMN schedule_type TEXT NOT NULL DEFAULT 'daily';
ALTER TABLE scheduled_tasks ADD COLUMN interval_minutes INTEGER;
ALTER TABLE scheduled_tasks ADD COLUMN last_triggered_at TEXT;

DROP INDEX IF EXISTS idx_scheduled_tasks_project;
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_project
    ON scheduled_tasks(project_id, enabled, schedule_type, minute_of_day);
