# Completion checklist

This document tracks every remaining gate between Arno's built codebase (`v0.4.2`, layers L0–L10)
and "shipped" as defined in [`plan.md`](plan.md#what-shipped-means). Completed build phases,
release-hardening waves, and synthetic sign-off logs live in git history, GitHub Releases, and
[`docs/archive/records-2026-11/`](../archive/records-2026-11/).

Work through the sections in order: live deployment and real-data checks come before the four-week
daily-use clock starts.

## Owner deployment and live acceptance

These items verify live provider parity and current-release cutover on the owner's instance.

- [ ] **Deploy `v0.4.2` to the owner instance.** Pull the `0.4.2` API image, verify
      health and schema version `44`, and confirm normal operation over the tailnet.
- [ ] **Live daily-cap check.** Confirm `llm_usage` rows for chat on the owner's chosen provider
      carry a nonzero `usd` when the price catalog knows the model, and that the $20 cap pauses the
      next request. OpenCode-routed models are unpriced and log `$0`, so they never count.
- [ ] **Real-statement UI import pass.** Run a full real-bank import through the browser UI
      (upload, responsive chat during extraction, review, commit, and
      post-commit categorization).

## Real-data and device verification

Each item requires real statements, real endpoints, a phone, or a second machine and cannot be
closed in CI.

### Backups and platform

- [ ] **Verify the 48-hour stale-backup alert.** Stop the `api` container's backups intentionally (for example, unset `RESTIC_PASSWORD`), wait past
      the 48-hour threshold (`backup_check` runs daily at 03:00 Asia/Kolkata), confirm one
      `backup_stale` inbox alert appears and stays deduplicated on the following day, then restore
      the setting and confirm catch-up backup succeeds.
- [ ] **Test Linux Compose shutdown.** Verify `docker compose` restarts and the 30-second
      `stop_grace_period` on a Linux host ([`plan.md`](plan.md#6-open-questions)).
- [ ] **Fresh-machine off-site restore drill (milestone 37).** Restore the latest encrypted off-site
      backup onto a separate machine using only the recovery kit and `RESTIC_PASSWORD`, and verify
      all manifest and balance checks pass.

### Statements

- [ ] **Collect and test Union Bank statements.** Add 12 months of real Union Bank statements under
      `private/statements/union/`, import them through the review screen, and record the format and
      extraction result in [`specs/import.md`](specs/import.md#bank-format-findings) and
      [`plan.md`](plan.md#6-open-questions).
- [ ] **Complete the 12-month real backfill through the review screen (milestone 24).** Reconcile
      the remaining Slice cycles (blocked on missing repayments in Slice's exported PDFs) and
      commit the backfill through the review screen so baselines reflect every active account.

### Models, latency, and traces

- [ ] **Reconcile cloud latency targets.** The measured eval median is 23.4 s against the
      `plan.md` targets of about 5 s for a single lookup and 15 s for a multistep what-if. Either
      meet the targets or update `plan.md` with measured expectations.
- [ ] **Answer open L2 trace questions from real usage.** Inspect live Phoenix traces to decide
      whether rule 1 holds without a post-reply number check, measure real
      token counts and prompt-cache hit rates, check confirm-card tap rates, and confirm model
      timeouts ([`plan.md`](plan.md#6-open-questions)).

### Phone, voice, and vision

- [ ] **Complete phone tailnet access (milestone 4).** Reach `https://<host>.ts.net` from an
      Android phone over Tailscale and sign in as the owner (the host half already returns `200`).
- [ ] **Test real Hinglish voice capture.** Configure `STT_*` (a cloud or self-hosted
      OpenAI-compatible endpoint), record real Hinglish voice notes, and decide whether to
      keep `STT_PAYEE_CONTEXT=1` (`src/arno/capture/transcribe.py:14`).
- [ ] **Test real photo capture and Android share target.** Configure `VISION_*`, log real receipts
      and UPI screenshots (`src/arno/capture/photo.py:8`), and share a screenshot directly from
      GPay, PhonePe, or Paytm into the installed PWA (`web/src/sw.ts:6`).

## Human ship gates

Start these gates after owner deployment, durability, and backfill items above are complete.

- [ ] **Four weeks of daily real-money use (milestone 41).** Use Arno daily for four weeks without
      falling back to a spreadsheet or mental math.
- [ ] **Measure live categorizer accuracy.** Confirm from `audit_log` during the four-week window
      that at least 90% of LLM-assigned categories remain uncorrected.
- [ ] **Second-person installation from documentation alone (milestone 40).** Have someone else
      install Arno using only [`operations/self-hosting.md`](operations/self-hosting.md) and fix
      every gap they encounter.
- [ ] **One-week freeze.** Ship no code fixes for seven consecutive days after closing the gates
      above.
- [ ] **Mark shipped.** Remove the ship-gate `TODO` in [`src/arno/__init__.py`](../../src/arno/__init__.py)
      and mark [`plan.md`](plan.md) as shipped.
