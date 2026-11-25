# Documentation

Arno's documentation lives in three trees with distinct levels of authority.

| Tree | Purpose | Authority |
|---|---|---|
| [`arno/`](arno/) | Product plan, architecture, subsystem specifications, operations guides, and open planning | Current and authoritative |
| [`research/`](research/) | Independent external surveys and prototypes | Reference only. Describes the outside world, not product decisions |
| [`archive/`](archive/) | Superseded specs and completed point-in-time records | Not authoritative. Never cite as a decision |

The repository root also holds four concise entry points: [`README.md`](../README.md) (overview,
features, and quickstart), [`AGENTS.md`](../AGENTS.md) (contributor workflow and checks),
[`PRODUCT.md`](../PRODUCT.md) (product positioning and principles), and
[`DESIGN.md`](../DESIGN.md) (visual design tokens and component system).

---

## Project documentation in `arno/`

This index is the single map of every living document in `docs/arno/`. Every topic has one owning
document. If another document conflicts with [`plan.md`](arno/plan.md), `plan.md` wins and the
other document is stale.

### Core documents

Start with these three root files.

| Document | Owns |
|---|---|
| [`arno/plan.md`](arno/plan.md) | Product scope, stack choices, architecture rules, L0–L10 milestones, open questions, and shipped criteria |
| [`arno/architecture.md`](arno/architecture.md) | End-to-end system architecture, control model, trust boundaries, reliability, success measures, and system diagrams |
| [`arno/completion-checklist.md`](arno/completion-checklist.md) | Open owner deployment, real-data, device, and human ship gates |

### Subsystem specifications (`arno/specs/`)

Technical design and contracts for each subsystem.

| Document | Owns |
|---|---|
| [`arno/specs/data-and-engine.md`](arno/specs/data-and-engine.md) | Database schema, trust boundaries and write tiers, projection and affordability engine, pattern detection, money calculators, recaps, and financial invariants |
| [`arno/specs/tools.md`](arno/specs/tools.md) | Contracts for all 14 read tools, 6 write tools, 5 delegation tools, 13 parser functions, and 5 categorizer functions |
| [`arno/specs/harness.md`](arno/specs/harness.md) | PydanticAI and `pydantic-ai-harness` capabilities, wake turn, prompt map discipline, context compaction, redaction, and eval harness |
| [`arno/specs/import.md`](arno/specs/import.md) | Statement readers, parser subagent pipeline, verification checks, reconciliation, transfer and card-credit decisions, post-commit categorization, photos, and voice |
| [`arno/specs/cost-tracking.md`](arno/specs/cost-tracking.md) | The `llm_usage` log, the shared daily spend cap, and unpriced-model handling |
| [`arno/specs/platform.md`](arno/specs/platform.md) | Repository layout, API surface, authentication and enrollment, scheduler and jobs, database connections and integrity, threat model, and India-specific code |
| [`arno/specs/web-interface.md`](arno/specs/web-interface.md) | Web navigation, hash routes, page specifications, subagent run views, and frontend stack (visual tokens live in [`DESIGN.md`](../DESIGN.md)) |

### Operations and engineering (`arno/operations/`)

Procedures for running, observing, and maintaining an Arno instance.

| Document | Owns |
|---|---|
| [`arno/operations/self-hosting.md`](arno/operations/self-hosting.md) | End-to-end installation, model setup, privacy and provider retention, backups, restore drill, updates, security posture, and troubleshooting |
| [`arno/operations/legacy-upgrades.md`](arno/operations/legacy-upgrades.md) | One-time migrations for pre-0.3.0 installs: upgrading from Fin-Agent and demoting the superuser `app` role |
| [`arno/operations/observability.md`](arno/operations/observability.md) | Turning tracing on, Phoenix span attributes, settings, retention, and Docker logs |
| [`arno/operations/development-operations.md`](arno/operations/development-operations.md) | Maintainer deployment file map, local development, testing, E2E journeys, triage, and release checklist |
| [`arno/operations/typing.md`](arno/operations/typing.md) | Python type-checking policy with Pyrefly, per-path strictness, diagnostic fixes, and tightening roadmap |

### Planning (`arno/planning/`)

Forward-looking scope boundaries and unbuilt proposals.

| Document | Owns |
|---|---|
| [`arno/planning/parked.md`](arno/planning/parked.md) | Features kept deliberately out of scope, each with its rationale, future shape, and revisit trigger |
| [`arno/planning/capability-catalog.md`](arno/planning/capability-catalog.md) | Unbuilt capability proposals (Tiers 2–4), priority order, external-capability rules, and external tool lookup |
| [`arno/planning/complexity-audit.md`](arno/planning/complexity-audit.md) | Whole-repo complexity and over-engineering audit, target simplifications, and Grounded GenUI blueprint |
| [`arno/planning/simplification-plan.md`](arno/planning/simplification-plan.md) | Step-by-step execution plan (Phases 0–5) implementing the locked-in simplified architecture |

### Diagrams (`arno/diagrams/`)

Architecture and workflow diagrams (`.png`, `.svg`, `.html`, and `.md` notes) embedded by
[`README.md`](../README.md), [`architecture.md`](arno/architecture.md), and the subsystem specs.

---

## Research surveys in `research/`

Research surveys record external landscapes and exploratory prototypes. They are not approved
product decisions unless explicitly adopted in [`plan.md`](arno/plan.md).

| Survey | Covers |
|---|---|
| [`research/agent-harness-engineering/`](research/agent-harness-engineering/) | Agent harness patterns: context, tools, loop, evals, security, and ecosystem |
| [`research/agentic-system-design/`](research/agentic-system-design/) | System composition: control patterns, multiagent topology, memory, operations, and 18 case studies |
| [`research/agent-toolkit-2026/`](research/agent-toolkit-2026/) | Open-source agent tool shortlist filtered by Arno's design steps |
| [`research/engine-research/`](research/engine-research/) | Numerical engine audit and external benchmark survey |
| [`research/monty-document-parsing/`](research/monty-document-parsing/) | Prototype evaluation of Monty for model-written statement parsers |
| [`research/personal-agent-platforms-2026-09-23.md`](research/personal-agent-platforms-2026-09-23.md) | OpenClaw and Hermes agent platforms, finance skills, and affordability prior art |
| [`research/personal-finance-agent-2026/`](research/personal-finance-agent-2026/) | Consumer finance products, India data access, research tools, and capability assessment |
| [`research/model-cost-and-gateways-2026/`](research/model-cost-and-gateways-2026/) | Model cost accounting, spend controls, Phoenix, subscription access, and gateway comparisons |

## Archived records in `archive/`

Superseded design passes, completed audits, and point-in-time run logs live under
[`archive/`](archive/). See [`archive/README.md`](archive/README.md) for the catalog.

---

## Documentation standards

These standards govern every living document under `docs/arno/` and `docs/README.md`.

### Structure and lifecycle

- **Single source of truth:** Each topic has one owning document listed in the tables above. Other
  documents link to the owner instead of repeating tables, rules, or procedures.
- **Root minimalism:** Keep `docs/arno/` root to the three core documents (`plan.md`,
  `architecture.md`, and `completion-checklist.md`). Place subsystem specifications in `specs/`,
  runbooks and policies in `operations/`, and unbuilt scope or proposals in `planning/`.
- **Timeless living documents:** Describe the system as it works today, in present tense. Avoid
  chronological addenda, dated status diaries, and pre-build future tense in living specs. Git
  history and GitHub Releases own changelogs.
- **Archive completed records:** Move superseded specifications, completed audits, acceptance logs,
  and one-off run logs to `docs/archive/<topic>-YYYY-MM/` and record them in
  [`archive/README.md`](archive/README.md).
- **Avoid volatile duplication:** Do not copy prompt text, source-code snippets that drift,
  `shortcut:` code comments, or inline test-function lists into prose documents. Link to the owning
  source file or owning specification instead.
- **Opening scope line:** Open each document with one concise sentence stating what it owns and,
  when relevant, linking to the document that owns adjacent detail.
- **Files and links:** Use lowercase, hyphenated filenames and relative Markdown links with
  descriptive link text. Give each `research/` folder a `README.md` index and a `sources.md` file.
- **Privacy hygiene:** Never commit real tailnet hostnames, real statement filenames, container IDs,
  localhost trace URLs, or personal financial figures in any document, including archived records.

### Writing style

Living documents follow the
[Google developer documentation style guide](https://developers.google.com/style). Where the
following project rules specify a choice, they take precedence over the guide:

- **Voice and person:** Address readers, operators, and maintainers in the second person (_you_).
  Use the third person (_Arno_, _the owner_) for product behavior and owner-specific constraints.
  Never use _we_, _our_, or _us_. Use the first person (_I_, _my_) only inside quoted chat examples
  such as `"How much did I spend?"`.
- **Spelling:** Use American English in prose. Preserve exact spellings from code and schemas
  (for example, `instalment`).
- **Headings:** Use sentence case. Start task headings with a bare infinitive verb and conceptual
  headings with a noun phrase. Avoid starting headings with _-ing_ words. Keep `plan.md`'s
  numbered top-level sections (`1.` through `6.`) so existing `plan.md §N` references stay stable.
- **Bold and italics:** Use `**bold**` for UI elements and run-in headings (place the ending colon
  or period inside the bold, as in `**Note:**`). Use `_underscores_` for italics, sparingly, for
  terms on first use and mathematical variables.
- **Lists and tables:** Introduce every table with a complete sentence. Leave empty cells blank
  rather than inserting dashes. End list items with periods consistently when items contain verbs.
- **Dates:** Spell out dates in prose (for example, `November 22, 2026`). Abbreviate months in
  compact table cells when space is tight (for example, `Nov 22, 2026`).
- **Abbreviations:** Spell out unfamiliar abbreviations on first use in a document, such as
  _annual percentage rate_ (_APR_). Common audience terms (`API`, `CLI`, `CSP`, `EMI`, `LLM`,
  `PWA`, `SQL`, `STT`, `UPI`) do not need expansion.
- **Line length:** Wrap prose at 100 columns. Table rows, code blocks, and long URLs may exceed
  100 columns.

### Verification

The `google-developer-style` plugin is enabled in `.claude/settings.json`. Before finishing a
documentation change:

1. Run `style_check.py` on every edited document and resolve all real findings. Known acceptable
   false positives in Arno docs are _holiday_ (Indian bank holidays), _allowlist_ used as a noun,
   _MAD_ (median absolute deviation), and _off the_ (matched against the idiom "off the
   reservation").
2. Verify that every relative Markdown link, `#anchor`, and embedded diagram path resolves.
3. Confirm that `docs/README.md` indexes every living document in `docs/arno/` and that no removed
   or archived document remains linked from active docs or code comments.
