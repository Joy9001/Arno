# Documentation

Everything written lives here, in three trees that carry different kinds of authority:

| Tree | What it is | Authority |
|---|---|---|
| [`fin-agent/`](fin-agent/) | The project: plan, design, decisions | Current and authoritative |
| [`research/`](research/) | Independent surveys of outside fields | Reference. Describes the world, not this project |
| [`archive/`](archive/) | Superseded work, kept for history | _Not authoritative._ Never cite as a decision |

---

## Project docs in the `fin-agent/` directory

Start with the plan. It owns decisions, and the other docs own the detail. If they disagree, the
plan wins. The project has the following docs:

| Doc | Holds |
|---|---|
| [`plan.md`](fin-agent/plan.md) | Start here. The final product: scope, stack, architecture, build order, definition of done |
| [`design.md`](fin-agent/design.md) | Data model, agent tool surface, projection and affordability engine, pattern detection |
| [`platform.md`](fin-agent/platform.md) | API, web app, auth, notifications, scheduler, deployment, backups, security |
| [`harness.md`](fin-agent/harness.md) | Agent framework, harness capabilities and their triggers, eval discipline |
| [`import.md`](fin-agent/import.md) | Statements, photos, and voice: parsers, the invariant, reconciliation |
| [`parked.md`](fin-agent/parked.md) | Deliberately out of the product, each with a trigger to revisit |
| [`system-design.md`](fin-agent/system-design.md) | The eight-step system design walk: where each step is recorded, what waits for which step, and review notes |

**Rule for keeping these readable:** a decision and its one-line rationale go in the `plan.md`
file. Anything that needs a table, a schema, or three paragraphs of justification goes in a
satellite doc and gets a link. The [`archive/`](archive/) directory shows what happens when that
rule isn't followed: the May 2026 docs reached nine files and zero code.

## Research surveys in the `research/` directory

These surveys are deliberately unfiltered by this project's requirements. They record what exists
in a field, not what this project should do. Applying them is a separate exercise. The directory
holds the following surveys:

| Survey | Covers |
|---|---|
| [`agent-harness-engineering/`](research/agent-harness-engineering/) | The discipline of building the system around a model: context, tools, loop, evals, security, ecosystem. 10 files, about 1,800 lines, annotated sources with a gap log |
| [`agentic-system-design/`](research/agentic-system-design/) | How whole agentic systems are composed: patterns, multi-agent topology, retrieval and memory, production operations, and 18 case studies |
| [`agent-toolkit-2026/`](research/agent-toolkit-2026/) | Let's Code's catalog of 404 open source agent tools, shortlisted for each design step. _Filtered by this project's requirements on purpose_ |
| [`personal-agent-platforms-2026-09-23.md`](research/personal-agent-platforms-2026-09-23.md) | OpenClaw- and Hermes-style agent platforms and their finance skills, plus the "Buy or Wait?" affordability prior art. _Applied to this project on purpose, unlike the rest of this tree_ |

## Superseded work in the `archive/` directory

This directory holds the May 2026 design pass. It's kept because the reasoning is occasionally
useful as _evidence_, never as a conclusion. For what's still worth reading and what is known to be
stale, see [`archive/README.md`](archive/README.md).

---

## Conventions

These conventions apply to every doc in this tree.

### Files and links

- Use lowercase, hyphenated filenames.
- Use ordinary relative Markdown links, so the tree works on disk and on GitHub.
- Give each research folder its own `README.md` index and a `sources.md` file that marks the
  confidence of each source.
- When a doc is superseded, move it to `archive/` and say so at the top of its replacement—don't
  edit history in place.

### Writing style

Docs follow the [Google developer documentation style guide](https://developers.google.com/style).
The following house rules record the choices the guide leaves open and the places this project
departs from it. Where they differ from the guide, the house rules win.

- **Voice:** the author writes in the first person (_I_, _my_) about their own money and setup.
  Project-level statements name Fin-Agent instead of using _we_, _our_, or _us_.
- **Spelling:** American, in prose. Code keeps its own spelling, so the `instalment` schema value
  stays even though the prose says _installment_.
- **Headings:** sentence case. Use a colon, not a dash, before a subtitle, and capitalize the word
  after it. Avoid code in headings; if you need it, add a noun, as in "the `fin-agent/` directory."
- **Dates:** write them out, as in "October 7, 2026." In table cells, abbreviate the month, as in
  "Oct 7, 2026."
- **Bold:** only for run-in headings at the start of a paragraph or list item, with the ending colon
  or period inside the bold. After a colon, start the text in lowercase. Don't use bold in table
  cells or as a stand-in for a heading.
- **Italics:** for emphasis, sparingly, and for terms and abbreviations on first use. Mark italics
  with underscores, not asterisks.
- **Tables:** introduce every table with a complete sentence. Leave a cell blank when there's
  nothing to say; don't use a dash as a placeholder.
- **Lists:** end an item with a period when it has a verb, and keep the punctuation consistent
  within a list.
- **Abbreviations:** spell out unfamiliar ones on first use in each doc, as in "_net asset value_
  (_NAV_)." Terms this audience knows, such as API, LLM, PWA, UPI, EMI, and STT, stay short.
- **Words:** write out _database_, _dependencies_, _environment variables_, _development_,
  _production_, and _administration_. Use _per_ only for rates, as in "per day," and _for each_
  otherwise. Close up prefixes, as in _autocapture_ and _multiuser_.
- **References to the plan:** write `plan.md` §N for a section, "rule N in `plan.md` §4" for one
  of its rules, `plan.md` §6, LN for an open question, and "milestone N" for a build-order item.
  Keep the plan's numbered headings, because other docs and the root README link to them.
- **Satellite docs:** a doc that holds the detail behind a plan section opens with "This file holds
  the detail behind `plan.md` §N: …" followed by "The `plan.md` file owns decisions, and this file
  owns their shape. If the two disagree, the plan wins and this file is stale."
- **Line length:** wrap prose at 100 columns. Table rows, code blocks, and long URLs can run
  longer.

The `google-developer-style` plugin is enabled for this repository in `.claude/settings.json`. To
check a whole doc, run `/google-developer-style:style-check FILE`, replacing `FILE` with the doc's
path. To see the findings without edits, add `--report-only`. The reviewer it runs looks only for
files such as `CLAUDE.md` and `STYLE.md`, so tell it that the house rules are in this section. For
a single style question, use the `/google-developer-style:google-developer-style` skill. The check
flags a few known false positives in these docs: _holiday_ (bank holidays), _off the internet_, and
_allowlist_ (used as a noun).
