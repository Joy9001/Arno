-- 0003: schema v1. Every table in docs/fin-agent/design.md.
--
-- Conventions: money is whole paise in BIGINT (`*_paise`), pgledger keeps NUMERIC; IDs are
-- prefixed text ULIDs from pgledger_generate_id; dates are date, moments timestamptz; kinds are
-- text with CHECK constraints; unknown is NULL, never 0. The ledger itself is append-only.
--
-- `spend_counters` and `spend_dedup` are absent on purpose: pydantic-ai-harness's
-- PostgresSpendStore creates and owns them at L2 (design.md, "Memory, conversations, and
-- provenance"), so creating them here would collide with its schema.

CREATE TABLE settings (
    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    base_currency TEXT NOT NULL DEFAULT 'INR' CHECK (base_currency ~ '^[A-Z]{3}$'),
    time_zone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    floor_paise BIGINT CHECK (floor_paise >= 0),
    default_horizon_days INTEGER NOT NULL DEFAULT 90 CHECK (default_horizon_days BETWEEN 1 AND 3650),
    notification_time TIME NOT NULL DEFAULT '08:00',
    weekly_briefing_dow SMALLINT NOT NULL DEFAULT 0 CHECK (weekly_briefing_dow BETWEEN 0 AND 6),
    last_model_error_type TEXT,
    last_model_error_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO settings (id) VALUES (1);

-- Auth -------------------------------------------------------------------------------------

CREATE TABLE users (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('usr'),
    -- The WebAuthn user handle. Stable across passkey resets so credentials cannot cross users.
    webauthn_user_handle BYTEA NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT 'You',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE webauthn_credentials (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('wac'),
    user_id TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    credential_id BYTEA NOT NULL UNIQUE,
    public_key BYTEA NOT NULL,
    sign_count BIGINT NOT NULL DEFAULT 0 CHECK (sign_count >= 0),
    transports TEXT[] NOT NULL DEFAULT '{}',
    aaguid UUID,
    name TEXT NOT NULL DEFAULT 'Passkey',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ
);

CREATE INDEX webauthn_credentials_user_id_idx ON webauthn_credentials (user_id);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('ses'),
    user_id TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    -- Only the SHA-256 of the opaque token is stored; the token itself lives in the cookie.
    token_hash BYTEA NOT NULL UNIQUE,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);

CREATE INDEX sessions_user_id_idx ON sessions (user_id);

-- One-time WebAuthn challenges. They live in Postgres so a restart mid-ceremony doesn't lose
-- them, and they are consumed exactly once.
CREATE TABLE webauthn_challenges (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('wch'),
    kind TEXT NOT NULL CHECK (kind IN ('setup', 'login')),
    challenge BYTEA NOT NULL,
    user_handle BYTEA,
    user_id TEXT REFERENCES users (id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((kind = 'setup') = (user_handle IS NOT NULL))
);

CREATE INDEX webauthn_challenges_expires_at_idx ON webauthn_challenges (expires_at);

-- First-run setup tokens, stored as hashes. The plaintext is printed to the API log once.
CREATE TABLE setup_tokens (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('sup'),
    token_hash BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ
);

CREATE INDEX setup_tokens_expires_at_idx ON setup_tokens (expires_at);

-- Ledger ------------------------------------------------------------------------------------

CREATE TABLE categories (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('cat'),
    name TEXT NOT NULL UNIQUE,
    reducible BOOLEAN NOT NULL DEFAULT false,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('acc'),
    kind TEXT NOT NULL CHECK (
        kind IN (
            'bank', 'card', 'cash', 'wallet', 'loan', 'asset',
            'goal', 'person', 'spending', 'income', 'equity'
        )
    ),
    ledger_account_id TEXT NOT NULL UNIQUE REFERENCES pgledger_accounts (id),
    name TEXT NOT NULL,
    institution TEXT,
    last4 TEXT CHECK (last4 IS NULL OR last4 ~ '^[0-9]{4}$'),
    -- The last balance a statement or the user stated, as a check on the ledger's own balance.
    statement_balance_paise BIGINT,
    statement_balance_as_of DATE,
    -- Counts toward spendable cash in the projection. Only bank, cash, and wallet accounts.
    liquid BOOLEAN NOT NULL DEFAULT false,
    credit_limit_paise BIGINT CHECK (credit_limit_paise > 0),
    statement_day SMALLINT CHECK (statement_day BETWEEN 1 AND 31),
    due_day SMALLINT CHECK (due_day BETWEEN 1 AND 31),
    rate_bps INTEGER CHECK (rate_bps >= 0),
    tenure_months INTEGER CHECK (tenure_months > 0),
    emi_schedule_id TEXT,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (NOT liquid OR kind IN ('bank', 'cash', 'wallet')),
    CHECK (kind = 'card' OR (credit_limit_paise IS NULL AND statement_day IS NULL AND due_day IS NULL)),
    CHECK (kind = 'loan' OR (rate_bps IS NULL AND tenure_months IS NULL AND emi_schedule_id IS NULL)),
    CHECK (statement_balance_paise IS NULL OR statement_balance_as_of IS NOT NULL)
);

-- One spending account, one income account, one equity account (design.md, "The ledger").
CREATE UNIQUE INDEX accounts_one_system_account_per_kind
    ON accounts (kind) WHERE kind IN ('spending', 'income', 'equity');

CREATE INDEX accounts_kind_idx ON accounts (kind);
CREATE INDEX accounts_ledger_account_id_idx ON accounts (ledger_account_id);

CREATE TABLE payees (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('pay'),
    display_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('person', 'merchant', 'organization')),
    upi_id TEXT,
    -- Keyed one-way hash of identifiers used only for matching, never the identifier itself.
    match_hash TEXT,
    default_category_id TEXT REFERENCES categories (id),
    person_account_id TEXT UNIQUE REFERENCES accounts (id),
    -- True when the name was copied from statement text; the mark travels with the copy.
    outside_text BOOLEAN NOT NULL DEFAULT false,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX payees_upi_id_idx ON payees (upi_id) WHERE upi_id IS NOT NULL;

CREATE TABLE movements (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('mov'),
    date DATE NOT NULL,
    description TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (
        kind IN (
            'expense', 'income', 'transfer', 'split', 'loan',
            'refund', 'settlement', 'adjustment', 'reversal'
        )
    ),
    reverses_id TEXT REFERENCES movements (id),
    -- The chat message ID plus the tool call ID, also passed to pgledger.
    idempotency_key TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (kind <> 'reversal' OR reverses_id IS NOT NULL),
    CHECK (reverses_id IS NULL OR reverses_id <> id)
);

CREATE INDEX movements_date_idx ON movements (date);
CREATE INDEX movements_reverses_id_idx ON movements (reverses_id);

CREATE TABLE transfer_labels (
    transfer_id TEXT PRIMARY KEY REFERENCES pgledger_transfers (id),
    movement_id TEXT NOT NULL REFERENCES movements (id),
    category_id TEXT REFERENCES categories (id),
    payee_id TEXT REFERENCES payees (id),
    expected_date DATE,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX transfer_labels_movement_id_idx ON transfer_labels (movement_id);
CREATE INDEX transfer_labels_category_id_idx ON transfer_labels (category_id);

CREATE TABLE captures (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('cap'),
    movement_id TEXT REFERENCES movements (id),
    kind TEXT NOT NULL CHECK (kind IN ('chat', 'voice', 'photo', 'statement_line')),
    ref TEXT,
    upi_ref TEXT,
    confidence NUMERIC(3, 2) CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX captures_movement_id_idx ON captures (movement_id);
CREATE INDEX captures_upi_ref_idx ON captures (upi_ref) WHERE upi_ref IS NOT NULL;

CREATE TABLE imports (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('imp'),
    file_hash TEXT NOT NULL,
    parser TEXT,
    account_id TEXT REFERENCES accounts (id),
    period_start DATE,
    period_end DATE,
    opening_balance_paise BIGINT,
    closing_balance_paise BIGINT,
    check_result JSONB,
    status TEXT NOT NULL DEFAULT 'review' CHECK (status IN ('review', 'committed', 'rejected')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    committed_at TIMESTAMPTZ,
    CHECK (period_start IS NULL OR period_end IS NULL OR period_end >= period_start)
);

CREATE INDEX imports_account_id_idx ON imports (account_id);
CREATE INDEX imports_file_hash_idx ON imports (file_hash);

CREATE TABLE statement_lines (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('stl'),
    import_id TEXT NOT NULL REFERENCES imports (id) ON DELETE CASCADE,
    date DATE NOT NULL,
    description_raw TEXT NOT NULL,
    amount_paise BIGINT NOT NULL,
    upi_ref TEXT,
    -- Dedupe key across reimports of the same statement.
    content_hash TEXT NOT NULL,
    match_state TEXT NOT NULL DEFAULT 'new'
        CHECK (match_state IN ('new', 'certain', 'ambiguous', 'merged', 'ignored')),
    movement_id TEXT REFERENCES movements (id),
    outside_text BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (import_id, content_hash)
);

CREATE INDEX statement_lines_content_hash_idx ON statement_lines (content_hash);
CREATE INDEX statement_lines_movement_id_idx ON statement_lines (movement_id);

CREATE TABLE statement_layouts (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('lay'),
    institution TEXT NOT NULL,
    -- Normalized column headers.
    fingerprint TEXT NOT NULL UNIQUE,
    parser TEXT NOT NULL,
    helper_version TEXT,
    source_import_id TEXT REFERENCES imports (id),
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE attachments (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('att'),
    kind TEXT NOT NULL CHECK (kind IN ('statement', 'photo', 'audio')),
    filename TEXT,
    content_type TEXT,
    size_bytes BIGINT CHECK (size_bytes IS NULL OR size_bytes >= 0),
    sha256 TEXT NOT NULL,
    data BYTEA,
    import_id TEXT REFERENCES imports (id),
    purged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (data IS NOT NULL OR purged_at IS NOT NULL)
);

CREATE INDEX attachments_import_id_idx ON attachments (import_id);

-- Plan --------------------------------------------------------------------------------------

CREATE TABLE schedules (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('sch'),
    kind TEXT NOT NULL CHECK (
        kind IN (
            'income_fixed', 'income_variable', 'income_oneoff',
            'obligation', 'instalment', 'goal_contribution'
        )
    ),
    subtype TEXT CHECK (
        subtype IS NULL
        OR subtype IN ('rent', 'emi', 'subscription', 'insurance', 'family_transfer', 'other')
    ),
    name TEXT NOT NULL,
    amount_paise BIGINT CHECK (amount_paise IS NULL OR amount_paise > 0),
    account_id TEXT REFERENCES accounts (id),
    category_id TEXT REFERENCES categories (id),
    -- iCalendar RRULE, handled by python-dateutil.
    rule TEXT NOT NULL,
    confirmed BOOLEAN NOT NULL DEFAULT true,
    starts DATE NOT NULL,
    ends DATE,
    active BOOLEAN NOT NULL DEFAULT true,
    outside_text BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (ends IS NULL OR ends >= starts),
    CHECK (kind = 'obligation' OR subtype IS NULL)
);

CREATE INDEX schedules_active_idx ON schedules (active);

ALTER TABLE accounts
    ADD CONSTRAINT accounts_emi_schedule_id_fkey
    FOREIGN KEY (emi_schedule_id) REFERENCES schedules (id);

CREATE TABLE goals (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('gol'),
    name TEXT NOT NULL,
    target_paise BIGINT NOT NULL CHECK (target_paise > 0),
    deadline DATE,
    priority SMALLINT NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
    -- The goal's own ledger account, so fenced savings are a real balance. Never `liquid`.
    account_id TEXT NOT NULL UNIQUE REFERENCES accounts (id),
    contribution_schedule_id TEXT REFERENCES schedules (id),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'achieved', 'paused', 'abandoned')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE scheduled_questions (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('sq'),
    prompt TEXT NOT NULL,
    rule TEXT NOT NULL,
    spend_limit_paise BIGINT CHECK (spend_limit_paise IS NULL OR spend_limit_paise >= 0),
    last_run_at TIMESTAMPTZ,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Categorization ----------------------------------------------------------------------------

CREATE TABLE category_rules (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('rul'),
    merchant_pattern TEXT NOT NULL UNIQUE,
    category_id TEXT NOT NULL REFERENCES categories (id),
    source TEXT NOT NULL CHECK (source IN ('user', 'llm')),
    hits BIGINT NOT NULL DEFAULT 0 CHECK (hits >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE baselines (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('bas'),
    category_id TEXT NOT NULL UNIQUE REFERENCES categories (id),
    amount_per_day_paise BIGINT NOT NULL CHECK (amount_per_day_paise >= 0),
    source TEXT NOT NULL CHECK (source IN ('estimated', 'computed')),
    window_days INTEGER CHECK (window_days IS NULL OR window_days > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Memory, conversations, and provenance -----------------------------------------------------

CREATE TABLE conversations (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('cnv'),
    title TEXT,
    -- What the model gets. Compaction may edit it; `messages` never changes.
    model_history JSONB,
    snapshot_at TIMESTAMPTZ,
    -- Server-set when outside text first entered the thread; the model can't change it.
    outside_text_at TIMESTAMPTZ,
    -- Set while a turn runs, cleared in the transaction that saves both records.
    run_started_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX conversations_updated_at_idx ON conversations (updated_at DESC);

CREATE TABLE messages (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('msg'),
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    -- The client's message ID. A message ID starts one turn, ever.
    client_id TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    -- What you saw, as UI messages. Never edited.
    content JSONB NOT NULL,
    body_text TEXT,
    -- `simple`, so Hinglish isn't stemmed.
    search TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', coalesce(body_text, ''))) STORED,
    trace_id TEXT,
    flagged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX messages_conversation_id_idx ON messages (conversation_id, created_at);
CREATE INDEX messages_search_idx ON messages USING GIN (search);

CREATE TABLE preferences (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('prf'),
    body TEXT NOT NULL,
    created_by TEXT NOT NULL CHECK (created_by IN ('agent', 'user')),
    source_message_id TEXT REFERENCES messages (id),
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('aud'),
    actor TEXT NOT NULL CHECK (actor IN ('user', 'agent', 'system')),
    message_id TEXT REFERENCES messages (id),
    -- One write group: one undo reverts the whole group.
    group_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_id TEXT,
    before JSONB,
    after JSONB,
    undone_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX audit_log_group_id_idx ON audit_log (group_id);
CREATE INDEX audit_log_row_idx ON audit_log (table_name, row_id);

-- Notifications and jobs --------------------------------------------------------------------

CREATE TABLE notifications (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('ntf'),
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    data JSONB,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX notifications_unread_idx ON notifications (created_at DESC) WHERE read_at IS NULL;

CREATE TABLE push_subscriptions (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('psh'),
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ
);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('job'),
    -- "kind:period", so a restart can't send the same job twice.
    key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'done', 'failed')),
    run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX jobs_pending_idx ON jobs (run_at) WHERE status = 'pending';
CREATE INDEX jobs_running_idx ON jobs (started_at) WHERE status = 'running';

CREATE TABLE job_schedules (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('jsc'),
    kind TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    rule TEXT NOT NULL,
    next_run_at TIMESTAMPTZ NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX job_schedules_next_run_idx ON job_schedules (next_run_at) WHERE active;
