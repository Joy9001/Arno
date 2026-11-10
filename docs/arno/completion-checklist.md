# Completion checklist

Audit on November 3, 2026, at `109c7ea`. It checks [`plan.md`](plan.md) §5 and "What 'shipped'
means" against the code, CI, the live instance, and the TODOs in the source.

**Verdict: not complete.** Every layer's code exists, and all of group A is closed: `main` is green
in CI and the versioned images are published. Two kinds of gap remain:

- Measurements that need real data, real endpoints, or a phone were never run.
- The human gates haven't started. The live ledger now has 5 accounts, 11 transfers, and
  0 conversations, so the four-week clock for milestone 41 still isn't running.

The observability reconciliation on November 5, 2026 updates section D and the plan's T/A tracker.
It records what is verified and what remains; it does not change the overall verdict.

The release-hardening pass on November 10, 2026 adds section E. It changes no earlier status without
saying so, and it never ticks a simulated run.

Work the groups in order, because each depends on the one before it.

## A. Code and docs: closable now

- [x] **Fix the red CI.** `test_restore_drill_brings_back_every_area` calls bare `pg_dump`. It finds
      the runner's v16 client and aborts against the 18.6 server: 339 passed, 1 failed on run
      41622521117. Fixed in three places:
  - [x] The test uses `os.environ.get("PG_DUMP") or "pg_dump"`.
  - [x] `ci.yml` sets `PG_RESTORE: /usr/lib/postgresql/18/bin/pg_restore`. The restore goes
        through `_binary("PG_RESTORE", ...)`, and v16 can't read an 18 dump.
  - [x] The `skipif` checks the binaries the env vars name, not `shutil.which`.
- [x] **The restore drill passes in CI at least once.** Green on run 41627342626. It also passes
      locally (341 passed, 0 skipped on November 3, 2026).
- [x] **Correct plan.md's L8 status.** Milestones 37-39 are "built", not "built and tested":
  - 37: the drill ran on the build host, not a fresh machine.
  - 39: no `v*` tag exists, so no image has been published.
- [x] **Tag `v0.1.0`.** Then confirm that `release.yml` pushes both `ghcr.io/joy9001/arno`
      images and that `self-hosting.md` pulls them. Run 41628402245 pushed `0.1.0` and `v0.1.0` for
      `arno` and `arno-backup`; `compose.yaml` pins `0.1.0` by default.
- [x] *(Optional)* Remove the backup manifest race. The dump and the manifest now come from one
      `pg_export_snapshot()`, and `tests/test_l8_ship.py` forces a write between them.

## A2. Import redesign: code, in this order

Agreed November 7, 2026 ([`import.md`](import.md#the-pipeline)). Each phase ends with tests green
and pushed to `main`. Group B's import items wait for this group.

### Phase 1: schema, kept files, versions, the gate (no model)

- [x] Migration `0013`: `statement_layouts` by bank and statement type, with header variants and an
      active-version pointer; append-only `parser_versions`; `attachments` keep statement files.
- [x] Statement files are kept: no deletion at commit or reject, an unlocked copy stored once the
      file opens, and an endpoint to delete one.
- [x] `submit_parser` is the gate: a fresh sandbox, the checks, and a replay on every stored,
      committed, not-undone statement from that bank that must reproduce its `statement_lines`
      exactly; the first mismatch is the reason.
- [x] A commit saves a new version and moves the pointer; a rollback moves it back.
      The route is `POST /api/layouts/{id}/rollback`, behind a confirm in the Imports panel.
- [x] Tests: replay set excludes undone imports and deleted files; a mismatch blocks; rollback works.

### Phase 2: the parser subagent

- [x] One `DelegationTasks` owner opened at startup, with `TASKS_DIR` and a `PostgresStepStore`;
      each chat turn binds it and passes `DelegationReports`.
- [x] `SubAgents` with task control on the main agent; the `parser` subagent with its own
      instructions (the old skill text), the `PARSE` model role, `forward_usage=False` and its own
      ceilings, `ask_parent`, and `Planning` keyed by task ID.
- [x] Parser tools keyed by `import_id`: `open_imports`, `wait_for_password`, `saved_parser`, the
      `doc_*` readers, `ledger_opening`, `check_statement`, `transcribe_page`, `submit_parser`,
      `suggest_transfers`.
- [x] Removed: `Skills`, `skills_backend.py`, `SKILL.md`, `parse_run.py`, the `import_statement`
      job kind, the job body in `pipeline.py`, and `import_document`.
- [x] Tests: a full parse, a clean replay, a repair after a gate failure, a two-bank upload, a resume
      after a simulated crash, and no password in any history. Parse evals updated (the synthetic
      and spike benchmarks run through the gate).

### Phase 3: web

- [x] An upload from the Imports panel posts into chat; there's no direct import path.
- [x] The import card uses assistant-ui's Task card, Agent plan, Agent status, Job progress, and
      Option list, fed by an API for the task's state and plan.
- [x] The review screen drops categories and keeps the transfer confirmations.

### Phase 4: the categorizer

- [x] A migration gives `category_rules` `match`, `pattern`, `priority`, and the `seed` source, and a
      label records the rule that fired.
- [x] Rules engine: match types and priority, payment-rail stripping, the seed list of about 150
      merchants, payment apps never matched by name, refunds take the original category.
- [x] Categorizer agent and tools; `categorize_import` and `collect_batch` jobs; the `BATCH` role.
- [x] The Uncategorized list, grouped by payee, with people asked once.
- [x] Removed: the pre-commit categorizer call and `suggested_category` in commit.
- [x] Tests, including the agreement rate saved with each run.

### Phase 5: end to end on the dev instance

- [ ] A real SBI import: chat stays responsive, a restart resumes, a second import of the same
      layout replays, and categorizing finishes in minutes with no timeouts. Compare with Phoenix
      session `__LOCALID_y8OJ3G9`.
      Operator run on November 7 (see the run log): responsive chat, a verified 939-row parse,
      commit, replay of the saved parser, and categorizing in 3-5 minutes all pass. Open: the
      restart and the same run through the UI with a passkey. The categorizing chunks that hit
      the request limit were fixed on November 8: every chunk returned, 591 of 939 labelled.

## B. Owner setup and measurements

Each item needs real data, a real endpoint, or a device. Most can't run in CI.

### Durability, first and before any real money

- [ ] Point `RESTIC_REPOSITORY` **off-site**. Google Drive for now, through restic's `rclone:`
      backend. R2 or S3 later is a `.env`-only switch (`self-hosting.md` §6.1).
  - [x] Both images ship rclone 1.71.2. Before this, neither had `ssh` or `rclone`, so the documented
        SFTP and rclone targets couldn't work. SFTP is still unsupported, and the docs say so.
  - [x] `RESTIC_REPOSITORY=rclone:gdrive:fin-agent-backups`, configured from env vars with the
        `drive.file` scope and the owner's own Google OAuth client. rclone's shared client ID is
        being retired, and it hit `rateLimitExceeded`: a first seed took 317 s for 136 KB. Seeded
        on November 3, 2026: snapshot `3883b6902129`, 136,174 bytes, `backup_runs` success at
        12:56:02 UTC, 60 s with no rate limits.
  - [x] Restored from Drive into a scratch database with `arno restore`. All five checks
        passed (tables, schema version, transfers balance, account balances, manifest).
  - [x] Confirm that the OAuth consent screen is **In production**. A "Testing" app's refresh
        token dies after 7 days, around November 10.
  - [x] Delete the older `fin-agent-backups` folder in Drive, the one from the shared client. The
        new client can't see it under `drive.file`.
  - [x] Released `v0.1.1` (run 41648263436), so the published images carry rclone. `pip-audit`
        now audits the lockfile export: `--strict` failed once the version left `0.1.0`, a name
        that an unrelated PyPI package also uses.
  - [x] The repository and both GHCR packages are public. Anonymous pulls of `0.1.1` work, and
        the API runs the published image. The sidecar picks it up on `docker compose up -d backup`.
- [x] Get the backup sidecar running. The `Exited (137)` was a SIGKILL. Dash defers the TERM trap
      until the foreground `sleep 3600` exits, so every stop hit the 30-second grace period. The
      sleep now runs in the background under `wait`, and a stop takes 0.66 s with exit 0.
- [ ] Confirm that a new `backup_runs` success lands within 24 hours. The loop's last local
      success was 12:18:20 UTC on November 3. This item closes when the restart after the alert
      test produces a scheduled off-site success.
- [ ] Stop the sidecar on purpose and confirm the 48-hour backup alert fires once. **Running:**
      the sidecar was stopped at 12:51 UTC on November 3. The alert line is 12:56 UTC on
      November 5, and `backup_check` runs daily at 03:00 IST (21:30 UTC):
  - [ ] November 6, 03:00 IST: one `backup_stale` row in `notifications`.
  - [ ] November 7, 03:00 IST: still one row (dedupe). Then run `docker compose up -d backup`
        and confirm the catch-up success. Any `docker compose up -d` before then restarts the
        sidecar and resets the test.

### Import (L3), in this order

- [ ] Put 12 months of real SBI, Union Bank, and Slice statements in `private/statements/<bank>/`,
      then run `arno import-spike`.
  - [x] SBI: one download covers 12 months. The PDF gave 939 rows, `VERIFIED` after 1 repair in
        321 s. The `.xlsx` gave the same 939 rows with 0 repairs in 85 s.
  - [x] Slice: 5 cycles (May 25 to Oct 24, 2026). One parser, replayed with no LLM: 5/5
        `VERIFIED`, 80 rows. The yearless cycle header needed a period fix (`626e421`).
  - [ ] Union Bank: no statements provided.
- [ ] Record each bank's decision (saved parser, or `xfina`/OCR) in `import.md` and plan.md §6 L3.
      Note what Slice exports.
  - [x] SBI and Slice: saved Monty parser. Slice exports unlocked monthly PDFs. `xfina` 0.10.0
        recognized neither SBI file, and OCR wasn't needed.
  - [ ] Union Bank: open until its statements arrive.
- [ ] **Milestone 24:** commit the 12-month backfill through the review screen, then recompute
      baselines from the real rows. The 7 baselines are computed from SBI only, so they move once
      Slice's cycles are in. The backfill was committed with local code, not the screen.
  - [x] SBI, November 4, 2026: re-imported from the pre-backfill snapshot with the new parser.
        939 rows, `VERIFIED`, 0 ambiguous, 0 adjustment, balance equals the printed closing.
        928 of the 939 lines carry a category suggestion, 349 payees and 348 rules were written
        (down from 709/703), and 7 baselines were recomputed. The parser and layout were saved.
        Ran with local code while the API was stopped, not through the review screen.
  - [x] Fix own-account moves booked as spend. The parser names the counterparty; one batched
        categorizer call per import returns a category or one of your own accounts. Commit books
        a suggested own-account line as a transfer, not an expense. The review screen now asks you
        to confirm every such line before commit, and commit refuses an unconfirmed one. The 11
        SBI `slice sm / ccrepaymen` debits (May 30 to Oct 29) were confirmed against Slice's app
        and booked as transfers. UPI transfers to people, some possibly to your own Union Bank
        account, still land in "Family Transfer" and count as spend, because no Union Bank account
        exists in the ledger yet.
  - [x] Fix SBI UPI merchant extraction. The agent-written parser now emits `counterparty` from
        the narration's own name field, and the regex guesser is gone; one batched call replaces
        one call per line. The re-import made 349 payees from 939 rows, and 928 lines were
        categorized in about 9 minutes, including a 120-second timeout retry. Restoring
        `private/pre-m24.dump` removed the old 709 payees and their 703 rules. The names stay short
        ("Swiggy L", "ZEPTONO W") because SBI truncates them in the narration; that is the file,
        not the extractor.
  - [ ] Import Slice after the own-account fix, in period order (May 25 cycle first). The June
        cycle (25 May – 24 Jun) is committed: 8 rows, `VERIFIED`, closes at ₹2,023.00. July is
        held: the PDF closes at ₹8,821.92 but Slice's app says ₹6,798.92, and the app shows two
        repayments the PDF omits (₹200 on 27 Jun, ₹1,823 on 7 Jul). The August PDF also omits
        ₹3,004.67 and ₹3,794.25, and Slice's transaction list dates the ₹4,969.91 repayment 9 Sep
        where the PDF says 10 Sep. These are export gaps, so the remaining cycles wait on
        corrected exports or manual rows, not a parser fix.
  - [x] Release `v0.1.2` (run 41715939134), so the live image reads Slice's yearless period
        (`626e421`) and asks for own-account transfer confirmation.

### Engine measures (L5), after the backfill

- [ ] **Milestone 29:** run `arno backtest` on the real backfill, and publish the median
      30-day error and band calibration in place of the synthetic block. Remove the TODO at
      `engine/backtest.py:413`.
- [ ] Set the forecast-accuracy target in `system-design.md`. The success measures said to set it
      after the first real backtest.
- [ ] **Milestone 30:** run `arno hackerrank --dataset <folder>` on the 25 samples and
      publish the score. Set its target, and remove the TODO at `engine/hackerrank.py:420`.

### Evals and models (L2, L4)

- [ ] **Milestones 15 and 26:** run the scorecard on at least one local model through Ollama (for
      example, `qwen3:4b`). Today the table has one cloud model.
- [x] Rerun the cloud model on the full suite. The suite is 134 cases now, and the published run is
      402 trials on November 10, 2026 (`e445680`); it includes the projection and affordability
      cases for the first time, every gate passed, and two categories each slipped once in three
      trials (section E).
- [ ] Fill the **median cost** column. It's `—` today, so the scorecard is missing price data.
- [x] Run `wr_chai_voice_hinglish` against the configured chat endpoint (`evals/cases.py:441`). It
      is a write case and passed all three trials in the published run.
- [ ] **Latency:** the measured median is 16.5 s against the targets of about 5 s for one lookup
      and about 15 s for a multistep what-if. Either meet the targets or restate them in plan.md
      with the measured numbers.
- [ ] Answer the open L2 questions from real-use traces, and record the answers in plan.md §6:
  - Is the number check noisy? This decides between keeping the note and tappable numbers.
  - What are the real token counts, and is a 1-hour cache worth it?
  - What's the confirm-card tap rate?
  - Are the model timeouts right for a real provider?

### Watch and capture (L6, L7), on real endpoints and a phone

- [ ] Get HTTPS working through `tailscale serve` on the host, and reach it from a phone. This
      covers milestone 4, which was never exercised.
- [ ] Install the PWA on Android and receive a real web push (`web/src/sw.ts:4`,
      `notifications.py:204`).
- [ ] Walk through Home's notifications dialog and Settings in a real browser (`web/src/pages/Home.tsx:1`).
- [ ] Run a weekly briefing and a scheduled question against the real chat endpoint, including the
      timeout behavior (`jobs/briefing.py:222`, `jobs/questions.py:48`).
- [ ] Configure `STT_*` (`speaches` or Qwen3-ASR), then record real Hinglish voice notes.
      Compare cloud against local, and keep or drop `STT_PAYEE_CONTEXT` based on the results
      (`capture/transcribe.py:9`).
- [ ] Configure `VISION_*`, then log real receipts and UPI screenshots (`capture/photo.py:8`).
- [ ] Share a screenshot from GPay, PhonePe, or Paytm into the installed PWA (`web/src/sw.ts:6`).

### Platform

- [ ] Check Compose restarts and the 30-second grace period on a Linux host (plan.md §6, L0).
- [ ] **Milestone 37 as written:** restore the off-site backup on a different machine, using only
      the restic password. On Drive this also needs the OAuth client (or a manual folder download
      in the Drive web UI), so the drill tests the recovery kit, not the password alone.

## C. Human gates

These start only after group B's durability and import work is done.

- [ ] **Milestone 41:** four weeks of daily use with real money, without falling back to a
      spreadsheet or mental math. Start the clock on the first real day.
- [ ] Measure the categorizer during those four weeks. The target is 90% or more of LLM
      categories left uncorrected, read from `audit_log`.
- [ ] **Milestone 40:** someone else installs Arno from `self-hosting.md` alone. Fix every
      gap they report in the docs.
- [ ] **Freeze:** no fix pushed for one week after the last item, per "What 'shipped' means".
- [ ] Remove the TODO in `src/arno/__init__.py`, and mark plan.md as shipped.

## D. Observability

Reconciled November 5, 2026. Arno uses Pydantic AI's native OpenTelemetry instrumentation
and the standard Phoenix SDK, exporting directly to Phoenix. Standard Python/Uvicorn logs stay on
Docker stdout. There is no Collector, Loki, Prometheus, Grafana, custom metric, manual span, log
bridge, dashboard, or alert pipeline. The operator path is
[`observability.md`](observability.md).

- [x] **Source and Compose.** One `phoenix.otel.register()` and `Agent.instrument_all()` per
      process; Phoenix is the only telemetry service. Its authentication, loopback UI, and own
      Postgres database and role remain.
- [x] **Removed custom stack.** Domain spans, metrics, the custom JSON formatter and access-log
      middleware, Collector/Loki/Prometheus/Grafana configs, probes, runbooks, dashboards, alerts,
      and their tests are gone.
- [x] **Business behavior preserved.** Shared daily spending enforcement, summarizer budgeting,
      approvals and guardrails, real error messages, the CLI store close, and the installed eval
      `REPO_ROOT` fix are unchanged.
- [x] **Native integration smoke.** A stub Phoenix OTLP endpoint receives Pydantic AI agent, model,
      and tool spans with one root, real parent links, and no SQL spans.
- [x] **Standard logs.** `logging.basicConfig` plus Uvicorn; Docker `json-file` rotation at three
      10 MB files per service; no central log store.
- [ ] **Owner deployment update.** Rebuild and recreate the API and Phoenix on the host with the
      commands in [`observability.md`](observability.md#owner-commands), without deleting volumes.
      Defaults are not claimed as deployed until the owner does this.
- [x] **Trace authentication.** Compose maps `PHOENIX_API_KEY` to the local
      `PHOENIX_ADMIN_SECRET` by default, so the normal start exports with no API-key creation or
      second restart. An explicit `PHOENIX_API_KEY` overrides the fallback.
- [x] **Retention.** Phoenix's supported default is 0 days (infinite); the repository sets no
      custom retention.

## E. Release-hardening pass, November 10, 2026

A four-wave audit over the money paths, the agent loop, imports, and the platform, then six
real-model journeys and three regression rounds. Strict type checking and the first published
scorecard followed. The record, with findings, commit SHAs, decisions, and the method to re-run it,
is [`release-hardening.md`](release-hardening.md). This section adds what the pass proved and what
it could not; the groups above keep their original detail.

### Closed here, for real

- [x] **Every area audit's fixes.** Ledger and write paths, reads, engine, agent loop, imports,
      watch jobs, and platform, in four waves on `main`, each with a regression test that failed
      before the fix. CI green before every wave; the suite grew from 661 to more than 1,100 tests.
- [x] **A financial invariant checker.** `tests/invariants.py`, one checker over a whole database,
      green on the demo persona and after every journey write. A
      `uv run python -m tests.invariants` run on a scratch `_test` database now belongs in the
      definition of done for ledger, tool, import, and engine changes (`AGENTS.md`).
- [x] **Real journeys without a browser.** `tests/e2e/` starts a real API process, enrolls a
      software passkey, and speaks the web app's own HTTP: uploads, confirm cards, approvals,
      imports, commits, undo. Six journeys and three regression rounds ran on it with the real chat
      model. Results: [`e2e-run-log.md`](e2e-run-log.md).
- [x] **Migration rehearsal on production-shaped data.** Migrations `0022`, `0023`, and `0024`
      applied to a throwaway copy of the live ledger, with the invariants green before and after and
      the copy dropped.
- [x] **The openings repair, rehearsed.** `arno repair-openings --dry-run` then the apply on a
      second copy: the named import's opening moved to its statement's first day, the closing
      difference was zero, the current balance was unchanged, and a second dry run found nothing.
- [x] **Secret redaction and export hygiene.** A password, OTP, PIN, or card number typed in chat is
      redacted in English, Hinglish, and Devanagari before the model, storage, titles, or traces see
      it, and the JSON and CSV exports exclude credential and session tables.
- [x] **Strict type checking.** `uv run pyrefly check` is at zero diagnostics with six suppressions,
      each with its reason; the policy, the rules for each path, and the plan to tighten them are in
      [`typing.md`](typing.md). A pre-commit hook and a CI step run it over the whole project
      (`5788325`; the Type check step is green in CI).
- [x] **The eval scorecard, published.** The 134-case suite ran 402 trials on November 10, 2026
      (`e445680`): injection gate 150/150, answer number gate 78/78, answers 96% (25/26), writes
      22/22, honesty 11/11, advice 11/11, never-moving-money 10/10, long threads 3/4. The two
      one-trial misses are named in
      [`release-hardening.md`](release-hardening.md#known-limitations-left-open).

### Simulated, and still unchecked

- [ ] **Milestone 40, install from the docs alone.** Simulated on November 10: same owner, same
      Windows host, `127.0.0.1`, a software passkey, no tailnet and no phone. A second person and a
      real browser remain the gate.
- [ ] **Milestone 37, restore on a different machine.** Simulated: a second Compose project with new
      volumes, restored from the off-site backup. On Drive it also needed the OAuth client, so the
      drill tested the recovery kit, not the restic password alone.
- [ ] **The restart mid-parse.** Simulatable, not run this pass: the driver and the `jobs` table now
      make it reproducible with `driver up --keep` after a kill.

### Blocked on the owner

- [ ] **Milestone 29, the backtest.** A run against the copy refused to publish: with no income
      schedule in the ledger, every month is unusable as a measurement. Add the salary schedule in
      chat (or setup), then `uv run arno backtest --publish`, and set the accuracy target in
      `system-design.md` from the result.
- [ ] **Milestone 30, the HackerRank score.** The dataset is not in the repository and was not
      available on this machine. Put the 25 samples in a folder, then
      `uv run arno hackerrank --dataset <folder>`.
- [ ] **Union Bank.** No statement has been provided, so its parser decision stays open. Add 12
      months under `private/statements/union-bank/` and run `arno import-spike`.
- [ ] **Milestone 24 and the remaining Slice cycles.** Several repayments are missing from the
      PDFs, so those cycles wait on corrected exports or manual rows. The backfill still has to be
      committed through the review screen, not local code.
- [ ] **The live instance's repair.** After upgrading, run `uv run arno repair-openings
      --dry-run`, then `uv run arno repair-openings --yes`. It is idempotent, and it is what
      makes every committed statement's opening sit at its first day on the real ledger.

### Impossible here

- [ ] **Milestone 41, four weeks of daily use.** Needs the owner's real use, with no spreadsheet or
      mental-math fallback. The clock starts on the first real day.
- [ ] **The freeze week.** Starts after the last item above; it cannot be simulated.
- [ ] **A real phone: PWA install and web push.** Needs Android and a web-push endpoint. The audit
      verified the server and the service worker, not the device.
- [ ] **Owner voice notes.** Needs a microphone, an `STT_*` endpoint, and Hinglish audio; no audio
      corpus was available, so the Hinglish cases still test the agent downstream of a transcript.
- [ ] **Statement passwords.** Always the owner's: a statement password goes on the import card,
      never in chat, a test, or a doc.
- [ ] **Real receipts and UPI screenshots.** No `VISION_*` endpoint was configured; the extraction
      path is covered by scripted fixtures only.

### Found and not yet fixed

- [ ] **Install documentation.** The fresh-install rehearsal found twelve gaps, the costly ones
      being: passwords generated with `openssl rand -base64` break `DATABASE_URL` (use `-hex`); the
      README omits the four `PHOENIX_*` values, so Phoenix restart-loops; `restic snapshots` needs
      `--entrypoint restic`; `.env.example` pins an old `ARNO_VERSION`; and a second stack
      needs `COMPOSE_PROJECT_NAME` and `PHOENIX_UI_PORT`. They are being fixed in the same release
      step and are not yet re-checked by a second installer.
- [ ] **`observability.md` and host tracing.** The settings table still gives a host default of
      `http://127.0.0.1:6006/v1/traces`, while tracing is now opt-in and nothing is registered
      without a collector endpoint. The code is right; the guide needs the same correction.
