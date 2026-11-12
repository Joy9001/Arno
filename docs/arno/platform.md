# Platform

This file holds the detail behind [`plan.md`](plan.md) §3 and §4: everything around the agent
that makes it a self-hostable product—API, web app, auth, notifications, scheduler, deployment,
backups, and security.

The `plan.md` file owns decisions, and this file owns their shape. If the two disagree, the plan
wins and this file is stale.

---

## Repository layout

The repository has the following layout:

```
src/arno/
  db/          connection, migrations/*.sql, roles, vendor/pgledger/ (unmodified, with licenses)
  ledger/      plain functions over the DB — the tools live here (no PydanticAI types)
  engine/      projection, affordability, backtest, pattern detection
  importers/   readers, parser-subagent host functions, the gate, reconciliation, photo extraction
  agent/       PydanticAI agent, system prompt, tool wrappers, the parser subagent
  api/         FastAPI app, routers, auth
  jobs/        job loop, briefing, reminders, patterns
  india/       categories, statement parsers, holidays, ₹ formatting, prompt notes
  cli.py
web/           Vite and React app (a static build)
backup/        sidecar image: pg_dump + restic on a schedule
evals/         agent evals: dataset, graders, scorecard (the old model-selection runner retires at L4)
tests/
compose.yaml
```

Folders get created when their layer starts, not before.

---

## API

FastAPI, one process, _one worker_ (running turns and the job loop live in that process, and recovery
at boot assumes there's only one). OpenAPI is the contract every client shares. The API also serves the static web build, so
web and API share one origin.

The API exposes the following surfaces:

| Surface | Shape |
|---|---|
| Chat | `POST /api/chat`—PydanticAI's `VercelAIAdapter` with `manage_message_history="server"`, `sdk_version=7`, and client file URLs off. The client sends the thread's ID and its new message, and the server holds the history ([step 6](system-design.md#two-records-both-on-the-server)). The run is a task of its own, so `GET /api/chat/{thread}/stream` can reattach and `POST /api/chat/{thread}/stop` stops it ([step 8](system-design.md#a-turn-outlives-its-connection)). Streams text, tool calls, and custom data parts (cards) in the Vercel AI UI protocol |
| Ledger REST | Accounts, movements, schedules, goals, payees, categories, rules, preferences. Same functions the agent's tools call. Panels read it; chat and settings write through it |
| Projection | `GET /api/projection`—the read-only Projection panel and the inline projection/verdict card. `horizon_days` and an optional hypothetical (`amount_paise`, `date`, `method`, `emi_months`, `account`) select the run. The same payload carries the curve, data quality, and source IDs |
| Uploads | `POST /api/attachments` (statement, photo, audio), up to 25 MB. Statement imports then go through `password`, `review`, and `commit` endpoints; a photo becomes one transaction at once, with undo; a voice note sends at once and shows its transcript. `GET /api/attachments/{id}` serves a photo or voice note back to the same origin under `img-src 'self'` |
| Capture | `POST /api/transcribe` saves the audio and returns a transcript or a retry card; `POST /api/chat/{thread}/photo` saves the image, runs the isolated `VISION_*` call, logs one movement at once, and returns the image-plus-fields undo card. Retries use the saved attachment ID |
| Conversations | List, read, rename, and full-text search of threads, and flag a reply. A chat request with no thread ID creates one. Reading a thread also returns a running turn's message and merges saved cards back in ([step 10](system-design.md#the-chat-contract)) |
| Inbox and push | List and mark read, open a notification in a new chat, subscribe or unsubscribe; the scheduler's job status, a pure-read activity poll, retry of a failed job, and the user's scheduled questions |
| Export | Full CSV and JSON export, plus a `pg_dump`. The JSON and CSV exports leave out the credential tables (`users`, `sessions`, `webauthn_credentials`, `webauthn_challenges`, `setup_tokens`, `vapid_keys`, and `push_subscriptions`); `pg_dump` stays complete because it is the backup and restore source |
| Auth | See [Auth](#auth) |

The endpoint map and conventions are in [system design, step 10](system-design.md#api).

Chat and settings converge on the same ledger functions and the same audit log, so an edit made
in settings is undoable from chat, and an edit made in chat is undoable from settings.

**Future clients.** An Android app consumes the same REST API and the same chat stream (it's
documented SSE). PydanticAI also ships an AG-UI adapter if a non-Vercel client wants it—adopt
when that client exists.

---

## Web app

Vite and React—a static bundle the API serves. No Node server in production; all data and auth live
in the API ([why](system-design.md#web-stack)).

The web app has the following parts:

- **Ask and Chats:** Ask is the agent's home (a new conversation with what it noticed); Chats adds
  the conversation list, a Chat or Flow view of each conversation, and a canvas for sources and the
  purchase planner. Vercel AI SDK `useChat`, with `resume` on, shadcn/ui's chat patterns, and Streamdown
  for markdown. You see your threads, as in ChatGPT or
  Claude: open an old one, or start a new chat at any time. Tool-return metadata renders as cards:
  a logged transaction with undo, a "Remembered" memory card, a projection chart showing the
  verdict and its floor probability, a spending breakdown, an import summary, a chart the agent
  made. The adapter doesn't stream metadata from tools called inside a script, so Arno emits
  those cards itself from the `run_code` return. Citations open the rows behind a number.
  Voice-note recording and photo upload live in the composer. Links in
  agent replies render as plain text, and external images never load ([Security](#security)). An
  amount the [number check](system-design.md#the-number-check) can't trace gets a small note, and any
  reply can be flagged.
  The AI SDK's major version, 7, is pinned to match the adapter's `sdk_version`, because with the
  adapter's default, 5, approvals are silently ignored and the card just comes back
  ([tested](system-design.md#the-safety-tests-october-31-2026)). Changing it means rerunning that test.
- **Panels (read-only):** Money (Timeline, Accounts, Schedules, Goals, People), Activity (Ledger,
  Imports, Inbox), and Recaps (sums of ledger rows told as stories). Changes go through chat.
- **Editable screens:** Imports (upload, password, review, history, undoing a committed import) and
  Settings.
- **Schedule payments:** Schedules includes recent occurrences and read-only payment details.
  Paid/received rows are struck through; partial rows show the matched amount and remainder.
  Find/unlink actions seed chat for the user to send. REST exposes occurrence details and
  link/unlink routes under `/api/schedules/{id}/occurrences/{date}`; each write is undoable.
  Generated card-due entries are labeled forecasts rather than statement payment evidence.
- **Settings:** profile and floor, categories and rules, preferences (the agent's memory: turn one
  on or off, or delete it for good), notification times, passkeys and sessions, backup and job
  status, the last model error, and a link to Phoenix for agent traces. Each section lands
  with its layer. The app renders no trace link.
- **PWA:** `vite-plugin-pwa`, with a manifest and service worker for install, push, and a _share target_—on Android, sharing
  a UPI screenshot from GPay or PhonePe opens Arno with the image attached. Recharts draws
  the charts.
- **Language:** the UI is English only and isn't translated. The chat replies in English by default
  and follows the user's language or mix only when they write in it.

---

## Auth

Single user. The instance is reachable only on a private Tailscale network, so auth guards
against other devices on the tailnet and a borrowed laptop—not the open internet.

The following table lists the choice for each piece of auth:

| Piece | Choice |
|---|---|
| First run | The host prints an [enrollment link](#enrollment-links) and a terminal QR code when it creates one: in the API log while setup is open, or from `arno setup`. A restart keeps a valid link and tells you to run `arno setup` if the printed link is gone. Opening it creates the one user and registers a passkey. Setup closes after that |
| More devices | Settings → Passkeys → _Add a device_ creates an enrollment link and QR code for the signed-in user. The new device opens it and registers its own passkey. Settings also lists, renames, and deletes passkeys, but never the last one |
| Login | Passkeys through `py_webauthn`. They need HTTPS and a stable hostname; the tailnet `*.ts.net` name gives both |
| Recovery | `arno passkeys reset` on the host deletes every passkey and session, reopens setup, and prints a fresh enrollment link and QR code. Anyone who has a shell on the host already has the data, so this opens no new door |
| Sessions | Opaque token, hashed in the database. Browser: `HttpOnly; Secure; SameSite=Strict` cookie. Future Android app: same token as a bearer header |

Same-origin (the API serves the web app), so no CORS. Password + TOTP fallback and login lockout
are parked with public-internet access ([`parked.md`](parked.md)).

### Enrollment links

First-run setup, adding a device, and recovery all use the same thing: a single-use link that
registers one passkey. Every link starts from something only the owner has, either a shell on the
host or a signed-in session. The owner never has to copy a token out of `docker compose logs`.

```text
https://<machine>.<tailnet>.ts.net/setup#t=<token>
```

- **Built from `ARNO_RP_ORIGIN`, never `localhost`.** A passkey registered on
  `localhost:8000` only works on `localhost` and won't work on the `*.ts.net` name. The link takes
  the owner to the right origin. If the setup page is opened on any origin other than
  `ARNO_RP_ORIGIN`, it says so and doesn't start the ceremony, instead of letting the browser
  fail with a `SecurityError`.
- **The token goes in the fragment.** Browsers never send the part after `#` to the server, so
  the token stays out of request logs and the `Referer` header. The page reads it, posts it to
  the API, and removes it from the address bar with `history.replaceState`. A paste box remains
  as a fallback.
- **Terminal QR code.** The CLI and the startup log print the link as a QR code (`segno`) as well,
  so the owner can set up from their phone. The log only carries one when it had to issue a token;
  a restart with a live link tells you to run `arno setup`. That startup record goes to the
  trusted local Docker stdout, not a central log store. The Settings page draws the
  add-device QR in the browser (`qrcode.react`) instead of an inline image, so `img-src 'self'`
  stays intact. Registering the phone's passkey first is the easiest path. Synced passkeys
  (iCloud Keychain, Google Password Manager) appear on the owner's other devices on the same
  account, and a desktop browser can sign in with the phone over the browser's own cross-device
  QR flow.
- **Tokens and lifetimes.** Tokens are stored as SHA-256 hashes and can each be used once. Each
  kind has one active token at a time. The first-run token lasts `ARNO_SETUP_TOKEN_TTL_HOURS`
  (24 hours by default) and is kept across restarts while it's valid, so a restart doesn't break a
  link the owner already copied. An _Add a device_ token lasts 10 minutes, is tied to the user who
  created it, and is cancelled when that user's sessions are revoked.
- **Rejected:** first visitor wins (anyone on the tailnet could claim the instance before the
  owner does), a long-lived setup secret in `.env`, and approving a browser code from the host
  (it still needs a host shell). Trusting the `Tailscale-User-Login` header that `tailscale serve`
  adds would remove the token entirely. That only works once nothing can reach the API without
  going through `tailscale serve`, and today other containers on the Docker network can, so it's
  parked.

---

## Notifications and scheduler

A `jobs` table and one loop in the API process run the following jobs, plus statement parsing
([system design, step 8](system-design.md#jobs-a-plain-table-and-one-loop)):

| Job | When |
|---|---|
| Weekly briefing | Sunday, configurable time |
| Reminders | N days before an obligation, EMI, or card due date |
| Risk check | Nightly, plus after every write |
| Pattern detection | Nightly and after each import |
| Baseline recompute | Nightly |
| Your scheduled questions | On the schedule you set. The agent runs read-only, with no write tools and its own spend limit, and answers into the inbox |

Every job except your scheduled questions is fixed code with at most one LLM call, so it still runs
when the LLM is down.

The loop works as follows:

- **A due schedule creates one job and moves on from now.** A run missed while the machine slept or
  restarted fires once when it's back, however many were missed. Times are computed in India time.
  A `cost_replay` job runs every five minutes for cost-only tracing recovery.
- **Each job carries a key for its job and period,** such as one week's briefing, so a restart can't
  send it twice.
- **At boot, anything marked `running` goes back to `pending`.** There's one process, so it was
  interrupted. A job runs again from the top, so every handler is safe to rerun.
- **A failing job retries with backoff, up to 3 attempts.** After the last one, it's marked `failed`
  and you get an inbox item.
- **At most two jobs run at once,** so a long job can't hold up a reminder. Each has a time limit.
  Imports aren't jobs: the parser subagent is a delegated task ([`import.md`](import.md#the-subagents-run)).
- **A job can commit in the same transaction as its data,** so a write and its follow-up job exist
  together or not at all.
- **A finished job keeps what its handler returned,** so the app can say what the job did—how many
  rows a categorize run labeled, for example. A handler that knows another attempt cannot help
  raises `JobGaveUp` and fails at once, without retries; the categorizer does this at its spend or
  step ceiling. A scheduled question answers instead: it writes one plain inbox answer naming the
  ceiling and marks the item `ceiling: true`, so you can see what stopped it.
- **A failed job can be retried from the app,** which resets the same row and clears its inbox
  alert.

Categorizing a committed statement reports `done`, `partial` when the model gave no answer for some
payees, `nothing_to_do`, or `batch_submitted`. A finished run lands one counts-only
`categorize_done` inbox item that never names a payee; only a batch run pushes, since it can take
hours.

The app shows background work while it runs: a rail indicator, toasts, the import's own status,
and Settings > Background work. It reads `GET /api/jobs/activity`, a pure read of active jobs plus
what finished after a cursor, polled every 3 s while work is active and every 30 s otherwise.

Backups are not a job. They run in their own sidecar, so they keep running when the
app is broken.

Numbers in briefings come from the engine; the LLM writes only the prose. If the LLM endpoint is
down, the briefing goes out as a plain template—the numbers still arrive.

**Delivery:** every item lands in the in-app inbox. Web push (`pywebpush`, VAPID keys generated
at first run) sends a contentless ping—"Your weekly briefing is ready"—so no financial content
passes through the browser vendor's push service. Tapping an inbox item opens a new chat seeded
with that item, so a follow-up or accepting a proposal goes through the agent and the normal write
rules.

---

## Deployment

Compose was re-checked on its merits against other ways to deploy an agent on October 31, 2026
([system design, step 10](system-design.md#step-10-interfaces-and-operations)). The self-hoster
walkthrough is [`self-hosting.md`](self-hosting.md). The `compose.yaml` file defines the following
services:

```yaml
# compose.yaml — shape, not final
services:
  api:        # FastAPI + agent + job loop + static web build; stop_grace_period: 30s
  db:         # postgres, one pinned major version; volume for its data
  backup:     # pg_dump → restic → off-site, when the last success is over 24 hours old
  phoenix:    # native Pydantic AI traces; own database; loopback UI; auth on
  ollama:     # profile: local-llm
  speaches:   # profile: local-stt  (OpenAI-compatible faster-whisper)
```

A deployment works as follows:

- Running `docker compose up` with the default profile needs a cloud LLM API key, and the
  `--profile local-llm` flag also starts Ollama. Each role's endpoint is set by an environment
  variable.
- Access is through Tailscale on the host: `tailscale serve --bg` proxies HTTPS on the tailnet to the
  API, which Compose publishes on loopback only. A sidecar stays an option for a VPS
  ([step 10](system-design.md#running-it)).
- Updating is `docker compose pull`, `up -d`, then `up -d --build phoenix`. `compose.yaml` pins
  `ghcr.io/joy9001/arno:$ARNO_VERSION` (and the matching backup image), and the release
  workflow pushes the version tag. Phoenix is built locally from its pinned context. The API runs `pg_dump` before any migration and writes it to the
  `pre-migration-data` volume, and migrations run in a transaction. After upgrading an instance that
  imported statements before this release, run `arno repair-openings --dry-run` and then
  `arno repair-openings --yes` once: it books each import's printed opening at the statement's
  first day, and it is idempotent. A Postgres major upgrade is a
  documented dump and restore.
- API keys stay in `.env`, never in the database or dumps. Logs rotate at 10 MB (`max-size: 10m`,
  `max-file: 3`) on every Compose service.
- Host disk encryption (BitLocker, LUKS, FileVault) is an install requirement, stated in the
  docs—it's what protects the database at rest.
- Images are amd64; arm64 is parked.
- Every service restarts unless stopped, and `api` waits for a `pg_isready` health check on `db`. On a
  deploy, running turns get 20 seconds to finish, then a `CancellationToken` stops and saves the rest
  ([step 8](system-design.md#every-ending-saves-the-same-two-records)).
- `CHAT_FALLBACK_CONTEXT_WINDOW` and, for a local main model, `LLM_CONTEXT_WINDOW` must match what the
  server really uses, such as Ollama's `OLLAMA_CONTEXT_LENGTH`. The install docs say so
  ([step 8](system-design.md#when-a-model-fails)).
- A normal `docker compose up` starts [Phoenix](system-design.md#traces-phoenix) beside the app.
  Pydantic AI spans export directly over OTLP/HTTP; there is no Collector, Loki, Prometheus,
  Grafana, custom metric, or log shipper. Standard Python/Uvicorn logs stay on Docker stdout.
  Phoenix uses its own database on the same Postgres server, requires authentication, and publishes
  only on loopback. Compose builds `arno-phoenix-cost:21.0.0` from `deploy/phoenix-cost` so a
  supplied cost, including an explicit zero, is shown rather than recalculated from Phoenix's
  catalog, and an unknown stays an unpriced count instead of `$0`. Its retention stays at the
  Phoenix default (0, infinite) unless the operator sets a policy. The app has no telemetry health
  dependency and keeps serving when Phoenix is down. Tracing is opt-in: with
  `PHOENIX_COLLECTOR_ENDPOINT` unset, nothing is registered or instrumented. The standard
  `OTEL_SDK_DISABLED` kill switch, cost journal, replay, and operator journey are in
  [`observability.md`](observability.md).
- Each model role reads `*_PROVIDER` and `*_BILLING_MODE`. The modes are `metered`, `local`, and
  `reported`; there is no subscription mode. Every paid path shares one strict store and pricing
  callback, so a database failure refuses paid inference instead of running uncapped.
- The API opens one database connection per use rather than a pool, so a Postgres restart leaves
  nothing to recover. The two exceptions are a small asyncpg pool for the spend store and
  per-operation journal connections. While Postgres is down, the API answers with a plain
  "database unreachable" and refuses paid inference.

---

## Backups

- The `backup` sidecar checks hourly and runs `pg_dump` whenever its last success is over 24 hours
  old, because a nightly cron misses a sleeping laptop. It writes `backup_manifest.json` (a row
  count and content hash for every table); the dump and the manifest come from one exported
  snapshot, so a mid-backup write is in both or neither. It hands both files to restic, which
  encrypts, dedupes, and ships them to any S3-compatible target (R2, B2, S3) or an rclone remote.
  It's independent
  of the API process. It dumps the app's database only, so telemetry volumes stay out of backups;
  the ledger, `audit_log`, job status, and backup status remain in Postgres and are backed up.
- The sidecar records each success in Postgres, through its own role, which can read for `pg_dump` and
  write only that record. A daily job checks it, and no success for 48
  hours sends an inbox item and a push.
- Backups exist from L1—before real money goes in at L2.
- The restore drill is `arno restore`. On a fresh machine, given the backup and the restic
  password, it pulls the dump and manifest out of the newest snapshot, restores into a
  database that has no tables, recomputes every table's hash, and checks that every transfer
  balances and every account balance equals its entries. The dump carries the migrations' table
  grants and the restore recreates the two login roles, so `run_query` and the backup sidecar work
  on the restored database. Restore from a backup the current image made: an older dump still
  restores, but a dump written without its table grants leaves `run_query` unprivileged until a new
  backup replaces it. It refuses to overwrite a populated database without `--force`.
  `tests/test_l8_ship.py` runs the round trip through a real restic repository.
  [Install and restore steps](self-hosting.md#62-restore-drill-on-a-fresh-machine).

The restic password is the one secret that has to live somewhere other than this machine. Losing
it makes the off-site copies unreadable—the install docs say so in bold.

---

## Security

The following table is the threat model, refreshed at L8 on November 3, 2026; its observability rows
were updated on November 4, 2026:

| Threat | Defense |
|---|---|
| Internet attacker | Not reachable: the instance is only on the tailnet. Passkeys, security headers, single origin, `SameSite=Strict` cookies, and `TrustedHost` |
| Stolen laptop or disk | Host disk encryption, an install requirement (BitLocker, LUKS, FileVault) |
| Leaked backup | restic encryption; the password isn't stored with the backups |
| Lost restic password | _Not defended; it is data loss._ The install docs say in bold to keep it off the machine. There is no recovery path |
| VPS provider reads the disk | _Not defended._ Stated in the docs; in-database encryption is parked with this as its trigger |
| Live host compromise | _Not defended._ Stated in the docs |
| Borrowed unlocked browser | Sessions expire; export and every passkey change need a fresh passkey check within five minutes (`sessions.reverified_at`) |
| Prompt injection through statements or photos | Rule 2 in `plan.md` §4: statements are parsed in a run with no write or ledger tools, and photos by a tool-less call into a typed schema; payee names and notes reach the agent marked as data; lasting or wide writes ask first in a conversation that has read them ([`design.md`](design.md#untrusted-text-and-write-tiers)). Eval cases test it |
| A forged or replayed confirm answer | Server-held history: an answer counts once, only for a pending call, with the stored arguments ([tested](system-design.md#the-safety-tests-october-31-2026)) |
| Secrets typed into chat | `InputGuardrail` removes API keys, card numbers, PAN, and spaced Aadhaar before the model or storage sees them. Passwords, OTPs, and bank account numbers can't be detected, so a statement password has its own field on the import card |
| Data carried out through rendered output | A [strict content security policy](system-design.md#security) from L0, with no inline scripts and images only from the app's own origin; links in agent replies render as plain text. Client file URLs are off, so a client can't make the model fetch one |
| Agent-written SQL | The `agent_ro` grants are the boundary: `SELECT` only, no auth tables. The read-only default, the timeout, one statement through the extended protocol, and a transaction that's always rolled back are extra layers ([`tools.md`](tools.md#run_query)). `TEMP` is revoked, and no network extension such as `dblink` is installed |
| Agent-written code | Monty: no filesystem, environment, or network, with time and memory limits. A saved parser reruns with `os_policy` and a sleep limit set, because raw Monty otherwise exposes the clock and `random` |
| LLM provider sees data | The self-hoster's choice for each role. See [What each role sends](#what-each-role-sends) |
| Malicious or compromised dependency | Lockfiles, the permissive-license allowlist in `scripts/license_audit.py`, and `pip-audit` + `pnpm audit` in CI. An egress proxy is [parked](parked.md#an-egress-proxy) |
| GPL, AGPL, or unknown license in the image | The same audit fails the build. Weak copyleft (`pywebpush` MPL-2.0) is allowed only as unmodified libraries and is named in `THIRD_PARTY_NOTICES.md` |
| Upload flood or a huge file | 25 MB cap on statements, photos, and audio; one user behind a passkey; spend ceilings |
| Logs leak finances | Logs are standard Python/Uvicorn stdout, kept on the host. They are not shipped to a central log store |
| Traces hold your finances | Phoenix stays on the host's loopback with outside requests off and authentication on. It uses its own role, which owns only its database, and `agent_ro` can't connect to that database. Retention is the Phoenix default (0, infinite) unless the operator sets a policy. Reaching it over the tailnet needs a deliberate change and its login. Pydantic AI's default instrumentation records prompts, replies, and tool data. There is no custom pre-export redaction |

Logs are standard application output and stay on the host. Traces include prompt, reply, and tool
content because Pydantic AI's default instrumentation is used; that is why Phoenix stays on this
machine. There is no Loki, Grafana, or Collector in the path.

The `agent_ro` setup, tested on Postgres 18.6:

```sql
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '2s';
GRANT SELECT ON <the ledger, plan, people, categorization, and conversation tables and views> TO agent_ro;
REVOKE TEMP ON DATABASE arno FROM PUBLIC;

-- Phoenix owns only its own database, and agent_ro can't connect to it.
CREATE ROLE phoenix LOGIN;
CREATE DATABASE phoenix OWNER phoenix;
REVOKE CONNECT ON DATABASE phoenix FROM PUBLIC;
```

### What each role sends

The following table lists what leaves the box, by role:

| Role | Sends | When |
|---|---|---|
| Chat | Snapshot (balances, names, preferences), thread history, tool returns | Every turn |
| Parser subagent (`PARSE`, or `CHAT` when unset) | Sample statement rows, including payee names and notes | Each import; a few rows when a saved parser replays cleanly |
| Vision | The photo, or statement pages with no text | Each photo or such page |
| Batch (only when `BATCH` is set) | Payee names, a sample narration for each, and your category names | Big categorizing backfills. Batch APIs are cloud-only; a local setup leaves `BATCH` unset and categorizes with the chat model |
| Speech-to-text | Your audio | Each voice note |
| Backups, push | Encrypted backups; contentless pings | Nightly; on notification |
| Never | Statement passwords, API keys, session tokens, full account or card numbers from statements | |

There is no third-party analytics; traces and logs stay on this machine. A local model for a role
means nothing leaves for that role.

### What providers keep

Checked October 31, 2026. Which provider runs each role is your choice, and the install docs repeat
this table with its date:

| Provider | Trains on it | Keeps it |
|---|---|---|
| [Anthropic API](https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training) | No, by default | [Up to 30 days](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data). [Fable 5 and 5.1, Mythos 5 and 5.1](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention) require 30 days, with no zero-retention option. Flagged content can stay up to 2 years |
| [OpenAI API](https://developers.openai.com/api/docs/guides/your-data) | No, by default | Abuse-monitoring logs, up to 30 days, longer if legally required or to prevent harm |
| [Gemini paid](https://ai.google.dev/gemini-api/terms) | No | Limited abuse-monitoring logs |
| [Gemini unpaid](https://ai.google.dev/gemini-api/terms) | **Yes, with human review.** Google's terms say not to send personal information | Don't use it for any role |
| Local models (Ollama, `speaches`) | Nothing leaves | |

---

## India-specific code

Everything country-specific lives in `src/arno/india/`. India is the only country, so there
is no pack interface—that's parked until a second country needs one.

The following table lists the India-specific pieces:

| Piece | India |
|---|---|
| Default categories | Indian spending categories, such as UPI, rent, groceries, and family transfer |
| Statement parsers | Registered for each bank. See [`import.md`](import.md) |
| Calendar | Holidays for "last working day" schedules (`holidays` package) |
| Formatting | ₹, lakh and crore grouping |
| Prompt notes | Hinglish examples for the system prompt |
