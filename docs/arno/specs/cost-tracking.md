# Model cost tracking

This specification owns the model-usage log and the shared daily spend cap. [`plan.md`](../plan.md)
owns the decision, and [`observability.md`](../operations/observability.md) owns Phoenix tracing,
which is separate from cost.

## Usage log

Every model response writes one row to `llm_usage` (migration `0045`): `created_at`, `role`
(`chat`, `summary`, `parse`, `categorize`, `vision`, `briefing`, or `stt`), `model`,
`input_tokens`, `output_tokens`, and `usd`. The `agent_ro` role has no grant on the table, because
spend is operational data, not ledger data. A `pg_dump` carries it like any other table.

`usage_hooks(role)` in `src/arno/agent/usage.py` is a core PydanticAI `Hooks` capability attached to
each agent. Its `before_model_request` hook checks the cap, and its `after_model_request` hook
records the response. The cost is the response's `usage.cost`, which PydanticAI computes from the
`genai-prices` catalog. There is no pricing module, retry-aware accounting, or per-run budget.

Speech-to-text is not an `Agent` run. `transcribe_audio` checks the cap before the request and calls
`record_usage` with the token counts and cost the endpoint reported, or zero when it reports none.
A malformed `usage` object never discards the transcript.

## Daily cap

All roles share one cap of $20 a day (`DAILY_LIMIT_USD`). The day is India time: the cap is the sum
of `usd` since the last midnight in `Asia/Kolkata`, so it resets at midnight IST.

- The check runs before each request, so the cap is a runaway brake and not an invoice limit. A
  response that crosses it still lands in the log, and the next request is refused with
  `DailyBudgetExceeded`.
- The check reads Postgres on every call and raises on failure. An unreachable database never lets
  inference run uncapped.
- Callers treat `DailyBudgetExceeded` and `UsageLimitExceeded` (`COST_STOP_ERRORS`) as a plain stop:
  chat shows a paused message, and background jobs end without a retry.

## Unpriced models

`genai-prices` does not price every model. When it returns no cost, `record_usage` logs a warning
and records `$0`, so those calls never count toward the cap. In particular, `genai-prices` does not
price OpenCode-routed models such as DeepSeek on OpenCode Go, so OpenCode traffic is logged with
tokens but with `usd = 0` and is not capped. Local endpoints (Ollama) are unpriced for the same
reason. If a paid model that the catalog lacks becomes the default, add a per-model price override
then.

## Tracing

Tracing is independent of cost. Phoenix prices spans from its own catalog, so treat its cost totals
as indicative; `llm_usage` is the source of truth for the cap.
