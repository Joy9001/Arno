-- 0018: an import whose parser ran out of steps is paused, not failed. The owner can continue it,
-- which resumes the same parser task with a fresh step budget.

ALTER TABLE imports DROP CONSTRAINT IF EXISTS imports_status_check;
ALTER TABLE imports
    ADD CONSTRAINT imports_status_check CHECK (
        status IN (
            'parsing', 'needs_password', 'needs_input', 'review', 'not_a_statement',
            'failed', 'committed', 'rejected', 'paused'
        )
    );
