# Parked

Ideas deferred with a reason and a trigger. **Not rejected** — each has a stated condition
under which it comes back.

The rule: anything here is decided *after* two weeks of real use, because every item is an
answer to "the shipped thing has a gap" and nobody knows yet whether it does. Adding them
before that is guessing.

If something moves out of this file, it goes into [`plan.md`](plan.md) §5 as a numbered
milestone. Nothing gets built straight from here.

---

## Code execution sandbox

Let the agent write and run Python or bash against the ledger, for analysis the eight tools
in [`design.md`](design.md) don't cover.

### The case for

A fixed tool surface has edges. "Plot my food spend against last year", "run a regression on
my weekend spending" — real questions that composition of the eight tools may not reach. It
is the "answer any question" ambition taken to its logical end.
[Prior art](../archive/prior-art/research-2026-05-24.md) noted `yorkeccak/finance`
using a Daytona sandbox for exactly this.

### The case against

**It's in tension with the one rule.** `plan.md` §4 says the LLM never does arithmetic.
Generated pandas *executes* deterministically, but the model still chose the filter and the
join — a wrong `WHERE` returns a confidently wrong number with no tested semantics behind
it. The eight tools have tests; generated code has none.

**"Sandbox" on Windows without containers isn't one.** Doing it properly means Docker or
gVisor — a subsystem explicitly cut in `plan.md` §2.

**Small models can't write correct pandas against a half-remembered schema.** This pushes
toward cloud-only and undermines the local/cloud parity in `plan.md` §3.

**It's a remote-code-execution surface if a messaging channel ever lands.** A crafted
merchant string in an imported statement becomes an injection vector. Not a concern for a
local CLI; a serious one the day Signal appears.

### The 90% alternative — read-only SQL

A `run_query(sql)` tool against a read-only SQLite connection. SQLite has a genuine
read-only mode, SQLite does the arithmetic deterministically, and it's ~10 lines instead of
a subsystem. This is a ledger — most "arbitrary analysis" is aggregation, which SQL already
is. The agent shows the SQL it ran, so a wrong filter is visible rather than silent.

**Caveat worth re-reading first:** the
[archived prior-art research](../archive/prior-art/research-2026-05-24.md) says *"do
not give the LLM raw SQL access."* That warning was about SQL as the **primary** interface.
As a read-only escape hatch beside tested tools it's a different risk — a wrong query, not
wrong arithmetic. Different, not zero.

### Trigger

After two weeks of use, write down the questions the eight tools couldn't answer.

| Finding | Action |
|---|---|
| No list | Nothing. The surface was sufficient |
| Short list, mostly aggregation | `run_query(sql)`, read-only |
| Long list of genuinely computational questions | Revisit the sandbox, with containers |

---

## Scanned PDF and photo import

The two deferred handlers from [`import.md`](import.md).

**Why deferred:** OCR errors land on digits, in a ledger. Photos need a vision model, which
4 GB of VRAM can't run locally — breaking the local/cloud parity that `plan.md` §3 is built
on. Receipts are the likely driver and were already cut from the MVP.

**Trigger:** real usage produces a document the shipped extractor can't read. The dispatcher
already has the slot; adding a handler is additive, not a redesign.

---

## Everything from the May 2026 spec

The full NOT-list in `plan.md` §2 — Signal bot, web UI, receipt OCR, voice notes, memory
layer, briefings, scheduler, anomaly detection, subscription detection, tax shadow engine,
goals with deadlines, multi-account, split and reimbursable tracking, cash-float
attribution, learned categorisation rules.

These aren't parked pending a decision; they're **post-ship features**, and the trigger for
all of them is the same: the sixteen milestones are done and the thing has been used.

Original specs are in [`docs/archive/`](../archive/) for whoever picks one up. Treat
them as evidence of past intent, not as designs — they were written against a different
stack and are known to contradict each other.
