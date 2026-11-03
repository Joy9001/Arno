# Completion checklist

Audit on November 3, 2026, at `109c7ea`. It checks [`plan.md`](plan.md) §5 and "What 'shipped'
means" against the code, CI, the live instance, and the TODOs in the source.

**Verdict: not complete.** Every layer's code exists, and all of group A is closed: `main` is green
in CI and the versioned images are published. Two kinds of gap remain:

- Measurements that need real data, real endpoints, or a phone were never run.
- The human gates haven't started. The live ledger has 3 accounts, 0 transfers, and
  0 conversations, so the four-week clock for milestone 41 isn't running.

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
- [x] **Tag `v0.1.0`.** Then confirm that `release.yml` pushes both `ghcr.io/joy9001/fin-agent`
      images and that `self-hosting.md` pulls them. Run 41628402245 pushed `0.1.0` and `v0.1.0` for
      `fin-agent` and `fin-agent-backup`; `compose.yaml` pins `0.1.0` by default.
- [x] *(Optional)* Remove the backup manifest race. The dump and the manifest now come from one
      `pg_export_snapshot()`, and `tests/test_l8_ship.py` forces a write between them.

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
  - [x] Restored from Drive into a scratch database with `fin-agent restore`. All five checks
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
      then run `fin-agent import-spike`.
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
  - [ ] Release `v0.1.2`, so the live image reads Slice's yearless period (`626e421`) and asks for
        own-account transfer confirmation.

### Engine measures (L5), after the backfill

- [ ] **Milestone 29:** run `fin-agent backtest` on the real backfill, and publish the median
      30-day error and band calibration in place of the synthetic block. Remove the TODO at
      `engine/backtest.py:413`.
- [ ] Set the forecast-accuracy target in `system-design.md`. The success measures said to set it
      after the first real backtest.
- [ ] **Milestone 30:** run `fin-agent hackerrank --dataset <folder>` on the 25 samples and
      publish the score. Set its target, and remove the TODO at `engine/hackerrank.py:420`.

### Evals and models (L2, L4)

- [ ] **Milestones 15 and 26:** run the scorecard on at least one local model through Ollama (for
      example, `qwen3:4b`). Today the table has one cloud model.
- [ ] Rerun the cloud model on the full 120-case suite. The 4 L5 projection and affordability
      cases have never run on a real model.
- [ ] Fill the **median cost** column. It's `—` today, so the scorecard is missing price data.
- [ ] Run `wr_chai_voice_hinglish` against the configured chat endpoint (`evals/cases.py:441`).
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
- [ ] Walk through the Inbox and Settings screens in a real browser (`web/src/panels/Inbox.tsx:1`).
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
- [ ] **Milestone 40:** someone else installs Fin-Agent from `self-hosting.md` alone. Fix every
      gap they report in the docs.
- [ ] **Freeze:** no fix pushed for one week after the last item, per "What 'shipped' means".
- [ ] Remove the TODO in `src/fin_agent/__init__.py`, and mark plan.md as shipped.
