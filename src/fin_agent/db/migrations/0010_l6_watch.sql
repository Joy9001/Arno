-- 0010: L6 watch.
--
-- `job_schedules` is the recurring half of the plain-table scheduler: a due row creates one job
-- keyed by its kind and period, then moves on from now, so a restart or a sleeping machine can
-- collapse any number of missed runs into one. The unique `(kind, payload)` index makes seeding
-- built-in schedules and syncing the user's scheduled questions idempotent.
--
-- `notifications.dedupe_key` is the inbox's idempotency key. A handler that runs twice after a
-- crash inserts the same reminder, risk warning, pattern proposal, or briefing only once.

ALTER TABLE notifications
    ADD COLUMN dedupe_key TEXT;

-- A plain unique index: Postgres allows many NULL keys, so notifications without one are fine.
CREATE UNIQUE INDEX notifications_dedupe_key_idx ON notifications (dedupe_key);

ALTER TABLE job_schedules
    ADD COLUMN last_run_at TIMESTAMPTZ;

CREATE UNIQUE INDEX job_schedules_kind_payload_idx
    ON job_schedules (kind, payload);

-- Reminder lead days, settled at 3 in plan.md §6.
ALTER TABLE settings
    ADD COLUMN reminder_lead_days SMALLINT NOT NULL DEFAULT 3
        CHECK (reminder_lead_days BETWEEN 0 AND 30);

-- A scheduled question's spend limit is in US cents, not paise. The L1 column name was
-- misleading; the chat tool has always stored dollars × 100.
ALTER TABLE scheduled_questions
    RENAME COLUMN spend_limit_paise TO spend_limit_cents;

-- VAPID keys are generated once, on the first run, and never sent to a client except the public
-- key. They live in the database so a container rebuild keeps the same push identity.
CREATE TABLE vapid_keys (
    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    public_key TEXT NOT NULL,
    private_key TEXT NOT NULL,
    subject TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The last projection state, so a risk episode warns once. `episode_key` is the notification's
-- dedupe key; `armed` goes false on the first warning and true again after the picture recovers.
CREATE TABLE risk_state (
    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    floor_probability DOUBLE PRECISION,
    floor_threshold DOUBLE PRECISION,
    first_floor_crossing DATE,
    armed BOOLEAN NOT NULL DEFAULT true,
    episode_key TEXT,
    last_checked_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO risk_state (id) VALUES (1);

COMMENT ON COLUMN job_schedules.last_run_at IS
    'The occurrence this schedule last turned into a job, before moving on from now.';
COMMENT ON COLUMN notifications.dedupe_key IS
    'The inbox idempotency key: a job retried after a crash cannot send an item twice.';
COMMENT ON COLUMN scheduled_questions.spend_limit_cents IS
    'Per-run spend ceiling in US cents; 100 is $1.00.';
