# Parked

Deliberately out of the final product as planned on October 7, 2026, and revised October 8, 2026,
each with a reason and a trigger. _Not rejected_—each has a stated condition under which it comes
back.

If something moves out of this file, it goes into [`plan.md`](plan.md) §5 as a numbered
milestone. Nothing gets built straight from here.

---

## Surfaces

### Chat-app integrations: Signal, Telegram, WhatsApp

**Why parked:** the web app is the main face. A bridge is a client of the same chat API, so it
adds nothing the architecture has to anticipate.

**Shape when it comes:** a thin adapter per app that forwards to `/api/chat` and collapses the
stream into one message—or expose the tools over MCP and let Hermes or OpenClaw be the bridge
([personal-agent platforms research](../research/personal-agent-platforms-2026-09-23.md#5-could-hermes-or-openclaw-be-fin-agent)).
Signal is the privacy-preserving choice; Telegram bot chats are not end-to-end encrypted.

**Trigger:** L8 done, and logging in the PWA is measurably slower than texting.

### Native Android app

**Why parked:** the PWA covers chat, voice, camera, push, and the share sheet.

**Trigger:** something the PWA can't do is needed—SMS access, home-screen widgets. The API and
chat stream are already the contract.

### Real-time voice conversation

**Why parked:** voice notes (push-to-talk → text) cover capture. A spoken back-and-forth means
streaming audio, text-to-speech, and latency work—a project of its own.

**Trigger:** voice notes become my main way of logging.

### Editable panels

**Why parked:** chat edits have undo and go through the same functions; one editing path is less
code. Imports and settings stay editable.

**Trigger:** a recurring edit is clearly slower in chat than in a form.

---

## Capture

### SMS and email autocapture

**Why parked:** chosen against on October 7, 2026. Chat, voice, photos, and statements are the
inputs. Android SMS → webhook forwarders exist (open source) if this returns.

**Trigger:** statement reconciliation keeps showing a large share of transactions that were never
logged, and the stale balances make verdicts wrong.

### Balance nudges

**Why parked:** chosen against—balances update from statements only, and the agent states their
age.

**Trigger:** a verdict turns out wrong because a balance was stale and nobody noticed the age.

### Receipt line items

**Why parked:** one transaction per photo was chosen.

**Trigger:** questions like "how much did I spend on household items inside grocery runs" come up
and can't be answered.

---

## Money features

### Net worth from investment statements

A _Consolidated Account Statement_ (_CAS_) lists every mutual fund and demat holding you own in
one PDF. _Net asset value_ (_NAV_) is a mutual fund's price per unit, published daily.

**Why parked:** I hold no investments yet, so there's no real data to build or test it against.
Asset accounts that have hand-updated balances stay in the product.

**Shape when it comes:** CAS PDF → `casparser` → `holdings` rows on asset accounts; AMFI's free
daily NAV file revalues fund units nightly; daily `net_worth_snapshots`; net worth in the
financial context and a briefing.

**Trigger:** I've held investments for about three months.

### Broker APIs such as Zerodha Kite Personal

**Why parked:** CAS covers holdings across every broker; NAV covers fund values. Kite Personal is
free but Zerodha-only and needs a daily login token.

**Trigger:** net worth is back, and holdings need to be fresher than the monthly CAS.

### Tax engine

**Why parked:** chosen against. Salary TDS covers today's situation. The India code would
implement the Income-tax Act, 2025 (in force from April 1, 2026; new-regime slabs unchanged).

**Trigger:** freelance income appears (advance-tax dates matter), or self-hosters ask.

### Category budgets

**Why parked:** chosen against—the projection and baselines answer "am I overspending" without
caps.

**Trigger:** I keep asking "how much is left for dining this month".

### Multicurrency

**Why parked:** one base currency per instance.

**Trigger:** a foreign-currency account or foreign income.

### Multiuser households

**Why parked:** one person per instance.

**Trigger:** a self-hoster wants partner-shared money in one instance. This one is a schema
change (owner on every row), so it's the costliest to bring back.

### Per-account projection

**Why parked:** the projection pools liquid accounts.

**Trigger:** an autodebit fails because money was in the wrong account.

---

## Proactive

### Daily, monthly, and payday briefings

**Why parked:** the weekly briefing, due reminders, and risk warnings cover what needs saying.
More briefings are more noise.

**Trigger:** I keep asking the same question between weekly briefings.

### Anomaly detection: Large transactions, category spikes

**Why parked:** needs thresholds tuned on months of real data, and recurring-charge detection
covers the common case.

**Trigger:** a large or unusual charge slips past unnoticed.

---

## Platform

### Public-internet access

**Why parked:** Tailscale keeps the instance off the internet, which removes the password + TOTP
fallback, recovery codes, login lockout, CSP hardening, and most of the threat model.

**Shape when it comes:** Caddy for automatic HTTPS, password (argon2) + TOTP fallback using
`pyotp`, hashed recovery codes, login rate limiting, CSP.

**Trigger:** someone needs access without Tailscale.

### Encryption inside the database

**Why parked:** host disk encryption covers a stolen disk, and restic covers a leaked backup.
Community Postgres has no built-in encryption at rest.

**Trigger:** running on a VPS, where the provider can read the disk. Evaluate the options then.

### Locale-pack interface

**Why parked:** India is the only country; an interface that has one implementation is guesswork.
India code lives in one folder, so extracting an interface later is mechanical.

**Trigger:** someone writes a second country's pack.

### arm64 images

**Why parked:** amd64 covers my machines.

**Trigger:** someone asks to run it on a Raspberry Pi or an ARM VPS.

---

## Agent

### Code Mode: Sandboxed code execution

`pydantic-ai-harness` ships _Code Mode_: the model writes one Python script calling many tools
inside a Monty sandbox. The read-only `run_query(sql)` half of this idea is already in the product.

**What argues against it:**

- **Generated code has no tested semantics.** The model chooses the filter and the join;
  `run_query` has the same property in weaker form, which is why it ships first and gets watched.
- **Small models can't write correct code against a half-remembered schema.** It would push the
  product toward cloud-only models.
- **The product may not need it.** `run_query` covers aggregation, which is most of what a ledger
  is asked.

**Trigger:** the eval suite and real use show questions `run_query` can't answer that are genuinely
computational—regressions, multistep modeling. If they're aggregations, fix the tool
descriptions instead.

### Hidden memory extraction and semantic search (mem0-style)

**Why parked:** memory is agent-managed typed preferences—saved unprompted, but visible and
undoable. A hidden store that extracts facts from every turn risks a second source of truth beside
the ledger.

**Shape when it comes:** embeddings over conversations and preferences using `pgvector`, in the
same database.

**Trigger:** keyword search over past conversations keeps missing things I said, or I keep
repeating a fact that never becomes a preference.

---

## The May 2026 spec

The [archive](../archive/) holds the original feature specs. Most of that list is now in the
product; the rest is in the preceding sections. Treat the archive as evidence of past intent, not
as designs—it was written against a different stack and contradicts itself.
