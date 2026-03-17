-- Add category classification to project_memory notes.
-- category: 'general' | 'fact' | 'error' | 'convention' | 'decision'
-- Existing rows default to 'general' (no-noise default).

ALTER TABLE project_memory ADD COLUMN category TEXT NOT NULL DEFAULT 'general';

CREATE INDEX IF NOT EXISTS idx_project_memory_category
    ON project_memory(project_id, memory_type, category);
