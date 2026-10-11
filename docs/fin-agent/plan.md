# Fin-Agent plan

The core plan: what the product is, what it does, what it's built on, and the order it gets built.
**This file owns decisions.** Detail lives in the following docs:

| Doc | Holds |
|---|---|
| [`design.md`](design.md) | Data model, agent tool surface, projection and affordability engine, pattern detection |
| [`platform.md`](platform.md) | API, web app, auth, notifications, scheduler, deployment, backups, security |
| [`harness.md`](harness.md) | Agent framework, harness capabilities and their triggers, eval discipline |
| [`import.md`](import.md) | Statements, photos, and voice: parsers, the invariant, reconciliation |
| [`parked.md`](parked.md) | Deliberately out of the product, each with a trigger to revisit |

**Rule for keeping this readable:** decisions and their one-line rationale go here. Anything
that needs a table, a schema, or three paragraphs of justification goes in a satellite doc
and gets a link. If this file grows by accretion, split it—that's what happened to the
[May 2026 docs](../archive/), which reached nine files and zero code.

If a satellite disagrees with this file, this file wins and the satellite is stale.

**2026-10-07 re-plan.** The MVP framing is dropped. This plan aims for the final product from the
start: every layer is built to its final shape, in dependency order, so nothing is written twice.

**2026-10-08 complexity review.** Breadth that slows a solo build without making the product
better is parked with triggers: public-internet hosting, net worth from investment statements,
extra briefings, anomaly detection, editable panels. Depth goes where it can be measured and
shown: a probabilistic projection, a backtest of its accuracy, citations, and published eval
scores. Database decided: Postgres, plain SQL.

---

## 1. The product

**One sentence:** A self-hosted, open-source finance agent that holds your whole financial
picture—accounts, cards, obligations, goals, people you owe—and answers any question about it,
including whether you can afford something and what it costs you.

"Can I afford this?" was the spark, not the scope. An agent that has the data should answer
anything the data supports. The following table answers the basic questions:

| Question | Answer |
|---|---|
| Who uses it? | **One person per instance.** Others self-host their own copy |
| Where does it run? | Anywhere Docker runs—laptop, mini PC, VPS. Reached over a **private Tailscale network**, never the open internet |
| Main face | **Web app (PWA):** chat first, with read-only live panels. CLI for admin and dev |
| Who is it for first? | Me. India-first; everything India-specific lives in one folder |
| License | Open source, permissive (MIT or Apache-2.0). No GPL or AGPL in the shipped image |

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
| Currency | One base currency per instance, configurable. Foreign spends logged at the charged amount |
| Income | Fixed salary, variable or freelance (expected versus confirmed), bonuses, and one-offs |
| Obligations | Rent, EMIs, subscriptions, insurance, **family transfers** as a type of their own; card installment plans (no-cost EMI, "slice in 3") |
| Goals | **Goals with deadlines.** Target, date, fenced savings, contribution plan |
| Floor | **A fixed amount** the projection must never cross |
| People | **Splits, lending, borrowing, reimbursements**—one counterparty ledger |
| Investments | **Asset accounts with hand-updated balances** (EPF, FD, PPF, NPS, gold, funds). Net worth driven by investment statements is parked until I hold investments |
| Categories | Editable defaults, **merchant→category rules learned from corrections**. Rules always win; the LLM only categorizes unknown merchants |
| Memory | **Agent-managed typed preferences.** The agent saves lasting facts on its own and shows a "Remembered" card with undo; all visible and editable in settings. Memory never holds a number the ledger owns |
| Conversations | **Kept and searchable.** Every ledger write links to the message that caused it |

### How data gets in

Day to day: chat, voice, and photos. Statements are the monthly reconciliation that catches what
daily capture missed.

1. **Chat:** text, in any mix of English and Hinglish. Also how setup happens: a conversation
   walks through accounts, income, obligations, goals, and the floor.
2. **Voice notes:** push-to-talk, speech-to-text, then the same chat path.
3. **Photos:** a receipt or a UPI payment screenshot becomes one transaction, image kept as an
   attachment. On Android the installed app is a share target, so a screenshot goes straight from
   the UPI app to Fin-Agent.
4. **Statement import:** PDF, CSV, or XLS; password-protected PDFs; 12-month backfill at setup.
   Reconciles against rows already logged. See [`import.md`](import.md).
5. **Web UI:** uploads, the import review screen, and settings. Panels are read-only; edits go
   through chat, where every write has undo.

**Balances update from statements and explicit statements of balance only.** The agent never
nags for a balance; it always states how old its numbers are.

### What it answers and does

- **Any question about the ledger:** read tools plus a read-only `run_query(sql)` escape hatch.
- **Affordability:** yes, yes-but, or no, from a projection computed in code. Scheduled events are
  exact; day-to-day spending is simulated from my own history, so the verdict carries a
  probability ("94% chance you stay above the floor"). "Yes-but" may propose any of: **wait until
  date X, pay by card or EMI, delay a goal, cut a reducible category**.
- **Citations:** every number in an answer links to the rows and schedules behind it.
- **Proactive:** bill, EMI, and card-due reminders; risk warnings when the projection turns; a
  **weekly briefing**; and detection of recurring charges and new or missed subscriptions.
  Delivered to an in-app inbox plus **web push**.
- **Writes:** chat writes and memory saves land immediately with an inline card and **undo**.
  Imports and photos go through a review step before commit.
- **Advice boundary: facts and tradeoffs only.** It never recommends what to do with investments
  or which product to buy.
- **Language:** mirrors the user's language mix.

### Deliberately out

Chat-app integrations (Signal, Telegram, WhatsApp), native Android app, real-time voice
conversation, SMS and email auto-capture, net worth from investment statements, extra briefings
(daily, monthly, payday), anomaly detection, editable panels, public-internet access, tax engine,
category budgets, multi-currency, multi-user households, broker APIs, Code Mode, hidden memory
extraction. Each has a trigger in [`parked.md`](parked.md).

Test for anything wanting in: *does the agent answer questions about my money incorrectly, or
fail to warn me, without it?* If not, it stays parked.

### Non-functional requirements

The following table states what each requirement demands:

| Requirement | Target |
|---|---|
| Scale | One user per instance |
| Latency | Under ~5 s per chat reply on a cloud model; local models are allowed to be slower |
| Portability | `docker compose up` on any x86-64 Linux host, Docker Desktop, or a VPS |
| Durability | **Do not lose the ledger.** Nightly `pg_dump` shipped off-site encrypted, from before the first real money goes in, with a tested restore |
| Security | **Private network only** (Tailscale). Passkey login. Host disk encryption at rest, encrypted off-site backups, secrets only in env |
| Privacy | Inference provider is switchable per role; nothing else leaves the box except encrypted backups and contentless push pings |
| Honesty | Tools distinguish *zero* from *unknown*, and every number carries its age and source |
| Measured | Forecast accuracy (backtest), engine accuracy (HackerRank samples), and agent reliability (`pass^k` per model) are measured and published, not claimed |

Honesty is a real requirement, not a nicety: a general query surface over incomplete data
otherwise answers confidently with no basis.

---

## 3. Stack

The following sections cover build-versus-buy decisions, the chosen stack, and the provider switch.

### Build versus buy

The playbook's rule: **buy, unless the piece is core to the product.** The following table applies
it to each piece:

| Piece | Decision | Why |
|---|---|---|
| Agent loop, tool calling, UI streaming | **Buy:** PydanticAI | Harness library, Vercel-AI-protocol adapter, provider switching. [`harness.md`](harness.md) |
| Statement parsing | **Buy where it exists**, generic fallback otherwise | `xfina`, `bankstatementparser`. [`import.md`](import.md) |
| Speech-to-text | **Buy:** any OpenAI-compatible STT endpoint | `speaches` (faster-whisper) locally, or a cloud API |
| Photo extraction | **Buy:** any OpenAI-compatible vision model | The `VISION_*` role, local or cloud |
| Passkeys, web push | **Buy:** `py_webauthn`, `pywebpush` | Security code is not ours to invent |
| Private access + HTTPS | **Buy:** Tailscale | `tailscale serve` gives HTTPS on the tailnet; nothing is exposed to the internet |
| Off-site backup | **Buy:** restic | Encrypts, dedupes, any S3-compatible target, zero app code |
| Mapping extracted rows → our schema | **Build** | No library knows our data model |
| Projection and affordability engine | **Build** | This *is* the product |
| Pattern detection | **Build** | Small, rule-based, needs our schedules and categories |

Anything not listed: search PyPI or npm first, write code only if nothing fits.

### Choices

The following table records the choice for each layer:

| Layer | Choice | Why |
|---|---|---|
| Backend language | Python 3.14 | Already set up; the whole agent ecosystem is here |
| Agent | PydanticAI + `pydantic-ai-harness` | [`harness.md`](harness.md) |
| API | FastAPI | Built on Pydantic; OpenAPI is the contract every client shares. Also serves the static web build |
| LLM | **Switchable, per role** | Chat, vision, and STT each take `*_BASE_URL`, `*_API_KEY`, and `*_MODEL`. Any OpenAI-compatible endpoint, local (Ollama) or cloud. Cost is not a constraint; Spend Limits still meter it |
| Database | **PostgreSQL**, one pinned major version | Chosen 2026-10-08 over SQLite: a read-only role and statement timeout enforced by the database for `run_query`, real date and numeric types, `pgvector` if semantic memory returns, and the industry default. Costs one container and an occasional major-version upgrade |
| Data access | **Plain SQL through psycopg 3**, Pydantic models at the boundary | No ORM. The agent writes raw SQL against this schema, so the schema stays readable SQL |
| Migrations | Numbered `.sql` files + a `schema_version` table | No migration framework |
| Web app | **React and Next.js, static export**, Vercel AI SDK `useChat` + AI Elements | Best chat-UI ecosystem; static export means no Node server in production |
| Distribution | **Docker Compose:** API + Postgres + backup sidecar, optional Ollama and speaches | Runs anywhere; one command |
| License | Permissive; final pick (MIT versus Apache-2.0) at release | All chosen deps are permissive |

### Provider switch

One code path for local and cloud, per role:

```
CHAT_BASE_URL=http://ollama:11434/v1      # or https://api.<provider>.com/v1
CHAT_MODEL=qwen3:4b                       # or a cloud model id
VISION_BASE_URL=...  VISION_MODEL=...     # photos, fallback statement extraction
STT_BASE_URL=http://speaches:8000/v1      # or a cloud transcription endpoint
```

No PII-stripping layer, no split routing inside a role. Which role runs where is the self-hoster's
choice. Develop against cloud; the eval suite decides which local models are good enough.

---

## 4. Architecture

```
 Browser / PWA ──┐                          [API process] ────────────> LLM endpoints (chat, vision, STT)
 (future Android)├── HTTPS over tailnet ──>   FastAPI: static web app, auth,      local or cloud
 CLI (on host) ──┘                            REST, chat (Vercel AI stream)
                                              PydanticAI agent ─> tools ─> ledger / engine
                                              scheduler: briefing, reminders, patterns, risk
                                                    │
                                                    v
                                              [Postgres] ──pg_dump──> [backup sidecar] ──restic──> off-site
```

One API process, one Postgres database. The agent is a backend service; every interface—web, CLI,
a future Android app or chat-app bridge—is a client of the same API. Detail:
[`platform.md`](platform.md).

### The rules

1. **The LLM never does arithmetic.** Every number is computed by Python (or Postgres) and handed
   back. The model parses language, picks tools, and phrases results.
2. **Untrusted text never reaches a write-capable agent.** Statements and photos are extracted by
   an isolated, tool-less model call into a typed schema. Only typed fields reach the agent. Voice
   transcripts are the user's own words and take the normal chat path.
3. **Tools are plain Python over the database, free of PydanticAI types.** The agent, the REST API,
   and any future MCP server wrap the same functions.
4. **The database is the whole state.** Photos live in it; a `pg_dump` is a complete backup and
   export.

---

## 5. Build order

Every layer is built to its final shape—no throwaway versions. Order is by dependency.
**Real money goes into it from L2 onwards**, so backups land in L1, before it holds anything worth
losing. Settings sections land with the layer that needs them. Milestones are binary. No dates.

**L0: Foundations**

1. Repository reset: `pyproject.toml` deps match this plan; `src/fin_agent/`, `web/`, `compose.yaml`.
2. Postgres in compose for dev and prod; numbered SQL migrations with `schema_version`; the `app` and read-only `agent_ro` roles.
3. Schema v1: every table in [`design.md`](design.md), money as `BIGINT` paise.
4. `docker compose up` serves the web shell and an API health check, reached over HTTPS through `tailscale serve`.
5. Auth: first-run setup, passkey login, sessions, passkey reset from the host CLI.
6. CI: ruff, pytest against a Postgres service container, web lint and build.

**L1: Ledger**

7. Accounts—bank, card (cycle, limit), cash, wallet, loan, asset—through the API and read-only panels.
8. Transactions: record, edit, delete; transfers as linked pairs; splits, lending, and reimbursements through counterparties.
9. Schedules: income (fixed, variable, one-off), obligations, family transfers, installment plans—recurrence rules, holiday-aware "last working day."
10. Goals with fenced savings; categories with a reducible flag; learned categorization rules.
11. Audit log with undo for every write; full export (CSV, JSON, and `pg_dump`).
12. Backup sidecar: nightly `pg_dump` shipped off-site by restic; a restore tested locally.
13. Demo persona: a seeded synthetic ledger—the demo instance, the test fixtures, and the template database every eval trial clones.

**L2: Agent** *(dogfooding starts)*

14. Web chat streams from the PydanticAI agent; conversations stored and full-text searchable.
15. Same build runs against Ollama and a cloud endpoint by env alone; Spend Limits; per-run trace log.
16. Read tools including `run_query` on `agent_ro` with a statement timeout; zero versus unknown, data age, and source ids (for citations) in every return.
17. Write tools with inline write-show-undo cards; corrections learn rules.
18. Memory: the agent saves preferences unprompted, with a "Remembered" card and undo; visible and editable in settings.
19. Setup conversation lands accounts, income, obligations, goals, floor, and estimated baselines.
20. CLI: chat plus admin (setup, migrate, export, passkey reset).

**L3: Import**

21. **Spike:** my real SBI, Union Bank, and Slice statements through the candidate parsers. Output is a decision per bank. Protocol in [`import.md`](import.md).
22. Pipeline: upload → parse → invariant → dedupe and reconcile against logged rows → review screen → commit.
23. Generic fallback for banks with no parser, invariant-gated.
24. 12-month backfill done for my accounts; baselines recomputed from real rows.

**L4: Evals** *(gate: engine work starts when this exists)*

25. 50+ agent-eval tasks from real usage: a balance of answer and refuse cases, Hinglish, prompt-injection cases, a fresh database per trial cloned from the demo template, `pass^k`, run per configured model. Shape in [`harness.md`](harness.md).
26. Model scorecard: `pass^k` per local model, published in the README.

**L5: Engine**

27. `project()`: day-by-day curve over income, obligations, installments, card cycles, fenced goals, and simulated variable spend, against the floor (median, band, and floor probability). Seeded; unit and property tests.
28. Affordability: verdict plus the four tradeoffs, all computed in code.
29. **Backtest:** `project()` run as of past dates over the backfill; median 30-day error and band calibration published.
30. **HackerRank "Buy or Wait":** the engine scored on the 25 labeled samples, score published (the data stays local).
31. Projection and verdict render as inline chart cards with citations, plus a projection panel.

**L6: Watch**

32. Scheduler, notification inbox, web push from the installed PWA.
33. Weekly briefing; bill, EMI, and card-due reminders; risk warnings when the projection turns.
34. Recurring-charge detection—new and missed subscriptions—presented as proposals, never silent writes.

**L7: Capture**

35. Voice notes: push-to-talk in the web app → STT → chat. Hinglish eval cases pass.
36. Photos: receipt or UPI screenshot → isolated vision extraction → review card → one transaction with the image attached; Android share target.

**L8: Ship**

37. Restore drill on a fresh machine: the backup, the restic password, and the ledger comes back.
38. Security pass: threat model, dependency audit, security headers.
39. Docs: install (Docker + Tailscale), configure LLMs, add a bank parser. License file, versioned image.
40. Someone else installs it from the docs alone.
41. Four weeks of daily use without falling back to mental math or a spreadsheet.

**Estimate:** at 15-20 hours a week, roughly **7-9 months**. L3 (import) is the one genuinely
unpredictable layer, which is why it opens with a spike. If a layer runs past twice its share,
something has crept in—check it against [`parked.md`](parked.md).

---

## 6. Open questions

Answer each when its layer arrives, not before.

- **L0:** Tailscale on the host, or as a sidecar container in compose?
- **L2:** One discriminated-union `update_setup` tool, or split per entity? Evals decide. Which
  local chat model clears the bar? Evals decide.
- **L3:** What does Slice export, and in what format? Does any parser read Union Bank, or is it
  the generic path?
- **L5:** Resample single days of spend, or whole weeks (weekend spend clusters)? What floor
  probability makes a "yes"—90% or 95%? Card share of variable spend—learned from history or set
  per account?
- **L6:** Briefing time, reminder lead days, and recurring-charge thresholds—tune on real data.
- **L7:** Which STT endpoint passes the Hinglish evals—cloud or local?
- **L8:** MIT or Apache-2.0?

---

## What "shipped" means

All of the following must be true:

- Every milestone in §5 done
- Used daily for four weeks with no spreadsheet or mental-math fallback
- Installed by someone else from the docs alone
- No fix pushed in the last week

Then it's shipped. Parked items get promoted from a working, used system—not from a document.
