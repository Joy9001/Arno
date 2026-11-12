# Implementation seams for model cost accounting

Research note, November 12, 2026. This note maps where inference enters Arno and what must change so
every path is priced, gated, and durable without a second ledger or a new dashboard. It is not an
approved change to [`plan.md`](../../arno/plan.md).

No paid model request, eval, credential, or private file was used. I read the installed packages
(Pydantic AI 2.64.0, pydantic-ai-harness 0.64.0, genai-prices 0.2.1, per
[`uv.lock`](../../../uv.lock#L2248)) and Arno's source. Claims are labeled **official docs**,
**inspected source**, **inference**, **experiment**, or **unknown**.

**Parent resolution after inspection:** this note's dispatch inventory and native API findings are
evidence; its proposed implementation choices are not all adopted. The current
[OpenCode accounting contract](opencode-accounting-contract.md) resolves the documented field
semantics, and an [offline event probe](verification.md#capability-event-delivery-follow-up)
confirms filtered event delivery without an external stream handler. A `SpendLimits.price`
callback returns `Decimal | None`, so it needs an adapter around a structured price decision.
Keeping Anthropic in the generic OpenAI wire adapter, continuing paid work on an in-memory fallback,
reserving a batch only after remote submission, and independent counter/audit commits are not
acceptable final designs. The implementation plan must choose one durable authority for all call
paths and close those seams explicitly.

## What the code does today

The gate exists. Most paths attach `SpendLimits` from
[`spend_limits()`](../../../src/arno/agent/capabilities.py#L295), defaulting to one
`Budget(usd=20, window="day", name="shared daily")`
([`capabilities.py:286`](../../../src/arno/agent/capabilities.py#L286)). Counters live in Postgres
through `PostgresSpendStore`, so a restart does not reset the day
([`spend.py:160`](../../../src/arno/agent/spend.py#L160)).

What is missing is everything around that gate: the price it charges, the identity it charges
against, the record of what it decided, and any admission guarantee. Three facts matter.

1. **There is no reservation.** `before_model_request` reads the counters and refuses if one is
   already spent
   ([`_capability.py:286`](../../../.venv/Lib/site-packages/pydantic_ai_harness/spend/_capability.py#L286)).
   `exhausted()` says it "reads the counters; it reserves nothing and records nothing"
   ([`:619`](../../../.venv/Lib/site-packages/pydantic_ai_harness/spend/_capability.py#L619)), and
   `PostgresSpendStore` documents the read and its increment as separate round trips, so concurrent
   runs can each pass ([`_postgres.py:64`](../../../.venv/Lib/site-packages/pydantic_ai_harness/spend/_postgres.py#L64)).
   **inspected source.**
2. **The registry does not know Arno's models.** With the bundled snapshot, `calc_price` raises
   `LookupError` for `deepseek-v4.1-flash` under both `openai` and `deepseek`, and for any model
   under `provider_id='ollama'`, which is not a provider in 0.2.1 at all; it prices `gpt-5.4` at
   `0.0040` for 1,000/100 tokens. **experiment.**
3. **A reported charge is ignored on the success path.** `_price_of()` consults `price=`, then
   `response.usage.cost` only when usage is incomplete or the attempt failed, then
   `response.cost()`, then zero
   ([`:811`](../../../.venv/Lib/site-packages/pydantic_ai_harness/spend/_capability.py#L811)).
   **inspected source**, matching [`verification.md`](verification.md).

So the meter charges `$0` for the default chat model: a correctness bug, not a missing feature.

## The complete inference dispatch inventory

Every place Arno can cause a provider to bill. `gate` is the capability attached; `pricing` decides
the number.

| # | Path | Entry point | Gate | Pricing |
|---|---|---|---|---|
| 1 | Chat turn (API) | [`turns.py:616`](../../../src/arno/agent/turns.py#L616), agent from [`app.py:145`](../../../src/arno/api/app.py#L145) | `turn` run + `shared daily` | registry |
| 2 | CLI chat | [`cli.py:236`](../../../src/arno/cli.py#L236), run at [`cli.py:290`](../../../src/arno/cli.py#L290) | same, `SharedSpendStore` | registry |
| 3 | Compaction summarizer | [`capabilities.py:406`](../../../src/arno/agent/capabilities.py#L406) | second `SpendLimits`, day + conversation ([`:377`](../../../src/arno/agent/capabilities.py#L377)) | registry |
| 4 | Parser subagent | [`parser.py:1073`](../../../src/arno/agent/parser.py#L1073), built at [`app.py:136`](../../../src/arno/api/app.py#L136) | shared store | registry |
| 5 | Parser spike harness | [`spike.py:316`](../../../src/arno/importers/spike.py#L316) | **none** (`spend_store=None`) | none |
| 6 | Categorizer, immediate | [`categorizer.py:172`](../../../src/arno/agent/categorizer.py#L172), from [`importers/categorize.py:129`](../../../src/arno/importers/categorize.py#L129) | `shared daily` | registry |
| 7 | Categorizer, batch submit | [`categorizer.py:316`](../../../src/arno/agent/categorizer.py#L316) | `shared daily`, checked **before** submit | registry, **after** results |
| 8 | Categorizer, batch collect | [`importers/categorize.py:228`](../../../src/arno/importers/categorize.py#L228) | `shared daily`, fresh store | registry, on the collect day |
| 9 | Statement vision | [`vision.py:43`](../../../src/arno/importers/vision.py#L43), parser tool at [`parser.py:790`](../../../src/arno/agent/parser.py#L790) | `shared daily` | registry |
| 10 | Receipt/UPI photo | [`photo.py:51`](../../../src/arno/capture/photo.py#L51) | `shared daily` | registry |
| 11 | Speech to text | [`transcribe.py:87`](../../../src/arno/capture/transcribe.py#L87) | **none**, raw `httpx` | **none**, no usage parsed |
| 12 | Weekly briefing prose | [`briefing.py:243`](../../../src/arno/jobs/briefing.py#L243) | `shared daily` | registry |
| 13 | Scheduled question | [`questions.py:48`](../../../src/arno/jobs/questions.py#L48) | `scheduled question` run + `shared daily` | registry |
| 14 | Eval trial | [`runner.py:214`](../../../evals/runner.py#L214) | `trial` conversation budget, in-memory | registry |
| 15 | Eval judge | [`judge.py:33`](../../../evals/judge.py#L33) | **none** | none |

Retries and fallbacks are already counted. `wrap_model_request` accrues every billed response the
provider returned during the wrapped lifecycle, including `response.failed_attempts` from a
`FallbackModel`, responses `ModelFallback` rejected, and attempts recorded with no response
([`_capability.py:298`](../../../.venv/Lib/site-packages/pydantic_ai_harness/spend/_capability.py#L298)).
An attempt whose usage is `None` is skipped by `_billed_attempts`
([`:872`](../../../.venv/Lib/site-packages/pydantic_ai_harness/spend/_capability.py#L872)): right
for a connection that never reached the provider, wrong for a timeout after acceptance.
**inspected source.** Arno uses both mechanisms: `ModelFallback` for chat
([`capabilities.py:429`](../../../src/arno/agent/capabilities.py#L429)), `FallbackModel` for the
summarizer ([`:384`](../../../src/arno/agent/capabilities.py#L384)).

## Proposed final shape

### Provider config and billing identity

`RoleConfig` carries `base_url`, `api_key`, `model`, `context_window`
([`models.py:46`](../../../src/arno/agent/models.py#L46)), and `build_model` chooses
`OllamaProvider` or `OpenAIProvider` from the URL
([`models.py:95`](../../../src/arno/agent/models.py#L95)). Every non-Ollama endpoint therefore
presents to Pydantic AI, and to Phoenix, as provider `openai`.

Add one field, `provider`, from an explicit `{PREFIX}_PROVIDER` with a small allowlist: `openai`,
`anthropic`, `google`, `deepseek`, `openrouter`, `ollama`, `opencode`, `unknown`. Default from the
host for two cases (`opencode.ai` to `opencode`, an Ollama port on loopback to `ollama`) and
to `unknown` otherwise. This is a billing identity, not a transport: the model object stays
`OpenAIChatModel`.

The important negative rule: **loopback never implies a zero price.** `RoleConfig.configured`
already treats `"127.0.0.1" in base_url` as configured
([`models.py:53`](../../../src/arno/agent/models.py#L53)), and every unset role defaults to the closed
port `127.0.0.1:9` ([`:74`](../../../src/arno/agent/models.py#L74)). Inferring zero from loopback
would bill an unconfigured role as free. A zero is valid only when the operator declares
`{PREFIX}_PROVIDER=ollama` with a real base URL.

### Pricing resolver

One function, `price_response(response) -> PriceDecision`, carrying
`source in {provider_reported, catalog, own_zero, unknown}`, `amount: Decimal | None`,
`revision: str | None`, and `reason: str | None`. Order:

1. `provider == "ollama"`, explicitly configured: `Decimal(0)`, `own_zero`.
2. A charge the response carries that the identity is documented to report:
   `provider_reported` (**official docs**). OpenRouter documents an account charge in `usage.cost`;
   whether OpenCode reports one is **unknown**, and stays unknown until observed.
3. `genai_prices.calc_price(usage, response.model_name, provider_id=provider)`, keeping
   `auto_update_timestamp` as the revision. `LookupError` and `ValueError` both mean `unknown`,
   which is already the harness's reading ([`:853`](../../../.venv/Lib/site-packages/pydantic_ai_harness/spend/_capability.py#L853)).
4. Otherwise `unknown`.

That is the native decision: no catalog fork, no LiteLLM dependency, no homegrown engine. It covers
the 47 bundled provider ids (**experiment**) and nothing else.

**Narrow OpenCode support** means the operator may declare a rate for a specific `{PREFIX}_MODEL`
as `{PREFIX}_PRICE_INPUT_PER_MTOK`, `_OUTPUT_PER_MTOK`, `_CACHE_READ_PER_MTOK`, stored with a
revision. Absent that, an OpenCode response is `unknown`, never the direct-DeepSeek rate; matching a
model name to another vendor's catalog is the error
[`README.md`](README.md#L117) warns about.

Wire it as `SpendLimits(price=price_response, on_unpriced="zero")`. A `None` from `price` falls
through to the registry and ends as `unknown`, so the native `unpriced` counter increments. Keeping
`"zero"` is deliberate: `"raise"` fires after a request has been billed, so it is a tripwire, not
admission control.

### The typed record

One append-only table, written from `SpendRecordedEvent` by a capability subscribing with
`@on_event` ([`abstract.py:1045`](../../../.venv/Lib/site-packages/pydantic_ai/capabilities/abstract.py#L1045)).
The event carries `model`, `usage`, `usd`, `priced`, `failed_attempt`, and per-budget status
([`_events.py:32`](../../../.venv/Lib/site-packages/pydantic_ai_harness/spend/_events.py#L32)) but no
run, conversation, or agent name. Read those from the current span, as
[`RootSpanIO`](../../../src/arno/agent/capabilities.py#L111) reads `user.id` (**inference**; the test
below settles it).

Columns are numeric or identifier, never prompt or statement text:

```text
id                  text primary key
attempt_key         text not null unique   -- run_id|run_step|model|usage digest|attempt index
occurred_at         timestamptz not null   -- the response timestamp
recorded_at         timestamptz not null default now()
run_id, conversation_id, agent_name, job_kind, job_id
provider            text not null          -- billing identity
transport_provider  text not null          -- what Pydantic AI named it, e.g. openai
model, model_ref, endpoint_host
batch_id, batch_request_index
price_source        text not null check (price_source in
                      ('provider_reported','catalog','own_zero','unknown'))
price_revision      text
usage               jsonb not null         -- input, output, cache_read, cache_write,
                                           -- reasoning, requests, unmeasured
gross_usd           numeric(20,10)         -- catalog-equivalent value
net_usd             numeric(20,10) not null-- what the counter applied
allowance_units     numeric(20,10)         -- subscription debit, when known
unknown_reason      text
failed_attempt      boolean not null default false
budget_keys         text[] not null
```

Gross, net, and allowance answer three questions and must never be summed as one. Own Ollama is
`gross_usd = null`, `net_usd = 0`, `own_zero`; an unknown row is `net_usd = 0`, `unknown`, with the
reason; a subscription row can carry `allowance_units` with zero `net_usd`.

**One authority.** The native counter decides admission. This table audits what the counter was
told, is written after accrual, and is never read to decide admission. That avoids a second USD
ledger: no parallel total, no reconciliation job that could disagree, no code path that adds two.

### Budgets, admission, and durability

Keep the native windows and fix their basis.

- **Day basis.** `Budget(window="day")` buckets on `now.date()` from `SpendLimits.clock`
  ([`_budget.py:207`](../../../.venv/Lib/site-packages/pydantic_ai_harness/spend/_budget.py#L207)).
  The default is UTC; Arno's product day is IST (`INDIA` in
  [`jobs/schedules.py`](../../../src/arno/jobs/schedules.py), `ledger_today`). Pass
  `clock=lambda: datetime.now(INDIA)`. Store expiry stays on the server clock, so the TTL skews by
  5h30m against a 48h horizon: immaterial, worth one comment.
- **Run basis.** A resumed batch run gets a new `run_id`, so a `run` budget resets across a resume.
  The batch path counts on the day window and an explicit reservation, never a run window.
- **Token ceiling.** A dollar ceiling over an unknown price enforces nothing. Add `tokens` to the
  shared daily budget, and set `ModelSettings(max_tokens=...)` per role so the worst-case charge is
  computable. Where a route ignores `max_tokens` (a subscription passthrough, say), the bound does
  not hold: record the route as unbounded rather than assume it.
- **Summary attribution.** The summarizer's `SpendLimits` is a second instance sharing the day
  counter by name and window; its rows carry the summarizer's `agent_name`, so the total is right
  and the attribution visible. A reservation keyed per capability instance would break this.
- **Preflight.** Refuse before starting if the day budget is exhausted **or** the role's price
  source is `unknown` under a policy that promises a dollar ceiling:
  `{PREFIX}_PRICE_POLICY = estimate | provider_reported | refuse_unknown`.

**Reservation.** Native reservation is unavailable: `BatchSpendStore` has only `get_many` and
`add_many`, with no verb for holding a charge
([`_store.py:118`](../../../.venv/Lib/site-packages/pydantic_ai_harness/spend/_store.py#L118)).
Rather than fork the harness, adopt a bounded brake and state its guarantee:
`spend <= ceiling + K * max_charge_per_request`, where `K` is the maximum concurrent paid requests
and `max_charge_per_request` follows from `max_tokens` and the rate. `K` is small and known: one
turn per thread, two jobs ([`runner.py:273`](../../../src/arno/jobs/runner.py#L273)), eight
categorize chunks ([`categorizer.py:42`](../../../src/arno/agent/categorizer.py#L42)). State it in
`harness.md`; do not claim a strict ceiling.

**Batch is the one place a real reservation is possible and necessary.** Arno owns submission and
knows the request count. In the transaction that writes `categorize_runs.batch_runs` and enqueues
`collect_batch` ([`categorizer.py:348`](../../../src/arno/agent/categorizer.py#L348)), insert a
`batch_reservation` row: `batch_id`, `run_id`, `reserved_usd` (chunk count times the per-request
bound), `reserved_at`, `settled_at null`. Count unsettled rows against the day in the preflight.
Settle exactly once in `collect_batch`, in the transaction that writes `batch_answers`
([`importers/categorize.py:293`](../../../src/arno/importers/categorize.py#L293)), and attribute the
accrued rows to `reserved_at`'s day. An unsettled reservation past a bounded horizon is a visible
unknown.

**Retries and cancellations.** A transport retry that reaches the provider and dies before usage is
parsed is an unknown charge: record `unknown_reason = 'no usage after retry'`. A cancelled turn
accrues whatever core recorded in the `wrap_model_request` `finally`
([`:328`](../../../.venv/Lib/site-packages/pydantic_ai_harness/spend/_capability.py#L328)), so a
cancel before terminal usage is a lower bound and the record says so. `turns.py` already saves
`ending="cancelled"` ([`turns.py:591`](../../../src/arno/agent/turns.py#L591)); link the two by
`run_id`.

**Fail closed, exporter independent.** A failed **record** write leaves the audit incomplete for a
billed request: fail loudly, and let the next paid preflight refuse until it succeeds. A failed
**Phoenix export** changes nothing; `bootstrap_observability` already treats a missing collector as
off ([`observability.py:44`](../../../src/arno/observability.py#L44)).

The store is harder. `SharedSpendStore` reads zero and drops writes when Postgres is down
([`spend.py:88`](../../../src/arno/agent/spend.py#L88)), turning a strict budget into none. Replace
`_UnavailableStore` with an `InMemorySpendStore` holding the run ceiling, and record that window's
turns as `unknown`. The daily ceiling is then knowingly unenforced: say so in the log and on the
existing settings surface, and refuse background, batch, and eval work outright.

## Database, backup, and grants

- `spend_counters` and `spend_dedup` are created by the store at first use
  ([`_postgres.py:264`](../../../.venv/Lib/site-packages/pydantic_ai_harness/spend/_postgres.py#L264)),
  so no migration owns them and `app` needs `CREATE` at runtime. Add a migration with the pinned
  DDL, and a test that `arno migrate` leaves both present and round-trips a key. Create the charge
  table there too, with indexes on `occurred_at` and `(conversation_id, occurred_at)` and a unique
  `attempt_key`.
  `shortcut: DDL copied from harness 0.64.0, re-copy when the spend store changes`.
- **No `agent_ro` grant.** Cost records are operator data. The boundary is
  [`0005_grants.sql`](../../../src/arno/db/migrations/0005_grants.sql); the negative test belongs
  beside [`test_migrations.py:121`](../../../tests/test_migrations.py#L121).
- **Backup.** `pg_dump` carries both tables, and `BACKUP_GRANTS` gives the `backup` role `SELECT ON
  ALL TABLES` with default privileges
  ([`backup_restore.py:59`](../../../src/arno/backup_restore.py#L59)). Verify it in
  [`test_backup_restore.py`](../../../tests/test_backup_restore.py) with one spend row and one charge
  row.
- **Retention.** One row per request forever is fine at single-user volume and bad to guess about.
  Count rows per month; if it grows awkwardly, add a monthly `prune_inference_charges` job with a
  400-day horizon.

## Tests and exact commands

New `tests/test_inference_charge.py` covers resolver sources, record shape, unique attempt key, no
content, the IST day boundary, own-Ollama zero, and unknown-not-zero.

```bash
uv run pytest tests/test_inference_charge.py tests/test_budget_ceiling.py -q -n 0
uv run pytest tests/test_categorizer.py tests/test_jobs.py -q -n 0
uv run pytest tests/test_migrations.py tests/test_backup_restore.py -q -n 0
uv run pytest tests/test_capture.py tests/test_evals.py -q -n 0
uv run ruff check . && uv run ruff format --check . && uv run pyrefly check
uv run pytest -q
uv run arno demo seed && uv run python -m tests.invariants   # on a scratch _test database
```

## Dependency-ordered phases

1. **Billing identity.** `RoleConfig.provider`, the allowlist, the two host defaults, and the rule
   that loopback is not a price.
2. **Pricing resolver.** `PriceDecision`, `price=` at every construction site in the inventory,
   `on_unpriced="zero"`, and the operator rate override for OpenCode.
3. **Typed record.** Migration, `@on_event` subscriber, `attempt_key`, fail-closed write policy.
4. **Budget basis.** IST clock, token ceiling, `max_tokens` per role, preflight refusal for
   `refuse_unknown` roles.
5. **Batch reservation.** Reserve in the submit transaction, settle once in `collect_batch`,
   attribute to the submit day.
6. **Store resilience.** Bounded in-process brake, and refusal of background work while the ledger
   is down.
7. **The ungated paths.** STT records an explicit unknown; the eval judge gets the trial's store or
   an explicit eval budget; the spike harness gets a store or a comment.
8. **Docs and the counter migration.** `plan.md`, `harness.md`, `observability.md`, and the
   `spend_counters` DDL migration.

Phases 3 and 5 depend on 2; 4 depends on 2; 6 depends on 3.

## High-risk seams

- The summarizer's second `SpendLimits` ([`capabilities.py:378`](../../../src/arno/agent/capabilities.py#L378)):
  a per-instance reservation double-charges. Key on the store key.
- `_SingleConnectionPool` ([`spend.py:36`](../../../src/arno/agent/spend.py#L36)) opens a connection
  per run; reservation SQL must be short and never held across a model call.
- `collect_batch` runs on a later day than submission, in a fresh process with a fresh pool.
- Two fallback mechanisms both produce `failed_attempts`.
- Photo, vision, and briefing runs pass no `drain_events`, so whether `SpendRecordedEvent` reaches
  `on_event` there is **unknown**; the phase 3 test answers it, and the fallback is an explicit
  handler on those three calls.

## Findings and evidence gaps

The gate is sound and coverage is nearly complete; the meter is not. Arno prices its default chat
model at zero because no `price=` callback exists and genai-prices has no entry for it. Fixing
pricing, identity, and the record is small. Fixing admission is not: the harness offers a brake, not
a reservation, so the honest design states a bounded overshoot and reserves atomically only where
Arno controls submission.

Evidence gaps, each with a resolution path:

- **OpenCode billing semantics.** Whether it reports a charge, and in which field. Inspect a
  documented response schema or one explicitly authorized sample; until then, `unknown`.
- **`SpendRecordedEvent` without a stream handler.** Resolve with the phase 3 test.
- **Span attributes for run and conversation on every path.** Assert the row's `run_id` and
  `conversation_id` in that same test.
- **Real batch provider behavior.** A provider sandbox run, explicitly requested; the path is
  testable now with `FunctionModel`.
- **Phoenix cost for `provider=opencode`.** Phoenix prices `gen_ai.request.model` and maps
  `gen_ai.provider.name` to `llm.provider`; an OpenCode model has no manifest entry, so the trace
  shows `$0`. That is the unknown-as-zero problem in
  [`native-and-alternatives.md`](native-and-alternatives.md), and why the trace is not the authority.
- **Invoice reconciliation.** Unperformed. Resolve against a dedicated key over a stated interval,
  keeping fixed fees, allowance, and tax separate.

I added no cost dashboard, gateway, second ledger, or runtime change. Phoenix remains the only place
a person looks at cost.
