-- Preserve opaque conversation attribution when replaying a cost-only span.
ALTER TABLE llm_calls ADD COLUMN session_id TEXT;
