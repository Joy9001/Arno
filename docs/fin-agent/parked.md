# Parked

This file holds the detail behind [`plan.md`](plan.md) §2: what is deliberately out of the final
product as planned on October 7, 2026, and revised on October 8, 2026, each with a reason and a
trigger. _Not rejected_—each has a stated condition under which it comes back.

The `plan.md` file owns decisions, and this file owns their shape. If the two disagree, the plan
wins and this file is stale.

If something moves out of this file, it goes into `plan.md` §5 as a numbered milestone. Nothing
gets built straight from here.

---

## Surfaces

### Chat-app integrations: Signal, Telegram, WhatsApp

**Why parked:** the web app is the main face. A bridge is a client of the same chat API, so it
adds nothing the architecture has to anticipate.

**Shape when it comes:** a thin adapter for each app that forwards to `/api/chat` and collapses the
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

### OCR for scanned statements

**Why parked:** the `VISION` model reads pages with no text layer, and a harsh phone photo needs it
either way. In the Monty test, RapidOCR read clean scans and a moderate photo after 1-2 repairs, at
4-8 s a page on a CPU, and returned nothing on a harsh photo. It also pulls in OpenCV, which bundles
an LGPL FFmpeg that would need a license check before shipping. The evidence comparing OCR with
vision models on dense bank-statement tables is weak.

**Shape when it comes:** one more source of positioned words for `doc_words`, so a saved parser can
reuse it. The pipeline doesn't change.

**Trigger:** the L3 spike finds scanned statements common or OCR clearly better, or a self-hoster
has no vision model.

### Receipt line items

**Why parked:** one transaction for each photo was chosen.

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

**Trigger:** I keep asking "how much is left for dining this month."

### Multicurrency

**Why parked:** one base currency for each instance.

**Trigger:** a foreign-currency account or foreign income.

### Multiuser households

**Why parked:** one person for each instance.

**Trigger:** a self-hoster wants partner-shared money in one instance. This one is a schema
change (owner on every row), so it's the costliest to bring back.

### Projection for each account

**Why parked:** the projection pools liquid accounts.

**Trigger:** an autodebit fails because money was in the wrong account.

---

## Proactive

### Daily, monthly, and payday briefings

**Why parked:** the weekly briefing, due reminders, and risk warnings cover what needs saying.
More briefings are more noise.

**Trigger:** I keep asking the same question between weekly briefings.

### Anomaly detection: Large transactions and category spikes

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

### Hidden memory extraction and semantic search (mem0-style)

**Why parked:** memory is agent-managed typed preferences—saved unprompted, but visible and
undoable. A hidden store that extracts facts from every turn risks a second source of truth beside
the ledger. Rechecked in [step 6](system-design.md#memory-no-memory-library), October 31, 2026.

**Shape when it comes:** embeddings over conversations and preferences using `pgvector`, in the
same database.

**Trigger:** keyword search over past conversations keeps missing things I said, or I keep
repeating a fact that never becomes a preference.

### A separate code sandbox

**Why parked:** Monty runs model-written parsers inside the API process, with no filesystem,
environment, or network, and it worked for every text-based format in the
[Monty test](../research/monty-document-parsing/README.md). Inside Docker Compose, the others cost
more: Docker needs the host's socket, BoxLite and Smol need `/dev/kvm`, and Bubblewrap's docs say it
isn't a boundary against a hostile agent.

**Shape when it comes:** a no-network sidecar holding only the statement being parsed, or BoxLite or
Smol with `/dev/kvm` passed through, as an optional Compose profile. Neither is tested. The skill and
the checks stay as they are.

**Trigger:** the L3 spike finds a real statement that can't be parsed in Monty.

---

## The May 2026 spec

The [archive](../archive/) holds the original feature specs. Most of those features are now in the
product; the rest are in the preceding sections. Treat the archive as evidence of past intent, not
as designs—it was written against a different stack and contradicts itself.
