-- A durable job keeps the W3C origin context it was enqueued under, never prompt content.
-- Nullable and additive: old rows, a malformed payload, and an idle scheduler tick all read as
-- "no origin" and the worker starts a new root trace.
ALTER TABLE jobs ADD COLUMN trace_context JSONB;
