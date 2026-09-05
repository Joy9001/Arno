# Fin-Agent

A private, self-hosted personal finance agent. All inference and financial data stay on one
local machine; only encrypted backups leave it. Reached through Signal (E2E-encrypted bot)
plus a local-only web UI bound to `127.0.0.1`.

> **Status: research / design phase.** The design is largely settled and written down under
> [`docs/`](docs/). The only code that exists is the local-model eval harness in
> [`evals/`](evals/) — the application itself has not been started. Nothing here is a
> placeholder waiting to be filled in; if a module isn't present, it hasn't been designed
> into existence yet.

## Layout

```
.
├── docs/          design docs and research notes — currently the bulk of the project
│   ├── product-picture/   canonical product spec (source of truth)
│   ├── capabilities/      feature list, per version
│   ├── tools-and-mcps/    tool servers / MCP integrations, per version
│   ├── security/          threat model + security decisions
│   ├── memory/            agent memory design + research
│   ├── models/            local model selection, eval battery, research notes
│   ├── decisions/         one file per significant tech decision + rationale
│   ├── prior-art/         landscape research
│   ├── infrastructure.md  hardware, hosting, backup, networking
│   └── initial-conversation.md   original brainstorm transcript (the "why")
└── evals/         human-judged eval harness for candidate local models
    └── results/   recorded eval runs (JSON transcripts + markdown scorecards)
```

Application code will land in `src/fin_agent/` with tests in `tests/`. Neither directory
exists yet, deliberately.

## Development

Python 3.14, managed by [uv](https://docs.astral.sh/uv/).

```bash
uv sync --dev
```

```bash
uv run ruff check . && uv run ruff format --check .
```

Optional: `uv run pre-commit install` to run ruff on commit
(config in [`.pre-commit-config.yaml`](.pre-commit-config.yaml)).
CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the same two ruff commands
on every push and PR to `main`.

### Running the eval harness

Drives each task in [`evals/eval_tasks.py`](evals/eval_tasks.py) as a multi-turn
conversation against a local Ollama model, with you as the judge. Requires Ollama running.

```bash
uv run evals/eval_finagent.py --models granite4.1:3b --buckets critical
```

Results are written to `evals/results/`. See the module docstring for all flags, and
[docs/models/evaluation-battery-2026-05-24.md](docs/models/evaluation-battery-2026-05-24.md)
for what the battery is testing and why.

## Where to start reading

| If you need… | Read |
|---|---|
| The canonical product vision | [docs/product-picture/v1.0.0.md](docs/product-picture/v1.0.0.md) |
| The backstory and reasoning behind decisions | [docs/initial-conversation.md](docs/initial-conversation.md) |
| Hardware, deployment, backup strategy | [docs/infrastructure.md](docs/infrastructure.md) |
| What the agent can do (feature list) | [docs/capabilities/v1.0.0.md](docs/capabilities/v1.0.0.md) |
| Tool & MCP server plans | [docs/tools-and-mcps/v1.0.0.md](docs/tools-and-mcps/v1.0.0.md) |
| Why specific tech was chosen | [docs/decisions/](docs/decisions/) |
| Local model selection notes | [docs/models/ollama-try-outs.md](docs/models/ollama-try-outs.md) |
| Threat model + security decisions | [docs/security/v1.0.0.md](docs/security/v1.0.0.md) |
| How memory works | [docs/memory/v1.0.0.md](docs/memory/v1.0.0.md) |

**The Product Picture is the source of truth.** If another document conflicts with it, the
Product Picture wins until the conflict is resolved.

## Conventions

- Versioned specs are `vMAJOR.MINOR.PATCH.md`. When a spec evolves, add `v1.1.0.md` rather
  than overwriting — superseded versions stay as history.
- One concern per file under `docs/decisions/`; the filename states the decision.
- Paths and filenames are lowercase and hyphenated.
- Docs cross-reference with ordinary relative Markdown links (this was an Obsidian vault;
  the wikilinks are gone).

## Stack (decided 2026-05-17)

FastAPI + PydanticAI, PostgreSQL (SQLAlchemy 2.0 + pgvector + pgcrypto), Ollama for local
inference, mem0 for personalization memory, pdfplumber + pymupdf4llm for bank statements,
pytesseract for receipts, signalbot + signal-cli-rest-api for Signal, APScheduler for
briefings, Docker Compose for deploy. No Cloudflare Tunnel in v1 — Signal is outbound-only
and the web UI is `127.0.0.1`. Rationale in
[docs/decisions/language-python-over-go.md](docs/decisions/language-python-over-go.md),
[docs/decisions/agent-framework-pydanticai.md](docs/decisions/agent-framework-pydanticai.md),
and [docs/security/v1.0.0.md](docs/security/v1.0.0.md).

## Constraints worth knowing before proposing anything

- **Hardware:** Asus TUF A15, Ryzen 5 4600H, 24 GB RAM, GTX 1650 Mobile with **4 GB VRAM**.
  Model choice is tightly constrained; 8B models need aggressive quantization or CPU offload.
  The eval battery exists because of this.
- **Privacy:** inference and financial data never leave the machine.
- **Scope:** finance only. A broader multi-skill assistant was considered and explicitly
  scoped out. Don't re-expand it — journaling, calendar, research and health are out until
  finance is built and stable.
- **Product boundaries** (no investment advice, no moralizing, no autonomous actions) are
  decisions, not implementation details to revisit. See the no-list in the Product Picture.
- Open questions for the next planning session are at the bottom of the Product Picture.
