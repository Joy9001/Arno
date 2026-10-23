# Fin-Agent

A self-hosted, open-source finance agent that holds your whole financial picture and answers any
question about it — including whether you can afford something, and what it would cost you.

> **Status: full-product plan written 2026-10-07, revised 2026-10-08; no application code yet.** The plan is
> [`docs/fin-agent/plan.md`](docs/fin-agent/plan.md) — read that first, it's the only authoritative document. The May 2026 design docs are in
> [`docs/archive/`](docs/archive/) and are not authoritative. No application code exists yet.

## Layout

```
.
├── docs/            all documentation — see docs/README.md
│   ├── fin-agent/   the project: plan, design, platform, harness, import, parked
│   ├── research/    independent surveys (agent-harness-engineering)
│   └── archive/     superseded May 2026 design docs — historical only
└── evals/           human-judged eval harness for local models (+ recorded runs)
```

[`docs/README.md`](docs/README.md) indexes everything. `docs/fin-agent/plan.md` owns decisions;
other project docs own detail. If they disagree, the plan wins.

Application code will land in `src/fin_agent/`, the web app in `web/`, tests in `tests/`.

## Development

Python 3.14, managed by [uv](https://docs.astral.sh/uv/).

```bash
uv sync --dev
```

```bash
uv run ruff check . && uv run ruff format --check .
```

Optional: `uv run pre-commit install` to run ruff on commit.
CI runs the same two ruff commands on every push and PR to `main`.

## Configuration

Each LLM role (chat, vision, speech-to-text) is three env vars. Local and cloud are the same code
path — any OpenAI-compatible endpoint works.

```bash
CHAT_BASE_URL=http://localhost:11434/v1  # or a cloud provider's /v1
CHAT_API_KEY=ollama                      # or a real key
CHAT_MODEL=qwen3:4b                      # or a cloud model id
```

`VISION_*` and `STT_*` follow the same pattern. Details in
[`docs/fin-agent/platform.md`](docs/fin-agent/platform.md).

## Eval harness

Drives each task in [`evals/eval_tasks.py`](evals/eval_tasks.py) as a multi-turn
conversation against a local Ollama model, with you as the judge.

```bash
uv run evals/eval_finagent.py --models granite4.1:3b --buckets critical
```

Results land in `evals/results/`. Predates the current plan but is stack-independent and
still useful for checking whether a given local model can hold a tool-calling conversation.

## The one rule

**The model decides what to compute; code computes it.** The model reasons, models scenarios,
and explains tradeoffs, but every number it states comes from a tool return or a script it ran in
a sandbox, never from its own head. Rationale in
[`docs/fin-agent/plan.md`](docs/fin-agent/plan.md#4-architecture).
