-- 0001: baseline schema. Squashes the pre-release migrations 1-50; the pgledger ledger is replaced
-- by plain double-entry legs, and undo is a snapshot log (see docs/arno/specs).
--
-- The API connects as `app` and owns the schema. `agent_ro` is the boundary for model-written
-- SQL: SELECT grants only, no TEMP, read-only by default, a two-second statement timeout. `backup`
-- reads everything for `pg_dump` and inserts one `backup_runs` row per attempt. Passwords are set
-- from the environment at boot; the roles have none until then.

-- Roles

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_ro') THEN
        CREATE ROLE agent_ro LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backup') THEN
        CREATE ROLE backup LOGIN;
    END IF;
END
$$;

ALTER ROLE agent_ro NOCREATEDB NOCREATEROLE;
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '2s';
ALTER ROLE backup NOCREATEDB NOCREATEROLE;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO agent_ro, backup', current_database());
    EXECUTE format('REVOKE TEMP ON DATABASE %I FROM PUBLIC', current_database());
END
$$;

-- Ids: a prefix plus a time-sortable UUIDv7, e.g. mov_0198...

CREATE FUNCTION new_id(prefix text) RETURNS text
    LANGUAGE sql VOLATILE
    AS $$ SELECT prefix || '_' || replace(uuidv7()::text, '-', '') $$;

-- Tables

CREATE TABLE accounts (
    id text DEFAULT new_id('acc') NOT NULL,
    kind text NOT NULL,
    name text,
    institution text,
    last4 text,
    statement_balance_paise bigint,
    statement_balance_as_of date,
    liquid boolean DEFAULT false NOT NULL,
    credit_limit_paise bigint,
    statement_day smallint,
    due_day smallint,
    rate_bps integer,
    tenure_months integer,
    emi_schedule_id text,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    emi_rate_bps integer,
    emi_fee_paise bigint,
    finance_rate_bps integer,
    min_due_bps integer,
    min_due_floor_paise bigint,
    CONSTRAINT accounts_card_fields_check CHECK (((kind <> 'card') OR ((credit_limit_paise IS NOT NULL) AND (statement_day IS NOT NULL) AND (due_day IS NOT NULL)))),
    CONSTRAINT accounts_check CHECK (((NOT liquid) OR (kind = ANY (ARRAY['bank', 'cash', 'wallet'])))),
    CONSTRAINT accounts_check1 CHECK (((kind = 'card') OR ((credit_limit_paise IS NULL) AND (statement_day IS NULL) AND (due_day IS NULL)))),
    CONSTRAINT accounts_check2 CHECK (((kind = 'loan') OR ((rate_bps IS NULL) AND (tenure_months IS NULL) AND (emi_schedule_id IS NULL)))),
    CONSTRAINT accounts_credit_limit_paise_check CHECK ((credit_limit_paise > 0)),
    CONSTRAINT accounts_due_day_check CHECK (((due_day >= 1) AND (due_day <= 31))),
    CONSTRAINT accounts_emi_fee_paise_check CHECK ((emi_fee_paise >= 0)),
    CONSTRAINT accounts_emi_rate_bps_check CHECK (((emi_rate_bps >= 0) AND (emi_rate_bps <= 10000))),
    CONSTRAINT accounts_emi_terms_card_check CHECK (((kind = 'card') OR ((emi_rate_bps IS NULL) AND (emi_fee_paise IS NULL)))),
    CONSTRAINT accounts_finance_rate_bps_check CHECK (((finance_rate_bps >= 0) AND (finance_rate_bps <= 10000))),
    CONSTRAINT accounts_finance_terms_card_check CHECK (((kind = 'card') OR ((finance_rate_bps IS NULL) AND (min_due_bps IS NULL) AND (min_due_floor_paise IS NULL)))),
    CONSTRAINT accounts_kind_check CHECK ((kind = ANY (ARRAY['bank', 'card', 'cash', 'wallet', 'loan', 'asset', 'goal', 'person', 'spending', 'income', 'equity']))),
    CONSTRAINT accounts_last4_check CHECK (((last4 IS NULL) OR (last4 ~ '^[0-9]{4}$'))),
    CONSTRAINT accounts_min_due_bps_check CHECK (((min_due_bps >= 0) AND (min_due_bps <= 10000))),
    CONSTRAINT accounts_min_due_floor_paise_check CHECK ((min_due_floor_paise >= 0)),
    CONSTRAINT accounts_name_kind_check CHECK (((kind = ANY (ARRAY['person', 'goal'])) = (name IS NULL))),
    CONSTRAINT accounts_rate_bps_check CHECK ((rate_bps >= 0)),
    CONSTRAINT accounts_statement_balance_pair_check CHECK (((statement_balance_paise IS NULL) = (statement_balance_as_of IS NULL))),
    CONSTRAINT accounts_statement_day_check CHECK (((statement_day >= 1) AND (statement_day <= 31))),
    CONSTRAINT accounts_tenure_months_check CHECK ((tenure_months > 0))
);

CREATE TABLE attachments (
    id text DEFAULT new_id('att') NOT NULL,
    kind text NOT NULL,
    filename text,
    content_type text,
    size_bytes bigint,
    sha256 text NOT NULL,
    data bytea,
    purged_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    unlocked_at timestamp with time zone,
    CONSTRAINT attachments_check CHECK (((data IS NOT NULL) OR (purged_at IS NOT NULL))),
    CONSTRAINT attachments_kind_check CHECK ((kind = ANY (ARRAY['statement', 'photo', 'audio']))),
    CONSTRAINT attachments_size_bytes_check CHECK (((size_bytes IS NULL) OR (size_bytes >= 0)))
);

CREATE TABLE backup_runs (
    id text DEFAULT new_id('bkr') NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    status text NOT NULL,
    dump_bytes bigint,
    duration_ms bigint,
    restic_snapshot text,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT backup_runs_dump_bytes_check CHECK (((dump_bytes IS NULL) OR (dump_bytes >= 0))),
    CONSTRAINT backup_runs_duration_ms_check CHECK (((duration_ms IS NULL) OR (duration_ms >= 0))),
    CONSTRAINT backup_runs_status_check CHECK ((status = ANY (ARRAY['success', 'failed'])))
);

CREATE TABLE baselines (
    id text DEFAULT new_id('bas') NOT NULL,
    category_id text NOT NULL,
    amount_per_day_paise bigint NOT NULL,
    source text NOT NULL,
    window_days integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT baselines_amount_per_day_paise_check CHECK ((amount_per_day_paise >= 0)),
    CONSTRAINT baselines_source_check CHECK ((source = ANY (ARRAY['estimated', 'computed']))),
    CONSTRAINT baselines_window_days_check CHECK (((window_days IS NULL) OR (window_days > 0)))
);

CREATE TABLE captures (
    id text DEFAULT new_id('cap') NOT NULL,
    movement_id text,
    kind text NOT NULL,
    ref text,
    upi_ref text,
    confidence numeric(3,2),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT captures_confidence_check CHECK (((confidence IS NULL) OR ((confidence >= (0)::numeric) AND (confidence <= (1)::numeric)))),
    CONSTRAINT captures_kind_check CHECK ((kind = ANY (ARRAY['chat', 'voice', 'photo', 'statement_line'])))
);

CREATE TABLE categories (
    id text DEFAULT new_id('cat') NOT NULL,
    name text NOT NULL,
    reducible boolean DEFAULT false NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE category_rules (
    id text DEFAULT new_id('rul') NOT NULL,
    category_id text NOT NULL,
    source text NOT NULL,
    hits bigint DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    match text DEFAULT 'equals' NOT NULL,
    pattern text NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    CONSTRAINT category_rules_hits_check CHECK ((hits >= 0)),
    CONSTRAINT category_rules_match_check CHECK ((match = ANY (ARRAY['equals', 'contains', 'regex']))),
    CONSTRAINT category_rules_source_check CHECK ((source = ANY (ARRAY['user', 'llm'])))
);

CREATE TABLE conversations (
    id text DEFAULT new_id('cnv') NOT NULL,
    title text,
    model_history jsonb,
    snapshot_at timestamp with time zone,
    outside_text_at timestamp with time zone,
    run_started_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    active_run_id text,
    CONSTRAINT conversations_active_run_id_check CHECK (((active_run_id IS NULL) OR (length(active_run_id) <= 100)))
);

COMMENT ON COLUMN conversations.active_run_id IS 'The running turn; cleared with run_started_at when both records are saved.';

CREATE TABLE goals (
    id text DEFAULT new_id('gol') NOT NULL,
    name text NOT NULL,
    target_paise bigint NOT NULL,
    deadline date,
    priority smallint DEFAULT 3 NOT NULL,
    account_id text NOT NULL,
    contribution_schedule_id text,
    status text DEFAULT 'active' NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT goals_priority_check CHECK (((priority >= 1) AND (priority <= 5))),
    CONSTRAINT goals_status_check CHECK ((status = ANY (ARRAY['active', 'achieved', 'paused', 'abandoned']))),
    CONSTRAINT goals_target_paise_check CHECK ((target_paise > 0))
);

CREATE TABLE imports (
    id text DEFAULT new_id('imp') NOT NULL,
    file_hash text NOT NULL,
    account_id text,
    period_start date,
    period_end date,
    opening_balance_paise bigint,
    closing_balance_paise bigint,
    check_result jsonb,
    status text DEFAULT 'review' NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    committed_at timestamp with time zone,
    attachment_id text,
    institution text,
    reason text,
    counts jsonb,
    review jsonb,
    commit_groups jsonb DEFAULT '[]'::jsonb NOT NULL,
    rejected_at timestamp with time zone,
    undone_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    progress text,
    categorize_groups jsonb DEFAULT '[]'::jsonb NOT NULL,
    check_status text,
    categorize jsonb,
    CONSTRAINT imports_check CHECK (((period_start IS NULL) OR (period_end IS NULL) OR (period_end >= period_start))),
    CONSTRAINT imports_check_status_check CHECK (((check_status IS NULL) OR (check_status = ANY (ARRAY['VERIFIED', 'DISCREPANCY', 'FAILED'])))),
    CONSTRAINT imports_status_check CHECK ((status = ANY (ARRAY['parsing', 'needs_password', 'needs_input', 'review', 'not_a_statement', 'failed', 'committed', 'rejected'])))
);

COMMENT ON COLUMN imports.categorize_groups IS 'Write groups the rules engine and categorizer made from this import, oldest first.';

COMMENT ON COLUMN imports.categorize IS 'The categorize state: queued, running, done, partial, or failed, with its counts or error.';

CREATE TABLE llm_usage (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    role text NOT NULL,
    model text NOT NULL,
    input_tokens integer NOT NULL,
    output_tokens integer NOT NULL,
    usd numeric(10,6) NOT NULL
);

CREATE TABLE messages (
    id text DEFAULT new_id('msg') NOT NULL,
    conversation_id text NOT NULL,
    client_id text NOT NULL,
    role text NOT NULL,
    content jsonb NOT NULL,
    body_text text,
    search tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, COALESCE(body_text, ''))) STORED,
    trace_id text,
    flagged_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    run_id text,
    CONSTRAINT messages_role_check CHECK ((role = ANY (ARRAY['user', 'assistant', 'system']))),
    CONSTRAINT messages_run_id_check CHECK (((run_id IS NULL) OR (length(run_id) <= 100)))
);

COMMENT ON COLUMN messages.run_id IS 'The turn that produced this message, used to rebuild the transcript on every ending.';

CREATE TABLE notifications (
    id text DEFAULT new_id('ntf') NOT NULL,
    kind text NOT NULL,
    title text NOT NULL,
    body text,
    data jsonb,
    read_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    dedupe_key text
);

COMMENT ON COLUMN notifications.dedupe_key IS 'The inbox idempotency key: a job retried after a crash cannot send an item twice.';

CREATE TABLE payees (
    id text DEFAULT new_id('pay') NOT NULL,
    display_name text NOT NULL,
    kind text NOT NULL,
    upi_id text,
    match_hash text,
    default_category_id text,
    person_account_id text,
    outside_text boolean DEFAULT false NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT payees_kind_check CHECK ((kind = ANY (ARRAY['person', 'merchant', 'organization'])))
);

CREATE TABLE preferences (
    id text DEFAULT new_id('prf') NOT NULL,
    body text NOT NULL,
    created_by text NOT NULL,
    source_message_id text,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    kind text DEFAULT 'preference' NOT NULL,
    subject text,
    CONSTRAINT preferences_created_by_check CHECK ((created_by = ANY (ARRAY['agent', 'user']))),
    CONSTRAINT preferences_kind_check CHECK ((kind = ANY (ARRAY['preference', 'fact', 'rule'])))
);

COMMENT ON COLUMN preferences.subject IS 'Normalized topic key; a new active memory with the same kind and subject replaces the old one.';

CREATE TABLE recap_views (
    period_id text NOT NULL,
    seen_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE schedule_occurrences (
    id text DEFAULT new_id('occ') NOT NULL,
    schedule_id text NOT NULL,
    due_date date NOT NULL,
    name text NOT NULL,
    kind text NOT NULL,
    subtype text,
    outside_text boolean DEFAULT false NOT NULL,
    amount_paise bigint,
    account_id text,
    category_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT schedule_occurrences_amount_paise_check CHECK (((amount_paise IS NULL) OR (amount_paise > 0))),
    CONSTRAINT schedule_occurrences_kind_check CHECK ((kind = ANY (ARRAY['income_fixed', 'income_variable', 'income_oneoff', 'obligation', 'instalment', 'goal_contribution']))),
    CONSTRAINT schedule_occurrences_obligation_subtype_check CHECK (((kind = 'obligation') OR (subtype IS NULL))),
    CONSTRAINT schedule_occurrences_subtype_check CHECK (((subtype IS NULL) OR (subtype = ANY (ARRAY['rent', 'emi', 'subscription', 'insurance', 'family_transfer', 'other']))))
);

CREATE TABLE schedule_payments (
    id text DEFAULT new_id('pay') NOT NULL,
    occurrence_id text NOT NULL,
    movement_id text NOT NULL,
    amount_paise bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT schedule_payments_amount_paise_check CHECK ((amount_paise > 0))
);

CREATE TABLE scheduled_questions (
    id text DEFAULT new_id('sq') NOT NULL,
    prompt text NOT NULL,
    rule text NOT NULL,
    spend_limit_cents bigint,
    last_run_at timestamp with time zone,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    next_run_at timestamp with time zone,
    CONSTRAINT scheduled_questions_spend_limit_paise_check CHECK (((spend_limit_cents IS NULL) OR (spend_limit_cents >= 0)))
);

COMMENT ON COLUMN scheduled_questions.spend_limit_cents IS 'Per-run spend ceiling in US cents; 100 is $1.00.';

CREATE TABLE schedules (
    id text DEFAULT new_id('sch') NOT NULL,
    kind text NOT NULL,
    subtype text,
    name text NOT NULL,
    amount_paise bigint,
    account_id text,
    category_id text,
    rule text NOT NULL,
    confirmed boolean DEFAULT true NOT NULL,
    starts date NOT NULL,
    ends date,
    active boolean DEFAULT true NOT NULL,
    outside_text boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    holiday_adjust text DEFAULT 'none' NOT NULL,
    CONSTRAINT schedules_amount_paise_check CHECK (((amount_paise IS NULL) OR (amount_paise > 0))),
    CONSTRAINT schedules_check CHECK (((ends IS NULL) OR (ends >= starts))),
    CONSTRAINT schedules_check1 CHECK (((kind = 'obligation') OR (subtype IS NULL))),
    CONSTRAINT schedules_holiday_adjust_check CHECK ((holiday_adjust = ANY (ARRAY['none', 'next', 'previous']))),
    CONSTRAINT schedules_kind_check CHECK ((kind = ANY (ARRAY['income_fixed', 'income_variable', 'income_oneoff', 'obligation', 'instalment', 'goal_contribution']))),
    CONSTRAINT schedules_subtype_check CHECK (((subtype IS NULL) OR (subtype = ANY (ARRAY['rent', 'emi', 'subscription', 'insurance', 'family_transfer', 'other']))))
);

CREATE TABLE series_detections (
    id text DEFAULT new_id('det') NOT NULL,
    payee_id text NOT NULL,
    kind text NOT NULL,
    family text NOT NULL,
    rrule text NOT NULL,
    starts date NOT NULL,
    holiday_adjust text NOT NULL,
    description text NOT NULL,
    confidence double precision NOT NULL,
    alive double precision NOT NULL,
    skip double precision NOT NULL,
    jitter jsonb NOT NULL,
    amount_kind text NOT NULL,
    level_paise bigint NOT NULL,
    low_paise bigint NOT NULL,
    high_paise bigint NOT NULL,
    samples bigint[] NOT NULL,
    matched_movement_ids text[] NOT NULL,
    card_id text,
    category_id text,
    as_of date NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT series_detections_alive_check CHECK (((alive >= (0)::double precision) AND (alive <= (1)::double precision))),
    CONSTRAINT series_detections_amount_kind_check CHECK ((amount_kind = ANY (ARRAY['fixed', 'stepped', 'variable']))),
    CONSTRAINT series_detections_confidence_check CHECK (((confidence >= (0)::double precision) AND (confidence <= (1)::double precision))),
    CONSTRAINT series_detections_kind_check CHECK ((kind = ANY (ARRAY['spend', 'income']))),
    CONSTRAINT series_detections_skip_check CHECK (((skip >= (0)::double precision) AND (skip <= (1)::double precision)))
);

CREATE TABLE settings (
    id smallint DEFAULT 1 NOT NULL,
    base_currency text DEFAULT 'INR' NOT NULL,
    time_zone text DEFAULT 'Asia/Kolkata' NOT NULL,
    floor_paise bigint,
    default_horizon_days integer DEFAULT 90 NOT NULL,
    notification_time time without time zone DEFAULT '08:00:00'::time without time zone NOT NULL,
    weekly_briefing_dow smallint DEFAULT 0 NOT NULL,
    last_model_error_type text,
    last_model_error_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    reminder_lead_days smallint DEFAULT 3 NOT NULL,
    display_name text DEFAULT 'You' NOT NULL,
    CONSTRAINT settings_base_currency_check CHECK ((base_currency ~ '^[A-Z]{3}$')),
    CONSTRAINT settings_default_horizon_days_check CHECK (((default_horizon_days >= 1) AND (default_horizon_days <= 3650))),
    CONSTRAINT settings_floor_paise_check CHECK ((floor_paise >= 0)),
    CONSTRAINT settings_id_check CHECK ((id = 1)),
    CONSTRAINT settings_reminder_lead_days_check CHECK (((reminder_lead_days >= 0) AND (reminder_lead_days <= 30))),
    CONSTRAINT settings_weekly_briefing_dow_check CHECK (((weekly_briefing_dow >= 0) AND (weekly_briefing_dow <= 6)))
);

CREATE TABLE statement_lines (
    id text DEFAULT new_id('stl') NOT NULL,
    import_id text NOT NULL,
    date date NOT NULL,
    description_raw text NOT NULL,
    amount_paise bigint NOT NULL,
    upi_ref text,
    content_hash text NOT NULL,
    match_state text DEFAULT 'new' NOT NULL,
    movement_id text,
    outside_text boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    debit_paise bigint,
    credit_paise bigint,
    balance_paise bigint,
    match_detail jsonb,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT statement_lines_match_state_check CHECK ((match_state = ANY (ARRAY['new', 'certain', 'ambiguous', 'merged', 'ignored'])))
);

-- Unique constraints and primary keys

ALTER TABLE accounts
    ADD CONSTRAINT accounts_pkey PRIMARY KEY (id);

ALTER TABLE attachments
    ADD CONSTRAINT attachments_pkey PRIMARY KEY (id);

ALTER TABLE backup_runs
    ADD CONSTRAINT backup_runs_pkey PRIMARY KEY (id);

ALTER TABLE baselines
    ADD CONSTRAINT baselines_category_id_key UNIQUE (category_id);

ALTER TABLE baselines
    ADD CONSTRAINT baselines_pkey PRIMARY KEY (id);

ALTER TABLE captures
    ADD CONSTRAINT captures_pkey PRIMARY KEY (id);

ALTER TABLE categories
    ADD CONSTRAINT categories_pkey PRIMARY KEY (id);

ALTER TABLE category_rules
    ADD CONSTRAINT category_rules_match_pattern_key UNIQUE (match, pattern);

ALTER TABLE category_rules
    ADD CONSTRAINT category_rules_pkey PRIMARY KEY (id);

ALTER TABLE conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);

ALTER TABLE goals
    ADD CONSTRAINT goals_account_id_key UNIQUE (account_id);

ALTER TABLE goals
    ADD CONSTRAINT goals_pkey PRIMARY KEY (id);

ALTER TABLE imports
    ADD CONSTRAINT imports_pkey PRIMARY KEY (id);

ALTER TABLE messages
    ADD CONSTRAINT messages_client_id_key UNIQUE (client_id);

ALTER TABLE messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);

ALTER TABLE notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);

ALTER TABLE payees
    ADD CONSTRAINT payees_person_account_id_key UNIQUE (person_account_id);

ALTER TABLE payees
    ADD CONSTRAINT payees_pkey PRIMARY KEY (id);

ALTER TABLE preferences
    ADD CONSTRAINT preferences_pkey PRIMARY KEY (id);

ALTER TABLE recap_views
    ADD CONSTRAINT recap_views_pkey PRIMARY KEY (period_id);

ALTER TABLE schedule_occurrences
    ADD CONSTRAINT schedule_occurrences_pkey PRIMARY KEY (id);

ALTER TABLE schedule_occurrences
    ADD CONSTRAINT schedule_occurrences_schedule_id_due_date_key UNIQUE (schedule_id, due_date);

ALTER TABLE schedule_payments
    ADD CONSTRAINT schedule_payments_occurrence_id_movement_id_key UNIQUE (occurrence_id, movement_id);

ALTER TABLE schedule_payments
    ADD CONSTRAINT schedule_payments_pkey PRIMARY KEY (id);

ALTER TABLE scheduled_questions
    ADD CONSTRAINT scheduled_questions_pkey PRIMARY KEY (id);

ALTER TABLE schedules
    ADD CONSTRAINT schedules_pkey PRIMARY KEY (id);

ALTER TABLE series_detections
    ADD CONSTRAINT series_detections_payee_id_kind_key UNIQUE (payee_id, kind);

ALTER TABLE series_detections
    ADD CONSTRAINT series_detections_pkey PRIMARY KEY (id);

ALTER TABLE settings
    ADD CONSTRAINT settings_pkey PRIMARY KEY (id);

ALTER TABLE statement_lines
    ADD CONSTRAINT statement_lines_import_id_content_hash_key UNIQUE (import_id, content_hash);

ALTER TABLE statement_lines
    ADD CONSTRAINT statement_lines_pkey PRIMARY KEY (id);

-- Ledger. A movement is one money event. Edits UPDATE in place; undo restores snapshots.

CREATE TABLE movements (
    id text PRIMARY KEY DEFAULT new_id('mov'),
    date date NOT NULL,
    description text NOT NULL CHECK (btrim(description) <> ''),
    kind text NOT NULL CHECK (kind IN (
        'expense', 'income', 'transfer', 'split', 'loan', 'refund', 'settlement', 'adjustment'
    )),
    idempotency_key text UNIQUE,
    one_off boolean NOT NULL DEFAULT false,
    import_id text REFERENCES imports(id) ON DELETE SET NULL,
    category_rule_id text REFERENCES category_rules(id) ON DELETE SET NULL
        DEFERRABLE INITIALLY DEFERRED,
    category_hint jsonb,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now()
);

COMMENT ON COLUMN movements.one_off IS 'A one-off payment will not recur, so the projection leaves it out of spend history.';

COMMENT ON COLUMN movements.import_id IS 'The import a balance adjustment belongs to: its opening day or the commit that re-anchored.';

-- One balanced posting pair: amount_paise moves out of from_account_id and into to_account_id, so
-- every leg, and therefore every movement, sums to zero by construction. An account's balance is
-- the sum of its incoming legs minus the sum of its outgoing legs.
CREATE TABLE movement_legs (
    transfer_id text PRIMARY KEY DEFAULT new_id('trf'),
    movement_id text NOT NULL REFERENCES movements(id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    from_account_id text NOT NULL REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED,
    to_account_id text NOT NULL REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED,
    amount_paise bigint NOT NULL CHECK (amount_paise > 0),
    category_id text REFERENCES categories(id) DEFERRABLE INITIALLY DEFERRED,
    payee_id text REFERENCES payees(id) DEFERRABLE INITIALLY DEFERRED,
    expected_date date,
    note text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CHECK (from_account_id <> to_account_id)
);

-- One row per write group (what `undo_id` names). `snapshot` is a JSON array of
-- {"table": t, "key": pk, "before": row-or-null, "after": row-or-null} in write order.
CREATE TABLE undo_log (
    id text PRIMARY KEY DEFAULT new_id('und'),
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    actor text NOT NULL CHECK (actor IN ('user', 'agent', 'system')),
    message_id text REFERENCES messages(id),
    summary text NOT NULL,
    snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot) = 'array'),
    undone_at timestamp with time zone
);

CREATE INDEX undo_log_created_idx ON undo_log (created_at DESC, id DESC);

CREATE INDEX undo_log_message_idx ON undo_log (message_id) WHERE message_id IS NOT NULL;

-- Undo conflict checks and "which group wrote this row" search snapshots with `@>`.
CREATE INDEX undo_log_snapshot_idx ON undo_log USING gin (snapshot jsonb_path_ops);

CREATE INDEX movement_legs_movement_id_idx ON movement_legs (movement_id);

CREATE INDEX movement_legs_from_account_idx ON movement_legs (from_account_id, movement_id);

CREATE INDEX movement_legs_to_account_idx ON movement_legs (to_account_id, movement_id);

CREATE INDEX movement_legs_category_id_idx ON movement_legs (category_id);

-- Indexes

CREATE UNIQUE INDEX accounts_one_system_account_per_kind ON accounts USING btree (kind) WHERE (kind = ANY (ARRAY['spending', 'income', 'equity']));
CREATE INDEX backup_runs_success_idx ON backup_runs USING btree (finished_at DESC) WHERE (status = 'success');
CREATE INDEX captures_movement_id_idx ON captures USING btree (movement_id);
CREATE INDEX captures_upi_ref_idx ON captures USING btree (upi_ref) WHERE (upi_ref IS NOT NULL);
CREATE UNIQUE INDEX categories_lower_name_idx ON categories USING btree (lower(name));
CREATE INDEX conversations_updated_at_idx ON conversations USING btree (updated_at DESC);
CREATE UNIQUE INDEX imports_attachment_id_uidx ON imports USING btree (attachment_id) WHERE (attachment_id IS NOT NULL);
CREATE INDEX imports_status_idx ON imports USING btree (status, created_at DESC);
CREATE INDEX llm_usage_created_at_idx ON llm_usage USING btree (created_at);
CREATE INDEX messages_conversation_id_idx ON messages USING btree (conversation_id, created_at);
CREATE INDEX messages_run_id_idx ON messages USING btree (run_id) WHERE (run_id IS NOT NULL);
CREATE INDEX messages_search_idx ON messages USING gin (search);
CREATE INDEX movements_date_idx ON movements USING btree (date);
CREATE INDEX movements_import_id_idx ON movements USING btree (import_id) WHERE (import_id IS NOT NULL);
CREATE UNIQUE INDEX notifications_dedupe_key_idx ON notifications USING btree (dedupe_key);
CREATE INDEX notifications_unread_idx ON notifications USING btree (created_at DESC) WHERE (read_at IS NULL);
CREATE UNIQUE INDEX payees_lower_upi_id_idx ON payees USING btree (lower(upi_id)) WHERE (upi_id IS NOT NULL);
CREATE INDEX schedule_payments_movement_idx ON schedule_payments USING btree (movement_id);
CREATE INDEX series_detections_as_of_idx ON series_detections USING btree (as_of);
CREATE INDEX statement_lines_content_hash_idx ON statement_lines USING btree (content_hash);
CREATE INDEX statement_lines_movement_id_idx ON statement_lines USING btree (movement_id);

-- Foreign keys

ALTER TABLE accounts
    ADD CONSTRAINT accounts_emi_schedule_id_fkey FOREIGN KEY (emi_schedule_id) REFERENCES schedules(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE baselines
    ADD CONSTRAINT baselines_category_id_fkey FOREIGN KEY (category_id) REFERENCES categories(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE captures
    ADD CONSTRAINT captures_movement_id_fkey FOREIGN KEY (movement_id) REFERENCES movements(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE category_rules
    ADD CONSTRAINT category_rules_category_id_fkey FOREIGN KEY (category_id) REFERENCES categories(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goals
    ADD CONSTRAINT goals_account_id_fkey FOREIGN KEY (account_id) REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goals
    ADD CONSTRAINT goals_contribution_schedule_id_fkey FOREIGN KEY (contribution_schedule_id) REFERENCES schedules(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE imports
    ADD CONSTRAINT imports_account_id_fkey FOREIGN KEY (account_id) REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE imports
    ADD CONSTRAINT imports_attachment_id_fkey FOREIGN KEY (attachment_id) REFERENCES attachments(id);

ALTER TABLE messages
    ADD CONSTRAINT messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE;

ALTER TABLE payees
    ADD CONSTRAINT payees_default_category_id_fkey FOREIGN KEY (default_category_id) REFERENCES categories(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE payees
    ADD CONSTRAINT payees_person_account_id_fkey FOREIGN KEY (person_account_id) REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE preferences
    ADD CONSTRAINT preferences_source_message_id_fkey FOREIGN KEY (source_message_id) REFERENCES messages(id);

ALTER TABLE schedule_occurrences
    ADD CONSTRAINT schedule_occurrences_account_id_fkey FOREIGN KEY (account_id) REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE schedule_occurrences
    ADD CONSTRAINT schedule_occurrences_category_id_fkey FOREIGN KEY (category_id) REFERENCES categories(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE schedule_occurrences
    ADD CONSTRAINT schedule_occurrences_schedule_id_fkey FOREIGN KEY (schedule_id) REFERENCES schedules(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE schedule_payments
    ADD CONSTRAINT schedule_payments_movement_id_fkey FOREIGN KEY (movement_id) REFERENCES movements(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE schedule_payments
    ADD CONSTRAINT schedule_payments_occurrence_id_fkey FOREIGN KEY (occurrence_id) REFERENCES schedule_occurrences(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE schedules
    ADD CONSTRAINT schedules_account_id_fkey FOREIGN KEY (account_id) REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE schedules
    ADD CONSTRAINT schedules_category_id_fkey FOREIGN KEY (category_id) REFERENCES categories(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE series_detections
    ADD CONSTRAINT series_detections_card_id_fkey FOREIGN KEY (card_id) REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE series_detections
    ADD CONSTRAINT series_detections_category_id_fkey FOREIGN KEY (category_id) REFERENCES categories(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE series_detections
    ADD CONSTRAINT series_detections_payee_id_fkey FOREIGN KEY (payee_id) REFERENCES payees(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE statement_lines
    ADD CONSTRAINT statement_lines_import_id_fkey FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE;

ALTER TABLE statement_lines
    ADD CONSTRAINT statement_lines_movement_id_fkey FOREIGN KEY (movement_id) REFERENCES movements(id) DEFERRABLE INITIALLY DEFERRED;

-- Views. The agent reads these instead of the ledger tables.

CREATE VIEW v_accounts AS
SELECT
    a.id,
    a.kind,
    CASE
        WHEN a.kind = 'person' THEN p.display_name
        WHEN a.kind = 'goal' THEN g.name || ' goal'
        ELSE a.name
    END AS name,
    a.institution,
    a.last4,
    a.statement_balance_paise,
    a.statement_balance_as_of,
    a.liquid,
    a.credit_limit_paise,
    a.statement_day,
    a.due_day,
    a.rate_bps,
    a.tenure_months,
    a.emi_schedule_id,
    a.active,
    a.created_at,
    a.updated_at,
    (
        (SELECT COALESCE(sum(l.amount_paise), 0) FROM movement_legs l WHERE l.to_account_id = a.id)
        - (SELECT COALESCE(sum(l.amount_paise), 0) FROM movement_legs l
           WHERE l.from_account_id = a.id)
    )::bigint AS balance_paise,
    a.emi_rate_bps,
    a.emi_fee_paise,
    a.finance_rate_bps,
    a.min_due_bps,
    a.min_due_floor_paise
FROM accounts a
LEFT JOIN payees p ON a.kind = 'person' AND p.person_account_id = a.id
LEFT JOIN goals g ON a.kind = 'goal' AND g.account_id = a.id;

CREATE VIEW v_balances AS
WITH flows AS (
    SELECT l.to_account_id AS account_id, m.date AS as_of, l.amount_paise AS delta
    FROM movement_legs l JOIN movements m ON m.id = l.movement_id
    UNION ALL
    SELECT l.from_account_id, m.date, -l.amount_paise
    FROM movement_legs l JOIN movements m ON m.id = l.movement_id
), daily AS (
    SELECT account_id, as_of, sum(delta) AS delta FROM flows GROUP BY account_id, as_of
)
SELECT
    a.id AS account_id,
    a.name,
    a.kind,
    a.liquid,
    d.as_of,
    (sum(d.delta) OVER (
        PARTITION BY d.account_id ORDER BY d.as_of ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ))::bigint AS balance_paise
FROM daily d
JOIN v_accounts a ON a.id = d.account_id;

CREATE VIEW v_legs AS
SELECT
    m.id AS movement_id,
    l.transfer_id,
    m.date,
    m.kind AS movement_kind,
    m.description,
    fa.id AS from_account_id,
    fa.name AS from_account_name,
    fa.kind AS from_account_kind,
    fa.liquid AS from_account_liquid,
    ta.id AS to_account_id,
    ta.name AS to_account_name,
    ta.kind AS to_account_kind,
    ta.liquid AS to_account_liquid,
    l.amount_paise,
    l.category_id,
    c.name AS category_name,
    l.payee_id,
    p.display_name AS payee_name,
    l.note,
    l.expected_date
FROM movement_legs l
JOIN movements m ON m.id = l.movement_id
JOIN v_accounts fa ON fa.id = l.from_account_id
JOIN v_accounts ta ON ta.id = l.to_account_id
LEFT JOIN categories c ON c.id = l.category_id
LEFT JOIN payees p ON p.id = l.payee_id;

CREATE VIEW v_owed AS
WITH owed AS (
    SELECT
        p.id AS payee_id,
        p.display_name,
        a.id AS account_id,
        COALESCE(sum(CASE WHEN l.to_account_id = a.id THEN l.amount_paise ELSE -l.amount_paise END), 0)
            AS balance,
        min(l.expected_date) FILTER (
            WHERE l.expected_date >= (now() AT TIME ZONE 'Asia/Kolkata')::date
        ) AS next_expected_date
    FROM accounts a
    JOIN payees p ON p.person_account_id = a.id
    LEFT JOIN movement_legs l ON l.to_account_id = a.id OR l.from_account_id = a.id
    WHERE a.kind = 'person'
    GROUP BY p.id, p.display_name, a.id
)
SELECT
    payee_id,
    display_name,
    account_id,
    balance::bigint AS balance_paise,
    CASE WHEN balance > 0 THEN 'owes_you' WHEN balance < 0 THEN 'you_owe' ELSE 'settled' END
        AS direction,
    next_expected_date
FROM owed;

CREATE VIEW v_spending AS
SELECT
    m.id AS movement_id,
    m.date,
    m.description,
    l.transfer_id,
    l.category_id,
    c.name AS category_name,
    l.payee_id,
    p.display_name AS payee_name,
    CASE WHEN l.to_account_id = sp.id THEN l.amount_paise ELSE -l.amount_paise END
        AS amount_paise
FROM movement_legs l
JOIN movements m ON m.id = l.movement_id
JOIN accounts sp ON sp.kind = 'spending'
LEFT JOIN categories c ON c.id = l.category_id
LEFT JOIN payees p ON p.id = l.payee_id
WHERE l.from_account_id = sp.id OR l.to_account_id = sp.id;

-- Grants. agent_ro's list is the boundary for model-written SQL: SELECT only, no auth, undo,
-- attachment, notification, usage, or backup tables. New readable objects need an explicit grant.

GRANT USAGE ON SCHEMA public TO agent_ro, backup;

GRANT SELECT ON
    v_accounts, v_balances, v_legs, v_owed, v_spending,
    movements, captures, schedules, schedule_occurrences, schedule_payments, series_detections,
    goals, scheduled_questions, imports, statement_lines, payees, categories, category_rules,
    baselines, preferences, conversations, messages, settings
TO agent_ro;

-- The dumper reads everything, including tables added later, and writes one `backup_runs` row per
-- attempt (no UPDATE, no DELETE).
GRANT SELECT ON ALL TABLES IN SCHEMA public TO backup;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO backup;
ALTER DEFAULT PRIVILEGES FOR ROLE app IN SCHEMA public GRANT SELECT ON TABLES TO backup;
ALTER DEFAULT PRIVILEGES FOR ROLE app IN SCHEMA public GRANT SELECT ON SEQUENCES TO backup;
GRANT INSERT ON backup_runs TO backup;

-- Seed

INSERT INTO settings (id) VALUES (1);
