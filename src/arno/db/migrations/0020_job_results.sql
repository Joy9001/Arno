-- 0020: a finished job keeps what its handler returned, so the app can say what the job did
-- (how many rows a categorize run labeled, say) instead of only that it ran.

ALTER TABLE jobs ADD COLUMN result jsonb;
