# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One person per instance: the self-hoster who runs their own copy and wants their complete
financial picture in one place. No household accounts, no multi-user roles.

Day-to-day use happens on the phone, through the installed PWA: chat, photo and voice capture,
notifications, and the inbox. Statement imports, settings, and long review sessions happen on a
desktop browser.

The current instance is India-first: rupee amounts with lakh/crore grouping, Indian bank
statement layouts, UPI-heavy spending, and chat that accepts English, Hindi, or Hinglish. Other
self-hosters are equal users of their own copies; no second country is supported yet.

## Product Purpose

Arno holds the whole financial picture — accounts, cards, obligations, goals, and people you
owe — and answers any question the data supports, including whether you can afford something and
what it would cost you.

It exists so its owner can stop doing mental math or maintaining a spreadsheet. Success means four
weeks of daily use without falling back to either, and someone else installing it from the docs
alone.

## Positioning

A private, self-hosted, single-user finance agent rather than a cloud account-aggregation app. It
keeps one double-entry ledger of every real money movement, computes affordability from a seeded
probabilistic projection instead of the model's guess, and traces every number it states to a tool
return or a script it ran. It never moves money and never recommends investments or products.

A neighboring product could copy the chat interface, but not truthfully claim all three at once:
your data never leaves your own machine, every verdict is computed in code with a stated
probability, and every figure is cited back to ledger rows.

## Operating Context

- Docker Compose on hardware the owner controls (laptop, mini PC, VPS); reached over a private
  Tailscale network, never the public internet.
- Passkey login; one user; sessions in secure cookies. Admin commands run on the host, not over
  HTTP.
- PostgreSQL holds the ledger, plan, people, categorization, settings, and conversation records.
  Daily restic backups run inside the API and are restorable with `arno restore`.
- Daily capture is chat, voice notes, and photos (receipts, UPI screenshots); monthly statement
  imports reconcile what daily capture missed.
- The web app is agent-first, with read-only ledger, accounts, schedules, goals, people, timeline,
  recap, and inbox panels. Writes go through chat with undo; imports and settings are the editable screens.
- A CLI covers administration, restore, and demo seeding.
- The local model and trace profiles are optional Compose profiles for owners who want
  nothing to leave the machine.

## Capabilities and Constraints

Confirmed functionality: multi-account ledger with credit-card cycles; fixed and variable income;
obligations, EMIs, subscriptions, and card installments; goals with deadlines; a floor the
projection must not cross; people and splits; hand-updated asset accounts; learned
merchant-to-category rules; typed agent memory; searchable conversation threads; probabilistic
projection and affordability verdicts; citations; proactive briefings, reminders, risk checks,
pattern detection, and user-scheduled read-only questions; statement review, photo, and voice
capture;
CSV/JSON/`pg_dump` export.

Durable constraints:

- One user per instance. Multiuser households are out.
- India is the only country implemented; country-specific code lives in one folder.
- The UI is English-only. Chat replies in English by default and follows the user's language (or
  mix, such as Hinglish) only when they write in it.
- Every number in an answer comes from a tool return or a script the agent ran in an isolated
  sandbox; the model decides what to compute, code computes it.
- The agent never moves money. Advice is limited to facts and tradeoffs.
- Writes land immediately with undo, except statement imports (review screen first) and lasting or
  wide writes such as a category rule for every past row, archiving, or changing settings (one
  confirm tap).
- The instance is private-network only. No analytics, no public hosting, no third-party telemetry.
- Open source under MIT; no GPL, AGPL, SSPL, or Elastic code in the shipped image.
- Explicitly undecided or parked: native Android app, public-internet access, multicurrency,
  multiuser, editable panels, locale-pack interface, net worth from investment statements, broker
  APIs, tax engine, budgets, SMS/email autocapture, OCR for scanned statements, arm64 images. Each
  has a revisit trigger in `docs/arno/planning/parked.md`.

## Brand Commitments

- Name: **Arno**. License: **MIT**, open source.
- The interface is agent-first and expressive (owner's decision, November 8, 2026, replacing the
  earlier Apple-quiet canon): the agent is a living iridescent orb, answers land as bold verdict
  cards, and money is what the agent works on. Dark, lime for actions and verdicts, cobalt for the
  user's voice. The computed receipt stays the distinctive surface — verdict, probability, and the
  rows behind it.
- The product speaks plainly and precisely: facts, tradeoffs, and measured results, with no hype
  and no invented proof.
- Chat replies render links as plain text; external images never load.
- No analytics or tracking of any kind.

## Evidence on Hand

- `arno demo seed` builds a deterministic synthetic persona for demos and evals.
- `evals/` holds the pydantic_evals suite and the demo-persona dataset.

Absences future work must not fabricate:

- No real 12-month statement backfill exists on this machine, and no published forecast backtest
  or affordability benchmark exists.
- No customer testimonials, logos, press, case studies, or usage benchmarks exist.
- No pricing or hosted-service claims apply: the product is self-hosted and MIT licensed.

## Product Principles

1. **Code computes; the model explains.** No stated number without a traceable source.
2. **The complete picture beats feature breadth.** One record per real money movement, one ledger
   for accounts, cards, obligations, goals, and people.
3. **Private by default.** Self-hosted, single-user, private network, no analytics, no hidden data
   paths.
4. **Never move money, never give advice.** Record, model, and explain tradeoffs; the owner acts.
5. **State uncertainty honestly.** Probabilities, data quality, and measured error are part of the
   answer; "no data" is better than an invention.
