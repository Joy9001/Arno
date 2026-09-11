# Documentation

Everything written lives here. Three trees, three different kinds of authority.

| Tree | What it is | Authority |
|---|---|---|
| [`fin-agent/`](fin-agent/) | The project: plan, design, decisions | **Current and authoritative** |
| [`research/`](research/) | Independent surveys of outside fields | Reference. Describes the world, not this project |
| [`archive/`](archive/) | Superseded work, kept for history | **Not authoritative.** Never cite as a decision |

---

## `fin-agent/` — the project

Start with the plan. It owns decisions; the others own detail. If they disagree, the plan wins.

| Doc | Holds |
|---|---|
| [`plan.md`](fin-agent/plan.md) | **Start here.** Scope, stack, architecture, milestones, definition of done |
| [`design.md`](fin-agent/design.md) | Data model, tool surface, projection engine |
| [`import.md`](fin-agent/import.md) | Document extraction: library choice, the invariant, the M7 spike |
| [`parked.md`](fin-agent/parked.md) | Deferred ideas, each with a trigger to revisit |

**Rule for keeping these readable:** a decision plus a one-line rationale goes in `plan.md`.
Anything needing a table, a schema, or three paragraphs of justification gets its own file and a
link. The [`archive/`](archive/) is what happens when that rule isn't followed — nine files, zero
code.

## `research/` — independent surveys

Deliberately unfiltered by this project's requirements. These record what exists in a field, not
what this project should do. Applying them is a separate exercise.

| Survey | Covers |
|---|---|
| [`agent-harness-engineering/`](research/agent-harness-engineering/) | The discipline of building the system around a model: context, tools, loop, evals, security, ecosystem. 10 files, ~1,800 lines, annotated sources with a gap log |

## `archive/` — superseded

The May 2026 design pass. Kept because the reasoning is occasionally useful as *evidence*, never
as a conclusion. See [`archive/README.md`](archive/README.md) for what's still worth reading and
what is known to be stale.

---

## Conventions

- Lowercase, hyphenated filenames.
- Ordinary relative Markdown links, so the tree works on disk and on GitHub.
- Research folders carry their own `README.md` index and a `sources.md` with per-source
  confidence marks.
- When a doc is superseded, move it to `archive/` and say so at the top of its replacement —
  don't edit history in place.
