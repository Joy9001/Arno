-- 0023: a photo's extraction reaches the model context once. A retry of an already-logged photo
-- re-reads the image and returns the same movement, but it must not append the same note again.

ALTER TABLE captures ADD COLUMN history_at TIMESTAMPTZ;

COMMENT ON COLUMN captures.history_at IS
    'When the extraction was appended to the model history, so a retry appends once.';
