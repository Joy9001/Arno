# Archive

Superseded specifications, completed audits, and point-in-time execution logs live here for
historical context. **Nothing in this directory is authoritative.** Never cite files in `archive/`
as current product decisions or active behavior.

## May 2026 planning pass

Archived September 7, 2026. The original design pass drifted out of sync across nine files and
described an earlier Go backend that was never built (`capabilities/v1.0.0.md`). Planning restarted
under `docs/arno/`.

Two files remain useful as historical context only:

- [`prior-art/research-2026-05-24.md`](prior-art/research-2026-05-24.md): External landscape notes
  on LLM arithmetic limits and rules-then-ML-then-LLM categorization ordering (predates June 2026
  consumer launches).
- [`initial-conversation.md`](initial-conversation.md): Original problem statement behind the
  project.

## Web redesign records (`web-2026-11/`)

Archived November 9, 2026, after the agent-first web redesign replaced the earlier UI design. Holds
planning, audit, and migration records for the assistant-ui adoption, quote contract, runtime
review, readiness evidence, and superseded screen designs. Replaced by
[`DESIGN.md`](../../DESIGN.md) and [`docs/arno/specs/web-interface.md`](../arno/specs/web-interface.md).

## Manual E2E catalog and run log (`e2e-2026-11/`)

Archived November 22, 2026. Holds the November 5–10, 2026 pre-redesign manual UI journey catalog
(`e2e-user-journeys.md`) and raw interactive debugging log (`e2e-run-log.md`). Replaced by the
automated E2E driver and browser journeys in [`tests/e2e/README.md`](../../tests/e2e/README.md),
the financial invariant checker in [`tests/invariants.py`](../../tests/invariants.py), and
[`docs/arno/completion-checklist.md`](../arno/completion-checklist.md).

## Engine redesign blueprint (`engine-2026-11/`)

Archived November 22, 2026. Holds `engine-blueprint.md`, the November 13, 2026 pre-redesign
proposal and build order written against commit `4d37028`. Every shipped calculation, model switch,
and backtest method now lives in
[`docs/arno/specs/data-and-engine.md`](../arno/specs/data-and-engine.md).

## Completed audits and acceptance logs (`records-2026-11/`)

Archived November 22, 2026. Point-in-time records whose findings and fixes are already merged on
`main`:

- `system-design-walk-2026-10.md`: Original 10-step October–November 2026 design walk, prototype
  probe test tables, and early review notes. Replaced by
  [`docs/arno/architecture.md`](../arno/architecture.md).
- `release-hardening.md`: November 10–11, 2026 four-wave `v0.2.0` hardening audit and commit ledger.
- `cost-tracking-acceptance.md`: November 12, 2026 synthetic pre-deploy acceptance log for cost
  tracking and the pinned Phoenix image.
- `import-spike-results.md`: Generated Milestone 21 parser spike output (bank summary now lives in
  [`docs/arno/specs/import.md`](../arno/specs/import.md#bank-format-findings)).
- `test-suite-audit.md`: November 22, 2026 test-suite performance and quality audit (all 20 findings
  resolved).
