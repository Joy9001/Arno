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
src/fin_agent/
  db/          connection, migrations/*.sql, roles, vendor/pgledger/ (unmodified, with licenses)
  ledger/      plain functions over the DB — the tools live here (no PydanticAI types)
  engine/      projection, affordability, backtest, pattern detection
  importers/   readers, parse-run host functions, checks, reconciliation, photo extraction
  agent/       PydanticAI agent, system prompt, tool wrappers, skills/parse-statement/
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
| Uploads | `POST /api/attachments` (statement, photo, audio), up to 25 MB. Statement imports then go through `password`, `review`, and `commit` endpoints; a photo becomes one transaction at once, with undo; a voice note sends at once and shows its transcript |
| Conversations | List, read, rename, and full-text search of threads, and flag a reply. A chat request with no thread ID creates one. Reading a thread also returns a running turn's message and merges saved cards back in ([step 10](system-design.md#the-chat-contract)) |
| Inbox and push | List and read notifications; register a push subscription |
| Export | Full CSV and JSON export, plus a `pg_dump` |
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

- **Chat (home):** Vercel AI SDK `useChat`, with `resume` on, shadcn/ui's chat patterns, and Streamdown
  for markdown. You see your threads, as in ChatGPT or
  Claude: open an old one, or start a new chat at any time. Tool-return metadata renders as cards:
  a logged transaction with undo, a "Remembered" memory card, a projection chart showing the
  verdict and its floor probability, a spending breakdown, an import summary, a chart the agent
  made. The adapter doesn't stream metadata from tools called inside a script, so Fin-Agent emits
  those cards itself from the `run_code` return. Citations open the rows behind a number.
  Voice-note recording and photo upload live in the composer. Links in
  agent replies render as plain text, and external images never load ([Security](#security)). An
  amount the [number check](system-design.md#the-number-check) can't trace gets a small note, and any
  reply can be flagged.
  The AI SDK's major version, 7, is pinned to match the adapter's `sdk_version`, because with the
  adapter's default, 5, approvals are silently ignored and the card just comes back
  ([tested](system-design.md#the-safety-tests-october-31-2026)). Changing it means rerunning that test.
- **Panels (read-only):** Ledger, Accounts and cards, Schedules, Goals, People, Projection, Inbox.
  Changes go through chat.
- **Editable screens:** Imports (upload, password, review, history, undoing a committed import) and
  Settings.
- **Settings:** profile and floor, categories and rules, preferences (the agent's memory: turn one
  on or off, or delete it for good), notification times, passkeys and sessions, backup and job status, the last model error, and a link to Phoenix for everything else. Each section lands with its layer.
- **PWA:** `vite-plugin-pwa`, with a manifest and service worker for install, push, and a _share target_—on Android, sharing
  a UPI screenshot from GPay or PhonePe opens Fin-Agent with the image attached. Recharts draws
  the charts.
- **Language:** the UI is English only and isn't translated. The chat mirrors the user's language
  mix.

---

## Auth

Single user. The instance is reachable only on a private Tailscale network, so auth guards
against other devices on the tailnet and a borrowed laptop—not the open internet.

The following table lists the choice for each piece of auth:

| Piece | Choice |
|---|---|
| First run | Setup token printed in the API log (or `fin-agent setup` on the host) creates the one user and registers a passkey. Setup closes after that |
| Login | Passkeys through `py_webauthn`. They need HTTPS and a stable hostname; the tailnet `*.ts.net` name gives both |
| Recovery | `fin-agent passkeys reset` on the host. Anyone who has a shell on the host already has the data, so this opens no new door |
| Sessions | Opaque token, hashed in the database. Browser: `HttpOnly; Secure; SameSite=Strict` cookie. Future Android app: same token as a bearer header |

Same-origin (the API serves the web app), so no CORS. Password + TOTP fallback and login lockout
are parked with public-internet access ([`parked.md`](parked.md)).

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
- **Each job carries a key for its job and period,** such as one week's briefing, so a restart can't
  send it twice.
- **At boot, anything marked `running` goes back to `pending`.** There's one process, so it was
  interrupted. A job runs again from the top, so every handler is safe to rerun.
- **A failing job retries with backoff, up to 3 attempts.** After the last one, it's marked `failed`
  and you get an inbox item.
- **At most two jobs run at once,** so a long parse run can't hold up a reminder. Each has a time limit.
- **A job can commit in the same transaction as its data,** so an import and its parse job exist
  together or not at all.

Backups are not a job. They run in their own sidecar, so they keep running when the
app is broken.

Numbers in briefings come from the engine; the LLM writes only the prose. If the LLM endpoint is
down, the briefing goes out as a plain template—the numbers still arrive.

**Delivery:** every item lands in the in-app inbox. Web push (`pywebpush`, VAPID keys generated
at first run) sends a contentless ping—"Your weekly briefing is ready"—so no financial content
passes through the browser vendor's push service.

---

## Deployment

Compose was re-checked on its merits against other ways to deploy an agent on October 31, 2026
([system design, step 10](system-design.md#step-10-interfaces-and-operations)). The `compose.yaml`
file defines the following services:

```yaml
# compose.yaml — shape, not final
services:
  api:        # FastAPI + agent + job loop + static web build; stop_grace_period: 30s
  db:         # postgres, one pinned major version; volume for its data
  backup:     # pg_dump → restic → off-site, when the last success is over 24 hours old
  ollama:     # profile: local-llm
  speaches:   # profile: local-stt  (OpenAI-compatible faster-whisper)
  phoenix:    # profile: traces  (its own image and database; loopback only)
```

A deployment works as follows:

- Running `docker compose up` with the default profile needs a cloud LLM API key, and the
  `--profile local-llm` flag also starts Ollama. Each role's endpoint is set by an environment
  variable.
- Access is through Tailscale on the host: `tailscale serve --bg` proxies HTTPS on the tailnet to the
  API, which Compose publishes on loopback only. A sidecar stays an option for a VPS
  ([step 10](system-design.md#running-it)).
- Updating is `docker compose pull`, then `up -d`. The API runs `pg_dump` before any migration, and
  migrations run in a transaction. A Postgres major upgrade is a documented dump and restore.
- API keys stay in `.env`, never in the database or dumps. Logs rotate at 10 MB.
- Host disk encryption (BitLocker, LUKS, FileVault) is an install requirement, stated in the
  docs—it's what protects the database at rest.
- Images are amd64; arm64 is parked.
- Every service restarts unless stopped, and `api` waits for a `pg_isready` health check on `db`. On a
  deploy, running turns get 20 seconds to finish, then a `CancellationToken` stops and saves the rest
  ([step 8](system-design.md#every-ending-saves-the-same-two-records)).
- `CHAT_FALLBACK_CONTEXT_WINDOW` and, for a local main model, `LLM_CONTEXT_WINDOW` must match what the
  server really uses, such as Ollama's `OLLAMA_CONTEXT_LENGTH`. The install docs say so
  ([step 8](system-design.md#when-a-model-fails)).
- `--profile traces` starts [Phoenix](system-design.md#traces-phoenix) with
  `PHOENIX_ALLOW_EXTERNAL_RESOURCES=false`, `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS=90`, and its own
  database on the same Postgres server. It needs about 400 MB of memory, and the app works the same
  without it.
- The API opens one database connection per use rather than a pool, so a Postgres restart leaves
  nothing to recover. The one exception is a small asyncpg pool for the harness's spend store. While Postgres is down, the API answers with a plain "database unreachable" and
  spends nothing on the LLM.

---

## Backups

- The `backup` sidecar checks hourly and runs `pg_dump` whenever its last success is over 24 hours
  old, because a nightly cron misses a sleeping laptop. It hands the dump to restic, which encrypts,
  dedupes, and ships it to any S3-compatible target (R2, B2, S3) or an rclone remote. It's independent
  of the API process. It dumps the app's database only, so traces stay out of backups.
- The sidecar records each success in Postgres, through its own role, which can read for `pg_dump` and
  write only that record. A daily job checks it, and no success for 48
  hours sends an inbox item and a push.
- Backups exist from L1—before real money goes in at L2.
- A restore drill is a milestone (L8). On a fresh machine, given the backup and the restic
  password, the ledger comes back.

The restic password is the one secret that has to live somewhere other than this machine. Losing
it makes the off-site copies unreadable—the install docs say so in bold.

---

## Security

The following table is the threat model, refreshed at L8:

| Threat | Defense |
|---|---|
| Internet attacker | Not reachable: the instance is only on the tailnet. Passkeys, security headers, single origin, `SameSite=Strict` cookies, and `TrustedHost` |
| Stolen laptop or disk | Host disk encryption, an install requirement |
| Leaked backup | restic encryption; the password isn't stored with the backups |
| VPS provider reads the disk | _Not defended._ Stated in the docs; in-database encryption is parked with this as its trigger |
| Live host compromise | _Not defended._ Stated in the docs |
| Prompt injection through statements or photos | Rule 2 in `plan.md` §4: statements are parsed in a run with no write or ledger tools, and photos by a tool-less call into a typed schema; payee names and notes reach the agent marked as data; lasting or wide writes ask first in a conversation that has read them ([`design.md`](design.md#untrusted-text-and-write-tiers)). Eval cases test it |
| A forged or replayed confirm answer | Server-held history: an answer counts once, only for a pending call, with the stored arguments ([tested](system-design.md#the-safety-tests-october-31-2026)) |
| Secrets typed into chat | `InputGuardrail` removes API keys, card numbers, PAN, and spaced Aadhaar before the model or storage sees them. Passwords, OTPs, and bank account numbers can't be detected, so a statement password has its own field on the import card |
| Data carried out through rendered output | A [strict content security policy](system-design.md#security) from L0, with no inline scripts and images only from the app's own origin; links in agent replies render as plain text. Client file URLs are off, so a client can't make the model fetch one |
| Agent-written SQL | The `agent_ro` grants are the boundary: `SELECT` only, no auth tables. The read-only default, the timeout, one statement through the extended protocol, and a transaction that's always rolled back are extra layers ([`tools.md`](tools.md#run_query)). `TEMP` is revoked, and no network extension such as `dblink` is installed |
| Agent-written code | Monty: no filesystem, environment, or network, with time and memory limits. A saved parser reruns with `os_policy` and a sleep limit set, because raw Monty otherwise exposes the clock and `random` |
| LLM provider sees data | The self-hoster's choice for each role. See [What each role sends](#what-each-role-sends) |
| Malicious dependency | Lockfiles, permissive-license allowlist, dependency audit in CI. An egress proxy is [parked](parked.md#an-egress-proxy) |
| Traces hold your finances | Phoenix stays on the host's loopback with outside requests off. It uses its own role, which owns only its database, and `agent_ro` can't connect to that database. Traces last 90 days. Reaching it over the tailnet needs its login on. Redaction runs before spans are written |

Logs never contain amounts, merchants, or message content—only IDs and timings. Traces do include
content, which is why they stay on this machine.

The `agent_ro` setup, tested on Postgres 18.6:

```sql
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '2s';
GRANT SELECT ON <the ledger, plan, people, categorization, and conversation tables and views> TO agent_ro;
REVOKE TEMP ON DATABASE fin_agent FROM PUBLIC;

-- With --profile traces: Phoenix owns only its own database, and agent_ro can't connect to it.
CREATE ROLE phoenix LOGIN;
CREATE DATABASE phoenix OWNER phoenix;
REVOKE CONNECT ON DATABASE phoenix FROM PUBLIC;
```

### What each role sends

The following table lists what leaves the box, by role:

| Role | Sends | When |
|---|---|---|
| Chat | Snapshot (balances, names, preferences), thread history, tool returns | Every turn |
| Parse run | Sample statement rows, including payee names and notes | New layouts only |
| Vision | The photo, or statement pages with no text | Each photo or such page |
| Speech-to-text | Your audio | Each voice note |
| Backups, push | Encrypted backups; contentless pings | Nightly; on notification |
| Never | Statement passwords, API keys, session tokens, full account or card numbers from statements | |

There are no analytics, and traces stay on this machine. A local model for a role means nothing leaves
for that role.

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

Everything country-specific lives in `src/fin_agent/india/`. India is the only country, so there
is no pack interface—that's parked until a second country needs one.

The following table lists the India-specific pieces:

| Piece | India |
|---|---|
| Default categories | Indian spending categories, such as UPI, rent, groceries, and family transfer |
| Statement parsers | Registered for each bank. See [`import.md`](import.md) |
| Calendar | Holidays for "last working day" schedules (`holidays` package) |
| Formatting | ₹, lakh and crore grouping |
| Prompt notes | Hinglish examples for the system prompt |
