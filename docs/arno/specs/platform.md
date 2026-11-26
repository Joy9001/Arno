# Platform

This specification owns Arno's repository layout, HTTP API surface, authentication
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
  agent/       PydanticAI chat agent, instructions, capabilities, toolset, turns and history,
               usage log and daily cap
  api/         FastAPI app, middleware, routers, SSE chat stream
  capture/     STT client and capture helpers
  db/          Connection cache, numbered SQL migrations, role bootstrap, vendored pgledger
  demo/        Synthetic demo persona seeder and template database helpers
  engine/      Pure scenario projection kernel, affordability, patterns, and money math
  importers/   File readers, verification checks, gate, reconciliation,
               commit/undo, and categorization
  india/       Default Indian categories, bank holiday calendar, ₹ formatting, prompt notes
  jobs/        Minute loop, `spawn` helper, scheduled questions, reminders, risk, patterns,
               backup check
  ledger/      Double-entry ledger writes, audit log, undo, deletion, exports, workbooks
  tools/       Plain-Python read, write, projection, and recap functions (no PydanticAI types)
  cli.py       Host administration CLI (`arno`)
web/           Vite and React PWA (built to `web/dist` and served by the API)
deploy/        Postgres role init script (`app-role.sh`)
sandbox/       Isolated `run_python` service (`server.py`, `Dockerfile`)
evals/         85-case `pydantic_evals` suite, graders, trial DB lifecycle
scripts/       Maintainer helper scripts
tests/         Unit, integration, invariant (`tests/invariants.py`), and live E2E tests
compose.yaml   Default Compose stack (`api`, `db`, plus optional `local-llm` and `obs` profiles)
```

## API surface

Arno runs as a single FastAPI process with **one worker** because active chat turns
and the job loop live in that process and boot recovery assumes single-process ownership. The API
serves the compiled static web bundle from `web/dist`, keeping the web app and API on one origin
with no CORS configuration. OpenAPI schema is exposed at `/api/openapi.json` (interactive docs are
disabled).

The following table summarizes every API router and endpoint group mounted in
`src/arno/api/app.py`:

| Surface | Endpoints and behavior |
|---|---|
| Health | `GET /api/health` (the single unauthenticated endpoint): checks `SELECT 1` and returns `schema_version` with `Cache-Control: no-store`, never exposing amounts or identifiers |
| Auth | `GET /api/auth/state`, `POST /api/auth/login` (passphrase), and `POST /api/auth/logout` |
| Chat and threads | `POST /api/chat` (`VercelAIAdapter` with `sdk_version=7`, `manage_message_history="server"`, `allowed_file_url_schemes=frozenset()`), `GET /api/chat/{thread_id}/stream` (reconnect to detached turn), `POST /api/chat/{thread_id}/stop`, and thread list, read, rename, search, and reply flagging |
| Home | `GET /api/home` composes account summary, 90-day default projection (configurable `horizon_days`), inbox preview, and recent activity |
| Ledger | Accounts (`GET/POST/PATCH/DELETE /api/accounts`, `POST /api/accounts/{id}/statement-balance`), movements (`GET/POST /api/movements`, `GET /api/movements/{id}`, `POST /api/movements/{id}/correct`, `GET /api/ledger/span`), categories and rules (`/api/categories`, `/api/category-rules`), uncategorized queue (`GET /api/uncategorized`, `POST /api/uncategorized/answer`), schedules and payment evidence (`/api/schedules`, `GET /api/schedules/upcoming`, `GET /api/schedules/{id}/occurrences/{date}`, `POST/DELETE .../payments`), goals (`/api/goals`), people and payees (`/api/people`, `/api/payees`), settings and memory (`GET/PATCH /api/settings`, `PATCH /api/preferences/{id}/content`), and undo (`POST /api/undo`, `POST /api/undo/{undo_id}`) |
| Projection | `GET /api/projection` and `GET /api/projection/workbook` (`.xlsx`, calculation version `2`) accept `horizon_days` from 1–3,650, `cut_percent` from 0–50 for reducible everyday spending, optional what-if `changes`, and hypothetical purchase terms (`amount_paise` and `emi_fee_paise` capped at `MAX_PAISE`, `emi_months` 2–48) |
| Attachments and imports | `POST /api/attachments` (accepts `purpose="statement"` or `"photo"`, up to 25 MB), `DELETE /api/attachments/{id}` (purges stored statement bytes), `GET /api/attachments/{id}` (serves photos and audio with `Cache-Control: private, no-store`), `GET /api/imports`, `GET /api/import-history` (keyset paginated), `GET /api/imports/{id}`, `PATCH /api/imports/{id}/lines/{line_id}`, `POST /api/imports/{id}/password`, `POST /api/imports/{id}/opening`, `POST /api/imports/{id}/commit`, `POST /api/imports/{id}/reject`, and `POST /api/imports/{id}/undo` |
| Capture | `POST /api/transcribe` and `POST /api/transcribe/{attachment_id}` (audio upload and retry, up to 25 MB). Photos attach to chat turns and reach the main agent natively |
| Recaps | `GET /api/recaps`, `GET /api/recaps/unseen`, `GET /api/recaps/{period_id}`, `GET /api/recaps/{period_id}/rows`, and `POST /api/recaps/{period_id}/seen` |
| Watch (inbox, questions) | `GET /api/notifications`, `POST /api/notifications/read-all`, `POST /api/notifications/{id}/read`, `POST /api/notifications/{id}/chat`, and CRUD `/api/scheduled-questions` |
| Audit history | `GET /api/history` (keyset-paginated audit log of undoable write groups) |
| Diagnostics and backups | `GET /api/runtime-info` (configured model labels/hosts and optional `ARNO_PHOENIX_UI_URL`, never credentials or collector addresses) and `GET /api/backup/status` |
| Export | `GET /api/export/scope` (lists excluded tables), `GET /api/export/json`, `GET /api/export/csv` (ZIP of CSVs with formula-injection quoting), and `GET /api/export/pg_dump` (owner only; no step-up) |
| Deletion | `GET /api/deletion/preview` (lists the rows affected) and `POST /api/deletion/everything` (requires the typed `"delete everything"` confirmation) |

Projection, affordability, and `.xlsx` workbook builds run off the event loop via
`asyncio.to_thread` behind a shared two-slot semaphore after loading inputs in one read-only
`REPEATABLE READ` transaction. Chat tools and REST endpoints call the same `src/arno/ledger/` and
`src/arno/tools/` functions and write the same audit log, so any edit is undoable from either
surface.

The web app consumes these routes with the [frontend stack](web-interface.md#frontend-stack).
**Settings → Diagnostics** shows each role's configured model and host, with an **Open Phoenix**
link when `ARNO_PHOENIX_UI_URL` is configured.

**Deployment and backups summary:** Arno ships as a Docker Compose stack (`api`, `db`, plus
optional `local-llm` and `obs` profiles; `obs` is stock Phoenix) published only on loopback and
exposed privately over Tailscale. The daily `backup_check` job inside `api` runs `pg_dump` into
restic whenever the last successful backup is over 22 hours old and records success through the
`backup` database role. See [`self-hosting.md`](../operations/self-hosting.md) for installation, provider retention,
backup, restore (`arno restore`), and upgrade procedures.

## Authentication

Arno serves one user on a private Tailscale network. Authentication keeps other tailnet devices
and hostile web pages out; it does not defend against the public internet, which never reaches
`api`. No auth state lives in the database.

A request is the owner when either method holds. A method whose setting is unset is disabled, and
with neither set every protected route returns `401` that names both variables, and boot logs a
warning.

| Method | Design |
|---|---|
| Tailscale identity | `ARNO_OWNER_EMAIL` is set and the `Tailscale-User-Login` header equals it (trimmed, case-insensitive). `tailscale serve` adds the header. This is trustworthy only while `api` listens on loopback behind `tailscale serve`; a port reachable any other way lets a caller type the header |
| Passphrase | `ARNO_PASSPHRASE` is set. `POST /api/auth/login` compares it with `hmac.compare_digest`, waits about one second on a miss, and sets `arno_session` to `<expiry>.<hmac>`, keyed from the passphrase. The cookie is `HttpOnly; SameSite=Strict`, `Secure` unless `ARNO_COOKIE_SECURE=0`, and lives `ARNO_SESSION_TTL_DAYS`. Changing the passphrase signs every device out |

Two checks run before either method, in `api/deps.py`:

- **Sandbox network.** The `api` container shares `sandbox-net` with the code sandbox, so
  model-written code could call `http://api:8000` with a forged header and its own `Host`.
  Compose pins that network to `10.89.250.0/24` and passes it as `ARNO_SANDBOX_SUBNET`. A request
  whose client address is inside it gets `403`, whatever header or cookie it carries.
- **Cross-site requests.** Tailscale Serve adds the header to every request from the owner's
  browser, including one a hostile page triggers. A request with `Sec-Fetch-Site` of `cross-site`
  or `same-site` gets `403`. Without that header, an unsafe method with an `Origin` whose host
  differs from `Host` gets `403`.

There is no step-up check. Exports rely on the owner check above, and erase-everything keeps its
typed `"delete everything"` phrase. Recovery is editing `.env`: no database state can lock you out.

## Scheduler and background tasks

Postgres holds all state and asyncio only runs the work. A minute loop inside the API process
(`src/arno/jobs/loop.py`) fires due scheduled questions and the daily tasks. Work that follows a
write, such as categorizing a committed import, starts through `spawn` in
`src/arno/jobs/background.py`. There is no job table: a restart resumes work from the state each
feature keeps on its own rows.

The following table lists every task, its trigger, and where its state lives:

| Task | Trigger (IST) | State that makes a restart safe |
|---|---|---|
| Scheduled question, including the seeded weekly briefing | The question's RRULE; the loop claims it by advancing `scheduled_questions.next_run_at` in one `UPDATE ... RETURNING` | `next_run_at`, plus the answer's `dedupe_key` |
| `reminders` | Daily at `notification_time` (N days before obligations, EMIs, and card due dates) | Reminder `dedupe_key` per source and due date |
| `risk_check` | Nightly at 02:00 (90-day default projection horizon) | `dedupe_key` per ISO week, so a standing risk warns once a week |
| `pattern_detection` | Nightly at 02:15 and after an import commit | Proposal `dedupe_key` |
| `recompute_baselines` | Nightly at 02:30, after an import commit, and after an import undo | Recomputed from ledger rows |
| `backup_check` | Nightly at 03:00 (alerts if no backup succeeded in 48 hours after install grace) | `backup_runs` |
| `categorize_import` | Spawned after the import commit transaction | `imports.categorize` |

Key properties:

- **Spawned work:** `spawn(coro, *, name)` runs a coroutine as an asyncio task, keeps a reference,
  and logs, never raises, its exception. With `ARNO_JOBS_ENABLED=0` it drops the coroutine. Each
  task holds the erase advisory lock (`ERASE_LOCK_KEY`) shared, so erase-everything refuses while a
  task runs and no task starts during an erase. `drain(timeout)` waits for tasks at shutdown, then
  cancels the rest.
- **Boot recovery:** `jobs.loop.recover()` respawns categorization for imports whose
  `imports.categorize` state is `queued` or `running`.
- **Scheduled questions:** Rules are RRULEs counted from the question's creation day at
  `notification_time`, in `Asia/Kolkata`. A machine that sleeps through several runs fires the
  question once on wake and moves on from now. A finite rule that has run its `COUNT` turns the
  question inactive. The weekly briefing is a default question with a fixed id, seeded once at
  boot; archiving or editing it sticks. A claimed run that the process dies in is not repeated.
- **Daily tasks:** The loop starts each task once per day after its time, and again after a
  restart. Every task is idempotent on its own rows, so the repeat sends nothing twice.
- **Notifications:** Every alert lands in the in-app inbox. With `ARNO_NOTIFY_WEBHOOK_URL` set, each
  new (not deduplicated) item is also posted there, through `spawn`, with a 10-second timeout; a
  failure is logged and never fails the write. A `api.telegram.org` URL receives the form field
  `text`; any other URL (for example an ntfy topic) receives a plain-text body with a `Title`
  header. Opening an inbox item in chat seeds a new thread and the server starts the turn, so Arno
  answers the seeded request instead of waiting for a user message that never comes.

## Database connections and integrity

### Connection cache and failure handling

The API process uses a per-loop cache of idle `asyncpg` connections (`API_POOL_MAX_IDLE = 8`) bound
in the FastAPI lifespan and closed after background tasks and active turns drain
(`src/arno/db/conn.py`):

- **Cache, not a capacity limiter:** `connect()` pops and pre-pings (`SELECT 1`) the last-returned
  released idle connection when available, and opens a fresh direct connection immediately when the
  idle deque is empty—never waiting on capacity. A bounded limiter pool could let long chat streams
  starve or deadlock tool calls; the cache preserves never-wait behavior. Exiting
  `async with connect()` runs `reset()` (rolling back open transactions and resetting session
  state) and returns up to eight connections to the deque, closing excess connections.
- **Direct bypasses:** `connect()` opens an uncached direct connection whenever an explicit `dsn`
  or `role` is passed, on other event loops (CLI and worker threads), or when `current_database_url()`
  differs from the bound DSN (eval trials). `agent_ro` queries stay direct for fresh session
  isolation.
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
  and `payees (lower(upi_id))`.
  `users_one_row_idx` on `users ((true))` enforces a single user row.
- **Account identity:** `accounts.name` must be `NULL` if and only if `kind` is `'person'` or
  `'goal'` (`accounts_name_kind_check`). Person names come from `payees.display_name`, goal names
  derive `<name> goal` from `goals.name`, `pgledger_accounts.name` stores the immutable
  `accounts.id`, and `v_accounts`, `v_legs`, `v_balances`, and `v_owed` (`Asia/Kolkata` cutoff)
  expose resolved names and balances.
- **Import metadata:** `imports.check_status` owns the check verdict (`VERIFIED`, `DISCREPANCY`,
  `FAILED`), `imports.counts` owns line counts, and `imports.attachment_id` owns the statement file
  link.

## Security and threat model

The following table summarizes Arno's threat model and implemented controls:

| Threat | Defense |
|---|---|
| Public internet attacker | Not exposed; private Tailscale access only, plus the owner check, the sandbox-subnet and cross-site refusals, single origin, `SameSite=Strict` cookies, `TrustedHostMiddleware`, and security headers |
| Stolen laptop or disk | Host disk encryption (BitLocker, LUKS, or FileVault), required at install |
| Leaked off-site backup | restic encryption; the restic password is stored off-host separately from backups |
| Lost restic password | Not defended (permanent data loss); must be backed up off-host |
| VPS host or live host compromise | Not defended; in-database encryption is parked ([`parked.md`](../planning/parked.md)) |
| Borrowed unlocked browser | Expiring passphrase cookies; erase-everything needs its typed phrase. A browser already signed in to the owner account is not defended |
| Prompt injection in statements or photos | The prompt treats statement text, payee notes, and image text as data. The agent cannot move money, and every write is audited and undoable. Wide writes (`correct_movement` with `scope` other than `this`, and `update_setup` to archive or change settings) require a confirm card |
| Forged or replayed confirm answer | Server-held history validates pending deferred calls once against stored arguments; partial multi-card answers are refused with HTTP `409` |
| Secrets typed into chat | Nothing redacts chat input: a secret you type reaches the model provider and is stored in `model_history`. The prompt forbids repeating a password, PIN, OTP, or full card number. Statement passwords use the dedicated import card field, stay in memory only for the unlock, and are never persisted |
| Data exfiltration through rendered output | Strict CSP (`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, plain-text links in agent replies, and `allowed_file_url_schemes=frozenset()` |
| Agent-written SQL | `agent_ro` role: `SELECT` only on allowed read views/tables, `default_transaction_read_only = on`, `2s` statement timeout, single extended-protocol statement rolled back after read, `TEMP` revoked, no auth, secret, or raw `pgledger_*` grants |
| Agent-written code | `run_python` posts the script to the `sandbox` service: a read-only container on an internal Docker network with no internet and no database route, a non-root user, 512 MB memory, one CPU, a 30-second timeout, a throwaway working directory, and no secrets. The script sees only the files it is sent |
| Credential leakage in data exports | JSON and CSV exports exclude the `SECRET_TABLES` entry `model_credentials` (`pg_dump` stays complete for backup/restore) |
| Supply-chain or copyleft dependency | Locked dependency files, a manual permissive-license check when a dependency changes, and `pip-audit` + `pnpm audit` in CI |
| Upload flood or runaway model spend | 25 MB upload limit, single-user sign-in gate, usage limits, and a fail-closed shared daily spend cap in Postgres (`llm_usage`) |
| Telemetry data exposure | Phoenix binds to loopback with authentication enabled, stores traces in its own SQLite volume, never in the Arno database, and vision agents disable tracing (`agent.instrument = False`). See [`self-hosting.md`](../operations/self-hosting.md) for provider data-retention policies |

### Postgres roles

The following table lists the four Postgres roles configured by `deploy/app-role.sh`:

| Role | Privileges | Access path |
|---|---|---|
| `postgres` | Bootstrap superuser, `NULL` password | Local container socket only; TCP refused |
| `app` | `LOGIN CREATEDB CREATEROLE NOSUPERUSER NOBYPASSRLS NOREPLICATION`, member of `pg_signal_backend`, `ADMIN OPTION` (`INHERIT FALSE, SET FALSE`) on `agent_ro` and `backup` | Password in `POSTGRES_PASSWORD`; owns Arno databases |
| `agent_ro` | `LOGIN`, `default_transaction_read_only = on`, `statement_timeout = '2s'`, `SELECT` on allowed read views/tables, `TEMP` revoked | Password set at boot from `AGENT_RO_PASSWORD` |
| `backup` | `LOGIN`, `SELECT` on `public` tables and sequences, `INSERT` on `backup_runs` only, `TEMP` revoked | Password set at boot from `BACKUP_PASSWORD` |

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
