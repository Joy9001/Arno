# Arno plan

This document is Arno's core product plan and decision authority: product scope, stack choices,
architecture rules, build milestones, open questions, and shipped criteria. For the complete
documentation map and subsystem owners, see [`docs/README.md`](../README.md). If another document
disagrees with `plan.md`, `plan.md` wins and the other document is stale.

## 1. The product

Arno is a self-hosted, open-source personal finance agent that holds your complete financial
picture—accounts, cards, obligations, goals, and people you owe—and answers any question the data
supports, including whether you can afford a purchase and what it costs.

"Can I afford this?" was the initial spark, not the scope boundary. The following table summarizes
the product's positioning:

| Question | Decision |
|---|---|
| Who uses it? | One person for each instance; others self-host their own copy |
| Where does it run? | Any x86-64 Docker host (laptop, mini PC, or VPS), reached over a private Tailscale network and never exposed to the public internet |
| What is its main interface? | Agent-first web app (PWA) with read-only financial panels around it; CLI for host administration and development |
| Which country is supported? | India-first; all country-specific logic lives in `src/arno/india/` |
| What license applies? | MIT for Arno application code; permissive dependencies only (no GPL, AGPL, SSPL, or Elastic code in the shipped image) |

**Done is:** Arno holds the owner's complete financial picture, answers any supported question with
computed tradeoffs, warns before cash flow turns, measures its forecasts against actual outcomes,
survives four weeks of daily use without falling back to mental math or a spreadsheet, and is
installed by a second person from the documentation alone.

---

## 2. Scope

The following sections define what Arno includes, what it leaves out, its non-functional targets,
and its operating constraints.

### The principle

**"Answers any question" is a property of the tool surface, not a feature list.** A small set of
orthogonal tools over complete ledger data composes into an unbounded question space. Scope is
governed by the data model and tool contracts in [`specs/data-and-engine.md`](specs/data-and-engine.md)
and [`specs/tools.md`](specs/tools.md).

### What Arno knows

The following table records each domain area in the ledger:

| Area | Scope |
|---|---|
| Accounts | Multiple bank accounts, credit cards (statement and due cycles, credit limit, finance and EMI terms), cash, wallets, loans, and hand-updated asset accounts |
| Currency | One configurable base currency for each instance; foreign purchases are recorded at the charged amount |
| Income | Fixed salary, variable or freelance income (expected versus confirmed), bonuses, and one-offs |
| Obligations | Rent, EMIs, subscriptions, insurance, family transfers, and card installment plans |
| Goals | Goals with target amounts, deadlines, priority, fenced savings, and contribution plans |
| Floor | A fixed balance threshold that the projection must not cross |
| People | Splits, lending, borrowing, and reimbursements modeled as ledger balances |
| Investments | Asset accounts with hand-updated balances (EPF, FD, PPF, NPS, gold, funds); statement-driven net worth stays parked |
| Categories | Editable defaults with a reducible flag and learned merchant-to-category rules; rules always win over model categorization, and payee corrections can apply retroactively |
| Memory | Agent-managed typed memories (`preference`, `fact`, or `rule`), keyed by `(kind, subject)` so updates replace active entries while keeping history in `audit_log`; never holds balances owned by the ledger |
| Conversations | Persistent, searchable chat threads where every ledger write links back to the message that caused it |

### How data enters

Day-to-day capture uses chat, voice notes, and photos. Monthly statement imports reconcile what
daily capture missed.

1. **Chat:** Text in English, Hindi, or Hinglish. Initial setup also runs through chat, walking
   through accounts, income, obligations, goals, and the floor.
2. **Voice notes:** Push-to-talk audio sent through speech-to-text into the chat path.
3. **Photos:** Receipts, UPI payment screenshots, bank-app transaction lists, bills, or charts
   attach directly to chat turns for the main agent to read natively. On Android, the installed PWA
   acts as a share target.
4. **Statement imports:** PDF, spreadsheet, or text statements (including password-protected files)
   uploaded to the import card, which reconciles rows against the ledger and commits only after user
   review. Statement extraction is being rebuilt
   ([`specs/import.md`](specs/import.md#extraction-status)).
5. **Web interface:** File uploads, import review, and settings are editable screens; financial
   panels are read-only so edits flow through chat with undo.

**Balances update only from committed statements or explicit statements of balance.** Arno never
nags for balances and always reports how old its figures are.

**One record represents each real money movement, regardless of capture path.** Certain matches
merge on import; uncertain matches wait on the import review screen.

**Useful from the first conversation.** A spoken snapshot of balances, card dues, and salary day is
enough for answers and affordability verdicts on day one.

### What Arno answers and does

- **Ledger queries:** A read-only `run_query(sql)` running as `agent_ro` over the ledger views.
- **Multistep modeling:** For arithmetic beyond SQL and the projection, the agent writes a short
  Python script (`run_python`) that runs in the isolated `sandbox` service and states only what
  the script prints.
- **Web search:** The model's own search, or Brave Search when `BRAVE_API_KEY` is set, for current
  rates and rules. Replies cite sources as `[n]`.
- **Affordability:** Computed in code as `yes`, `yes-but`, or `no` from 16,384 seeded daily
  scenarios. Scheduled events are exact and variable spend is resampled from history. A `yes`
  requires at least a 90% probability of staying above the floor and at least a 99% probability of
  meeting every obligation. A `yes-but` requires a same-date plan (card or EMI, delayed goal
  contribution, or reducible-category cut); a wait-only date remains `no`.
- **Safe to spend:** The largest cash payment today that still passes the `yes` test, read from the
  same projection run.
- **Multi-event what-ifs:** Up to 12 dated hypothetical changes (stopped income, paused or changed
  schedules, added recurring or one-time flows) evaluated by the same projection engine without
  saving changes, returning plan health and runway in days.
- **Debt cost analysis:** Loan amortization with prepayment, processing fees, and nominal _annual
  percentage rate_ (_APR_), offer comparison, and credit-card payoff walks at the card's stored or
  stated finance charge (never an assumed card rate), all computed by the agent in `run_python`.
- **Recaps:** Server-computed daily, weekly, and monthly template stories where every figure links
  to the ledger rows that sum to it ([`specs/data-and-engine.md`](specs/data-and-engine.md#recaps)).
- **Citations:** Every answer figure links to the ledger rows, schedules, or tool outputs behind it.
- **Proactive monitoring:** Fixed-code jobs generate bill, EMI, and card-due reminders, projection
  risk warnings, weekly briefings, and recurring-charge or price-change proposals into the inbox
  and web push. Because these run as code, they work even when the LLM is unavailable.
- **User-scheduled questions:** Read-only prompts run on your schedule with a per-run request limit
  and deliver answers to the inbox.
- **Write discipline:** Everyday chat writes, photos, and memory saves land immediately with an
  inline undo card. Statement imports wait for the review screen. Wide or account-wide writes
  (bulk category rules, archiving, settings) require a tap on a confirm card (rule 2 in §4).
- **Spending definition:** Spending measures consumption only. Own-account transfers (including card
  bill payments), lending, settlements, reimbursements, and savings or investment outflows do not
  count as spending; refunds offset the purchases they reverse.
- **Advice and action boundaries:** Arno states facts and tradeoffs only. It never moves money and
  never recommends investments or financial products.
- **Language:** Replies default to English and follow the user's language or mix (such as Hindi or
  Hinglish) when they write in it.

### Deliberately out of scope

The following capabilities are parked with explicit revisit triggers in
[`planning/parked.md`](planning/parked.md): chat-app bridges (Signal, Telegram, WhatsApp), native
Android app, real-time voice conversation, editable financial panels, SMS and email autocapture,
balance nudges, OCR for scanned statements, receipt line items, statement-driven investment net
worth, broker APIs, tax engine, category budgets, multicurrency, multiuser households, per-account
projections, extra briefings (daily, monthly, payday), anomaly detection, dedicated AI gateway,
public-internet hosting, Tailscale-identity setup, encryption inside Postgres, locale-pack
interface, arm64 images, egress proxy, external job library, MCP server, direct Claude Agent SDK
subscription runtime, hidden semantic memory extraction.

Promote a parked item only when its absence causes Arno to answer incorrectly or miss a warning.
Unbuilt proposals beyond the core product live in
[`planning/capability-catalog.md`](planning/capability-catalog.md).

### Non-functional requirements

The following table states the non-functional targets:

| Requirement | Target |
|---|---|
| Scale | One user for each instance; see [scale assumptions](architecture.md#scale-assumptions) |
| Latency | Backend trace targets are about 2 s to first streamed output, about 5 s for a single-lookup answer, and about 15 s for a multistep what-if on a cloud model (current measured eval median is 23.4 s; tracked in [`completion-checklist.md`](completion-checklist.md)) |
| Portability | `docker compose up` on any x86-64 Linux host, Docker Desktop, or VPS |
| Durability | Daily encrypted `pg_dump` through restic, run inside the API, with off-site targets and a verified `arno restore` drill |
| Security | Private Tailscale network only, WebAuthn passkey login, encrypted host disk, encrypted off-site backups, environment variables for deployment secrets, and an isolated `sandbox` service for model-written Python |
| Privacy | Per-role provider switch; only configured inference requests, encrypted backups, and contentless push pings leave the host |
| Honesty | Tools distinguish zero from unknown and report data age, coverage gaps, and source IDs |
| Measured quality | The agent eval suite with `pass^3` grading ([success measures](architecture.md#success-measures)) |
| Autonomy | The agent starts no write on its own except saving visible, undoable memories, and never moves money |

### Constraints

- **Solo maintainer:** Built and maintained at 15–20 hours a week.
- **India-first:** INR in integer paise, UPI workflows, Indian statement layouts, Hinglish, and
  Indian bank holidays.
- **No bank APIs:** India's Account Aggregator network serves regulated financial institutions only
  ([Sahamati](https://sahamati.org.in/financial-information-user-fiu)), so data comes from
  statements and user capture.
- **Permissive licenses only:** MIT application code and permissive runtime dependencies.
- **Modest hardware:** Runs on a laptop, mini PC, or small VPS; inference spend is logged and
  capped daily.

---

## 3. Stack

Arno buys commodity infrastructure and builds only the financial core.

### Build versus buy

The following table records the build-versus-buy decision for each component:

| Component | Decision | Rationale |
|---|---|---|
| Agent loop, tool calling, streaming | Buy: PydanticAI | Plain `Agent` with a few capabilities, native tool approval, Vercel AI protocol adapter, and provider switching ([`specs/harness.md`](specs/harness.md)) |
| Statement file readers | Buy: `pypdfium2`, `python-calamine`, `python-docx`, `striprtf`, `msoffcrypto-tool` | One reader library for each format ([`specs/import.md`](specs/import.md)) |
| Statement layout parsing | Under rebuild | Extraction is replaced in the simplification plan's Phase 3; the checks, reconciliation, and review stay ([`specs/import.md`](specs/import.md#extraction-status)) |
| Speech-to-text | Buy: OpenAI-compatible STT endpoint | Cloud API, or any self-hosted OpenAI-compatible server you run yourself (`STT_BASE_URL`); no bundled server |
| Textless-page PDF vision | Buy: OpenAI-compatible vision model | Isolated `VISION_*` role for scanned PDF pages during statement imports |
| Passkeys and web push | Buy: `py_webauthn`, `pywebpush` | Standard cryptographic and WebAuthn protocols |
| Private HTTPS access | Buy: Tailscale (`tailscale serve`) | End-to-end encrypted tailnet access with zero public exposure |
| Off-site backups | Buy: restic (with rclone) | Encrypted, deduplicated backups to S3-compatible or cloud targets |
| Agent evaluations | Buy: `pydantic_evals` | Runs the production agent directly against cloned test databases |
| Observability | Buy: Arize Phoenix + stock OpenTelemetry SDK | FastAPI, asyncpg, and PydanticAI spans export over OTLP/HTTP to stock Phoenix on SQLite (`--profile obs`, off by default); no custom span, exporter, or replay code ([`operations/observability.md`](operations/observability.md)) |
| Model cost | Build: `llm_usage` table + one shared daily cap | One row per model response, priced by `genai-prices` through PydanticAI; $20 a day shared by every role, reset at midnight IST; unpriced models count as $0. Replaces the spend ledger, cost journal, pricing and metering modules, and per-run budgets ([`specs/cost-tracking.md`](specs/cost-tracking.md)) |
| Model-written Python | Build: isolated `sandbox` service | Stateless container on an internal network with no internet or database route, read-only, resource-capped, and given no secrets; `api` reaches it at `ARNO_SANDBOX_URL` through `run_python` |
| Web search | Buy: model-native search, else Brave Search | The agent searches only when the model supports it or `BRAVE_API_KEY` is set ([`specs/tools.md`](specs/tools.md#web_search)) |
| Double-entry ledger core | Buy: vendored `pgledger` | MIT-licensed pure Postgres SQL with idempotency keys |
| Ledger mapping, projection engine, patterns | Build | Core product differentiator ([`specs/data-and-engine.md`](specs/data-and-engine.md)) |

### Technology choices

The following table records the stack for each layer:

| Layer | Choice | Rationale |
|---|---|---|
| Backend language | Python 3.14 (`uv`) | Primary ecosystem for PydanticAI and numerical tooling |
| Agent framework | PydanticAI | Native approvals, caching, history processors, fallback, and web search |
| API server | FastAPI + Uvicorn | Pydantic-native OpenAPI contracts; also serves the static web build |
| Database | PostgreSQL 18 (pinned) | Enforces the `agent_ro` read-only role and 2-second timeout natively; supports real date/numeric types and `pgvector` if needed |
| Data access | Plain SQL over `asyncpg` + per-loop idle connection cache (up to 8 connections) | Schema stays transparent for `run_query`; never-wait cache avoids pool deadlocks on long chat streams ([`specs/platform.md`](specs/platform.md#database-connections-and-integrity)) |
| Migrations | Numbered `.sql` files + `schema_version` table | Plain transactional SQL with recorded checksums |
| Type checking | Pyrefly (`strict` preset, zero warnings) | Native Pydantic understanding and per-path strictness ([`operations/typing.md`](operations/typing.md)) |
| Engine arithmetic | NumPy (integer arrays) | Vectorized 16,384-scenario integer kernel; BSD-3-Clause |
| Background jobs | Plain `jobs` table + single API-process loop | Zero broker overhead; jobs persist in every `pg_dump` |
| Web interface | Vite, React, `@assistant-ui/*`, `@radix-ui/themes`, Radix Primitives, `motion`, `Recharts`, `zustand` | Static build served by FastAPI under a strict content security policy ([`specs/web-interface.md`](specs/web-interface.md), [`DESIGN.md`](../../DESIGN.md)) |
| Deployment | Docker Compose (`db`, `api`; optional `local-llm` and `obs`) | Single host deployment; loopback-only ports behind Tailscale |
| License | MIT (application code) | Stock Phoenix (`obs` profile) carries Elastic License 2.0 outside the Arno image; direct dependencies are checked by hand against the permissive-only rule |

### Provider switch and inference accounting

Every model role (`CHAT`, `PARSE`, `VISION`, `STT`, and optional `CHAT_FALLBACK`) uses environment
variables for endpoint, model ID, and provider. The ChatGPT-plan provider and the provider Batch API
categorizer are removed; categorizing always runs through the `CHAT` role. Exact dated model IDs are
pinned rather than floating aliases, and model changes re-run the eval suite.

The design target is a frontier model; local models (such as Ollama) remain supported through the
same code path, with their capabilities measured by the eval suite. Cost accounting uses native
PydanticAI usage and `genai-prices`. A core `Hooks` capability writes one `llm_usage` row per model
response and enforces one shared daily cap; a model the catalog cannot price counts as $0
([`specs/cost-tracking.md`](specs/cost-tracking.md)).

---

## 4. Architecture

One FastAPI process serves the static web app, REST endpoints, chat stream, and background job loop
over one PostgreSQL database. Every client—web PWA, CLI, or a future mobile app—speaks to the same
API. For the full architecture, control model, and diagrams, see
[`architecture.md`](architecture.md).

```text
Browser / PWA ──┐                          [API process] ────────────> LLM endpoints (chat, vision, STT)
CLI (on host) ──┴── HTTPS over tailnet ──>   FastAPI: static web, auth, REST, chat stream
                                             PydanticAI agent -> plain-Python tools -> ledger / engine
                                             job loop: briefings, reminders, patterns, risk, recaps, replay
                                                   │  └── native OTLP spans ──> [Phoenix]
                                                   v
                                             [Postgres 18] <── pg_dump | restic (daily job in API) ──> off-site
```

### Architecture rules

Code must never violate these four rules:

1. **The model decides what to compute; code computes it.** Every figure the agent states must come
   from a tool return (`run_query`, `project`) or a `run_python` script output. Claims about
   current balances, dues, or goal progress require a live tool result from that turn. No code
   checks the reply afterward; the prompt and the evals carry this rule. Affordability verdicts
   come only from the tested engine.
2. **Lasting or wide writes ask first.** Statement text and payee notes are data, never
   instructions. Everyday undoable writes stay instant. A write that reaches wide or changes the
   account setup requires an explicit confirm card tap through PydanticAI's `ApprovalRequired`:
   `correct_movement` with `scope` other than `this`, and `update_setup` with `action="archive"`
   or `entity="settings"`.
3. **Tools are plain Python over the database, free of PydanticAI types.** The agent, REST API, and
   any future MCP server wrap the same functions in `src/arno/tools/` and `src/arno/ledger/`.
   Framework adapters belong strictly in `src/arno/agent/`.
4. **The database is the whole state.** Attachments, ledger entries, schedules, memories, jobs, and
   the model-usage log live in PostgreSQL so `pg_dump` produces a complete backup and export.

---

## 5. Build milestones

Layers L0 through L10 are built in dependency order and shipped through `v0.4.2`. Open verification,
owner-setup, and human ship gates are tracked in
[`completion-checklist.md`](completion-checklist.md).

### L0: Foundations

1. Repository layout, `pyproject.toml`, `src/arno/`, `web/`, and `compose.yaml`.
2. PostgreSQL 18 with numbered transactional SQL migrations (`schema_version`) and four isolated
   roles (`postgres`, `app`, `agent_ro`, `backup`), with `app` demoted to non-superuser.
3. Vendored `pgledger` core with `THIRD_PARTY_NOTICES.md` and integer-paise schema.
4. Static web shell and `/api/health` served behind `tailscale serve` under a strict content
   security policy.
5. Passkey authentication: first-run enrollment link and terminal QR code, WebAuthn sessions,
   device management in Settings, and host-CLI passkey reset.
6. CI pipeline: Ruff, Pyrefly, pytest against Postgres with model requests blocked,
   web lint/typecheck/build, and answer-contract tests.

### L1: Ledger

7. Accounts (bank, credit card with cycle and terms, cash, wallet, loan, asset) and read-only views.
8. Balanced double-entry movements through `pgledger`, `movement_legs` spine, unified reversal
   state (`movement_reversal_state`), splits, lending, and reimbursements.
9. Schedules (income, obligations, family transfers, card installments) with Indian holiday-aware
   recurrence and explicit reversal-aware payment allocations (`schedule_payments`).
10. Goals with fenced savings, reducible categories, and learned categorization rules.
11. Audit log with undo for every write and full data export (CSV, JSON excluding secrets, and
    `pg_dump`).
12. Encrypted daily backup inside the API (`pg_dump` piped to restic, off-site targets through
    `RESTIC_*`) and verified `arno restore`.
13. Deterministic synthetic demo persona (`arno demo seed`) for demos, tests, and eval templates.

### L2: Agent

14. Streaming web chat with persistent searchable threads, history trimming near the context
    limit, prompt caching, and detached turns that survive disconnects.
15. Environment-driven provider switching (cloud and local), a Postgres model-usage log with a
    shared daily spend cap, fallback model, and stock OpenTelemetry tracing to Phoenix.
16. Read tools (`run_query`, `project`, and `run_python` in an isolated sandbox), web search with
    source citations, and structured result cards.
17. Direct write tools with inline undo cards and confirm cards for wide or account-wide writes.
18. Agent-managed typed memories (`preference`, `fact`, `rule`) with inline undo cards and Settings
    management.
19. Agent-led setup conversation for accounts, income, obligations, goals, floor, and baselines.
20. Host CLI (`arno`) for migrations, setup, exports, restore, passkeys, and demo seeding.

### L3: Import and recaps

21. Real-statement parser spike confirming model-written parsers on real bank statements without
    OCR or third-party scrapers (Union Bank remains open pending statements).
22. Statement import pipeline: upload, verification checks, regression gate against stored
    statements, reconciliation, review screen, and atomic commit/undo. Extraction is being rebuilt
    ([`specs/import.md`](specs/import.md#extraction-status)).
23. 150-page PDF limit and server-computed daily, weekly, and monthly recaps (`tools/recaps.py`)
    shared across the web app and inbox.
24. Twelve-month real-statement backfill committed through the review screen with recomputed
    baselines (open in [`completion-checklist.md`](completion-checklist.md)).

### L4: Evals

25. `pydantic_evals` suite (85 cases on the demo persona, cloned database per trial, `pass^3`
    grading, Hindi/Hinglish/voice tags), run from Python through `evals.runner.run_eval`.

### L5: Projection and pattern engine

27. Integer-array scenario kernel (`project()`): 16,384 seeded scenarios over schedules, card
    cycles, fenced goals, high-confidence detected series, and whole-day resampled spend.
28. Critical-value affordability verdicts (`yes`, `yes-but`, `no`) with up to four minimal
    same-date tradeoff plans verified on the same scenarios.
31. Inline projection and verdict chart cards with source citations and a Timeline view.

### L6: Watch

32. Built-in job schedules, notification inbox, web push notifications, and 48-hour stale-backup
    alerts.
33. Weekly briefings, bill/EMI/card-due reminders (3-day lead), projection risk episodes, and
    user-scheduled read-only questions with a per-run request limit.
34. Calendar-rule recurring-charge detection (new, missed, and price-change proposals).

### L7: Capture

35. Push-to-talk voice notes through OpenAI-compatible STT (a cloud API or a self-hosted
    server you run) into chat.
36. Isolated vision extraction for receipts and UPI screenshots (instant undoable transaction with
    stored image) and Android PWA share-target support.

### L8: Ship

37. Fresh-machine off-site restore drill using only the recovery kit and `RESTIC_PASSWORD`.
38. Security pass: threat model, dependency and license review, strict CSP, and secret redaction.
39. Complete self-hosting guide, MIT license, and versioned GHCR container images.
40. Second-person installation from [`operations/self-hosting.md`](operations/self-hosting.md) alone.
41. Four weeks of daily use by the owner without falling back to mental math or a spreadsheet.

### L9: Decision tools and Tier-0 acceptance

42. Safe-to-spend headroom computed directly from the projection run and shown on **Ask** and
    projection cards.
43. Multi-event what-if scenarios (`engine/scenario.py`, up to 12 dated changes) with runway in days.
44. Recurring-charge price-change alerts at the first payment exceeding a 2% step.
45. Debt cost analysis: loan amortization with fees and APR, card payoff at stored or stated card
    rates, and offer comparison, computed by the agent in `run_python`.
46. Tier-0 acceptance additions: payment-evidence alert resolution, Settings **History** and
    **Delete everything**, XlsxWriter projection workbook export, `(kind, subject)` memory
    replacement, vision trace suppression, parse retry, and
    priority-ordered goal deferral.

### L10: Engine hardening

47. Trust-boundary and off-loop compute guards: block `--source db --publish`, cap projection/money
    bounds and dense-payee history, run `load_snapshot` in one `REPEATABLE READ` transaction, and
    offload `project()`, `afford()`, and workbook builds via `asyncio.to_thread` behind a semaphore.
48. Financial correctness fixes across projection, affordability, patterns, and money math
    (category-level attribution, shared card billing splits, overdue obligation clamping, strict
    partial-order plan dominance, RBI KFS Annex B APR vector, negative-amortization guard).
49. Backtest honesty and invariant hardening: per-group HAC centering, causal persona schedule
    visibility, `empirical_flat` baseline, and synchronous `check_engine()` invariants. Deferred
    modeling hypotheses and scale ceilings stay recorded as `shortcut:` comments in code until
    their real-ledger triggers fire.

---

## 6. Open questions

Only genuinely unresolved questions remain here. Settled design decisions live in their owning
specifications under [`specs/`](specs/) and [`operations/`](operations/).

- **Linux container shutdown (L0):** Do Compose restarts and the 30-second `stop_grace_period`
  behave cleanly on a Linux host (not only Windows Docker Desktop)?
- **Real-use agent traces (L2):** Across four weeks of real owner usage, does rule 1 hold without a post-reply
  check, what are real token counts and prompt-cache hit rates, how often do confirm cards appear,
  and should the 5 s / 15 s latency targets be revised against measured cloud-model latency?
- **Local chat models (L2/L4):** Which local Ollama models pass the eval suite on practical
  hardware?
- **Union Bank statements (L3):** What file format and layout does Union Bank export, and can the
  rebuilt extraction handle it cleanly?
- **Hinglish speech recognition (L7):** How does a self-hosted Whisper-class server compare against cloud STT (or
  Qwen3-ASR) on real owner Hinglish audio, and does `STT_PAYEE_CONTEXT=1` measurably improve
  merchant recognition?

---

## What "shipped" means

Arno is shipped when all four gates hold:

1. Every build milestone in §5 is complete.
2. The owner uses Arno daily for four weeks without falling back to mental math or a spreadsheet.
3. A second person installs Arno from [`operations/self-hosting.md`](operations/self-hosting.md)
   alone.
4. No code fix is pushed during the final week.

Passing automated tests, a local clean-room rehearsal, or a code audit does not satisfy the human
gates. Track open gates in [`completion-checklist.md`](completion-checklist.md).
