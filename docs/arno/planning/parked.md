# Parked features

This document owns features kept deliberately out of Arno's scope behind [`plan.md`](../plan.md)
§2, each with its rationale, future shape, and revisit trigger; see
[capability catalog](capability-catalog.md) for prioritized unbuilt proposals.

_Parked_ does not mean rejected: every entry names the condition under which it returns. Moving an
item out of this file requires adding a numbered milestone to `plan.md` §5; nothing is built
directly from here.

---

## Surfaces

### Chat-app integrations: Signal, Telegram, WhatsApp

**Why parked:** The web app is the primary surface. Any messaging bridge is a client of the same
chat API and requires no architectural preparation ahead of time.

**Future shape:** A thin adapter per app that forwards messages to `/api/chat` and collapses the
stream into one reply—or exposes Arno's read tools over MCP so Hermes or OpenClaw acts as the
bridge
([personal-agent platforms research](../../research/personal-agent-platforms-2026-09-23.md#5-could-hermes-or-openclaw-be-fin-agent)).
Signal is the privacy-preserving choice; Telegram bot chats are not end-to-end encrypted.

**Trigger:** Logging in the PWA proves measurably slower than texting in daily use.

### Native Android app

**Why parked:** The PWA covers chat, voice capture, camera, and the Android share sheet.

**Trigger:** Daily use requires a capability the PWA lacks, such as SMS access or home-screen
widgets. The REST API and chat stream already serve as the client contract.

### Real-time voice conversation

**Why parked:** Push-to-talk voice notes cover mobile capture. Spoken back-and-forth adds streaming
audio, text-to-speech, and latency engineering—a separate project.

**Trigger:** Voice notes become the owner's primary way of logging transactions.

### Editable panels

**Why parked:** Chat edits support undo and run through the same ledger functions; maintaining one
editing path reduces code. Imports and settings remain directly editable.

**Trigger:** A recurring edit proves clearly slower in chat than in a form.

---

## Capture

### SMS and email autocapture

**Why parked:** Chat, voice notes, photos, and bank statements cover capture without parsing noisy
SMS alerts. Open-source Android SMS-to-webhook forwarders exist if this returns.

**Trigger:** Statement reconciliation repeatedly reveals a large share of unlogged transactions
whose omission makes affordability verdicts wrong.

### Balance nudges

**Why parked:** Balances update from statements and explicit statements of balance only, and the
agent always states balance age beside figures.

**Trigger:** A verdict turns out wrong because a balance went stale and its age was missed.

### OCR for scanned statements

**Why parked:** The `VISION` model already reads statement pages with no text layer and handles
harsh phone photos. In an earlier evaluation, RapidOCR read clean scans and moderate photos after
one to two repairs at 4–8 seconds per page on CPU and failed on harsh photos; it also depends on
OpenCV, which bundles LGPL FFmpeg and requires license review.

**Future shape:** One additional source of text feeding the script path (Path B) so scanned
statements reuse the same pipeline unchanged.

**Trigger:** Real statements (for example, Union Bank or other Indian banks) commonly arrive as
scans where local OCR outperforms vision models, or a self-hoster runs without a vision model.

### Receipt line items

**Why parked:** One transaction per photo keeps capture fast and focused.

**Trigger:** Questions such as `"How much did I spend on household items inside grocery runs?"`
arise regularly and cannot be answered from whole-receipt totals.

---

## Money features

### Net worth from investment statements

A _Consolidated Account Statement_ (_CAS_) lists every mutual fund and demat holding in one PDF.
_Net asset value_ (_NAV_) is a mutual fund's daily unit price.

**Why parked:** The owner holds no investments, leaving no real records to build or validate
against. Asset accounts with manually updated balances remain in the product.

**Future shape:** CAS PDF parsed via `casparser` into `holdings` rows on asset accounts, nightly
AMFI daily NAV revaluation, daily `net_worth_snapshots`, and net worth surfaced in financial
context and recaps.

**Trigger:** The owner holds investments for about three months.

### Broker APIs such as Zerodha Kite Personal

**Why parked:** Monthly CAS covers holdings across every broker, and AMFI NAV covers mutual fund
valuations. Kite Personal is Zerodha-only and requires a daily login token.

**Trigger:** Net worth tracking returns and holdings must be fresher than a monthly CAS.

### Tax engine

**Why parked:** Salary TDS covers the owner's tax situation. An India tax engine must implement the
Income-tax Act, 2025 (in force from April 1, 2026).

**Trigger:** Freelance income appears (making advance-tax dates material), or self-hosters request
it.

### Category budgets

**Why parked:** The projection engine, safe-to-spend figure, and spending baselines answer
`"Am I overspending?"` without rigid category caps.

**Trigger:** The owner repeatedly asks `"How much is left for dining this month?"`.

### Multicurrency

**Why parked:** Each Arno instance uses one base currency.

**Trigger:** The owner holds a foreign-currency account or earns foreign-currency income.

### Multiuser households

**Why parked:** Each Arno instance serves one user.

**Trigger:** A self-hoster needs partner-shared finances in one instance. Because ownership touches
every table, this is the costliest schema change to introduce.

### Projection for each account

**Why parked:** The projection engine pools liquid accounts into one spendable total.

**Trigger:** An autodebit fails because money sat in the wrong account despite sufficient total
cash.

---

## Proactive

### Daily, monthly, and payday briefings

**Why parked:** Arno already ships template-based Daily, Weekly, and Monthly **Recaps**
([`data-and-engine.md`](../specs/data-and-engine.md#recaps)), plus due reminders and risk warnings.
Adding extra model-written prose briefings creates notification noise and extra inference spend.

**Trigger:** The owner repeatedly asks the same open-ended question between recaps.

### Anomaly detection: Large transactions and category spikes

**Why parked:** Reliable thresholds require months of real history, and recurring-charge detection
plus price-change alerts already cover the main failure mode.

**Trigger:** A large or unusual charge slips past unnoticed.

---

## Platform

### Dedicated AI gateway

**Why parked:** Native Pydantic AI providers and `genai-prices` cover the supported provider set,
and OpenCode needs only a narrow accounting adapter. A gateway does not solve billing-unit,
missing-usage, or Phoenix display requirements ([`cost-tracking.md`](../specs/cost-tracking.md)).

**Trigger:** A demonstrated need for shared credentials, routing, or centralized admission across
multiple applications, or a tested gateway that materially simplifies the provider boundary.
Re-verify license, persistence, cost parity, and failure semantics before adopting one.

### Public-internet access

**Why parked:** Tailscale keeps the instance off the public internet. Although Arno already ships a
strict Content Security Policy in `src/arno/api/middleware.py`, staying tailnet-only avoids
password-plus-TOTP fallback authentication, hashed recovery codes, login lockout, and public
internet exposure.

**Future shape:** Caddy for automatic HTTPS, Argon2 password plus TOTP fallback via `pyotp`, hashed
recovery codes, and login rate limiting alongside the existing security headers.

**Trigger:** A user requires access without Tailscale.

### Encryption inside the database

**Why parked:** Host disk encryption protects against a stolen drive, and restic encrypts backups.
Community PostgreSQL provides no built-in transparent data encryption.

**Trigger:** Deploying Arno on a third-party VPS where the hosting provider can inspect the running
disk.

### Locale-pack interface

**Why parked:** India is the sole supported locale; designing a plugin interface around one
implementation is premature. Because India-specific code lives in `src/arno/india/`, extracting an
interface later is mechanical.

**Trigger:** A contributor implements a second country's locale pack.

### arm64 images

**Why parked:** `amd64` covers the owner's machines.

**Trigger:** A self-hoster needs to run Arno on a Raspberry Pi or an ARM VPS.

### Egress proxy

**Why parked:** The agent has no general network tool of its own, so an egress proxy defends only
against a compromised dependency—which could still exfiltrate data through an allowed LLM endpoint
using an attacker's key ([`architecture.md`](../architecture.md)).

**Future shape:** An internal Docker Compose network paired with an allowlisting proxy sidecar
following
[Hermes network isolation](https://hermes-agent.nousresearch.com/docs/user-guide/egress/network-isolation),
allowing only configured LLM, speech, vision, webhook, Tailscale, and backup hosts.

**Trigger:** Any tool or bridge gains outbound network access, or a supply-chain incident occurs.

### Job library

**Why parked:** One process and a plain PostgreSQL table run Arno's scheduled jobs in roughly 80
lines of code ([`platform.md`](../specs/platform.md#scheduler-and-background-jobs)). A dedicated queue library
pays off only with multiple concurrent workers.

**Future shape:** Evaluate a Postgres-backed async queue compatible with `asyncpg` (such as
Procrastinate or `pgtask` once mature). Existing job handlers are already idempotent and stay
unchanged.

**Trigger:** Arno needs a second worker process, or edge cases in the custom loop start costing
maintenance time.

### MCP server

**Why parked:** The web app and REST API cover single-user access. Exposing Arno over MCP adds
a token authentication scheme and sends ledger data to whatever external model the MCP client
runs, bypassing Arno's number check and confirm cards.

**Future shape:** A read-only MCP adapter over `src/arno/tools/` (matching architecture rule 3 in
`plan.md` §4), reachable over the tailnet only, authenticated with a scoped, expiring, revocable
token issued from **Settings**
([`C31`](capability-catalog.md#c31-arno-as-an-mcp-server)).

**Trigger:** The owner needs Arno's ledger and affordability answers inside another desktop
assistant or agent.

---

## Agent

### Direct Claude Agent SDK subscription runtime

**Why parked:** Official Agent SDK subscription access runs its own agent loop, authentication,
tools, and sessions, requiring a separate architecture. Plan-funded Console API credits already
work with the native Anthropic adapter
([`cost-tracking.md`](../specs/cost-tracking.md)).

**Trigger:** API-credit access fails a concrete owner need and an official SDK integration can
preserve Arno's tool permissions, persistent state, tracing, and spend-budget contract without
token extraction or client impersonation.

### Hidden memory extraction and semantic search (mem0-style)

**Why parked:** Arno's memory consists of visible, undoable typed memories (`preference`, `fact`,
and `rule`). A hidden extraction store running on every turn risks creating a second source of
truth beside the ledger.

**Future shape:** `pgvector` embeddings over conversations and typed memories inside the existing
PostgreSQL database.

**Trigger:** Keyword search over past conversations repeatedly misses prior context, or the owner
keeps repeating facts that never get captured as typed memories.
