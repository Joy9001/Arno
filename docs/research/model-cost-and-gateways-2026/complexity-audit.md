# Cost plan complexity audit

November 12, 2026. Scope: the implementation plan, not a whole-repository code audit. Assumed load:
one owner, one API process, occasional CLI, overlapping chat/import/scheduled jobs. The original
draft had 649 lines; the revised plan has 324, a reduction of 325 lines. The revised
[plan](../../arno/cost-tracking.md) supersedes its larger design.
No runtime code, dependency, deployment, or real-model configuration changed in this audit.

The DeepSeek V4.1 Flash High review inspected the plan, model/spend/capture/batch paths, and native
spend APIs. Parent verification checked public store correction/deduplication, expiry defaults,
partial-usage behavior, and the existing job runner. Older review findings identified genuine
gaps but their proposed platforms were not proportionate fixes.

## Should fix: removed from the plan

1. **Replacing native budgets with a reservation engine.**
   **What this is:** locks, allocations, leases, upper-bound reservations, and manual settlement
   replaced Spend Limits. **Problem:** normal overlapping calls forced an invoice-cap architecture
   when the existing product promised runaway brakes. **Fix:** retain native Spend Limits and
   Postgres counters, add pricing and fail-closed handling, and document in-flight overshoot.
   **If kept:** the owner maintains another accounting engine without gaining exact invoice truth.

2. **Turning Phoenix into a receipt service.**
   **What this is:** observation-history tables, versioned POST/GET APIs, hash conflicts, custom
   OTLP transport/processor, and per-call readback. **Problem:** ordinary completed calls need one
   immutable cost, not invoice-driven edits. **Fix:** supplied-cost/provenance/unknown handling in
   existing Phoenix storage and views; standard batched export. **If kept:** each Phoenix upgrade
   must carry an Arno-specific billing API and delivery protocol.

3. **Mirroring the entire price catalog into Postgres.**
   **What this is:** catalog download, validation, activation, startup seeding, and stale-price
   jobs. **Problem:** a dependency's installed price snapshot became another user-state service.
   **Fix:** use the locked snapshot, record effective rates/package version per call, and upgrade
   deliberately. **If kept:** every restore and CLI start must participate in price synchronization.

4. **Invoice reconciliation, quota caps, and indefinite uncertainty locks.**
   **What this is:** an account-report reconciler, local Go allowance enforcement, and owner
   resolution states. **Problem:** account totals and other clients cannot establish per-call
   charges or remaining subscription quota. **Fix:** retain reported allowance/charge as separate
   observations; do not enforce invented quota or rewrite traces from monthly bills.
   **If kept:** extra counters suggest accuracy that the provider data cannot support.

5. **Rewriting microphone recordings to WAV.**
   **What this is:** browser decoding/encoding and server frame validation to prove a duration
   bound. **Problem:** a voice note required new audio code solely for the discarded reservation
   guarantee. **Fix:** preserve formats and byte limits; price reported or defensibly estimated
   units, and expose missing usage. **If kept:** accounting work risks breaking voice capture.

6. **Making every provider family a launch gate.**
   **What this is:** fixtures/transports for all API families and billing modes, configured or not.
   **Problem:** one owner's setup waits for integrations nobody uses. **Fix:** qualify configured
   native profiles and narrow OpenCode parsing; keep subscription access already in scope.
   **If kept:** future-provider testing delays fixing the actual unpriced deployment.

7. **Overclaiming batch atomicity and then adding recovery machinery.**
   **What this is:** a remote submit and local budget row were treated like one transaction.
   **Problem:** enqueue can auto-submit, native replay markers expire, and delayed corrections can
   outlive daily counters. **Fix:** pre-account estimates through public store APIs before enqueue,
   retain existing batch state/keys/tokens, cover replay expiry, and pause acknowledgement gaps.
   **If kept:** either a distributed protocol grows, or a supposedly simpler implementation double
   charges a late collection. Keep the concrete guards; remove the general reservation platform.

## Safeguards retained after parent review

A small cost-only call journal and same-ID replay remain because Phoenix is the only cost
dashboard and a backend outage must not erase recorded costs. This is deliberately narrower than
durable full-trace delivery: no receipt API, custom wire protocol, per-span acknowledgement,
version history, or content recovery. Standard exporter success is not proof of per-span ingestion;
keep observations inspectable and replayable instead of promising exactly-once delivery.

The review's suggestion to post batch deltas with stable tokens was incomplete without expiry
handling. Native `SpendEntry` supports negative USD, but replay markers normally last one hour and
daily counters 48 hours. The revised plan requires an adequate deduplication horizon, nonexpiring
shared-day counters, durable applied markers, and no blind retries beyond the replay horizon.
Counter accrual and the batch row are not claimed to commit atomically.

Keep preflight pricing checks as well as `on_unpriced="raise"`: the latter runs after spending.
Keep the focused Phoenix patch: stock ingestion ignoring supplied costs is an actual defect,
not optional complexity. Preserve per-attempt identity, privacy, grants/backup/restore, all-call
coverage, fail-closed paid budgets, and rendered Phoenix acceptance.

## Verification and limits

An offline public-API probe recorded a `$0.002` known subtotal from an incomplete response,
incremented `unpriced_requests`, and raised `UnpricedModelError`. Another posted a `$0.10` batch
estimate and a `$-0.03` correction twice with the same token; the result stayed `$0.07`. These use
`FunctionModel` and `InMemorySpendStore`, not provider calls or a running database.

Postgres transaction/deduplication behavior and retention defaults were inspected in the installed
harness source. Concurrency, restart, long-horizon expiry, Phoenix service/UI patch, outage replay,
and real-provider parity remain unchecked implementation acceptance. Runtime line/dependency
savings are not measured; only the plan reduction is counted.
