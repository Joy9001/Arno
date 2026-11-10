# Arno

A single-user, self-hosted personal finance agent (India-first, amounts in integer paise). One
FastAPI process + one Postgres 18 database, a PydanticAI agent over plain-Python tools, and a Vite
web app that the API serves from `web/dist`. [`README.md`](README.md) has install and operations;
this file holds only what you can't read off the code.

## Where the truth lives

- [`docs/arno/plan.md`](docs/arno/plan.md) owns decisions. If a satellite doc
  (`design.md`, `tools.md`, `platform.md`, `harness.md`, `import.md`) disagrees, the plan wins and
  the satellite is stale. Read the matching satellite before changing its area.
- `docs/archive/` is superseded. Never cite it as a decision. `docs/research/` describes the
  outside world, not this project.
- Never hand-edit the README blocks between `<!-- eval-scorecard -->`, `<!-- backtest -->`, and
  `<!-- hackerrank -->` markers. `arno eval`, `backtest`, and `hackerrank` write them.

## Architecture rules (plan.md §4) that code must not break

1. **The model decides what to compute; code computes it.** Every number the agent states must come
   from a tool return or a `run_code` script's output. `agent/number_check.py` flags untraced
   numbers. Affordability verdicts come only from the engine, never from the model.
2. **Untrusted text never makes a lasting change unseen.** Statement text and payee/UPI-note fields
   are data, not instructions. Writes that last or reach wide (memory, setup, many rows) need a
   confirm card in any conversation that has read such text; everyday writes stay instant and
   undoable. Don't add a lasting write path that skips the card.
3. **Tools are plain Python over the database, free of PydanticAI types** (`tools/`, `ledger/`).
   Agent, REST API, and any future MCP server wrap the same functions. Adapters belong in `agent/`.
4. **The database is the whole state.** No state in files or memory that a `pg_dump` wouldn't carry.
5. `engine/projection.py` is pure: no clock, no database, no model. Pass the date and data in.
6. Money is integer paise everywhere below the display layer. The agent never moves money and never
   handles passwords (a statement password goes on the import card, not in chat).

## Database

- Schema changes are a new numbered file in `src/arno/db/migrations/` (`NNNN_name.sql`, plain
  SQL, one transaction each). A migration's checksum is recorded when it runs: **never edit an
  applied migration**, add the next one.
- `src/arno/db/vendor/pgledger/` is vendored unmodified at a pinned commit. Don't edit it; its
  licence notice is in `THIRD_PARTY_NOTICES.md`.
- Three roles: `app` (owns schema), `agent_ro` (agent's `run_query`: SELECT only on ledger-side
  tables and views, no auth tables, 2 s timeout), `backup`. A new table the agent should see needs a
  grant in a migration; auth and secrets tables must stay out of `agent_ro`.

## Commands

Python 3.14 via `uv`; web uses pnpm 11 (Node 24). Use `uv run` and `pnpm --dir web`, never bare
`python`/`pip`/`npm`.

```bash
docker compose up -d db                       # dev Postgres on 127.0.0.1:5433
uv run arno migrate && uv run arno serve
pnpm --dir web dev                            # Vite, proxies /api to the API
pnpm --dir web build                          # what the API serves; web/dist is gitignored
```

Tests need a scratch database whose name ends in `_test`; `conftest.py` refuses anything else. Set
`DATABASE_URL` (e.g. `postgresql://app:<pw>@127.0.0.1:5433/arno_test`), `AGENT_RO_PASSWORD`,
and `BACKUP_PASSWORD` first. The suite blocks non-loopback sockets and DNS and points every model
role at a closed port, so tests can never call a model. xdist gives each worker its own database.

## Definition of done

Scale the check to the change; CI (`.github/workflows/ci.yml`) and `.pre-commit-config.yaml` run the
full set.

- Iterating: `uv run pytest tests/test_<area>.py -q -n 0`.
- Any Python change: `uv run ruff check . && uv run ruff format --check .` (line length 100) and
  `uv run pyrefly check` (zero errors and zero warnings; how to fix one without hiding it is in
  [`typing.md`](docs/arno/typing.md)), then the affected test files. Run the whole
  `uv run pytest -q` when you touch `ledger/`, `db/`, `tools/`, `agent/`, or `api/`, since they are
  shared.
- `web/` change: `pnpm --dir web lint && pnpm --dir web typecheck`; before finishing, `pnpm --dir web
  build`. A change to answer or result cards in `web/src/chat/` also needs
  `pnpm --dir web test:answers` (it reads fixtures from `tests/fixtures/presentation_contract.py`).
- `ledger/`, `tools/`, `importers/`, or `engine/` change: after `uv run arno demo seed`, run
  `uv run python -m tests.invariants` on a scratch `_test` database and keep the invariant tests
  green.
- Real journeys: [`tests/e2e/`](tests/e2e/README.md) drives a live API process with the real model
  and the real HTTP the web app sends. Use it to check a change by hand.
- Dependency change: `uv lock --check`, `uv run python scripts/license_audit.py`, and
  `THIRD_PARTY_NOTICES.md` if a direct dependency is added. Only permissive licences ship; no GPL,
  AGPL, SSPL, or Elastic code in the image. `pydantic-ai-harness` is 0.x: keep it pinned.
- Release: the git tag must equal the `pyproject.toml` version, and `compose.yaml`'s default image
  tags (`api` and `backup`) must carry the same version (`tests/test_release.py` fails otherwise).
- Sandbox helper docstrings: keep them in the existing simple form. `run_code` parses each one into
  a stub, so a docstring that reads like a call example can break positional calls.

## Agent behavior changes

- The prompt in `agent/instructions.py` is a short map. Each fixed rule in it has an eval case in
  `evals/cases.py`. Change a rule, change its case.
- Before designing a new agent mechanism, check the current PydanticAI and `pydantic-ai-harness`
  docs and source for a built-in (the `pydantic-*` skills in `.agents/skills/` cover this). Likewise
  read assistant-ui's docs before any chat UI work (the `assistant-ui` skill in `.claude/skills/`).
- `arno eval` is the only path that enables real model requests and it spends money. Don't run
  it unless asked; `--dry-run` lists cases for free.

## assistant-ui

This project uses assistant-ui for chat interfaces.

Documentation: https://www.assistant-ui.com/llms-full.txt

Key patterns:
- Use AssistantRuntimeProvider at the app root
- Thread component for full chat interface
- AssistantModal for floating chat widget
- useChatRuntime hook with AI SDK transport

## Local-only data

`private/`, `statements/`, `.env`, `evals/results/`, and the HackerRank dataset are real or
unlicensed data and are gitignored. Don't commit, quote, or paste their contents into docs, tests,
or fixtures; use the synthetic demo persona (`src/arno/demo/`) and `tests/fixtures/` instead.

## Working conventions

- Commit straight to `main`; no feature branches or PRs unless asked. Conventional Commits,
  imperative mood (`feat(import): …`, `fix(chat): …`), no co-author trailer.
- Fix the foundation, not the symptom. No stopgap wrappers or interim layers; each layer is built to
  its final shape, in dependency order.
- Docs follow the house style in [`docs/README.md`](docs/README.md#conventions): American spelling,
  sentence-case headings, prose wrapped at 100 columns, `plan.md` §N references. New decisions go
  in `plan.md`; anything needing a table or schema goes in a satellite doc with a link.
