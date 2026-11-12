# Cost tracking acceptance log

Status on November 12, 2026: implemented and reviewed in the working tree, not deployed to the
owner instance. This records synthetic evidence for the
[feature definition of done](cost-tracking.md#completion-checklist). Paid providers, a real
ChatGPT sign-in, and owner-deployment acceptance remain pending.

## Isolation

Tests used Postgres 18 on loopback port 5439 and disposable databases ending in `_test`. The
scratch API used port 8017 with jobs disabled and model endpoints pointing at closed port 9.
The qualified Phoenix ran on port 6007 with its own scratch database. Owner services on ports
5433, 6006, and 8000 were not changed. No real statements, owner credentials, paid calls, evals,
sign-ins, or deployment changes were used as evidence.

## Automated checks

The final current-source `uv run pytest -q` passed **1,408 tests**, with 4 skips and two existing
assistant-ui adapter warnings, in 401.11 seconds. This includes the batch-client retry checks
after disabling hidden SDK retries on submission. The two warnings concern dropped unmatched
client tool results in the existing approval-history tests; no test failed.

The tests exercise these failure boundaries as well as successful calls:

- Pricing/provider fixtures: reported versus catalog estimates, explicit local zero, unknown and
  partial values, invalid amounts, cached/reasoning/audio units, actual served model, effective
  rates, provider identity, and OpenCode metadata through JSON and streaming SDK paths.
- Native adapters: Anthropic and ChatGPT construction, parser inheritance, no ChatGPT-to-paid-API
  fallback, credential persistence, refresh compare-and-swap, revocation, background consent,
  and state-checked manual OAuth callbacks. Credential values stay out of JSON/CSV exports.
- Metering/journal: intent before dispatch, immutable finalization, individual fallback attempts,
  cancelled/truncated streams, failed journal writes, opaque session/project attribution,
  content-free storage, denied `agent_ro` access, and eval attribution to the installation DB.
- Native budgets: shared-day persistence across store restart, repeated-post deduplication,
  exhausted-budget refusal, DB-down reads/writes, and concurrent overshoot. Two admitted $0.06
  calls can cross a $0.10 brake to $0.12; the next request is refused. No invoice hard cap is
  claimed.
- Stop propagation: jobs, categorizer chunks, vision, briefing, parser Monty, and native Code Mode
  preserve fatal cost errors. A script catching an external-call error still cannot start a
  second paid vision/model call after the original accounting stop.
- STT: provider-reported cost, including a billed empty transcript, real duration/token units,
  explicit local zero, and unknown usage without invented tokens or duration.
- Batch: estimate before auto-submit, no hidden resubmission, ambiguous acknowledgement or
  post-submit database failure is terminal, pending work blocks resubmission, native submit and
  collect spans remain logical, and repeated collection does not charge twice. Saved answers
  survive a settlement-store failure; recovery settles without another provider request.
  Negative corrections and marker gaps use stable tokens; expired deduplication pauses safely.
- Recovery: same-ID replay, failed export retained, disabled tracing, original project/session,
  manual retry of an accepted observation, ordinary interrupted intents becoming unknown, batch
  intents remaining pending, and distinct five-minute replay schedule keys.

`uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyrefly check` pass with zero
diagnostics and no new suppressions. `uv lock --check` passes. The dependency audit reports zero
forbidden/unknown licenses and names every direct dependency in `THIRD_PARTY_NOTICES.md`.
`tests/test_release.py` passes all 8 tests. Demo seed followed by
`uv run python -m tests.invariants` passes **640 financial invariant checks** on a scratch database.

## Persistence and app image

The real `pg_dump`/restore test preserves exact journal rows, including an unfinished call,
ChatGPT credential state, native spend counters, and deduplication rows. The existing encrypted
restic round-trip also passes. These checks use synthetic credentials and financial data.

A scratch schema-29 database was upgraded to schema 30 through the app image. Its unfinished
intent and exact existing spend value of 123456789 USD nanos survived. The rebuilt image starts
against schema 30; health and the served web app return HTTP 200. `chatgpt-login --help` and
`cost-replay --help` work inside the image. This proves image startup and migration preservation,
not live-provider access or adoption of the owner's pending batches.

| Artifact | Qualified image ID |
|---|---|
| `arno-cost-api:test` | `sha256:05b1839279120670c78d664dccdef0cbf6f0b8e8540509c0ae19ba19eeafecf3` |
| `arno-phoenix-cost:21.0.0` | `sha256:488b6b1a792a4779c6b2a22cd91ea7f11254b40c76aba6694856b8b644892831` |

Both images build successfully. The Phoenix build compiles its actual Relay/Vite frontend and
passes TypeScript checking. The app image uses the pinned lockfile and serves its built web app.
Phoenix's separate Elastic License 2.0 code and notices remain outside the Arno image.

## Phoenix end to end

The final Phoenix image passes **16 live OTLP/GraphQL contract tests** in
`tests/test_phoenix_cost_contract.py`. Its source, base-image and patch hashes are recorded in
the [build notes](../../deploy/phoenix-cost/README.md).

The integrated case runs the real SDK against synthetic HTTP usage, then passes through
`BillingMetadataModel`, `MeteredModel`, the Postgres journal/native counter, the standard batched
OTLP exporter, and the running Phoenix service. All surfaces agree on **$0.0000924** and 15
tokens. A zero wallet charge and a separate allowance value remain distinct. Same-ID replay
retains the original span and adds neither budget spend nor dashboard cost.

Contract cases cover supplied total precedence, an unsplit amount, explicit zero, unknown,
partial, invalid negative cost, logical-parent exclusion, duplicate replay, audio without
invented tokens, native attribute conversion, legacy catalog spans, two providers serving one
model name, unresolved time bins, and agreement across span/trace/session/project/model/range
views.

Rendered headless Chrome checks on the compiled final UI show:

- Known costs, measured local zero, partial and unknown labels; fallback, audio, batch, and
  estimated calls retain their expected totals and status.
- A total-only amount draws as `Total (unsplit)`; no prompt/completion split is fabricated.
- An unpriced-only range reads `Cost unknown for 1 call in this range`. A mixed-bin tooltip reads
  `Cost is a subtotal: 1 call unpriced.`
- Two providers serving one model produce two bars and distinct `provider / model` labels.
  The final root check verified the rendered provider prefixes after the provider-label patch.

These are rendered DOM checks, not screenshots. The provenance label mapping is verified in
source and the shipped bundle; a synthetic hover did not expose every provenance tooltip.
An unpriced-only bin has no plot marker; its unresolved range/count remains visible. Long axis
labels can truncate; the ordinary tooltip retains the full provider/model label.

## Reviews and fixes

Independent pricing/security, native-budget/batch, Phoenix, and terminal-failure reviews were
followed by root inspection and regression tests. Confirmed issues were fixed: credential export,
interrupted-row constraints, post-spend retries, stream completeness, unattended consent,
container login, missing attribution, batch acknowledgement gaps, swallowed sandbox cost stops,
stranded settlement, replay schedule deduplication, dashboard uncertainty/provider grouping, and
hidden batch SDK retries. A review claim that same-ID replay overwrites a full Phoenix span was
rejected after source inspection and the live duplicate-span contract proved it retains the span.

## Still pending

- Real OpenCode/OpenRouter/Anthropic response parity, including plan-funded Anthropic API
  credits. A wire fixture does not prove an account's debit or invoice.
- A real ChatGPT app sign-in, eligibility for the selected host, refresh, and owner consent.
- Owner cutover: preserve its counters and pending batches, verify its Phoenix credentials and
  retention, inspect one live trace, and accept rollback behavior. No owner service was changed.

Native budgets remain pre-request checks followed by response-time accrual. In-flight work can
overshoot. Catalog prices can lag until a dependency upgrade. Cost-only replay cannot recover
lost prompts or tool content and cannot correct an observation already ingested by Phoenix.
