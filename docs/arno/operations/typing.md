# Type checking

This guide owns Python type-checking policy, Pyrefly per-path strictness, diagnostic fix rules, and
the tightening roadmap behind [`plan.md`](../plan.md) §3; see
[development operations](development-operations.md) for the full verification suite.

Arno type-checks every Python file with [Pyrefly](https://pyrefly.org/en/docs/). Static types do
not replace ledger invariants, integer-paise rules, or tests (an `int` annotation cannot
distinguish rupees from paise). Instead, they catch bugs that runtime tests reach late or miss,
such as an optional database row, a `None` reaching arithmetic, or a return-type mismatch across a
library upgrade.

---

## Why Pyrefly

Arno uses Pyrefly (selected over basedpyright after both caught the same Pydantic AI return-type
mismatch in trial runs) for two reasons:

- **Native Pydantic modeling:** Models `BaseModel`, `Field`, `ConfigDict`, strict and lax fields,
  frozen models, and aliases without plugins
  ([Pydantic support](https://pyrefly.org/en/docs/pydantic/)). API payloads, agent write contracts
  in `src/arno/agent/write_models.py`, and structured outputs are all Pydantic models.
- **Per-path strictness:** Combines a `strict` preset with rule-by-rule per-path overrides,
  supporting progressive tightening without hiding existing diagnostics
  ([configuration](https://pyrefly.org/en/docs/configuration/),
  [error kinds](https://pyrefly.org/en/docs/error-kinds/)).

Pyrefly is the sole Python type checker. Do not add a second checker.

---

## Where type checking runs

Pyrefly is pinned to one exact version in [`pyproject.toml`](../../../pyproject.toml) so local
runs, Git hooks, and CI produce identical diagnostics. The following table summarizes where type
checking runs.

| Environment | Command | Behavior |
|---|---|---|
| Local development | `uv run pyrefly check` | Advisory; run before committing |
| Git pre-commit hook | `uv run pyrefly check` | Blocks commits when files matching `(\.pyi?\|pyproject\.toml\|uv\.lock)$` change; checks the whole project |
| CI (`python` job) | `uv run pyrefly check --output-format=github` | Blocks merges; posts inline diff annotations |

The pre-commit hook always checks the entire project rather than staged files alone, because a
signature edit can break callers outside the commit. A full check finishes in about two seconds.

---

## Policy

Configuration lives under `[tool.pyrefly]` in [`pyproject.toml`](../../../pyproject.toml). Pyrefly
checks `src/`, `tests/`, `evals/`, and `scripts/` with `search-path = ["src", "."]`, targeting
Python 3.14 on Linux (the shipped container platform) so Windows workstations and Linux CI runners
see the same diagnostics.

The following baseline rules apply across all checked paths:

- **Strict preset:** `preset = "strict"` enables default checks plus implicit `Any`, `@override` on
  subclass overrides, open unpacking, possibly uninitialized attributes, and unused suppressions.
- **Zero warnings:** `min-severity = "warn"` treats warnings as errors so diagnostics never
  accumulate.
- **Deprecations and untyped imports:** `deprecated` and `untyped-import` are errors. Replace
  deprecated APIs before libraries remove them, and install stub packages (such as
  `types-python-dateutil`) or document narrow suppressions for untyped libraries.
- **Single suppression syntax:** Only `# pyrefly: ignore[<code>]` suppresses a diagnostic; blanket
  `# type: ignore` comments have no effect.

Per-path sub-configurations refine the baseline. The following table lists every path scope from
strictest to most relaxed.

| Path | Extra or relaxed rules | Rationale |
|---|---|---|
| `src/arno/engine/projection.py`, `src/arno/engine/money.py`, `src/arno/india/**` | Adds `explicit-any`, `implicit-bool`, and all `src/**` rules | The pure money core reads no database rows and has no reason for `Any`. Disallowing truthiness on amounts or dates prevents `if amount:` from treating ₹0 as missing. |
| `src/**` | Adds `no-any-return`, `unannotated-return`, `unknown-return-type`, `implicitly-defined-attribute`, `implicit-reexport` | Shipped code declares explicit return types and attribute definitions. |
| `evals/`, `scripts/` | Base `strict` preset only | Maintainer tooling uses the global strict baseline without `src/**` return-annotation extras. |
| `tests/**` | Relaxes `implicit-any` | Pytest injects fixtures as unannotated parameters; annotating every fixture parameter adds no safety. |

`unnecessary-type-conversion` stays disabled globally until database rows and tool payloads have
typed schemas. At trust boundaries (sandbox calls, JSON payloads, and database rows), explicit
`int()` and `str()` conversions normalize runtime values that annotations alone do not guarantee.

Sub-configurations use first-match precedence per key, so specific paths must appear before `src/**`
in `pyproject.toml`. Keep that ordering when adding new entries.

---

## Fix diagnostics

Fix the underlying runtime behavior rather than silencing the checker.

- **Database rows that may be absent:** `Connection.fetchrow` returns `dict | None`. When SQL
  guarantees a row (`INSERT ... RETURNING` without `ON CONFLICT DO NOTHING`, an ungrouped
  aggregate, or a row already locked in the transaction), call `Connection.fetchrow_required`. When
  an ID or name comes from a user, model, HTTP request, or concurrent transaction, handle `None`
  with the module's error type (such as `ToolInputError` or an API problem response) and add a
  regression test.
- **Other optional values:** Narrow with an explicit check that raises the module's error or
  returns the appropriate fallback. Avoid `assert` in `src/` because Python strips assertions under
  `-O`; `assert` is fine in `tests/`.
- **Truthful annotations:** Annotate what the function actually returns and verify callers handle
  it. Never widen a type to `Any` or `object` to silence an error. Database rows and JSON payloads
  use `dict[str, Any]`; do not introduce `Any` elsewhere.
- **Overrides:** Decorate subclass method overrides with `@override` from `typing`.
- **Context managers:** Annotate `@contextmanager` generators as `Generator[T]` and
  `@asynccontextmanager` generators as `AsyncGenerator[T]` rather than deprecated `Iterator` forms.
- **`cast()`:** Use only to express an invariant the checker cannot infer, accompanied by a comment
  explaining why it holds.
- **Suppressions:** Restrict `# pyrefly: ignore[<code>]` with an inline explanation to incorrect
  third-party stubs or documented checker limitations. `unused-ignore` fails the build once a
  suppression is no longer needed.

---

## Tightening roadmap

Strictness tightens incrementally as internal payloads gain typed records. Each step lands as one
`pyproject.toml` edit together with its fixes; never add a baseline file to hide existing errors.

The following table lists the planned tightening steps and the diagnostic counts measured in `src/`
on November 10, 2026.

| Step | Rules | Diagnostics in `src/` | Unlocks when |
|---|---|---|---|
| 1 | `implicit-bool` across `src/arno/engine/` and `src/arno/tools/` | About 240 | Ready; addresses the same ₹0-versus-missing bug class as the money core |
| 2 | `explicit-any` and `unknown-*` for `agent/write_models.py`, `agent/presentation.py`, and `tools/projection.py` | About 80 | Tool returns move from `dict[str, Any]` to `TypedDict` models |
| 3 | `unnecessary-type-conversion` | About 70 | Database rows are typed, making conversions either required or provably redundant |
| 4 | `explicit-any` across `src/` | About 1,200 | Database rows become typed records instead of `dict[str, Any]` |

---

## Upgrade Pyrefly

Pyrefly releases frequently and newer versions can introduce additional checks. Update the version
pin in [`pyproject.toml`](../../../pyproject.toml) deliberately, run `uv lock` and
`uv run pyrefly check`, fix any new diagnostics, and complete the dependency checks in
[`AGENTS.md`](../../../AGENTS.md).
