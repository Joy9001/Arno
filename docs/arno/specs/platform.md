# Platform

This specification owns Arno's repository layout, HTTP API surface, authentication and enrollment
model, background scheduler, database connection and schema integrity architecture, security and
threat model, and India-specific code; [`plan.md`](../plan.md) §3–§4 own decisions,
[`web-interface.md`](web-interface.md) owns web routes and UI views,
[`self-hosting.md`](../operations/self-hosting.md) owns installation, Compose, privacy retention,
backup, restore, and upgrade procedures, and [`observability.md`](../operations/observability.md)
owns Phoenix operations.

## Repository layout

The following tree shows the repository structure:

```text
src/arno/
  agent/       PydanticAI chat agent, instructions, capabilities, parser & categorizer agents,
               pricing, metering, cost journal, ChatGPT auth, redaction, number check
  api/         FastAPI app, middleware, routers, SSE chat stream, wake turns, run view
  auth/        WebAuthn passkeys, opaque sessions, single-use enrollment links
  capture/     Isolated receipt/UPI photo vision extraction and metered STT client
  db/          Connection cache, numbered SQL migrations, role bootstrap, vendored pgledger
  demo/        Synthetic demo persona seeder and template database helpers
  engine/      Pure scenario projection kernel, affordability, patterns, money math,
               backtesting, and HackerRank adapter
  importers/   File readers, Monty sandbox, verification checks, gate, reconciliation,
               commit/undo, seed rules, and batch categorization jobs
  india/       Default Indian categories, bank holiday calendar, ₹ formatting, prompt notes
  jobs/        Postgres job queue loop, RRULE schedules, briefing, reminders, risk, patterns,
               backup check, recaps, cost replay, scheduled questions
  ledger/      Double-entry ledger writes, audit log, undo, deletion, exports, workbooks
  tools/       Plain-Python read, write, projection, and recap functions (no PydanticAI types)
  cli.py       Host administration CLI (`arno`)
web/           Vite and React PWA (built to `web/dist` and served by the API)
backup/        Independent sidecar image (`pg_dump` + manifest + restic)
deploy/        Postgres role init/demote scripts and `phoenix-cost` custom image build
evals/         145-case `pydantic_evals` suite, graders, trial DB lifecycle, scorecard writer
scripts/       License audit and maintainer helper scripts
tests/         Unit, integration, invariant (`tests/invariants.py`), and live E2E tests
compose.yaml   Default Compose stack (`api`, `db`, `backup`, `phoenix`, `local-llm`, `local-stt`)
compose.gpu.yaml  Optional GPU overlay for local inference profiles
```

## API surface

Arno runs as a single FastAPI process with **one worker** because active chat turns, parser tasks,
and the job loop live in that process and boot recovery assumes single-process ownership. The API
serves the compiled static web bundle from `web/dist`, keeping the web app and API on one origin
with no CORS configuration. OpenAPI schema is exposed at `/api/openapi.json` (interactive docs are
disabled).

The following table summarizes every API router and endpoint group mounted in
`src/arno/api/app.py`:

| Surface | Endpoints and behavior |
|---|---|
| Health | `GET /api/health` (the single unauthenticated endpoint): checks `SELECT 1` and returns `schema_version` with `Cache-Control: no-store`, never exposing amounts or identifiers |
| Auth | Setup, WebAuthn registration and sign-in, passkey list/rename/delete, add-device enrollment links, fresh passkey re-verification, and session logout |
| Chat and threads | `POST /api/chat` (`VercelAIAdapter` with `sdk_version=7`, `manage_message_history="server"`, `allowed_file_url_schemes=frozenset()`), `GET /api/chat/{thread_id}/stream` (reconnect to detached turn), `POST /api/chat/{thread_id}/stop`, and thread list, read, rename, search, and reply flagging |
| Home | `GET /api/home` composes account summary, 90-day default projection (configurable `horizon_days`), inbox preview, and recent activity |
| Ledger | Accounts (`GET/POST/PATCH/DELETE /api/accounts`, `POST /api/accounts/{id}/statement-balance`), movements (`GET/POST /api/movements`, `GET /api/movements/{id}`, `POST /api/movements/{id}/correct`, `GET /api/ledger/span`), categories and rules (`/api/categories`, `/api/category-rules`), uncategorized queue (`GET /api/uncategorized`, `POST /api/uncategorized/answer`), schedules and payment evidence (`/api/schedules`, `GET /api/schedules/upcoming`, `GET /api/schedules/{id}/occurrences/{date}`, `POST/DELETE .../payments`), goals (`/api/goals`), people and payees (`/api/people`, `/api/payees`), settings and memory (`GET/PATCH /api/settings`, `PATCH /api/preferences/{id}/content`), and undo (`POST /api/undo`, `POST /api/undo/{undo_id}`) |
| Projection | `GET /api/projection` and `GET /api/projection/workbook` (`.xlsx`, calculation version `2`): `horizon_days` 1–3,650, optional what-if `changes`, and hypothetical purchase terms (`amount_paise` and `emi_fee_paise` capped at `MAX_PAISE`, `emi_months` 2–48) |
| Attachments and imports | `POST /api/attachments` (accepts `purpose="statement"` or `"photo"`, up to 25 MB), `DELETE /api/attachments/{id}` (purges stored statement bytes), `GET /api/attachments/{id}` (serves photos and audio with `Cache-Control: private, no-store`), `GET /api/imports`, `GET /api/import-history` (keyset paginated), `GET /api/imports/{id}`, `PATCH /api/imports/{id}/lines/{line_id}`, `POST /api/imports/{id}/password` (`GET` returns `405`), `POST /api/imports/{id}/opening`, `POST /api/imports/{id}/commit`, `POST /api/imports/{id}/reject`, `POST /api/imports/{id}/undo`, and `POST /api/imports/{id}/retry` |
| Capture | `POST /api/transcribe` and `POST /api/transcribe/{attachment_id}` (audio upload and retry, up to 25 MB); `POST /api/chat/{thread_id}/photo` and `POST /api/chat/{thread_id}/photo/{attachment_id}` (isolated vision logging and retry) |
| Tasks and layouts | `GET /api/tasks/{task_id}` (status, imports, plan, transcript, and `run_view`), `POST /api/tasks/{task_id}/answer` (binds account to parser question), `GET /api/layouts`, and `POST /api/layouts/{layout_id}/rollback` |
| Recaps | `GET /api/recaps`, `GET /api/recaps/unseen`, `GET /api/recaps/{period_id}`, `GET /api/recaps/{period_id}/rows`, and `POST /api/recaps/{period_id}/seen` |
| Watch (inbox, push, jobs, questions) | `GET /api/notifications`, `POST /api/notifications/read-all`, `POST /api/notifications/{id}/read`, `POST /api/notifications/{id}/chat`, `/api/push/{key,status,subscribe}`, CRUD `/api/scheduled-questions`, `GET /api/jobs`, `GET /api/jobs/activity` (pure-read activity poll), and `POST /api/jobs/{id}/retry` |
| Audit history | `GET /api/history` (keyset-paginated audit log of undoable write groups) |
| Diagnostics and backups | `GET /api/runtime-info` (configured model labels/hosts and optional `ARNO_PHOENIX_UI_URL`, never credentials or collector addresses) and `GET /api/backup/status` |
| Export | `GET /api/export/scope` (lists excluded tables), `GET /api/export/json`, `GET /api/export/csv` (ZIP of CSVs with formula-injection quoting), and `GET /api/export/pg_dump` (all downloads require fresh passkey re-verification) |
| Deletion | `GET /api/deletion/preview` (lists rows and parser task files affected) and `POST /api/deletion/everything` (requires fresh passkey re-verification and typed `"delete everything"` confirmation; refuses while a parser task is running) |

Projection, affordability, and `.xlsx` workbook builds run off the event loop via
`asyncio.to_thread` behind a shared two-slot semaphore after loading inputs in one read-only
`REPEATABLE READ` transaction. Chat tools and REST endpoints call the same `src/arno/ledger/` and
`src/arno/tools/` functions and write the same audit log, so any edit is undoable from either
surface.

The web app consumes these routes using React, Vite, `@assistant-ui/react`, `@assistant-ui/ai-sdk`,
`@assistant-ui/react-streamdown`, `@radix-ui/themes`, `motion`, and `zustand`
([`web-interface.md#frontend-stack`](web-interface.md#frontend-stack)). Under **Settings →
Background work → Models and diagnostics**, `RuntimeInfo.tsx` displays each role's configured model
and host and renders an **Open Phoenix** button when `ARNO_PHOENIX_UI_URL` is configured.

**Deployment and backups summary:** Arno ships as a Docker Compose stack (`api`, `db`, `backup`,
`phoenix`, plus optional `local-llm` and `local-stt` profiles) published only on loopback and
exposed privately over Tailscale. An independent `backup` sidecar runs `pg_dump` with a
single-snapshot `backup_manifest.json` whenever the last successful backup is over 24 hours old,
encrypts and uploads snapshots with restic, and records success through its own `backup` database
role. See [`self-hosting.md`](../operations/self-hosting.md) for installation, provider retention,
backup, restore (`arno restore`), and upgrade procedures.

## Authentication and enrollment

Arno serves one user on a private Tailscale network; authentication protects against other tailnet
devices and borrowed unlocked browsers rather than public-internet attackers.

The following table summarizes each authentication mechanism:

| Component | Design |
|---|---|
| First-run setup | Boot issues a single-use enrollment link and prints a terminal QR code (`segno`) to local Docker stdout (or on demand via `arno setup`). Opening the link creates the sole user (`users_one_row_idx`) and registers the first passkey, closing setup |
| Adding devices | **Settings → Passkeys → Add a device** issues a 10-minute enrollment link and browser-rendered QR code (`qrcode.react`) tied to the signed-in user |
| Sign-in | WebAuthn passkeys via `py_webauthn`, requiring HTTPS and a stable `*.ts.net` Relying Party origin (`ARNO_RP_ORIGIN`) |
| Re-verification | Sensitive operations (exports, passkey changes, and erase-everything) require a fresh passkey check within five minutes (`sessions.reverified_at`) |
| Recovery | `arno passkeys reset` on the host deletes all passkeys and sessions, reopens setup, and prints a fresh enrollment link and QR code |
| Sessions | Opaque random tokens stored hashed in Postgres; browser sessions use `HttpOnly; Secure; SameSite=Strict` cookies |

### Enrollment links

First-run setup, device enrollment, and recovery all use a single-use fragment URL:

```text
https://<machine>.<tailnet>.ts.net/setup#t=<token>
```

- **Origin-bound:** Links build from `ARNO_RP_ORIGIN` rather than `localhost`. Opening `/setup` on
  a mismatched origin explains the mismatch instead of letting WebAuthn fail with `SecurityError`.
- **Fragment transport:** Because browsers omit URL fragments from HTTP requests, tokens stay out
  of server logs and `Referer` headers. The setup page posts the token to the API and clears it
  from the address bar with `history.replaceState`.
- **Lifetimes and hashing:** Tokens are stored as SHA-256 hashes, consumed atomically once, and
  limited to one active token per kind. First-run tokens expire after `ARNO_SETUP_TOKEN_TTL_HOURS`
  (default 24 hours) and survive restarts while valid; add-device tokens expire after 10 minutes
  and are canceled if the issuing user's sessions are revoked.

## Scheduler and background jobs

One Postgres `jobs` table, one `job_schedules` RRULE table (anchored in `Asia/Kolkata` with stored
`dtstart`), and one `JobRunner` loop inside the API process execute all background jobs
(`src/arno/jobs/runner.py` and `src/arno/jobs/schedules.py`). Statement imports are delegated
subagent tasks (`TaskOwner`), and backups run in their own sidecar container.

The following table lists every job kind, its schedule or trigger, and its timeout policy:

| Job kind | Trigger or schedule (IST) | Timeout (`DEFAULT_TIMEOUTS`) |
|---|---|---|
| `cost_replay` | Every 5 minutes (`FREQ=MINUTELY;INTERVAL=5`) | Runner default (no model calls; OTLP replay only) |
| `weekly_briefing` | Weekly on `weekly_briefing_dow` at `notification_time` (engine numbers, LLM prose with template fallback) | `None` (unbounded) |
| `reminders` | Daily at `notification_time` (N days before obligations, EMIs, and card due dates) | `60` s |
| `recaps` | Daily at `notification_time` (announces new daily, weekly, and monthly recaps) | Runner default |
| `risk_check` | Nightly at 02:00 and debounced (10 s) after writes (90-day default projection horizon) | `120` s |
| `pattern_detection` | Nightly at 02:15 and after imports (new recurring items, missed bills, and amount steps) | `120` s |
| `recompute_baselines` | Nightly at 02:30 and on import commit | `120` s |
| `backup_check` | Nightly at 03:00 (alerts if no backup succeeded in 48 hours after install grace) | `30` s |
| `scheduled_question` | User-defined RRULE; read-only agent with per-question spend limit (default `$1`), answering to inbox (`ceiling: true` if stopped by a limit) | `None` (unbounded) |
| `categorize_import` | Enqueued inside import commit transaction | `None` (unbounded) |
| `collect_batch` | Enqueued when `submit_batch` submits a provider batch | `None` (unbounded) |

Key job-loop properties:

- **Idempotent period keys:** Each scheduled job carries a unique `(kind, period)` key so restarts
  cannot duplicate work, and missed periods during machine sleep fire once on wake.
- **Boot recovery and retries:** Rows left `running` at startup reset to `pending`. Failed jobs
  retry with backoff up to `MAX_ATTEMPTS = 3` before marking `failed` and creating an inbox item.
  Handlers raising `JobGaveUp` (including `COST_STOP_ERRORS` and `CommitOutcomeUnknownError`) fail
  immediately without retrying. Failed jobs can be retried from the UI (`POST /api/jobs/{id}/retry`),
  which resets the row and resolves its failure alert.
- **Concurrency and transactional enqueue:** Up to two jobs run concurrently (`FOR UPDATE SKIP
  LOCKED`), coordinating with erase-everything via a shared advisory lock (`ERASE_LOCK_KEY`). Jobs
  can enqueue inside the same database transaction as the write that triggered them.
- **Notifications and push:** Every alert lands in the in-app inbox. Web push (`pywebpush` with
  first-run VAPID keys) sends contentless pings so no financial data passes through browser push
  relays. Opening an inbox item in chat seeds a new thread through normal agent write rules.
- **Live progress polling:** The web app polls `GET /api/jobs/activity` every 3 seconds while jobs
  are active and every 30 seconds while idle.

## Database connections and integrity

### Connection cache and failure handling

The API process uses a per-loop cache of idle `asyncpg` connections (`API_POOL_MAX_IDLE = 8`) bound
in the FastAPI lifespan and closed after background jobs and active turns drain (`src/arno/db/conn.py`):

- **Cache, not a capacity limiter:** `connect()` pops and pre-pings (`SELECT 1`) the last-returned
  released idle connection when available, and opens a fresh direct connection immediately when the
  idle deque is empty—never waiting on capacity. A bounded limiter pool could let long chat streams
  starve or deadlock tool calls; the cache preserves never-wait behavior. Exiting
  `async with connect()` runs `reset()` (rolling back open transactions and resetting session
  state) and returns up to eight connections to the deque, closing excess connections.
- **Direct bypasses:** `connect()` opens an uncached direct connection whenever an explicit `dsn`
  or `role` is passed, on other event loops (CLI and worker threads), or when `current_database_url()`
  differs from the bound DSN (eval trials). `agent_ro` queries stay direct for fresh session
  isolation, while `spend_store` and `tasks` keep dedicated pools with fail-closed behavior.
- **Outage recovery:** Reused connections are pre-pinged before handoff and discarded if dead.
  After `connect()` returns, reads and writes never retry automatically. Connection `TimeoutError`
  converts to `ConnectionError`, and `asyncpg` connection errors or `InternalClientError` map to
  HTTP `503` ("The database is unreachable.") while non-database timeouts surface normally. The
  cache recovers automatically once Postgres returns without restarting the API.
- **Uncertain commits:** `write_group` distinguishes a lost connection before `COMMIT` (rolled back;
  ordinary `503`) from a connection lost while `COMMIT` is in flight, raising
  `CommitOutcomeUnknownError`. The API returns `503` asking you to verify before retrying, agent
  write tools return `"outcome_unknown"` instructing the model not to repeat the write, and the job
  runner fails the job immediately without retry.

### Measured database tuning

Indexes and Postgres settings are added only with `EXPLAIN (ANALYZE, BUFFERS)` verification at
ten-year single-user scale:

- `movement_legs_movement_id_idx` on `movement_legs (movement_id)` for batched movement leg lookups.
- `movement_legs_category_id_idx` on `movement_legs (category_id)` for category filters and
  `v_spending` joins.
- `audit_log_created_at_idx` on `audit_log (created_at)` for per-turn writes-since-snapshot checks.
- `random_page_cost=1.1` on `db` in `compose.yaml` for SSD/NVMe storage (use `4` on spinning disks).

### Schema integrity

Invariants relied on by application code are enforced in SQL constraints and checked by
`tests/invariants.py`:

- **Balanced ledger legs:** `pgledger_transfers` enforces integer paise (`amount = trunc(amount)`)
  and carries a deferred foreign key (`pgledger_transfers_leg_fkey`) to `movement_legs (transfer_id)`
  with `movement_id NOT NULL`, so no transfer commits without a leg and legs cannot be deleted.
- **State and reversal invariants:** `movements` pairs `kind = 'reversal'` with `reverses_id`;
  `accounts` enforces card cycle fields and statement balance/date pairing; `schedule_occurrences`
  mirrors closed `kind` and `subtype` sets. Reversal state has one definition,
  `movement_reversal_state` (`v_movement_state`).
- **Natural keys and single user:** Case-insensitive unique indexes guard `categories (lower(name))`,
  `payees (lower(upi_id))`, and `statement_layouts (lower(institution), lower(kind))`.
  `users_one_row_idx` on `users ((true))` enforces a single user row.
- **Account identity:** `accounts.name` must be `NULL` if and only if `kind` is `'person'` or
  `'goal'` (`accounts_name_kind_check`). Person names come from `payees.display_name`, goal names
  derive `<name> goal` from `goals.name`, `pgledger_accounts.name` stores the immutable
  `accounts.id`, and `v_accounts`, `v_legs`, `v_balances`, and `v_owed` (`Asia/Kolkata` cutoff)
  expose resolved names and balances.
- **Parser versions and scheduled questions:** Partial unique indexes enforce at most one active
  `parser_versions` row per layout and at most one `job_schedules` row per `scheduled_question_id`
  (`ON DELETE CASCADE`, paired with `job_schedules_scheduled_question_check`).
- **Import metadata:** `imports.check_status` owns the check verdict (`VERIFIED`, `DISCREPANCY`,
  `FAILED`), `imports.counts` owns line counts, and `imports.attachment_id` owns the statement file
  link.

## Security and threat model

The following table summarizes Arno's threat model and implemented controls:

| Threat | Defense |
|---|---|
| Public internet attacker | Not exposed; private Tailscale access only, plus passkeys, single origin, `SameSite=Strict` cookies, `TrustedHostMiddleware`, and security headers |
| Stolen laptop or disk | Host disk encryption (BitLocker, LUKS, or FileVault), required at install |
| Leaked off-site backup | restic encryption; the restic password is stored off-host separately from backups |
| Lost restic password | Not defended (permanent data loss); must be backed up off-host |
| VPS host or live host compromise | Not defended; in-database encryption is parked ([`parked.md`](../planning/parked.md)) |
| Borrowed unlocked browser | Expiring sessions; exports, passkey changes, and erase-everything require passkey re-verification within five minutes |
| Prompt injection in statements or photos | Statement parsing runs with no write or ledger tools; photos run a tool-less typed call; outside text is marked with `<outside>`; lasting or wide writes require confirm cards in flagged conversations |
| Forged or replayed confirm answer | Server-held history validates pending deferred calls once against stored arguments; partial multi-card answers are refused with HTTP `409` |
| Secrets typed into chat | `InputGuardrail` and `redact_text` redact API keys, credit card numbers (Luhn and 4×4 `card_number`), PAN, spaced Aadhaar (with lookaround boundaries), and keyword-anchored passwords, OTPs, PINs, and CVVs across English, Hinglish, and Devanagari (`keyword_secret_data`); unlabelled bare secrets and bank account numbers still rely on user caution, and statement passwords use the dedicated import card field |
| Data exfiltration through rendered output | Strict CSP (`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, plain-text links in agent replies, and `allowed_file_url_schemes=frozenset()` |
| Agent-written SQL | `agent_ro` role: `SELECT` only on allowed read views/tables, `default_transaction_read_only = on`, `2s` statement timeout, single extended-protocol statement rolled back after read, `TEMP` revoked, no auth, secret, or raw `pgledger_*` grants |
| Agent-written code | Monty sandbox: no filesystem, environment, or network access; memory, time, and suspension limits; frozen clock and RNG seed for parser replays |
| Credential leakage in data exports | JSON and CSV exports exclude all eight `SECRET_TABLES`: `model_credentials`, `push_subscriptions`, `sessions`, `setup_tokens`, `users`, `vapid_keys`, `webauthn_challenges`, and `webauthn_credentials` (`pg_dump` stays complete for backup/restore) |
| Supply-chain or copyleft dependency | Locked dependency files, permissive-license audit (`scripts/license_audit.py`), and `pip-audit` + `pnpm audit` in CI |
| Upload flood or runaway model spend | 25 MB upload limit, single-user passkey gate, usage limits, and fail-closed Postgres spend ceilings |
| Telemetry data exposure | Phoenix binds to loopback with authentication enabled, owns an isolated database unreachable by `agent_ro`, and vision agents disable tracing (`agent.instrument = False`). See [`self-hosting.md`](../operations/self-hosting.md) for provider data-retention policies |

### Postgres roles

The following table lists the five Postgres roles configured by `deploy/app-role.sh` and
`deploy/phoenix-db.sh`:

| Role | Privileges | Access path |
|---|---|---|
| `postgres` | Bootstrap superuser, `NULL` password | Local container socket only; TCP refused |
| `app` | `LOGIN CREATEDB CREATEROLE NOSUPERUSER NOBYPASSRLS NOREPLICATION`, member of `pg_signal_backend`, `ADMIN OPTION` (`INHERIT FALSE, SET FALSE`) on `agent_ro` and `backup` | Password in `POSTGRES_PASSWORD`; owns Arno databases |
| `agent_ro` | `LOGIN`, `default_transaction_read_only = on`, `statement_timeout = '2s'`, `SELECT` on allowed read views/tables, `TEMP` revoked | Password set at boot from `AGENT_RO_PASSWORD` |
| `backup` | `LOGIN`, `SELECT` on `public` tables and sequences, `INSERT` on `backup_runs` only, `TEMP` revoked | Password set at boot from `BACKUP_PASSWORD` |
| `phoenix` | `LOGIN`, owns database `phoenix` only (`PUBLIC` `CONNECT` revoked) | Password in `PHOENIX_PASSWORD` |

`app` never holds `SUPERUSER`, `BYPASSRLS`, `REPLICATION`, server-file privileges, or
`session_replication_role`. See [`legacy-upgrades.md`](../operations/legacy-upgrades.md) for
demoting pre-0.3.0 superuser `app` roles.

## India-specific code

All country-specific logic lives in `src/arno/india/` with no plugin indirection until a second
country is needed ([`parked.md`](../planning/parked.md)). The following table lists the modules:

| Module | Behavior |
|---|---|
| Default categories | Indian personal-finance categories (Food & Dining, Groceries, Rent, Utilities, Family Transfer, Savings & Investments, Fees & Charges, and related categories) |
| Holiday calendar | Indian bank holidays via `holidays` for "last working day" and `holiday_adjust` schedule rules, cached per covered year span with automatic expansion disabled |
| Currency formatting | `₹` symbol with Indian lakh and crore digit grouping (`format_paise`, `rupees`) |
| Prompt notes | `INDIA_NOTES` providing Hinglish examples, language-mirroring guidance, and UPI ID rules |
| Statement helpers | Bank layout conventions and 163 Indian merchant seed rules ([`import.md`](import.md)) |
