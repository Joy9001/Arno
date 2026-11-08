# Fin-Agent plan

This file is the core plan: what the product is, what it does, what it's built on, and the order
it gets built. _This file owns decisions._ Detail lives in the following docs:

| Doc | Holds |
|---|---|
| [`design.md`](design.md) | Data model, agent tool surface, projection and affordability engine, pattern detection |
| [`tools.md`](tools.md) | Each tool's description, input, output, and logic |
| [`platform.md`](platform.md) | API, web app, auth, notifications, scheduler, deployment, backups, security |
| [`harness.md`](harness.md) | Agent framework, harness capabilities and their triggers, eval discipline |
| [`import.md`](import.md) | Statements, photos, and voice: readers, saved parsers, the checks, reconciliation |
| [`parked.md`](parked.md) | Deliberately out of the product, each with a trigger to revisit |
| [`system-design.md`](system-design.md) | The ten-step agentic system design walk: where each step is recorded, what waits for which step, and review notes |
| [`completion-checklist.md`](completion-checklist.md) | What still stands between the built layers and "shipped", in dependency order |

**Rule for keeping this readable:** decisions and their one-line rationale go here. Anything
that needs a table, a schema, or three paragraphs of justification goes in a satellite doc
and gets a link. If this file grows by accretion, split it—that's what happened to the
[May 2026 docs](../archive/), which reached nine files and zero code.

If a satellite disagrees with this file, this file wins and the satellite is stale.

**Replan on October 7, 2026.** The MVP framing is dropped. This plan aims for the final product
from the start: every layer is built to its final shape, in dependency order, so nothing is
written twice.

**Complexity review on October 8, 2026.** Breadth that slows a solo build without making the product
better is parked with triggers: public-internet hosting, net worth from investment statements,
extra briefings, anomaly detection, editable panels. Depth goes where it can be measured and
shown: a probabilistic projection, a backtest of its accuracy, citations, and published eval
scores. Database decided: Postgres, plain SQL.

**Interface redesign on November 8, 2026.** The web app presents the agent as the product and the
money as what it works on. Six areas: Ask (the agent's home), Chats, Money, Activity, Recaps, and
Settings. A conversation has a Chat and a Flow view; big answers open a canvas. Money opens on a
timeline (ledger behind, projection ahead), Activity gains the inbox, and the context rail goes.
Every figure on these surfaces still comes from an API return. Layout and visual system:
[`web-interface-design.md`](web-interface-design.md).

---

## 1. The product

**One sentence:** a self-hosted, open-source finance agent that holds your whole financial
picture—accounts, cards, obligations, goals, people you owe—and answers any question about it,
including whether you can afford something and what it costs you.

"Can I afford this?" was the spark, not the scope. An agent that has the data should answer
anything the data supports. The following table answers the basic questions:

| Question | Answer |
|---|---|
| Who uses it? | One person for each instance. Others self-host their own copy |
| Where does it run? | Anywhere Docker runs—laptop, mini PC, VPS. Reached over a private Tailscale network, never the open internet |
| What's its main face? | Web app (PWA): the agent first, with read-only live panels around it. CLI for administration and development |
| Who is it for first? | Me. India-first; everything India-specific lives in one folder |
| What license does it use? | Open source, permissive (MIT or Apache-2.0). No GPL or AGPL in the shipped image |

**Done is:** it knows my complete financial situation, I can ask it anything about my money and
get a correct answer with real tradeoffs, it warns me before things go wrong, its forecasts are
measured against what actually happened, I've used it for four weeks without falling back to
mental math or a spreadsheet—and someone else has installed it from the docs alone.

---

## 2. Scope: The final product

The following sections define what the final product covers and what it leaves out.

### The principle

**"Answers any question" is a property of the tool surface, not a feature list.** A small set of
orthogonal tools over complete data composes into an unbounded question space. Scope is controlled
by the data model and the tool count, not by enumerating questions. Tools and schema:
[`design.md`](design.md).

### What it knows

The following table sets what the agent knows, by area:

| Area | Decision |
|---|---|
| Accounts | Multiple bank accounts, credit cards (with statement and due cycles), cash, wallets, loans, asset accounts |
| Currency | One base currency for each instance, configurable. Foreign purchases logged at the charged amount |
| Income | Fixed salary, variable or freelance (expected versus confirmed), bonuses, and one-offs |
| Obligations | Rent, EMIs, subscriptions, insurance, family transfers as a type of their own; card installment plans (no-cost EMI, "slice in 3") |
| Goals | Goals with deadlines. Target, date, fenced savings, contribution plan |
| Floor | A fixed amount the projection must never cross |
| People | Splits, lending, borrowing, reimbursements—what each person owes is a ledger balance |
| Investments | Asset accounts with hand-updated balances (EPF, FD, PPF, NPS, gold, funds). Net worth driven by investment statements is parked until I hold investments |
| Categories | Editable defaults, merchant→category rules learned from corrections. Rules always win; the LLM only categorizes unknown merchants. A correction about a payee or UPI ID ("ramesh@okaxis is my landlord") applies to that payee's past and future transactions |
| Memory | Agent-managed typed preferences. The agent saves lasting facts on its own and shows a "Remembered" card with undo; all visible and editable in settings. Memory never holds a number the ledger owns |
| Conversations | Threads you can see, reopen, or start at any time, kept and searchable. Every ledger write links to the message that caused it |

### How data gets in

Day to day: chat, voice, and photos. Statements are the monthly reconciliation that catches what
daily capture missed.

1. **Chat:** text, in any mix of English and Hinglish. Also how setup happens: a conversation
   walks through accounts, income, obligations, goals, and the floor.
2. **Voice notes:** push-to-talk, speech-to-text, then the same chat path.
3. **Photos:** a receipt or a UPI payment screenshot becomes one transaction, image kept as an
   attachment. On Android, the installed app is a share target, so a screenshot goes straight from
   the UPI app to Fin-Agent.
4. **Statement import:** PDF, spreadsheet, or text, from any bank; password-protected files; a
   12-month backfill at setup. The agent learns each new layout once. Reconciles against rows
   already logged. See [`import.md`](import.md).
5. **Web UI:** uploads, the import review screen, and settings. Panels are read-only; edits go
   through chat, where every write has undo.

**Balances update from statements and explicit statements of balance only.** The agent never
nags for a balance; it always states how old its numbers are.

**One record per real money movement, however it was captured.** A payment logged by chat, shared
as a photo, and later found on a statement is one record. Certain matches merge on import;
uncertain ones go to the import review screen and are never merged silently
([`import.md`](import.md)).

**Useful from the first conversation.** A spoken snapshot—balances, card dues, salary day—is
enough for answers and verdicts on day one. Statements and the 12-month backfill deepen it later.

### What it answers and does

- **Any question about the ledger:** read tools plus a read-only `run_query(sql)` escape hatch.
- **Modeling:** multistep what-ifs the read tools don't answer directly, such as prepaying a loan,
  interest saved, or how a monthly saving grows. The agent writes a short Python script that calls
  the read tools in a sandbox (Code Mode), and the script is shown with the answer.
- **Affordability:** yes, yes-but, or no, from a projection computed in code. Scheduled events are
  exact; day-to-day spending is simulated from my own history, so the verdict carries a
  probability ("94% chance you stay above the floor"). "Yes-but" may propose any of the
  following: wait until date X, pay by card or EMI, delay a goal, or cut a reducible category.
- **Citations:** every number in an answer links to the rows and schedules behind it.
- **Proactive:** bill, EMI, and card-due reminders; risk warnings when the projection turns; a
  weekly briefing; and detection of recurring charges and new or missed subscriptions.
  Delivered to an in-app inbox plus web push. These run as fixed code, so they work when the LLM
  is down.
- **Your own scheduled questions:** "every Sunday, tell me what I spent on food". The agent runs
  on your schedule, read-only, and answers into the inbox
  ([system design, step 2](system-design.md#agent-or-workflow-for-each-path)).
- **Writes:** chat writes, photos, and memory saves land immediately with an inline card and undo.
  Statement imports go through a review screen before commit. The one exception is a lasting or
  wide write—a memory save, a setup change, or a change to many rows—in a conversation that has
  read outside text. It asks for one tap first (rule 2 in §4).
- **Advice boundary:** facts and tradeoffs only. It never recommends what to do with investments
  or which product to buy.
- **Spending means consumption.** Moving money between your own accounts (including paying a card
  bill), lending, settlements, reimbursements, and savings or investment outflows such as SIPs and
  FDs never count as spend. A refund offsets the purchase it reverses.
- **Never moves money.** It reads, records, and models; paying is always done by you, in your own
  bank or UPI app.
- **Language:** replies in English by default; follows the user's language or mix when they write
  in it.

### Deliberately out

Chat-app integrations (Signal, Telegram, WhatsApp), native Android app, real-time voice
conversation, editable panels, SMS and email autocapture, balance nudges, receipt line items, net
worth from investment statements, broker APIs, tax engine, category budgets, multicurrency,
multiuser households, a projection for each account, extra briefings (daily, monthly, payday),
anomaly detection, public-internet access, encryption inside the database, a locale-pack
interface, arm64 images, hidden memory extraction, OCR for scanned statements, and a separate code
sandbox. Each has a trigger in
[`parked.md`](parked.md).

To decide whether something comes back in, ask: _does the agent answer questions about my money
incorrectly, or fail to warn me, without it?_ If not, it stays parked.

### Non-functional requirements

The following table states what each requirement demands:

| Requirement | Target |
|---|---|
| Scale | One user for each instance, and the data is small ([assumptions](system-design.md#scale-assumptions)) |
| Latency | Backend traces measure first streamed output and turn completion: targets are about 2 s to first output, about 5 s for an answer that needs one lookup, and about 15 s for a multistep what-if on a cloud model. Browser render timing is deferred. Local models may be slower |
| Portability | `docker compose up` on any x86-64 Linux host, Docker Desktop, or a VPS |
| Durability | Do not lose the ledger. A daily `pg_dump` shipped off-site encrypted, from before the first real money goes in, with a tested restore |
| Security | Private network only (Tailscale). Passkey login. Host disk encryption at rest, encrypted off-site backups, secrets only in environment variables |
| Privacy | Inference provider is switchable for each role; nothing else leaves the box except encrypted backups and contentless push pings |
| Honesty | Tools distinguish _zero_ from _unknown_, and every number carries its age and source |
| Measured | Forecast accuracy (backtest), engine accuracy (HackerRank samples), and agent reliability (`pass^k` for each model) are measured and published, not claimed. Targets: [success measures](system-design.md#success-measures) |
| Autonomy | The agent never starts a write on its own except saving a memory, and never moves money ([autonomy for each kind of action](system-design.md#autonomy-for-each-kind-of-action)) |

Honesty is a real requirement, not a nicety: a general query surface over incomplete data
otherwise answers confidently with no basis.

### Constraints

- **One developer,** 15-20 hours a week.
- **India-first:** INR, UPI, Indian statement formats, Hinglish, and Indian holidays.
- **No bank APIs.** India's Account Aggregator network serves only institutions regulated by
  RBI, SEBI, IRDAI, or PFRDA ([Sahamati](https://sahamati.org.in/financial-information-user-fiu)),
  so data comes from statements and from the user.
- **Permissive licenses only** (§1).
- **Modest hardware:** a laptop, mini PC, or VPS. LLM cost isn't a constraint, but it's metered.

---

## 3. Stack

The following sections cover build-versus-buy decisions, the chosen stack, and the provider switch.

### Build versus buy

The playbook's rule: _buy, unless the piece is core to the product_. The following table applies
it to each piece:

| Piece | Decision | Why |
|---|---|---|
| Agent loop, tool calling, UI streaming | Buy: PydanticAI | Harness library, Vercel-AI-protocol adapter, provider switching. [`harness.md`](harness.md) |
| Reading statement files | Buy: `pypdfium2`, `python-calamine`, and the like | One reader for each kind of file. [`import.md`](import.md) |
| Parsing a statement layout | Build once: the agent writes a parser in Monty, saved for that layout | No parser for each bank. Tested on synthetic files; the L3 spike is the real test. [System design, step 5](system-design.md#imports-the-agent-writes-the-parser) |
| Speech-to-text | Buy: any OpenAI-compatible STT endpoint | `speaches` (faster-whisper) locally, or a cloud API |
| Photo extraction | Buy: any OpenAI-compatible vision model | The `VISION_*` role, local or cloud |
| Passkeys, web push | Buy: `py_webauthn`, `pywebpush` | Fin-Agent doesn't invent its own security code |
| Private access + HTTPS | Buy: Tailscale | `tailscale serve` gives HTTPS on the tailnet; nothing is exposed to the internet |
| Off-site backup | Buy: restic | Encrypts, dedupes, any S3-compatible target, zero app code |
| Agent evals | Buy: `pydantic_evals` | Ships with PydanticAI, and runs the agent unchanged. [System design, step 9](system-design.md#agent-evals) |
| Observability | Buy: Phoenix and the standard Phoenix OpenTelemetry SDK | Native Pydantic AI agent, model, and tool traces export directly to Phoenix; standard Python/Uvicorn logs stay on Docker stdout. No Collector, Loki, Prometheus, or Grafana. See the [operator guide](observability.md) |
| Double-entry ledger core | Buy: pgledger, vendored | MIT, pure Postgres SQL, built-in idempotency. Credited in `THIRD_PARTY_NOTICES.md` and the README. Fit check in [system design, step 4](system-design.md#step-4-ground-truth) |
| Mapping extracted rows → the Fin-Agent schema | Build | No library knows the Fin-Agent data model |
| Projection and affordability engine | Build | This _is_ the product |
| Pattern detection | Build | Small, rule-based, needs Fin-Agent's schedules and categories |

Anything not listed: search PyPI or npm first, and write code only if nothing fits.

### Choices

The following table records the choice for each layer:

| Layer | Choice | Why |
|---|---|---|
| Backend language | Python 3.14 | Already set up; the whole agent ecosystem is here |
| Agent | PydanticAI + `pydantic-ai-harness` | [`harness.md`](harness.md) |
| API | FastAPI | Built on Pydantic; OpenAPI is the contract every client shares. Also serves the static web build |
| LLM | Switchable for each role | Chat, vision, and STT each take `*_BASE_URL`, `*_API_KEY`, and `*_MODEL`. Any OpenAI-compatible endpoint, local (Ollama) or cloud. Cost is not a constraint; Spend Limits still meter it |
| Database | PostgreSQL 18, pinned | Chosen Oct 8, 2026, over SQLite: a read-only role and statement timeout enforced by the database for `run_query`, real date and numeric types, `pgvector` if semantic memory returns, and the industry default. Costs one container and an occasional major-version upgrade |
| Data access | Plain SQL through asyncpg, Pydantic models at the boundary | No ORM. The agent writes raw SQL against this schema, so the schema stays readable SQL. One Postgres driver across the app, the tools, and the harness's spend store |
| Migrations | Numbered `.sql` files + a `schema_version` table | No migration framework |
| Background jobs | A `jobs` table and one loop in the API process | One process needs no broker or job library, and the jobs stay in every dump. [System design, step 8](system-design.md#jobs-a-plain-table-and-one-loop) |
| Web app | Vite and React, Vercel AI SDK 7 `useChat`, shadcn/ui's chat patterns, Streamdown | A static build means no Node server in production, and it runs under a strict content security policy, where a Next.js static export came up blank. [System design, step 10](system-design.md#web-stack) |
| Distribution | Docker Compose: API, Postgres, backup sidecar, and Phoenix; optional Ollama and speaches | The initial target is Windows with Docker Desktop. Phoenix publishes on localhost with auth; native Pydantic AI spans export over OTLP/HTTP. The agent container retains its separate filesystem boundary |
| License | Application code: MIT | Runtime dependencies and standalone service images have their own licenses, recorded in `THIRD_PARTY_NOTICES.md`. Phoenix is included in normal startup and can be disabled with the standard `OTEL_SDK_DISABLED` |

### Provider switch

Each role uses one code path for local and cloud:

```
CHAT_BASE_URL=http://ollama:11434/v1      # or https://api.<provider>.com/v1
CHAT_MODEL=qwen3:4b                       # or a cloud model id
VISION_BASE_URL=...  VISION_MODEL=...     # photos, statement pages with no text
STT_BASE_URL=http://speaches:8000/v1      # or a cloud transcription endpoint
CHAT_FALLBACK_BASE_URL=...  CHAT_FALLBACK_MODEL=...   # optional backup when the main endpoint fails
CHAT_FALLBACK_CONTEXT_WINDOW=...                      # the window the backup's server really uses
```

No reversible PII-scrubbing layer: identifiers the product doesn't use are dropped at import
instead ([system design, step 7](system-design.md#identifiers-at-import-not-a-scrubbing-layer)).
No routing by content inside a role, but a role may name a backup endpoint that takes over when
the main one fails, and replies the backup produced carry a badge. Which role runs where is the
self-hoster's choice.

**The design target is a frontier model.** Tool shapes, Code Mode, and prompts are chosen for
what a capable model can do, so a strong model is never held to a basic level. Local models stay
supported through this switch, and the eval scorecard records what each one handles. A small
model's limits go in the scorecard; they are never designed into the product.

Model IDs are pinned to exact, dated versions, never floating aliases, and any model change re-runs
the eval scorecard. The categorizer agent and briefing prose use the chat role. The parser subagent
uses an optional `PARSE` role that falls back to chat, and an optional `BATCH` role takes big
categorizing backfills ([system design, step 3](system-design.md#models)).

---

## 4. Architecture

The following diagram shows the processes and how requests flow between them:

```
 Browser / PWA ──┐                          [API process] ────────────> LLM endpoints (chat, vision, STT)
 (future Android)├── HTTPS over tailnet ──>   FastAPI: static web app, auth,      local or cloud
 CLI (on host) ──┘                            REST, chat (Vercel AI stream)
                                              PydanticAI agent ─> tools ─> ledger / engine
                                              job loop: parsing, briefing, reminders, patterns, risk
                                                    │  └──native spans──> [Phoenix]
                                                    v
                                              [Postgres] ──pg_dump──> [backup sidecar] ──restic──> off-site
```

One API process, one Postgres database. The agent is a backend service; every interface—web, CLI,
a future Android app or chat-app bridge—is a client of the same API. Detail:
[`platform.md`](platform.md).

### The rules

1. **The model decides what to compute; code computes it.** The model reasons freely: it decides
   what to look at, models scenarios, and explains tradeoffs. Every number it states comes from a
   tool return—the ledger, the engine, `run_query`, or a script it ran in Code Mode—and never from
   its own head. A check after each reply notes any amount it can't trace. Affordability verdicts come
   only from the tested engine.
2. **Untrusted text never makes a lasting change unseen.** Statements are parsed in a run with no
   write or ledger tools, and photos by an isolated, tool-less model call into a typed schema.
   Fields that still carry other people's words—payee names, UPI notes—reach the agent marked as
   data. Writes that are visible and undoable stay instant. Writes that last or reach wide—memory,
   setup, many rows—need a confirming tap in any conversation that has read such text. Voice
   transcripts are the user's own words and take the normal chat path.
3. **Tools are plain Python over the database, free of PydanticAI types.** The agent, the REST API,
   and any future MCP server wrap the same functions.
4. **The database is the whole state.** Photos live in it; a dump from `pg_dump` is a complete
   backup and export.

---

## 5. Build order

Every layer is built to its final shape—no throwaway versions. Order is by dependency.
_Real money goes into it from L2 onward_, so backups land in L1, before it holds anything worth
losing. Settings sections land with the layer that needs them. Milestones are binary. No dates.

### L0: Foundations

1. Repository reset: `pyproject.toml` dependencies match this plan; `src/fin_agent/`, `web/`,
   `compose.yaml`.
2. Postgres in compose for development and production; numbered SQL migrations with
   `schema_version`; the `app` and read-only `agent_ro` roles.
3. Schema v1: pgledger vendored unmodified at a pinned commit, with its licenses,
   `THIRD_PARTY_NOTICES.md`, and a "Built on" credit in the README; then every table in
   [`design.md`](design.md), money as whole paise.
4. The `docker compose up` command serves the web shell and an API health check, reached over
   HTTPS through `tailscale serve` on the host. The web shell ships with a strict content security
   policy, which blocks inline scripts and images from other origins.
5. Auth: first-run setup through an enrollment link and terminal QR code, passkey login, sessions,
   adding a device from Settings, passkey reset from the host CLI
   ([platform](platform.md#enrollment-links)).
6. CI: ruff, pytest against a Postgres service container with model requests blocked, web lint and
   build.

### L1: Ledger

7. Accounts—bank, card (cycle, limit), cash, wallet, loan, asset—through the API and read-only
   panels.
8. Movements: record as balanced transfers through pgledger, correct by reversal, edit labels;
   splits, lending, and reimbursements through payees' ledger accounts.
9. Schedules: income (fixed, variable, one-off), obligations, family transfers, installment
   plans—recurrence rules, holiday-aware "last working day."
   Payment status comes from explicit allocations of recorded ledger movements to individual
   occurrences, with partial payments, reversal-aware evidence, and undo. Matching never records
   another transaction or infers payment from a date or confirmation flag. Future projections
   reserve only the amount still expected. Generated card-due forecasts remain separate from
   statement-bill settlement; see [schedule payment evidence](design.md#schedule-payment-evidence).
10. Goals with fenced savings; categories with a reducible flag; learned categorization rules.
11. Audit log with undo for every write; full export (CSV, JSON, and `pg_dump`).
12. Backup sidecar: `pg_dump` shipped off-site by restic whenever the last success is over 24 hours
    old; a restore tested locally.
13. Demo persona: a seeded synthetic ledger—the demo instance, the test fixtures, and the
    template database every eval trial clones.

### L2: Agent (dogfooding starts)

14. Web chat streams from the PydanticAI agent; threads listed, stored, and full-text searchable.
    The server holds each thread's history, with a pinned financial snapshot, a per-turn reminder,
    compaction, and prompt caching. Instructions tuned by trial and error
    ([step 6](system-design.md#step-6-context-and-memory)). A turn outlives its connection: you can
    leave, reattach, or stop it, and every ending saves both records
    ([step 8](system-design.md#every-ending-saves-the-same-two-records)).
15. Same build runs against Ollama and a cloud endpoint by environment variables alone; Spend
    Limits with counters in Postgres; a backup model and timeouts. Phoenix starts with Compose and
    receives native Pydantic AI traces; see the [operator guide](observability.md). Design probes
    inform tests but do not replace product-level acceptance checks.
16. Read tools, including `run_query` on the `agent_ro` role with a statement timeout, the tested
    `amortize` and `compound` functions, and `show_chart`; zero versus unknown, data age, and source
    IDs (for citations) in every return. Code Mode over the read tools, so the agent can run
    multistep modeling in the sandbox; write tools stay direct calls. Fin-Agent emits cards from
    the `run_code` return. A check after each reply notes any amount it can't trace, and any reply
    can be flagged. Specs in [`tools.md`](tools.md).
17. Write tools with inline write-show-undo cards; corrections learn rules. Outside text is
    marked in tool returns, and lasting or wide writes ask first in a conversation that has read
    it ([`design.md`](design.md#untrusted-text-and-write-tiers)).
18. Memory: the agent saves preferences unprompted, with a "Remembered" card and undo; visible
    in settings, where each can be turned off or deleted.
19. Setup conversation lands accounts, income, obligations, goals, floor, and estimated baselines.
20. CLI: chat plus administration (setup, migrate, export, passkey reset).

### L3: Import

21. **Spike:** a fresh model writes a parser for my real SBI, Union Bank, and Slice statements in
    Monty. Output is a decision for each bank. Protocol in [`import.md`](import.md).
22. Pipeline: upload → the main agent hands it to the parser subagent in the background → replay,
    repair, or write the bank's parser → the gate → dedupe and reconcile against logged rows →
    review screen → commit. The subagent runs under one `DelegationTasks` owner; the `jobs` table
    keeps the scheduled work. Redesigned November 7, 2026 ([`import.md`](import.md#the-pipeline)).
23. The parser subagent: its instructions, host functions, `ask_parent`, and `Planning`; parser
    versions saved after commit, statement files kept, and every new version replayed on them.
24. A 12-month backfill done for my accounts; baselines recomputed from real rows.

### L4: Evals (gate: engine work starts when this exists)

25. At least 50 agent-eval tasks in `pydantic_evals`, shaped by real usage and rewritten on the demo
    persona: a balance of answer and refuse cases, Hinglish, modeling questions that need a script,
    50 prompt-injection notes, a fresh database for each trial cloned from the demo template,
    `pass^3`, run against every configured model. Shape in [`harness.md`](harness.md#tests-and-evals).
26. Model scorecard: `pass^3` by category, fooled rate, number flags, and median cost and latency for
    each configured model, cloud and local, published in the README. `fin-agent eval --model X` runs
    it for a self-hoster.

### L5: Engine

27. `project()`: day-by-day curve over income, obligations, installments, card cycles, fenced
    goals, and simulated variable spend, against the floor (median, band, and floor
    probability). Seeded; unit and property tests.
28. Affordability: verdict plus the four tradeoffs, all computed in code.
29. **Backtest:** the `project()` function run as of past dates over the backfill; median 30-day
    error and band calibration published.
30. **HackerRank "Buy or Wait":** the engine scored on the 25 labeled samples, score published
    (the data stays local).
31. Projection and verdict render as inline chart cards with citations, plus a projection panel.

### L6: Watch

32. Job schedules, notification inbox, web push from the installed PWA; an alert when backups stop.
33. Weekly briefing; bill, EMI, and card-due reminders; risk warnings when the projection turns.
    Your own scheduled questions, run by the agent read-only, with a spend limit for each run.
34. Recurring-charge detection—new and missed subscriptions—presented as proposals, never
    silent writes.

### L7: Capture

35. Voice notes: push-to-talk in the web app → STT → chat. Hinglish eval cases pass.
36. Photos: receipt or UPI screenshot → isolated vision extraction → one transaction, logged at
    once with an undo card that shows the image beside the extracted fields; Android share target.

### L8: Ship

37. Restore drill on a fresh machine: given the backup and the restic password, the ledger comes
    back.
38. Security pass: threat model, dependency audit, security headers.
39. Docs: install (Docker + Tailscale), configure LLMs. License file, versioned image.
40. Someone else installs it from the docs alone.
41. Four weeks of daily use without falling back to mental math or a spreadsheet.

**L8 status, November 3, 2026.** Milestones 37-39 are built; 37 is not yet tested the way it is
written. 37 is `fin-agent restore`: the sidecar writes a per-table manifest with every dump, and the
command restores the newest restic snapshot into a fresh database, recomputes the manifest, and
checks the ledger's balances. `tests/test_l8_ship.py` runs the round trip through a real restic
repository, and CI runs it green (run 41627342626). That drill ran on this build host, not on a fresh
machine; the fresh-machine restore, using only the restic password, stays open in
[`completion-checklist.md`](completion-checklist.md) group B.
38 is the refreshed threat model in [`platform.md`](platform.md#security), the exact CSP and
headers, the fresh-passkey wall on export and passkey changes, log sanitization, the `agent_ro` and
Phoenix isolation, and the Python + web license audit with `pip-audit`/`pnpm audit` in CI. 39 is
**MIT** ([`LICENSE`](../../LICENSE)), the versioned `ghcr.io/joy9001/fin-agent` and
`fin-agent-backup` images, the release workflow, a pre-migration `pg_dump`, 10 MB log rotation, and
[`self-hosting.md`](self-hosting.md). The `v0.1.0` tag is published: its release workflow pushed
`0.1.0` and `v0.1.0` for both `ghcr.io/joy9001/fin-agent` and `fin-agent-backup` (run 41628402245).

**Observability status, November 5, 2026.** The production stack uses Pydantic AI's native
OpenTelemetry instrumentation and the standard Phoenix SDK, exporting agent, model, and tool spans
directly to Phoenix. Standard Python/Uvicorn logs stay on Docker stdout. The custom Collector,
Loki, Prometheus, Grafana, dashboards, alerts, metric helpers, manual spans, and log bridge were
removed from source, Compose, and tests. Existing Phoenix and Postgres volumes are untouched.

Milestones 40 and 41 are **not** done, and no code change can make them done. 40 needs a second
person to install from the docs alone; 41 needs four weeks of the owner's daily use without a
spreadsheet or mental-math fallback. A clean-room Compose check ran on this build host on
November 3, 2026, from the documented steps and a separate project name, ports, volumes, and env
file: the images built, `/api/health` returned 200 with schema 11, the first-run enrollment link
printed, the backup sidecar produced a restic snapshot with its manifest, and
`fin-agent restore` brought it back with `required_tables`, `schema_version`,
`ledger_transfers_balance`, `account_balances_match_entries`, and `manifest_matches` all `ok`. That
is not milestone 40: it was the same owner, the same Windows/Docker Desktop host, and no
Tailscale, phone, or passkey. The Linux Compose grace period, `tailscale serve`, and a real
second installer remain unexercised.

**Estimate:** at 15-20 hours a week, roughly 7-9 months. L3 (import) is the one genuinely
unpredictable layer, which is why it opens with a spike. If a layer runs past twice its share,
something has crept in—check it against [`parked.md`](parked.md).

---

## 6. Open questions

Answer each when its layer arrives, not before.

- **L0:** do Compose restarts and the 30-second grace period behave on Linux?
- **L2:** is the [number check](system-design.md#the-number-check) noisy on real replies? If so, its
  note comes off and only the trace keeps it. If it's quiet, numbers become tappable to their source.
  Which local chat models handle which tasks? The scorecard records it. What are the real token
  counts, and is a 1-hour prompt cache worth it? How often do confirm cards appear and get tapped?
  Traces answer all three. Are the model timeouts right for a real provider, and do the latency
  targets hold?
- **L3:** settled in the Milestone 21 spike and recorded here.
  - **What does Slice export, and in what format?** Unlocked monthly PDFs, one for each cycle that
    runs from the 25th to the 24th.
  - **Can a fresh model write parsers for real statements in Monty, or is a separate sandbox
    needed?** It can, so no separate sandbox is needed. A fresh model wrote parsers that verify on
    the real SBI and Slice statements.
  - **Is `xfina` or OCR worth adding?** No. `xfina` 0.10.0 recognized neither SBI file and has no
    Slice support, and every real file had a text layer or was a spreadsheet.
  - **Union Bank.** Still open: no statement was provided, so its format and parser decision wait on
    one. Counts and quirks per bank are in [`import.md`](import.md#the-spike-milestone-21-in-the-plan).
  - **Do Slice's exports carry every repayment?** No. The PDFs omit several repayments that the
    app shows, and one is a day off. The importer treats an own-account move as a transfer only
    after the user confirms it on the review screen, and a statement whose printed closing
    disagrees with those confirmed moves keeps the difference as a visible adjustment rather than
    silently counting the payment as spending.
- **L5:** settled in code and recorded here.
  - **Resample single days or whole weeks?** Single days, with separate weekday and weekend pools.
    Weekend spend clusters survive without resampling whole weeks, and one bad week cannot repeat
    as a block. The engine reports the method in `data_quality`.
  - **What floor probability makes a "yes"—90% or 95%?** 90%. It matches the published P10-P90
    band: a `yes` means the tenth-percentile path still clears the floor. 95% would make the
    verdict and the band describe different things.
  - **Card share of variable spend—learned or set?** Learned for each category from the last 90
    days of spending on that card. With too little card history the share is zero (cash) and
    `data_quality` says so; there is no per-account setting to keep in sync.
- **L6:** settled in code and recorded here.
  - **Notification and briefing times.** The notification time is 08:00 Asia/Kolkata and the
    weekly briefing is Sunday at that time. Both are settings; nightly risk, pattern, baseline,
    and backup checks run at 02:00-03:00 India time so they don't compete with a reminder. A
    schedule that was missed while the machine slept fires once, keyed by its period.
  - **Reminder lead days.** 3 days before a bill, EMI, or card due date. A reminder is keyed by
    the source and due date, so it appears once inside the lead window.
  - **Recurring-charge thresholds.** At least 3 occurrences, gaps within 3 days of 7, 30, or 365
    days, and amounts within 10% of the median. An expected occurrence 3 or more days late with no
    matching payment is a missed charge. Detection runs nightly and after every import, and only
    proposes; it never writes a schedule.
  - **Risk episodes.** The engine's own 90% floor threshold decides a warning. One warning goes
    out per episode when the floor probability falls below it or the first crossing moves earlier;
    the episode re-arms after the probability recovers and the crossing no longer moves earlier.
    The check runs nightly and after every ledger or setup write.
  - **Backup alert.** 48 hours after the last recorded successful sidecar backup, once per outage;
    a new success starts the next episode.
  - **Scheduled questions.** Read-only, with the pinned financial snapshot and CodeMode read tools
    only. Each run has 20 model requests and a $1 default ceiling (`spend_limit_usd`), against the
    shared $20 daily ceiling. The answer goes to the Inbox.
- **L7:** the cloud-versus-local Hinglish comparison is **not measured on this host**. No `STT_*`
  endpoint and no Hinglish audio corpus are configured, so the Hinglish and voice-transcript eval
  cases test the agent downstream of a transcript, not the speech model. The code path is one for
  both. Locally, `speaches` 0.9.0-rc.3-cpu (Whisper through faster-whisper) is the `local-stt` Compose
  profile. **Qwen3-ASR 0.6B and 1.7B are Apache-2.0** (checked on Hugging Face, November 3, 2026),
  and vLLM serves them through the same OpenAI-compatible `/v1/audio/transcriptions` API,
  including its prompt field, so Qwen3-ASR is a viable local alternative to Whisper. The
  payee-name context prompt is implemented and on by default (`STT_PAYEE_CONTEXT=1`); whether it
  improves recognition is untested, so it is an option rather than a claim. A cloud STT endpoint
  receives those payee names too; setting `STT_PAYEE_CONTEXT=0` keeps them local.
- **L8:** **MIT.** It lets anyone use, modify, and redistribute Fin-Agent freely, matches the
  vendored pgledger license, and adds no patent or notice machinery a one-person project has to
  maintain. The permissive-license audit in CI keeps the dependency side honest. Apache-2.0's
  patent grant was the only real argument for it, and nothing in the stack or the product turns on
  patents. Recorded November 3, 2026.

---

## What "shipped" means

All of the following must be true:

- Every milestone in §5 done
- Used daily for four weeks with no spreadsheet or mental-math fallback
- Installed by someone else from the docs alone
- No fix pushed in the last week

Then it's shipped. Parked items get promoted from a working, used system—not from a document.

Milestones 40 and 41 are the human gates in that list. They are recorded as open until a second
person has installed the instance from [`self-hosting.md`](self-hosting.md) alone and the owner
has used it daily for four weeks. Passing tests, a clean-room install on the build host, or a
complete code audit is not a substitute for either.

