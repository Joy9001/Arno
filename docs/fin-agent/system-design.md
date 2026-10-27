# System design

This file tracks Fin-Agent's system design walk: eight steps, taken in order, each agreed before
the next one starts. It records where each step's decisions live, which questions wait for which
step, and what outside reviews contributed.

The decisions themselves live in [`plan.md`](plan.md) and its satellites, and this file points to
them. If the two disagree, the plan wins and this file is stale.

---

## The walk

The following table lists the steps, in order:

| Step | Covers | Recorded in | Status |
|---|---|---|---|
| 1. Requirements | Functional, non-functional, scale, constraints | [`plan.md`](plan.md) §1-2; scale [below](#scale-assumptions) | Recorded; one question open |
| 2. Trade-offs | The tensions the requirements create, and which side wins | This file | Next |
| 3. API design | Endpoints, the chat stream, auth, versioning | [`platform.md`](platform.md) | Not started |
| 4. Data model | Entities, schema, database | [`design.md`](design.md) | Not started |
| 5. High-level design | Layers and data flow | [`plan.md`](plan.md) §4 | Not started |
| 6. Deep dives | Agent loop, import, engine, memory, security | [`harness.md`](harness.md), [`design.md`](design.md), [`import.md`](import.md) | Not started |
| 7. Production readiness | Monitoring, security, backups, CI | [`platform.md`](platform.md) | Not started |
| 8. Capacity planning | Traffic, storage, memory, tokens; re-checks step 1 | This file | Not started |

Some decisions were committed before the walk began: rule 1 (the model decides what to compute;
code computes it), Code Mode, and the write tiers. Each is re-checked when its step arrives, not
treated as final.

---

## Step 1: Requirements

The requirements are recorded in [`plan.md`](plan.md) §1-2: the product, functional scope,
non-functional targets, and constraints.

### Scale assumptions

The following table lists the scale assumptions that step 8 re-checks:

| Dimension | Assumption |
|---|---|
| Users | One for each instance |
| Transactions | 100-500 a month—active UPI users run high (quick commerce, autos, chai, splits). About 60,000 rows over ten years, well under 50 MB |
| Chat | 10-50 turns a day; one conversation at a time |
| Statements | 3-5 files a month, plus a 12-month backfill at setup |
| Context per turn | Summaries and row IDs, never hundreds of raw rows |

The consequence: throughput doesn't shape the design. Correctness and trust do.

### Open question

**Retroactive payee corrections.** "ramesh@okaxis is my landlord" rewrites that payee's past rows,
which makes it a _wide_ write under the [write tiers](design.md#untrusted-text-and-write-tiers). In
a conversation that has read imported rows—exactly where such corrections happen—it would need a
confirming tap. The choice is to keep it instant, with an undo card that names the rule and how
many past rows changed, or to accept one tap.

### External review, October 27, 2026

An outside agent reviewed the step 1 draft. The following table records what was taken from it
and why:

| Point | Verdict |
|---|---|
| Savings and investment outflows (SIPs, FDs) aren't spending | Taken into `plan.md` §2 |
| Refunds offset the purchase; reimbursements aren't income | Taken into `plan.md` §2. Claims already model reimbursements; the requirement is now explicit |
| A payee or UPI ID correction applies to past and future rows | Taken into `plan.md` §2; the tier question is [open](#open-question) |
| Useful from a spoken snapshot on day one | Taken into `plan.md` §2. The setup conversation already lands balances; now it's a requirement |
| Higher transaction volume, and context size as a scale dimension | Taken into the [scale assumptions](#scale-assumptions) |
| Separate latency targets for lookups and what-ifs | Taken into `plan.md` §2 as targets that step 8 re-checks |
| Account Aggregator is closed to self-hosted apps | Taken into `plan.md` §2 constraints, checked against Sahamati's FIU page |
| Never moving money is a hard invariant | Taken into `plan.md` §2 |
| Coverage tracking, so "no data" isn't ₹0 | Already covered: data coverage for each account in every read tool ([`design.md`](design.md)) |
| Balance checksum on imports | Already covered: the invariant in [`import.md`](import.md). It catches most bad extractions, not all—swapped rows or wrong dates can still pass |
| Three reconciliation states | Already covered: certain, ambiguous, and none, plus "logged but not on the statement" ([`import.md`](import.md)) |
| Double-entry legs for each movement; EMI interest and principal as separate legs | Parked to [step 4](#step-4-data-model) |
| A PII scrubber before cloud LLM calls | Parked to [step 2](#step-2-trade-offs). `plan.md` §3 keeps "no PII-stripping layer" until then |
| Typed query plans or views for model-written SQL | Parked to [step 4](#step-4-data-model) |
| Writes only from direct user turns | Rejected: the write tiers replace it and keep everyday edits instant |
| Merge matching within ±5 minutes | Rejected: statements carry dates, not times. Matching uses the UPI reference, or the amount within ±3 days |
| Candidate merges surfaced in chat or the weekly briefing | Rejected: they stay on the import review screen, with no new chat surface |
| Extraction is "provably trustworthy" once balances reconcile | Rejected as stated; see the checksum row |
| A `upi://pay` link to settle up with a friend | Not now: outside the current scope. Noted in case settling up comes back |

---

## Parked by step

The following questions wait for their step.

### Step 2: Trade-offs

- PII scrubbing before cloud LLM calls: privacy, weighed against a reversible tokenizing layer to
  build and maintain.

### Step 4: Data model

- How spending is counted: the classification rules, and whether `run_query` reads views that
  apply them.
- One record for each movement: single rows with transfer pairs and claims (the current design),
  or double-entry legs. How a loan EMI's interest and principal are held for prepayment what-ifs.

### Step 6: Deep dives

- An output check that every number in a reply matches a tool return.
- Loading the financial context automatically at the start of a conversation.
