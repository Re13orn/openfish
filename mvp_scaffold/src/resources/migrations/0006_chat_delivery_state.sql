ALTER TABLE chat_context
    ADD COLUMN last_outbound_message_id TEXT;

ALTER TABLE chat_context
    ADD COLUMN last_outbound_dedup_key TEXT;

ALTER TABLE chat_context
    ADD COLUMN last_outbound_context TEXT;

ALTER TABLE chat_context
    ADD COLUMN last_outbound_sent_at TEXT;
