-- 0014: link an import to the parser task that opened it, and give the chat card a live line.
-- `progress` is host-made text only; a model or statement string never goes here.

ALTER TABLE imports
    ADD COLUMN task_id TEXT,
    ADD COLUMN progress TEXT;

CREATE INDEX imports_task_id_idx ON imports (task_id) WHERE task_id IS NOT NULL;
