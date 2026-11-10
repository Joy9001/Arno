# Type checking

This file holds the detail behind `plan.md` §3: the Python type-checking tool and its enforcement
policy. The `plan.md` file owns decisions, and this file owns their shape. If the two disagree, the
plan wins and this file is stale.

Arno checks every Python file with [Pyrefly](https://pyrefly.org/en/docs/). Types don't
replace the ledger invariants, the paise rules, or the tests: an `int` annotation can't tell
rupees from paise. They catch the bugs those checks reach late or never, such as a database row
that can be missing, a `None` that reaches arithmetic, or a wrong return type that hides a
version mismatch.

## Why Pyrefly

Pyrefly was chosen on Nov 10, 2026, over basedpyright. Both caught the same Pydantic AI
return-type mismatch in a trial run, so neither won on correctness alone. Pyrefly fits this stack
better for two reasons:

- **Pydantic awareness:** it models `BaseModel`, `Field`, `ConfigDict`, strict and lax fields,
  frozen models, and aliases without a plugin ([Pydantic support](https://pyrefly.org/en/docs/pydantic/)).
  The API inputs, the agent's write contracts in `agent/write_models.py`, and its structured
  outputs are Pydantic models.
- **Configurable strictness:** a `strict` preset plus rule-by-rule control for each path, including
  the `Any` checks the progressive plan below needs ([configuration](https://pyrefly.org/en/docs/configuration/),
  [error kinds](https://pyrefly.org/en/docs/error-kinds/)).

Pyrefly is the only Python type checker. Don't add a second one.

## Where it runs

Pyrefly is a development dependency pinned to one exact version in `pyproject.toml`, so the local
check, the git hook, and CI always agree. The following table shows where it runs:

| Where | Command | Blocks |
|---|---|---|
| Local | `uv run pyrefly check` | Nothing; run it before committing |
| Git hook (every commit) | `uv run pyrefly check` | The commit |
| CI, Python job | `uv run pyrefly check --output-format=github` | The merge; errors show as annotations on the diff |

The hook checks the whole project, not only the staged files, because a changed signature breaks
callers the commit didn't touch. A full run takes about two seconds.

## The policy

The configuration lives in `[tool.pyrefly]` in `pyproject.toml`. It checks `src/`, `tests/`,
`evals/`, and `scripts/` for Python 3.14 on Linux, the platform the image ships on, so a Windows
development machine and the CI runner see the same diagnostics.

These rules apply everywhere:

- **Strict preset:** `preset = "strict"` turns on the default checks plus implicit `Any`,
  `@override` on every override, open unpacking, possibly uninitialized attributes, and unused
  suppressions.
- **Zero warnings:** `min-severity = "warn"` makes a warning fail the check like an error. A warning
  that nobody has to fix only accumulates.
- **Deprecations and stubs:** `deprecated` and `untyped-import` are errors. A deprecated API gets
  replaced before its library removes it. A library without types gets stubs (for example,
  `types-python-dateutil`) or a narrow, explained suppression.
- **One suppression syntax:** only `# pyrefly: ignore[<code>]` suppresses. A blanket
  `# type: ignore` has no effect.

Each area then adds or relaxes rules for its own code. The following table shows the rules by path,
from strictest to most relaxed:

| Path | Extra rules | Why |
|---|---|---|
| `src/arno/engine/projection.py`, `src/arno/engine/money.py`, `src/arno/india/` | `explicit-any`, `implicit-bool`, and everything `src/` has | The pure money core reads no database rows, so it has no excuse for `Any`. No truthiness on amounts or dates: `if amount:` treats a real ₹0 like a missing value |
| `src/` | `no-any-return`, `unannotated-return`, `unknown-return-type`, `implicitly-defined-attribute`, `implicit-reexport` | Shipped code states what it returns and where its attributes come from |
| `tests/` | `implicit-any` off | Pytest passes fixtures as unannotated parameters; annotating each one adds no safety |

One rule is off everywhere for now. `unnecessary-type-conversion` flags `int()` or `str()` on a
value the checker already knows is an `int` or a `str`. At the trust boundaries (sandbox helpers,
JSON, database rows), those conversions normalize values that an annotation alone doesn't
guarantee. Removing them to satisfy the checker could change money handling, so the rule waits for
typed payloads.

A sub-config takes the first match for each key, so the specific paths come before `src/**` in
`pyproject.toml`. Keep that order when adding one.

## How to fix a diagnostic

Fix the code, not the report. A type error is often a real bug, so ask first what happens at run
time.

- **A row that may be missing:** `Connection.fetchrow` returns `dict | None`. When the SQL always
  yields a row (`INSERT ... RETURNING` without `ON CONFLICT DO NOTHING`, an aggregate with no
  `GROUP BY`, or a row this transaction already locked), use `Connection.fetchrow_required`. When
  the id or name came from the user, the model, a request, or another transaction, the missing row
  is a real case: handle `None` with the module's own error, such as `ToolInputError` or the API's
  problem response, and test it.
- **Other optional values:** narrow with a real check that raises the module's error or returns
  the right answer. Don't use `assert` in `src/`: Python drops it under `-O`, and its message
  doesn't help the user. Tests can use `assert`.
- **Truthful annotations:** annotate what the code really returns, then check that callers still
  do the right thing. Don't widen a type to `Any` or `object` to make an error go away. A database
  row or JSON payload as `dict[str, Any]` is the current house pattern; don't add new `Any`
  elsewhere.
- **Overrides:** add `@override` from `typing`.
- **Context managers:** annotate a `@contextmanager` generator as `Generator[T]`, and an
  `@asynccontextmanager` generator as `AsyncGenerator[T]`. The `Iterator` forms are deprecated.
- **`cast()`:** only to state a fact the checker can't see, with a comment that says why.
- **Suppressions:** only for a wrong third-party stub or a named checker limitation, written as
  `# pyrefly: ignore[<code>]` with a trailing reason. `unused-ignore` fails the check once the
  suppression stops being needed.

## Tightening over time

The policy tightens as the payloads get real types. Each step is one change to `pyproject.toml`
plus the fixes it needs. Don't use a baseline file to hide existing errors.

The following table lists the next steps and how many diagnostics each one adds to `src/`, measured
Nov 10, 2026, after the money core was typed:

| Step | Rules | Diagnostics in `src/` | Unlocks when |
|---|---|---|---|
| 1 | `implicit-bool` across `src/arno/engine/` and `src/arno/tools/` | About 240 | Now; the same ₹0-versus-missing bug class as the money core |
| 2 | `explicit-any` and `unknown-*` for `agent/write_models.py`, `agent/presentation.py`, and `tools/projection.py` | About 80 | Tool returns are `TypedDict`s instead of `dict[str, Any]` |
| 3 | `unnecessary-type-conversion` | About 70 | Database rows are typed, so a conversion is either needed or provably redundant |
| 4 | `explicit-any` across `src/` | About 1,200 | Rows are typed records instead of `dict[str, Any]` |

## Upgrading Pyrefly

Pyrefly ships often, and a new version can add checks. Bump the pin in `pyproject.toml`
deliberately, run `uv lock` and `uv run pyrefly check`, and fix what the new version finds, as with
any dependency change in the definition of done in `AGENTS.md`.
