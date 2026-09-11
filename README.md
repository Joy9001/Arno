# Fin-Agent

A chat agent that tracks what I spend and tells me whether I can afford something.

> **Status: planning restarted 2026-09-07.** The plan is [`docs/fin-agent/plan.md`](docs/fin-agent/plan.md) —
> read that first, it's the only authoritative document. The May 2026 design docs are in
> [`docs/archive/`](docs/archive/) and are not authoritative. No application code exists yet.

## Layout

```
.
├── docs/            all documentation — see docs/README.md
│   ├── fin-agent/   the project: plan, design, import, parked
│   ├── research/    independent surveys (agent-harness-engineering)
│   └── archive/     superseded May 2026 design docs — historical only
└── evals/           human-judged eval harness for local models (+ recorded runs)
```

[`docs/README.md`](docs/README.md) indexes everything. `docs/fin-agent/plan.md` owns decisions;
other project docs own detail. If they disagree, the plan wins.

Application code will land in `src/fin_agent/`, tests in `tests/`.

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

The LLM provider is three env vars. Local and cloud are the same code path — Ollama serves
an OpenAI-compatible API.

```bash
LLM_BASE_URL=http://localhost:11434/v1   # or a cloud provider's /v1
LLM_API_KEY=ollama                       # or a real key
LLM_MODEL=qwen2.5:3b                     # or a cloud model id
```

## Eval harness

Drives each task in [`evals/eval_tasks.py`](evals/eval_tasks.py) as a multi-turn
conversation against a local Ollama model, with you as the judge.

```bash
uv run evals/eval_finagent.py --models granite4.1:3b --buckets critical
```

Results land in `evals/results/`. Predates the current plan but is stack-independent and
still useful for checking whether a given local model can hold a tool-calling conversation.

## The one rule

**The LLM never does arithmetic.** Every number is computed by a Python function. The model
parses language and picks tools; it does not add, subtract, or project. Rationale in
[`docs/fin-agent/plan.md`](docs/fin-agent/plan.md#4-architecture).
