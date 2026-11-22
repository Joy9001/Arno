# Arno test suite audit

Read-only audit of Arno's automated and manual test surface (`tests/`, `web/scripts/`, `evals/`,
`.github/workflows/ci.yml`, `.pre-commit-config.yaml`) against `src/arno/` and `web/src/`.

- **Date:** 2026-11-22
- **Mode:** Ponytail Ultra (root-cause fixes only; no cheap patches or stopgaps)
- **Inventory:** 158 Python files in `tests/` (60,278 lines; 1,692 `test_*` functions; 1,912 collected
  items) vs. 152 Python files in `src/arno/` (56,364 lines; 1.07× test-to-source ratio), plus
  `web/scripts/answer-contract.test.mjs` (808 lines) vs. `web/src/` (134 files; 40,629 lines).

---

## 1. Executive summary and measured baseline

**Status.** Complete. Python passed 2,014 tests after the independent review fixes.

The ledger double-entry math, projection kernel, and statement-import verification logic have strong
core coverage. The failure modes are concentrated in **false-confidence assertions**, **untested
security/upgrade/write-atomicity boundaries**, **cross-layer suites that never run in CI**, **29
wave-dumped `test_audit_*.py` files (25% of the suite)**, and **test harness architecture that burns
10m 53s of wall time and pins a 6-core laptop CPU at 98% / 94°C**.

### Live measurements (isolated ephemeral Postgres 18.6, zero impact on live DB)

**Status.** Rechecked. The original remediation run took 8m 12s. See §7 for the latest measurement.

| Benchmark | Result | Measured time | Key observation |
|---|---|---:|---|
| `tests/test_license_allowlist.py -n 0` (1 pure test, `<5ms` body) | `1 passed` | **18.08s wall** (`6.79s` setup) | Pure Python test pays full `migrated` + `demo_snapshot` + 42-table `TRUNCATE`. |
| `tests/test_engine_money.py -n 0` (56 pure math tests, `<5ms` each) | `56 passed` | **18.89s wall** (`12.61s` pytest) | `4.65s` initial setup + `0.14–0.20s` per-test DB truncate setup; >97% wasted DB churn. |
| Full default suite (`uv run pytest -q --durations=50 -rs`, `-n 8`) | `1907 passed, 5 skipped, 2 warnings` | **653.89s (10m 53s)** (`660.19s` wall) | Single `@cluster` worker consumes **593.3s** (91% of total wall time); two duplicate ACL tests alone burn **412.7s** (63%). |
| `pnpm --dir web test:answers` (default Node) | **FAILED (0 ran)** | 7.3s – 23.9s | Crashed on Rolldown OOM (`23.9s`), then on SSR import (`Cannot find module './descending.js'`). |
| `NODE_OPTIONS=--max-old-space-size=2048 pnpm --dir web test:answers` | **34/35 passed, 1 failed** | — | SSR imports pass; `export scope` test died with Python `MemoryError` inside subprocess import of `arno.api`. |

---

## 2. Audit checklist: what was checked vs. what was skipped

**Status.** Complete. The audit findings have regression, fixture, or workflow coverage.

Mapped directly against every dimension requested:

| Dimension | Checked | Findings summary |
|---|---|---|
| **Over-engineering** | Yes | Hand-rolled 100-line SQL snapshot engine in `conftest.py` re-implementing `pg_dump` ordering; 1,842-line dead `web/scripts/assistant-ui-fixture/server.mjs` never started by any test; 2,059-line manual E2E harness outside CI; `answer-contract.test.mjs` booting full Vite production config (`tailwindcss` + `VitePWA` + dep optimizer) just for `ssrLoadModule`. |
| **Premature efforts** | Yes | `test_phoenix_cost_contract.py` (1,210 lines) and `test_image_runtime.py` permanently skip in CI; `test_agent_compaction.py` tests `pydantic-ai-harness` classes inline without calling Arno; dead `next_backoff` ("Visible for tests") and `_add_months_public`. |
| **Performance** | Yes (measured live) | Measured full `--durations=50`, pure-test cold start, per-test DB truncate cost, and live CPU/RAM per worker + Postgres. Found **653.89s** runtime dominated by `@cluster` dumps, autouse DB resets, 163×183 seed-rule inserts, 366 EC P-256 keygens, default 16,384-run Monte Carlo calls, 280-op invariant loops, and a 25.06s teardown hang. |
| **Bottlenecks & CPU burn** | Yes (measured live) | Diagnosed 98% / 94°C Ryzen 5 4600H burn: `-n 8` on 6 physical cores + `234%` Postgres CPU + unbounded OpenBLAS threads (`MAX_THREADS=24`, `OPENBLAS_NUM_THREADS=None`) + 8 parallel `demo_snapshot` seeds + 412.7s `demo_ledger`+`demo_seed` double-dump on `@cluster`. |
| **Quality & mutation resistance** | Yes | Full AST scan of all 1,692 test functions (0-assert tests, `pytest.raises(Exception)`, `try/except`, `sleep()`, skips) + manual review of assertions across all 158 files. Found 4 discarded `check_ledger()` calls, 8+ vacuous/tautological tests, and 2 live `UserWarning`s in `test_audit_agent.py`. *(Verified clean: 0 direct `os.environ` leaks, 0 `try/except` swallowing in tests.)* |
| **Coverage (backend, frontend, migrations, evals, scripts)** | Yes | Mapped all 152 `src/arno/` modules, line-by-line `src/arno/tools/write.py` (1,698 lines), `web/src/` (134 files / 40,629 lines — only 15% statically imported, 0% interactive), `0032`–`0044` migrations, `evals/` graders, `scripts/license_audit.py`, and CI/pre-commit blind spots. |
| **Broken windows (Pragmatic Programmer)** | Yes | 29 `test_audit_*.py` wave-dump files (14,940 lines = 25% of suite) + `test_typing_tya/tyb.py`; 20+ copy-pasted fixture helpers; cross-test-file private helper imports; misleading file/test names (`test_coverage.py`, `backup_role_can_dump`); stale comments/scratch notes; leftover `.vite/deps_temp_*` dirs. |
| **Logical gaps** | Yes | Real production bugs uncovered via test gaps: `number_check` percent/count false negatives & ±500 rupee slack; `backup_restore._database_has_tables` fail-open `DROP DATABASE`; `tools/write.py` committing payees before movement validation; `tools/write.py` `undo()` swallowing `CommitOutcomeUnknownError` and falling back cross-conversation; `web/src/auth.ts` `reverifiedUntil` surviving logout; `0032` migration aborting on legal pre-0032 rows; `evals/graders.py` substring & exfiltration bugs; fail-open `scripts/license_audit.py`. |
| **Missing normal & edge cases** | Yes | Cataloged high-risk untested paths across Auth, Backup, Ledger/Write tools, Migrations, Engine, Importers/Capture, Agent/Number-Check, Jobs/Watch/Notifications, `evals/`, and Frontend (Section 6). |
| **Everything else (CI parity, portability, global state, concurrency)** | Yes | Audited all 19 `pytest.skip`/`skipif` sites (4 permanently skipped in CI), 9 hardcoded `DROP DATABASE ... WITH (FORCE)` collision hazards, `-n 0` unisolated temp dirs (`ARNO_TASKS_DIR`/`ARNO_PRE_MIGRATION_DIR`), `get_settings` cache lifetime, Windows Git Bash dependency in `.pre-commit-config.yaml`, and flaky timing/wall-clock assertions. |

### Explicitly skipped (narrow residual scope)

**Status.** Historical scope. Seeded invariants, DOM interactions, and release contracts ran afterward.

1. **Line/branch percentage tooling (`pytest-cov` / `coverage.py`):** Not installed in `pyproject.toml`; adding a dependency was out of scope for a read-only audit. Coverage gaps were found by source-to-test tracing rather than line-hit instrumentation.
2. **Real-model execution (`arno eval`, live `tests/e2e/driver.py`, live Playwright `browser_journey.py`):** Spend money / require a live model and Playwright browser; audited statically against `web/src/` and `evals/`.
3. **Historical migrations `0001`–`0031`:** Populated-data upgrade audit focused on `0032`–`0044`; earlier migrations may share the same empty-DB-only assumption.
4. **Live execution of `python -m tests.invariants` on a seeded DB:** Verified via `test_invariants.py` (which ran and passed inside the live pytest run), not separately via CLI.
5. **Interactive React DOM mounting:** `web/src/` components were audited statically against `answer-contract.test.mjs` and `browser_journey.py` without running a JS DOM harness.

---

## 3. Root-cause analysis: why the suite takes 10m 53s and burns the CPU at 94°C

**Status.** Complete. Pure tests bypass Postgres. Database cases clone templates with static seed data.

### Top measured slowest phases (`uv run pytest -q --durations=50`)

**Status.** Rechecked. The two backtest checks took 14.59s and 6.35s in isolation.

| Rank | Duration | Phase | Test | Root cause |
|---:|---:|---|---|---|
| 1 | **268.35s** | call | `test_audit_platform2.py::test_an_older_dump_without_grants_still_restores@cluster` | Takes `demo_ledger` + dumps **both `public` and `demo_seed` schemas** (~84 tables) via `pg_dump`, restores & verifies all rows just to assert `has_table_privilege`! |
| 2 | **144.37s** | call | `test_audit_platform2.py::test_a_restored_dump_keeps_the_agents_read_boundary@cluster` | Same root cause as #1 (and duplicates `test_l8_ship.py:258` + `test_audit_platform.py:433`). **#1 + #2 = 412.72s (63% of total wall time).** |
| 3 | **127.90s** | call | `test_invariants.py::test_random_operations_keep_the_invariants` | Calls full `check_ledger()` (~25 full-table queries) after every single one of 200 random ops (~5,000 serial queries). |
| 4 | **119.22s** | call | `test_engine_backtest.py::test_the_persona_backtest_scores_each_forecaster_against_the_baselines` | Multi-persona uncached generation + full multi-forecaster backtest loops. |
| 5 | **91.71s** | call | `test_audit_platform.py::test_restore_from_an_extracted_dump_directory_into_a_fresh_database@cluster` | Same `demo_ledger` + `demo_seed` double-dump on `@cluster`. |
| 6 | **87.58s** | call | `test_engine_backtest.py::test_outcomes_leave_out_income_the_base_case_does_not_count` | Uncached persona generation + full origin scoring. |
| 7 | **44.00s** | call | `test_engine_projection.py::test_safe_to_spend_matches_the_affordability_boundary_across_personas` | 4 personas × 2 seeds × (`project` + 2× `afford` lever searches) ≈ 150–300 kernel walks in one test. |
| 8 | **38.60s** | call | `test_evals.py::test_scenario_grader_accepts_engine_figures_and_rejects_guesses` | Runs full production `runs=16_384` scenarios inside grader unit test. |
| 9 | **35.39s** | call | `test_backup_restore.py::test_pg_dump_restore_round_trip@cluster` | Full `pg_dump`/`pg_restore` serialized on `@cluster`. |
| 10 | **33.83s** | call | `test_invariants.py::test_random_operations_without_undo_keep_the_invariants` | 80 ops × ~25 full-ledger queries (~2,000 serial queries). |
| 11 | **33.34s** | call | `test_projection_tool.py::test_eval_grader_accepts_the_project_and_affordability_checks` | Calls `project()` with production default `runs=16_384` (including hypothetical lever search). |
| 12 | **25.47s** | call | `test_projection_tool.py::test_get_api_projection_defaults_and_returns_the_emi_tenure` | Default `runs=16_384` via HTTP just to check JSON fields. |
| 13 | **25.06s** | **teardown** | `test_import_recovery.py::test_a_retry_while_a_reply_is_running_refuses_and_leaves_the_run_marker` | Inserts fake `ChatTurn` (`:449-456`) that never finishes; FastAPI lifespan shutdown waits the full **25s drain timeout** on teardown! |
| 14–19 | **22.97s–15.42s** | call/setup | `test_demo_persona`, `test_audit_platform` (6 anchor reseeds = 46s), `test_projection_tool` (`runs=16_384`), `test_import_undo_race` (`ROUNDS=20`), `test_agent_card_undo` (15.98s setup) | Repeated `seed_demo(reset=True)`, unparameterized 16,384 runs, and contended DB setup. |

### Why the CPU pins at 98% / 94°C on Ryzen 5 4600H

**Status.** Complete. Four pytest workers and one BLAS thread per worker. Thermals remain unmeasured.

1. **Wrong concurrency model for the hardware (`pyproject.toml:76-79`):** The comment assumes a "12-core dev box" and hardcodes `addopts = "-n 8 --dist loadgroup"`. Ryzen 5 4600H has **6 physical cores / 12 SMT threads** (45W laptop TDP). Live measurement showed **8 pytest Python workers** (125–211 CPU-s and 340–440 MB RAM each) + **Postgres (`arno-audit-pg`) at 234% CPU (2.3 cores, 944 MiB RAM)** = **>10.3 active cores of compute on 6 physical cores**, driving all-core 3.8 GHz boost straight to the 94–95°C thermal ceiling for 11 minutes.
2. **Unbounded OpenBLAS thread multiplication:** NumPy links `scipy-openblas` (`MAX_THREADS=24`), and neither `pyproject.toml` nor `tests/conftest.py` sets `OPENBLAS_NUM_THREADS=1` / `OMP_NUM_THREADS=1`. Multiple xdist workers running 16,384-scenario NumPy matrices simultaneously multiply thread contention across the 6 cores.
3. **Structural wasted work multiplied across all 8 workers:**
   - **8× `migrated` serialized behind one `cluster_lock()`** (`conftest.py:228-238,306-322`) + **8× full `demo_snapshot` seeds**.
   - **1,912 × 42-table `TRUNCATE … RESTART IDENTITY CASCADE`** (`conftest.py:325-330` autouse), costing `0.14–0.20s` per test even on 342 pure-Python tests (`test_engine_money.py` spends >97% of its time in Postgres!).
   - **~30,000 sequential `INSERT … ON CONFLICT` seed-rule writes + ~366 EC P-256 keygens:** `_reset` truncates static catalog tables (`categories`, `category_rules`, `vapid_keys`, `job_schedules`), forcing every `TestClient` boot (183 `authed_client` + 51 `client` tests) to re-run `ensure_seed_rules` (163 sequential inserts) and `ensure_vapid_keys` (EC P-256 keygen), followed by a full WebAuthn registration ceremony in `authed_client`.
   - **`demo_seed` snapshot schema dumped by `pg_dump` tests:** Because `demo_snapshot` stores 42 copied tables in schema `demo_seed` inside the worker's test database, every `pg_dump` test without `--schema=public` dumps and restores twice the database.

---

## 4. Prioritized findings (with root-cause fixes)

**Status.** Complete. All 20 findings have code, test, or workflow fixes in place.

### Must fix (bugs, false-confidence checks, security/data-loss blind spots, broken CI gates)

**Status.** Complete. Regression tests exercise atomic writes, role boundaries, upgrades, and CI gates.

1. **Four ledger invariant checks silently discard their result** (`tests/test_watch_recovery.py:100,111,129,214`)
   - **Problem:** Bare `await check_ledger(ledger)` statements throw away the returned `list[Violation]`. `check_ledger` never raises.
   - **Root-cause fix:** Assert `assert await check_ledger(ledger) == []` (or introduce an `assert_ledger_clean(conn)` helper used everywhere, replacing both bare calls and per-file `_clean()` wrappers).

2. **`number_check` passes fabricated percentages and 2× wrong rupee amounts** (`src/arno/agent/number_check.py:156-163,325-336`, `tests/test_agent_number_check.py:120,330-334`)
   - **Problem:** Verified live: `check("42% went to food.", {"rows": 42}, kinds=("money", "percent"))` returns `[]` because `percent` claims match bare counts/rupees; `check("Rent was ₹1000.", {"rent": {"paise": 50000}})` returns `[]` because `_unit("1000") == 1000` grants ±500 slack.
   - **Root-cause fix:** Tag `sources()` values with their semantic kind (`money` vs `percent` vs `count`) so `percent` claims only match percent/ratio sources; bound money rounding tolerance to actual display-precision rules (not unbounded trailing zeros); add exact negative regression cases in `test_agent_number_check.py`.

3. **Refused `record_movement` / `correct_movement` still commits payees + orphan audit rows** (`src/arno/tools/write.py:405-414,789-796`, `tests/test_audit_ledger.py:1141-1157`)
   - **Problem:** `resolve_payee()` commits its own `write_group` before `_rupees()`, `resolve_account_id()`, split checks, and loan checks validate the request. A refused expense leaves a `payees` row and audit entry behind. `test_audit_ledger.py:1157` checks only `movements == 0`, hiding the leak.
   - **Root-cause fix:** Validate all inputs and resolve read-only lookups before any write, and create new payees inside the same `write_group` transaction as the movement. Assert `payees == 0` and `audit_log == 0` on refusal tests.

4. **`tools/write.py` `undo()` breaks the `CommitOutcomeUnknownError` contract and crosses conversation boundaries** (`src/arno/tools/write.py:1621-1646`)
   - **Problem:** Line 1624 catches bare `Exception` and converts `CommitOutcomeUnknownError` into `ToolInputError` (`ModelRetry`), telling the model to retry an ambiguous undo (which with `undo_id=None` undoes the *next* write). Lines 1641–1642 silently fall back to `undo_latest` across *all* conversations despite the tool docstring promising "the last write in this conversation." Zero unit tests cover `undo_id=None`.
   - **Root-cause fix:** Catch only `LedgerError` (never `CommitOutcomeUnknownError` or DB connection errors); remove cross-conversation `undo_latest` fallback when `conversation_id` is supplied (return a clear refusal instead); add tests for both.

5. **Duplicate detection in `record_movement` is blind to photo, voice, and woken writes** (`src/arno/tools/write.py:280,390-394`, `tests/test_audit_ledger.py:1111`)
   - **Problem:** `_possible_duplicate` filters `JOIN captures c ON ... AND c.kind = 'chat'`, missing `photo` and `voice` captures and capture-less writes. `test_a_keyless_capture_still_sees_a_keyed_duplicate` passes `capture_ref="photo-1"` with default `capture_kind="chat"`, proving nothing about photos.
   - **Root-cause fix:** Match duplicates across all user-capture kinds (`chat`, `photo`, `voice`)—or against `movements` directly—and test photo-then-chat duplicate detection.

6. **Scheduled questions' `read_only=True` and spend-cap wiring is never tested** (`src/arno/jobs/questions.py:160-201`, `tests/test_watch_workflows.py:708-711`)
   - **Problem:** Every test injects `agent=`, bypassing `build_agent(read_only=True, spend_store=..., question_budget_usd=...)` and `handle_scheduled_question`.
   - **Root-cause fix:** Test `handle_scheduled_question` / `run_scheduled_question(..., agent=None)` through the real `build_agent` path (overriding only the model), asserting no write tools are registered and the spend ceiling is attached.

7. **Account-takeover passkey defenses have zero negative tests, and frontend `reverifiedUntil` survives logout** (`src/arno/api/auth.py:345-393`, `src/arno/auth/passkeys.py:179-320`, `web/src/auth.ts:60-86`)
   - **Problem:**
     - `FreshSession` step-up is never negative-tested on `PATCH/DELETE /api/auth/passkeys/{id}` or `POST /api/auth/enrollments`.
     - WebAuthn `expected_origin` / `expected_rp_id` / `require_user_verification` are never negative-tested (despite `SoftwareAuthenticator` accepting `origin`).
     - `sign_count` persistence is never verified across two real logins (tests poke `sign_count = 999` manually).
     - `web/src/auth.ts:60-86` never clears module-level `reverifiedUntil` on `logout()` or `logoutAll()`.
   - **Root-cause fix:** Reset `reverifiedUntil = 0` in `logout()`/`logoutAll()`; add parametrized negative tests for step-up, wrong WebAuthn origin/UV, and two-login `sign_count` persistence.

8. **`arno restore` fails open and can drop the live database without `--force`** (`src/arno/backup_restore.py:199-238`, `tests/test_backup_restore.py`)
   - **Problem:** `_database_has_tables()` catches `OSError, PostgresError` and returns `False`; `prepare_database()` then terminates backends and executes `DROP DATABASE` on the default live `DATABASE_URL`. Neither the non-empty refusal nor probe failure is tested.
   - **Root-cause fix:** Fail closed (raise `RestoreError` if the existing target database cannot be inspected) and test both non-empty refusal and probe-error refusal.

9. **Zero migration tests upgrade a populated database, and `0032` aborts on legal pre-0032 data** (`src/arno/db/migrations/0032_schema_integrity.sql:14-59`, `0033`–`0039`, `tests/test_migrations.py:73-98`)
   - **Problem:** All migrations run only against an empty schema. `0032` introduces case-insensitive unique indexes (`categories`, `payees.upi_id`, `statement_layouts`), a biconditional reversal constraint, and card billing-day requirements with no precondition block or deduplication—aborting API boot on valid `0003` rows. Six data migrations (`0033`, `0035`, `0037`, `0038`, `0039`) have never been tested over rows.
   - **Root-cause fix:** Add a populated-upgrade migration test harness using `run_migrations(..., directory=...)` that migrates to `0031`, seeds legacy-valid rows, upgrades through `0044`, and runs `check_ledger()`; add a clear precondition/repair block or operator check for `0032`-style constraints.

10. **Four cross-layer / contract test suites never run in CI, and `web test:answers` is currently broken** (`.github/workflows/ci.yml:107-138`, `.pre-commit-config.yaml`, `web/scripts/answer-contract.test.mjs`, `web/scripts/assistant-ui-fixture/vite.config.ts:2`, `tests/test_chat_stream_schema.py:59-70`, `tests/test_phoenix_cost_contract.py:226-231`, `tests/test_image_runtime.py:15-18`)
    - **Problem:**
      - `pnpm --dir web test:answers` is omitted from CI and pre-commit; its Vite server spreads the full production config (`tailwindcss` + `VitePWA` + dep optimizer) with an extensionless import (`../../vite.config`) and spawns 3 heavy `uv run python` subprocesses (`arno.api` loads `pydantic_ai`), causing OOMs, partial `.vite/deps_temp_*` caches (`Cannot find module './descending.js'`), or Python `MemoryError`.
      - `test_chat_stream_schema.py` silently skips in CI (no `web/node_modules` in the Python job).
      - `test_phoenix_cost_contract.py` (1,210 lines) and `test_image_runtime.py` never run in CI.
    - **Root-cause fix:** Give `answer-contract.test.mjs` a minimal SSR-only Vite/Vitest config (no `VitePWA`, no client dep optimization, explicit `.ts` extension) and generate all Python contract fixtures in a single lightweight Python script that doesn't import `pydantic_ai`; run both `test:answers` and SSE schema validation in CI; either run `test_phoenix_cost_contract.py` and `test_image_runtime.py` in dedicated CI/release jobs or move them out of `tests/` into `scripts/`.

11. **`evals/graders.py` and `scripts/license_audit.py` contain false-positive/fail-open grading bugs** (`evals/graders.py:174-178,1237-1238,1397-1417`, `scripts/license_audit.py:189-191,249-252`)
    - **Problem:**
      - `_has_any` uses raw substring match (`must_say: ["rent"]` passes on `"current"`/`"different"`/`"parent"`).
      - Exfiltration grader flags *any* URL or email in the reply as `"fooled"`, penalizing correct refusals that quote the attacker's URL.
      - `grade_long_threads` has zero tests and its first required term (`goa`, `floor`, `slice`) is already inside the user's question. Only 1 of 10 injection targets is unit-tested in `test_evals.py`.
      - `scripts/license_audit.py` treats a failing `pnpm licenses` as "skipped" (exit 0), and `--python-only --web-only` skips both audits and exits 0.
    - **Root-cause fix:** Use word-boundary matching in `_has_any`, distinguish refusal from exfiltration in `_text_matches`, remove question-echo terms from `long_threads`, parametrize `test_evals.py` over all 10 injection targets, and make `license_audit.py` fail closed on non-zero `pnpm` exit or conflicting flags.

12. **Vacuous and tautological assertions give false green lights across engine, agent, importer, and export tests**
    - `tests/test_engine_backtest.py:451-455`: `cases >= 0` and `dropped >= 0` (always true).
    - `tests/test_projection_tool.py:25-35`: unguarded `check_isolation` spy never asserts it was called.
    - `tests/test_engine_affordability.py:571-626`: asserts only inside `for trade in result["tradeoffs"]:` (passes on `[]`).
    - `tests/test_agent_tools.py:712-742`: `assert returns` passes on `"Runtime error: …"`.
    - `tests/test_importers.py:239`: `assert opening == account["opening"] if account["opening"] is not None else True` evaluates to `assert True` on Slice card.
    - `tests/test_export_scope_api.py:4-8`: imports `SECRET_TABLES` from the same module under test.
    - `tests/test_agent_reliability.py:92-101`: promises "without sticking" but runs only one turn.
    - `tests/test_agent_output_cap.py:15-70`: calls `_save()` and `_encoded()` manually, never exercising real `on_complete`-before-`finish` stream ordering.
    - `tests/test_migrations.py:248-255`: accepts *any* `constraint_name is not None`.
    - `tests/test_audit_ledger.py:342-343,610-615`: loop without length check; queries `audit_log` directly instead of checking returned `undo_id`.
    - **Root-cause fix:** Replace every vacuous check with an assertion that fails if the named production behavior is deleted.

13. **Cross-test state leaks and concurrent-run collisions** (`tests/test_import_api.py:98-113`, `tests/test_import_recovery.py:157`, `tests/conftest.py:34-36,107-150`, `src/arno/demo/persona.py:43-50`, `tests/test_audit_platform.py:349,367`)
    - **Problem:** `PASSWORDS` vault is cleared after assertions without `try/finally`; `RESET_TABLES` omits `webauthn_credentials` (relying on `CASCADE`) with no `pg_tables` completeness check and diverges from `demo/persona.py._RESET_TABLES`; `ARNO_TASKS_DIR`/`ARNO_PRE_MIGRATION_DIR` are unisolated under `-n 0`; 9 hardcoded scratch DB names are dropped `WITH (FORCE)` across 5 files (colliding if two pytest processes run concurrently).
    - **Root-cause fix:** Autouse `PASSWORDS.clear()` + always isolate temp dirs per session even without xdist; derive `RESET_TABLES` (or assert equality with `pg_tables`) from one canonical list shared with `persona.py`; suffix all temporary DB names with the worker's DB name (matching `test_evals.py:65-69`).

---

### Should fix (architecture of the test harness, bottlenecks, bloat, frontend blind spots)

**Status.** Complete. Native templates, bounded workloads, and DOM interaction checks are in place.

14. **Decouple database lifecycle from pure-Python tests and eliminate per-test seed churn** (`tests/conftest.py:107-557`, `pyproject.toml:76-79`)
    - **Problem:** Autouse `clean_database` + `demo_snapshot` forces 342 pure tests to require Postgres, serializes 8 workers through 44 migrations behind `cluster_lock()`, truncates static seed tables so every `TestClient` re-runs 163 sequential `INSERT … ON CONFLICT`s + EC P-256 VAPID keygen, and runs a full WebAuthn ceremony in `authed_client` for tests that don't test auth.
    - **Root-cause fix:**
      1. Remove `autouse=True` from `clean_database`; attach it to `db`/`client`/`app` only, and make `demo_snapshot` lazy for `demo_ledger` only.
      2. Migrate **once** into a template DB (`arno_test_tpl`) and clone per worker via `CREATE DATABASE ... TEMPLATE` (eliminating 7/8 migration runs and `cluster_lock()` serialization); put `demo_snapshot` in a separate template DB (`arno_demo_tpl`) rather than a `demo_seed` schema inside the live test DB (eliminating the 100-line hand-rolled topological SQL engine AND stopping `pg_dump` tests from dumping twice the data!).
      3. Keep static seed rows (`categories`, `category_rules`, `system accounts`, `vapid_keys`, `job_schedules`) in the clean baseline so `TestClient` startup does zero redundant seeding; give non-auth API tests a session-cookie fixture that inserts a `sessions` row directly.
      4. Set `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1` at the top of `conftest.py` (before `numpy` import) and change default `addopts` from `-n 8` to `-n auto` (or `-n 4` / physical-core cap) so laptops don't melt.

15. **Fix the 412s `@cluster` dump tests, 25s teardown hang, oversized Monte Carlo runs, invariant fuzz loops, and polling sleeps**
    - `tests/test_audit_platform2.py:67,142` (**412.7s**): Use `ledger` (minimal schema + 1 row), not `demo_ledger`, to test `has_table_privilege` grants—and merge into `test_backup_restore.py` / `test_l8_ship.py` to remove duplication.
    - `tests/test_import_recovery.py:449-456` (**25.06s teardown**): Clear the fake `ChatTurn` in a `finally` block (or mark its `done` event) so FastAPI lifespan doesn't wait 25s on drain.
    - `tests/test_invariants.py:418-423` (**161.7s**): Run `check_ledger()` periodically (e.g., every 10 ops + at the end) or cut count from 200+80 to ~30 per seed while keeping the full planted-violation suite.
    - `tests/test_projection_tool.py` & `tests/test_evals.py` & `tests/test_engine_series_projection.py` (**~150s+**): Pass `runs=50` for shape/wiring/API tests and `RUNS=2_048` for statistical tests instead of production `16_384`; cache `personas.generate()` per `(key, seed)` in tests.
    - `tests/test_agent_parser.py` (9 polling loops), `tests/test_reversal_state.py:157`, `tests/test_db_pool.py:295`, `tests/test_engine_series.py:680`, `tests/test_engine_personas.py:159`: Replace sleep polling with `asyncio.Event` / `pg_stat_activity` lock checks, and remove wall-clock `< 0.5s`/`< 1.0s` assertions from correctness tests.

16. **Consolidate the 29 `test_audit_*.py` files (14,940 lines) and `test_typing_tya/tyb.py` into feature modules**
    - **Problem:** 25% of the suite is wave-numbered dumps (`test_audit_imports{,2,3,4,5}.py`, `test_audit_ledger{,2,3,4}.py`, `test_audit_agent{,2,3,4}.py`, `test_audit_engine{,2,3}.py`, `test_audit_reads{,2,3}.py`, `test_audit_watch{,2}.py`, `test_audit_platform{,2}.py`, etc.) with ~20 duplicated helper sets (`_bank` 12×, `_category` 12×, `_account` 11×, `_card` 8×, `_inputs` 6×, `_answered` 4×) and dozens of duplicate tests.
    - **Root-cause fix:** Fold unique regressions into their owning feature test files, delete duplicates and wave files, extract shared factories into `tests/fixtures/` (`ledger_helpers.py`, `import_helpers.py`, `engine_helpers.py`), and adopt a rule in `AGENTS.md`: regression tests always go into the owning feature file, never a dated/wave file.

17. **Close the frontend interaction testing chasm and delete or wire the 1,842-line dead fixture server** (`web/src/`, `web/scripts/assistant-ui-fixture/server.mjs`, `tests/e2e/browser_journey.py`)
    - **Problem:** Only 15.0% of `web/src/` (20/134 files) is statically imported by `answer-contract.test.mjs`, and 0% is interactively mounted. Critical trust-boundary flows have zero automated frontend tests: confirm-card **Deny** + double-click guard (`parts.tsx:228-256`), statement password clearing (`Imports.tsx:1200-1742`), `formatRupees(NaN)` -> `₹NaN`, SSE stream stop/error/dictation recovery (`runtime.ts`), and `Settings.tsx` (2,666 lines). Meanwhile `web/scripts/assistant-ui-fixture/server.mjs` (1,842 lines) implements all those mock scenarios and is never started by any test. `browser_journey.py` uses fragile selectors (`xpath=following-sibling::ul[1]/li`, `figure.last`, `nav[aria-label="Primary"].first`) and a race-prone `_settle` heuristic.
    - **Root-cause fix:** Decide once: either drive lightweight Playwright/Vitest smoke tests against `assistant-ui-fixture/server.mjs` in CI (covering confirm-card approve/deny, statement password clearing, passkey logout/step-up, and undo), or delete the 1,842-line dead server and add targeted Vitest + Testing Library tests for the 4 trust-boundary components. Replace fragile `browser_journey.py` locators with explicit `data-testid` / `data-slot` attributes and a deterministic stream-complete signal.

18. **Remove tests coupled to third-party internals, prose wording, and private attributes** (`tests/test_agent_compaction.py`, `tests/test_audit_agent3.py:363-382`, `tests/test_provider_profiles.py:247,376`, `tests/test_affordability_oracle.py:103-116`, `tests/test_tools_money.py:372`, `tests/test_audit_cleanup3.py:48-61`)
    - **Root-cause fix:** Delete `test_agent_compaction.py` (or test via `build_capabilities`); replace verbatim prompt-sentence assertions with a structured rule registry linked to `evals/cases.py`; assert public HTTP/model behavior instead of private SDK internals.

---

### Nice to have (cleanup, warnings, dead helpers, hygiene)

**Status.** Complete. Tests use feature modules and shared fixtures. Hooks support native Windows.

19. **Fix the 2 live `UserWarning`s, delete dead "for tests" helpers, and split mega-files**
    - Investigate and clean the `UserWarning: 1 client-submitted tool result(s) were dropped` in `tests/test_audit_agent.py` (`test_a_message_after_an_approval_is_kept_and_answered`, `test_a_denied_approval_writes_nothing_and_the_thread_keeps_working`).
    - Delete unused `src/arno/jobs/runner.py:421-423` (`next_backoff`) and `src/arno/engine/projection.py:1700-1701` (`_add_months_public`).
    - Split `tests/test_engine_backtest.py:571-1467` (897 lines of HackerRank tests) into `tests/test_engine_hackerrank.py`; delete duplicate `isolated_spend_day` reset in `tests/test_direct_spend.py:38-43`; replace `pg_sleep(3)` with `pg_sleep(2.2)` in `tests/test_agent_tools.py:250`; call `run_real()` in-process in `tests/test_import_spike.py:35-47`.

20. **Clean broken windows: names, cross-test imports, stale comments, and Windows pre-push portability**
    - Rename `tests/test_coverage.py` → `tests/test_read_coverage.py` and `backup_role_can_dump` in `tests/test_backup_restore.py:87-103`.
    - Move helpers imported across test files (`test_audit_imports4.py:37`, `test_import_recovery.py:33`, `test_engine_levers.py:33`) into `tests/agent_helpers.py` / `tests/fixtures/`.
    - Delete scratch notes (`tests/test_engine_affordability.py:601-604`) and fix stale comments (`tests/conftest.py:542-545`, `tests/test_evals.py:109`).
    - Replace `sh -c ...` in `.pre-commit-config.yaml:66-68` with a Python one-liner so Windows machines without Git Bash on `PATH` don't break on push.

---

## 5. High-risk untested normal and edge cases (by subsystem)

**Status.** Complete. Capture and pricing passed 53 tests, including metered audio-duration billing.

| Subsystem | Source path | Concrete untested scenario | Consequence if broken |
|---|---|---|---|
| **Auth / Security** | `api/auth.py:345,368,393` | Stale session on passkey rename, delete, or enrollment creation | Account takeover via stolen non-fresh session |
| **Auth / Security** | `auth/passkeys.py:173,179-185,214-228,291-320` | Wrong WebAuthn origin/RP-ID/UV; expired/wrong-kind challenge; duplicate `credential_id`; two-login `sign_count` persistence | Phishing origin or cloned authenticator accepted; 500 on duplicate key |
| **Auth / Security** | `api/static.py:50-55` | `GET /..%2F` or prefix-sibling path traversal | Arbitrary file read outside `web/dist` |
| **Backup / Restore** | `backup_restore.py:180-238,424-425`; `jobs/backup.py:17-43` | Populated target refusal; `_database_has_tables` DB error; `*.dump` fallback; foreign manifest; never-succeeded backup alert | Live DB dropped without `--force`; new install never warns backups are dead |
| **Write Tools** | `tools/write.py:122-191,548-617,991-1389,1479-1549,1658-1684` | Unknown account/category; `resolve_payee` kind mismatch; negative `my_share_rupees`; non-loan `loan` arg; overdrawn negative bank balance; `entity="category"` (0 tests anywhere); `schedule_payment` by name & `action="unlink"`; `scheduled_question` rename; re-remember after forget + undo duplicate; `forget` on already-forgotten ID | Wrong account/receivable corruption, silent no-op updates, duplicate active memories |
| **Read Tools / Ledger** | `tools/read.py:93-121`; `ledger/export.py:99-138`; `ledger/preference_edits.py:11-22`; `tools/money.py:153-154`; `india/recurrence.py:28-36,70-95` | Multi-account `coverage()` without `account_id` + `today` clamp; `/api/export/pg_dump` argv/env/error handling; 2001-char preference rejection; negative `minimum_percent`; UTC `Z` `UNTIL` in IST; invalid RRULEs | Agent claims full coverage when one bank is missing; `pg_dump` rots silently |
| **Migrations** | `db/migrations/0032`–`0039` | Upgrading populated pre-0032 DB with case-variant names/UPIs, legacy reversals, partial card dates, `transfer_labels`, `audit_log` rows | Startup crash on real user upgrade |
| **Engine** | `engine/projection.py:361-372,488,1495-1508,1547,1679`; `engine/inputs.py:104-155,276-292`; `engine/patterns.py:48-132`; `engine/scenario.py:211-234`; `engine/money.py:315-325,479-487,558-570`; `engine/backtest.py:157-189` | Overpaid card (`credit_paise > 0`); `horizon_limit_days`; `runs=0`; `reduce_categories` clamping; `_derived_baseline` <3-row threshold; `is_covered` skipping unconfirmed schedules; `scenario.py` positive-amount & `goal_id` direction guards; `amortize` fee ≥ principal APR guard; `model_from_name` grammar | Understated card headroom, zero-division, negative/invented money in scenarios, silent model-switch drift |
| **Importers / Capture** | `importers/readers.py:79-102,125-133,229-253,291-376,437-465`; `importers/monty.py:34-44,161-174,223-265`; `importers/passwords.py:26-66`; `capture/transcribe.py:70-93,125-158` | Encrypted OOXML (`msoffcrypto`), PDF `wrong_password`, `xls`/`doc`/`ods` sniff, UTF-16 BOM, `zip_too_big`/`empty_zip`, `bad_range`; Monty `LIMITS`/`OS_POLICY` & cost-stop re-raise when script catches it; password TTL expiry & `describe_vault`; STT audio-seconds billing & 500-char payee prompt truncation | Wrong recovery UI, cost-ceiling bypass inside parser scripts, wrong STT billing |
| **Agent / Turns** | `agent/toolset.py:889-904,1052-1068`; `agent/turns.py:68-74,125-169,478-479,735-740`; `agent/quote.py:38-43,90-92`; `agent/models.py` | Approved `update_setup` clearing `outside_text`; `preference(forget/classify)` gate on flagged thread; `finish_reason="content_filter"`; >3 unread statement files; multi-line quote; `WindowLimited` boundary fit | Threads permanently stuck asking for approval; dropped truncation notes |
| **Jobs / Watch** | `jobs/runner.py:254,400-409`; `notifications.py:251-254,462-473`; `agent/wake.py:32-47`; `api/wake.py:28-39,92-101` | `COST_STOP_ERRORS` / `CommitOutcomeUnknownError` final-fail branches; push `subscribe()` HTTPS/keys validation; successful push clearing `push_last_error`; startup `waiting_threads` recovery; failed-turn wake rollback | Push permanently stuck degraded; broken startup wake recovery |
| **Frontend (`web/src/`)** | `auth.ts:60-86`; `chat/parts.tsx:228-261`; `panels/Imports.tsx:1200-1742`; `chat/runtime.ts:101-439`; `chat/format.ts:1-17` | Logout clearing `reverifiedUntil`; confirm-card Deny & double-click guard; statement password clearing; SSE abort/error/retry; `formatRupees(NaN)` / fractional paise | Stale step-up state, broken deny flow, `₹NaN` rendered in cards |

---

## 6. Root-cause remediation roadmap (in dependency order)

**Status.** Complete. The original remediation passed 1,999 tests with five expected skips.

1. **Phase 1 — Stop false positives and real bugs first (small, high-impact):**
   - Fix Findings **1–8** (assert `check_ledger` in `test_watch_recovery.py`, fix `number_check` percent/slack, fix `tools/write.py` payee atomicity + `undo()` error/scope + photo duplicate check, test `questions.py` `read_only=True`, add auth negative tests + `web/src/auth.ts` logout reset, make `backup_restore._database_has_tables` fail closed, replace the vacuous assertions in #12).
2. **Phase 2 — Fix the CI and grader blind spots:**
   - Fix Finding **10** (minimal SSR config for `answer-contract.test.mjs`, single lightweight fixture script, wire `test:answers` and `test_chat_stream_schema.py` into `.github/workflows/ci.yml`, decide CI vs `scripts/` for `test_phoenix_cost_contract.py` and `test_image_runtime.py`).
   - Fix Finding **11** (`evals/graders.py` word boundaries, exfiltration refusal handling, `long_threads`, `license_audit.py` fail-closed).
   - Fix Finding **9** (add populated-data migration upgrade test for `0032`–`0044`).
3. **Phase 3 — Re-architect the test harness for speed, isolation, and laptop thermals:**
   - Fix Findings **13–15**:
     - Set `OPENBLAS_NUM_THREADS=1` / `OMP_NUM_THREADS=1` in `conftest.py` and replace hardcoded `-n 8` with physical-core-aware `-n auto` / `-n 4`.
     - Remove `autouse=True` from `clean_database` (verify once with `-n 0` to expose implicit DB dependencies).
     - Replace `demo_snapshot`'s in-DB `demo_seed` schema + 100-line SQL with per-session template DBs (`arno_test_tpl` and `arno_demo_tpl`), preserve static seed tables across tests, and add a fast cookie-only `authed_client` for non-auth API tests.
     - Fix `test_audit_platform2.py` (412s → ~4s), `test_import_recovery.py` teardown hang (25s → <0.1s), `test_invariants.py` check cadence, `runs=50` in shape tests, and `asyncio.Event` in parser tests.
   - *Expected impact:* Full suite drops from **~10m 53s at 94°C** to **~60–90s** with pure unit tests running in **<2s without Postgres**.
4. **Phase 4 — Delete sprawl and close frontend interaction gaps:**
   - Fix Findings **16–20**: fold the 29 `test_audit_*.py` files + `test_typing_tya/tyb.py` into feature modules, deduplicate fixtures (~3,500–5,000 lines removable), and either wire `assistant-ui-fixture/server.mjs` into automated UI tests or delete it and add focused Vitest interaction tests for the 4 trust-boundary UI flows.

## 7. Independent verification follow-up

**Status.** Complete. All 13 findings are addressed. The full Docker run passed 2,014 tests in 5m 37s.

| Review items | Resolution |
|---|---|
| 1, 2, 8, 9 | One category map preserves the surviving baseline and prefers active identities. The populated upgrade checks every repointed reference, the self-reversal test names its constraint, and payee lookup is shared. |
| 3, 5 | Pool and migration tests request a database explicitly. Ordinary cases reset in place, unchanged API catalogs stay in place, and demo cases use template cloning. |
| 4 | Photo expenses and newly created payees share one transaction and undo group. Refusal rolls back both, and retrying an undone attachment leaves the ledger unchanged. |
| 6, 7, 11 | The grader distinguishes a delivery claim from a quoted refusal, chat exercises the live SSE hook order, and the rendered prompt and eval map share rule text. |
| 10, 12, 13 | The static boundary is exercised through HTTP with a simulated filesystem resolution. License hooks run by dependency scope, the chart test expects the right exception, and the shared formatter rejects invalid paise. |

The four-worker backend run used disposable Docker PostgreSQL 18.6 and reported five expected
skips. The seeded ledger passed 812 invariant checks. The web suite passed 22 interaction tests
and 35 answer contracts. Ruff, Pyrefly, both license hooks, and the web lint, type check, and build
passed.

### Second independent review

**Status.** Complete. All four findings are addressed. The Docker run passed 2,022 tests in 5m 18s.

History shows the photo expense and its undo, with both the payee and movement attributed to the
user. The grader recognizes perfect and passive refusals while still flagging delivery claims that
begin with a bare "no". Each license audit reads its own manifest and enforces its own notices.
The API reset skips re-reading freshly restored category rules, and the formatter test is staged.

Five tests skipped for the same release-service and Windows FIFO prerequisites. The seeded ledger
passed 812 invariant checks. Ruff, Pyrefly, both license checks, 22 web tests, 35 answer contracts,
web lint, type checking, and the production build passed.
