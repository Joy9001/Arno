# Platform

Detail behind [`plan.md`](plan.md) §3 and §4: everything around the agent that makes it a
self-hostable product — API, web app, auth, notifications, scheduler, deployment, backups,
security.

`plan.md` owns decisions. This file owns their shape. If the two disagree, `plan.md` wins.

---

## Repo layout

```
src/fin_agent/
  db/          connection, migrations/*.sql, roles
  ledger/      plain functions over the DB — the tools live here (no PydanticAI types)
  engine/      projection, affordability, backtest, pattern detection
  importers/   parser registry, invariant, reconciliation, photo extraction
  agent/       PydanticAI agent, system prompt, tool wrappers
  api/         FastAPI app, routers, auth
  jobs/        scheduler, briefing, reminders, patterns
  india/       categories, statement parsers, holidays, ₹ formatting, prompt notes
  cli.py
web/           Next.js app (static export)
backup/        sidecar image: pg_dump + restic on a schedule
evals/         model-selection harness (existing) + agent evals
tests/
compose.yaml
```

Folders get created when their layer starts, not before.

---

## API

FastAPI, one process, **one worker** (the scheduler runs in-process; a second worker would
duplicate jobs). OpenAPI is the contract every client shares. The API also serves the static web
build, so web and API share one origin.

| Surface | Shape |
|---|---|
| Chat | `POST /api/chat` — PydanticAI `VercelAIAdapter.dispatch_request()`. Streams text, tool calls and custom data parts (cards) in the Vercel AI UI protocol |
| Ledger REST | CRUD for accounts, transactions, schedules, goals, counterparties, claims, categories, rules, preferences. Same functions the agent's tools call. Panels read it; chat and settings write through it |
| Uploads | `POST /api/attachments` (statement, photo, audio). Imports and photos then go through `review` → `commit` endpoints |
| Conversations | List, read, full-text search |
| Inbox & push | Notifications list/read; push subscription register |
| Export | Full CSV/JSON export and a `pg_dump` |
| Auth | See below |

Chat and settings converge on the same ledger functions and the same audit log, so an edit made
in settings is undoable from chat and vice versa.

**Future clients.** An Android app consumes the same REST API and the same chat stream (it's
documented SSE). PydanticAI also ships an AG-UI adapter if a non-Vercel client wants it — adopt
when that client exists.

---

## Web app

Next.js with **`output: 'export'`** — a static bundle the API serves. No Node server in
production; all data and auth live in the API. If server rendering is ever needed, switch to the
standalone server output.

- **Chat** (home): Vercel AI SDK `useChat` + AI Elements. Tool-return metadata renders as cards:
  a logged transaction with undo, a "Remembered" memory card, a projection chart with the verdict
  and its floor probability, a spending breakdown, an import summary. Citations open the rows
  behind a number. Voice-note recording and photo upload live in the composer
- **Panels**, read-only: Ledger, Accounts & cards, Schedules, Goals, People, Projection, Inbox.
  Changes go through chat
- **Editable screens:** Imports (upload, review, history) and Settings
- **Settings:** profile and floor, categories and rules, preferences (the agent's memory),
  notification times, passkeys and sessions, backup status. Each section lands with its layer
- **PWA:** manifest + service worker for install, push, and a **share target** — on Android,
  sharing a UPI screenshot from GPay or PhonePe opens Fin-Agent with the image attached. Charts
  with Recharts
- Hinglish and English UI copy are not translated — the UI is English, the chat mirrors the user

---

## Auth

Single user. The instance is only reachable on a private Tailscale network, so auth guards
against other devices on the tailnet and a borrowed laptop — not the open internet.

| Piece | Choice |
|---|---|
| First run | Setup token printed in the API log (or `fin-agent setup` on the host) creates the one user and registers a passkey. Setup closes after that |
| Login | **Passkeys** via `py_webauthn`. They need HTTPS and a stable hostname; the tailnet `*.ts.net` name gives both |
| Recovery | `fin-agent passkeys reset` on the host. Anyone with a shell on the host already has the data, so this opens no new door |
| Sessions | Opaque token, hashed in DB. Browser: `HttpOnly; Secure; SameSite=Strict` cookie. Future Android app: same token as a bearer header |

Same-origin (the API serves the web app), so no CORS. Password + TOTP fallback and login lockout
are parked with public-internet access ([`parked.md`](parked.md)).

---

## Notifications and scheduler

In-process scheduler (APScheduler) in the API process.

| Job | When |
|---|---|
| Weekly briefing | Sunday, configurable time |
| Reminders | N days before an obligation, EMI or card due date |
| Risk check | Nightly, plus after every write |
| Pattern detection | Nightly and after each import |
| Baseline recompute | Nightly |

Backups are not a scheduler job. They run in their own sidecar, so they keep running when the
app is broken.

Numbers in briefings come from the engine; the LLM only writes the prose. If the LLM endpoint is
down, the briefing goes out as a plain template — the numbers still arrive.

**Delivery:** every item lands in the in-app inbox. **Web push** (`pywebpush`, VAPID keys
generated at first run) sends a contentless ping — "Your weekly briefing is ready" — so no
financial content passes through the browser vendor's push service.

---

## Deployment

```yaml
# compose.yaml — shape, not final
services:
  api:        # FastAPI + agent + scheduler + static web build
  db:         # postgres, one pinned major version; volume for its data
  backup:     # nightly pg_dump → restic → off-site
  ollama:     # profile: local-llm
  speaches:   # profile: local-stt  (OpenAI-compatible faster-whisper)
```

- `docker compose up` with the default profile needs a cloud LLM key; `--profile local-llm`
  brings Ollama alongside. Each role's endpoint is just env
- **Access:** Tailscale, with `tailscale serve` proxying HTTPS on the tailnet to the API. Host
  install vs sidecar container is an open question (`plan.md` §6, L0)
- **Host disk encryption** (BitLocker, LUKS, FileVault) is an install requirement, stated in the
  docs — it's what protects the database at rest
- Images are amd64; arm64 is parked

---

## Backups

1. The `backup` sidecar runs `pg_dump` nightly and hands it to restic, which encrypts, dedupes and
   ships it to any S3-compatible target (R2, B2, S3) or an rclone remote. It's independent of the
   API process
2. Backups exist from L1 — before real money goes in at L2
3. **A restore drill is a milestone** (L8): a fresh machine, the backup, the restic password, and
   the ledger comes back

The restic password is the one secret that has to live somewhere other than this machine. Losing
it makes the off-site copies unreadable — the install docs say so in bold.

---

## Security

Threat model, refreshed at L8:

| Threat | Defence |
|---|---|
| Internet attacker | Not reachable: the instance is only on the tailnet. Passkeys, security headers, single origin |
| Stolen laptop or disk | Host disk encryption, an install requirement |
| Leaked backup | restic encryption; the password isn't stored with the backups |
| VPS provider reads the disk | **Not defended.** Stated in the docs; in-database encryption is parked with this as its trigger |
| Live host compromise | **Not defended.** Stated in the docs |
| Prompt injection via statements or photos | Rule 2 (`plan.md` §4): untrusted text goes through an isolated, tool-less extraction call into a typed schema. Eval cases test it |
| Agent-written SQL | The `agent_ro` role: read-only, no auth tables, statement timeout |
| LLM provider sees data | The self-hoster's choice per role. Docs state plainly what each role sends |
| Malicious dependency | Lockfiles, permissive-licence allowlist, dependency audit in CI |

Logs never contain amounts, merchants or message content — only ids and timings. Traces with
content live in `agent_runs` in the database.

---

## India-specific code

Everything country-specific lives in `src/fin_agent/india/`. India is the only country, so there
is no pack interface — that's parked until a second country needs one.

| Piece | India |
|---|---|
| Default categories | Indian spending categories (UPI, rent, groceries, family transfer…) |
| Statement parsers | Registered per bank. See [`import.md`](import.md) |
| Calendar | Holidays for "last working day" schedules (`holidays` package) |
| Formatting | ₹, lakh/crore grouping |
| Prompt notes | Hinglish examples for the system prompt |
