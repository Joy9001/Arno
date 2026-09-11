# Fin-Agent — Plan

The core plan: what this is, what's in scope, what it's built on, and the milestones.
**This file owns decisions.** Detail lives in its own doc.

| Doc | Holds |
|---|---|
| [`design.md`](design.md) | Data model, tool surface, projection engine |
| [`import.md`](import.md) | Document extraction: library choice, invariant, the M7 spike |
| [`parked.md`](parked.md) | Deferred ideas, each with a trigger to revisit |

**Rule for keeping this readable:** decisions and their one-line rationale go here. Anything
that needs a table, a schema, or three paragraphs of justification goes in a satellite doc
and gets a link. If this file starts growing by accretion again, split it — that's what
happened to the [May 2026 docs](../archive/), which reached nine files and zero code.

If a satellite disagrees with this file, this file wins and the satellite is stale.

Written 2026-09-07. Structure follows *The Ultimate Personal Project Playbook*; deviations
are marked.

---

## 1. The project

**One sentence:** A chat agent that holds my whole financial picture and answers any
question about it — including whether I can afford something.

"Can I afford this?" was the spark for the idea, not the scope of it. An agent that has the
data should answer anything the data supports.

| Filter question | Answer |
|---|---|
| Can I describe it in one sentence? | Yes (above) |
| Do I actually want to use it? | Yes — the checkout-counter problem, weekly |
| Am I the dumbest possible user? | Yes — I'm the target user, not an expert one |
| What does done look like? | Below |

**Done is:** It knows my complete financial situation — income, balance, obligations,
spending history — and I can ask it anything about my money and get a correct answer,
including affordability with real tradeoffs. And I've used it for two weeks without falling
back to mental math or a spreadsheet.

---

## 2. Scope

### The principle

**"Answers any question" is a property of the tool surface, not a feature list.** Eight
orthogonal tools over complete data compose into an unbounded question space. Scope is
controlled by counting tools, not by enumerating questions.

Tools and schema: [`design.md`](design.md).

### What it must know

Five tables — `profile`, `obligations`, `baselines`, `transactions`, `categories`. The agent
cannot project accurately without all five; that's the "full financial context" requirement
and it's why this MVP is bigger than a habit tracker. Shapes in [`design.md`](design.md).

### How the data gets in

1. **Conversational setup** — a one-time walkthrough: income, balance, obligations, rough
   category baselines. Ten minutes of chat and the agent projects accurately *immediately*,
   with no month-long wait for logged history. This is the answer to the cold-start problem
   and it costs one flow, not a subsystem.
2. **Ongoing chat logging** — *"spent 450 on Swiggy"*.
3. **Document import** — one generic entry point, any format. Library does the extraction,
   we do the mapping. See [`import.md`](import.md).

### Explicitly NOT in the MVP

Signal bot · web UI · scanned-PDF & photo import · receipt OCR · voice notes · memory layer
(mem0) · morning & weekly briefings · scheduler · anomaly detection · subscription
auto-detection · tax shadow engine · goals with deadlines · multi-account · split &
reimbursable tracking · cash-float attribution · learned categorisation rules · Docker ·
Postgres · auth

These are **surfaces and subsystems**, not core. Scope grew in the *depth* of the core —
full context, general query surface — and stayed cut on breadth. That's the trade that keeps
this finishable. Deferred items and their triggers: [`parked.md`](parked.md).

Test for anything wanting back in: *if I removed this, could the agent still answer
questions about my money correctly?* If yes, it stays cut.

### Non-functional requirements

| Requirement | Target |
|---|---|
| Scale | 1 user (me) |
| Latency | Under ~5s per reply; LLM-bound, fine |
| Availability | It's up when I run it |
| Durability | **Do not lose my ledger.** SQLite file, copied on write |
| Security | API key in env, never in the repo |
| Honesty | Tools distinguish *zero* from *unknown* — never an empty result the model reads as ₹0 |

Honesty is a real requirement, not a nicety: a general query surface over incomplete data
will otherwise answer confidently with no basis.

---

## 3. Stack

### Build vs buy — the standing rule

The playbook's rule: **buy, unless the piece is core to the product.**

| Piece | Decision | Why |
|---|---|---|
| Document extraction | **Buy** | Solved and tested. Writing it is reinventing the wheel |
| Agent loop + tool calling | **Buy** (PydanticAI) | Solved, and validation keeps the model honest |
| Mapping extracted rows → our schema | **Build** | ~50 lines. No library knows our data model |
| Projection engine | **Build** | This *is* the product |

Anything not on this list: search PyPI first, write code only if nothing fits. Applies to
every future milestone, not just the ones decided here.

### Choices

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.14 | Already know it; tooling already set up |
| Agent | PydanticAI | Structured output validation — the mitigation that works against hallucination |
| LLM | **Provider-agnostic** | `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL`. Nothing else changes |
| Database | **SQLite** | One user, one process, one file |
| Interface | CLI | A terminal is already a chat interface |
| Deploy | None | It runs on my laptop |

### Provider switch

Ollama serves an OpenAI-compatible API, so local and cloud are one code path:

```
# local
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen2.5:3b

# cloud
LLM_BASE_URL=https://api.<provider>.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=<model-id>
```

Full user query goes to whichever is configured. No PII-stripping layer, no split routing —
that complexity buys nothing and costs a subsystem. Develop against cloud, run local later.

### Two deliberate deviations from the playbook

**Postgres → SQLite.** For one user on one laptop, Postgres is a service to babysit for zero
benefit, and a single file is trivially backed up. Revisit if concurrent writers appear.

**No managed auth, no PaaS hosting, no CDN.** Those chapters assume a deployed multi-user
web app. This is a local single-user process.

---

## 4. Architecture

```
[CLI] ──> [PydanticAI agent] ──> [8 tools: Python functions] ──> [SQLite file]
                  │
                  └──> [LLM: local Ollama or cloud, via base_url]
```

One process. One file. No server, no queue, no cache, no container.

Tool signatures and the projection engine: [`design.md`](design.md).

### The one rule

**The LLM never does arithmetic.** Every number is computed by a Python function and handed
back. The model parses language and picks tools; it does not add, subtract, or project.
This is the externally documented failure mode that breaks LLM finance tools — it's why the
projection engine is plain Python with tests, not a prompt.

---

## 5. Milestones

Binary. No dates.

**Walking skeleton — every wire connected, no features:**

1. `finagent "hello"` runs, calls the configured LLM, prints a reply
2. Same command works against local Ollama *and* a cloud endpoint, changing only env
3. SQLite file exists with all five tables; I can insert a row by hand and read it back

**Context — the agent learns my situation:**

4. `update_profile` works — income, cadence, balance persist
5. Setup conversation: the agent walks me through income, obligations and baselines, and it all lands in the DB
6. `log_expense` — *"spent 450 on swiggy"* puts a real row in the ledger
7. **Spike (one evening, timeboxed):** real CSV *and* real password-protected PDF from my
   bank through `bankstatementparser`; `docling` if that fails. **Output is a decision, not
   code.** Protocol in [`import.md`](import.md)
8. `import_document` wraps whichever won — real statement in, real rows out, invariant
   passes, no duplicates on re-import

**Query surface — it answers things:**

9. `get_financial_context` returns the whole picture in one call
10. `query_transactions` — arbitrary filtered retrieval
11. `summarize_spending` — correct aggregates, summed in Python
12. `correct_transaction` — conversational repair

**The engine:**

13. `project_forward(days)` — correct day-by-day curve, with tests
14. `project_forward(days, hypothetical=...)` — *"can I afford 60k?"* → yes / yes-but / no, with the reason

**Ship:**

15. Ledger backup — the `.db` gets copied somewhere outside the working directory
16. Used it for two weeks without reverting to mental math or a spreadsheet

Milestones 1–3 are the skeleton and should be one sitting. Rough estimate: **5–6 weekends.**
M7 is the one genuinely unpredictable milestone, which is why it's timeboxed.

That's past the playbook's 3–5, deliberately: its worked example is a habit tracker, and a
finance agent with a projection engine has a larger irreducible core. The cut was made on
breadth, not core. Past 8 weekends, something has crept in — check it against the NOT-list.

---

## 6. Open questions

Answer these when the milestone that needs them arrives, not before.

- **M5:** How many categories in the fixed list? Fewer is better for a small model.
- **M6:** Confirm-before-write, or write-then-correct? M12 exists so write-then-correct is
  safe. Lean that way; less friction.
- **M7:** Does `bankstatementparser` read Indian bank statements? *The* question gating
  import — one real file answers it. Details in [`import.md`](import.md).
- **M13:** Variable-spend averaging window — trailing 30 days, or same-weekday-of-week?
- **M13:** What's the floor? A fixed emergency-fund number is the simplest thing that works.

---

## 7. Parked

Sandbox / `run_query`, scanned-PDF and photo import, and the whole May 2026 feature list —
each with the trigger that brings it back. See [`parked.md`](parked.md). Nothing gets built
straight from there; it becomes a numbered milestone here first.

---

## What "shipped" means

All four true:

- Walking skeleton works
- All sixteen milestones done
- I've personally used it for at least two weeks
- I haven't pushed a fix in three days

Then it's shipped. Features get added after that, from a working system — not before, from a
document.
