-- Replay must retain the original Phoenix project after a CLI/eval exits or config changes.
ALTER TABLE llm_calls ADD COLUMN project_name TEXT;
