# Phoenix cost patch

This directory builds `arno-phoenix-cost`, the Phoenix service Arno runs as its cost dashboard.
Stock Phoenix 21.0 cannot show what Arno measures, so this build patches exactly the gaps the
[implementation plan](../../docs/arno/cost-tracking.md#focused-phoenix-change) names and nothing
else. Phoenix is Elastic License 2.0; its source and notice ship in this image only, never in the
Arno image.

## What stock 21.0 does, and what this patch changes

| Stock behavior | Patched behavior |
| --- | --- |
| Prices every `LLM` span from its own model catalog, ignoring `llm.cost.*` | A supplied `llm.cost.total` wins, including an explicit `0`; the catalog is used only for spans that supply nothing |
| Prices the model Arno *requested*, not the served model | The supplied amount is authoritative; no lookup happens for a supplied observation |
| An unknown price is `NULL` cost, drawn and summed as `$0` | An unknown stays unknown: spans, traces, sessions, projects, and cost-series bins report an unpriced count beside the total, and a total with no known call reads as unknown |
| A partial estimate is indistinguishable from a complete one | `arno.cost.completeness = partial` marks the subtotal, and the total is labeled partial |
| No source, billing mode, or rate revision | `arno.cost.source`, `arno.cost.billing_source`, and `arno.cost.price_revision` are read from the span and shown in the cost tooltip |
| Cost is only computed for `LLM` spans with a model name | A supplied observation is priced on any span, so an audio transcription keeps its cost without invented language tokens |
| Nested `LLM` spans double count | `arno.cost.role` excludes logical request parents (and any collect/decode wrapper) from cost and from the unpriced count; the attempt is priced once |
| Top models by cost/tokens only include catalog-priced spans | Supplied observations group by the span's own `llm.model_name` and `llm.provider`, so the served model, its cost, and its tokens appear without a catalog record; the same name on two providers stays two rows |
| A time bin with only unpriced calls draws as an empty, apparently free range | Each bin carries its unpriced, partial, and costed counts; an unpriced-only range says the cost is unknown, and a mixed bin's tooltip calls the total a subtotal |
| Incoming `gen_ai.*` attributes synthesize `llm.*` with `setdefault` semantics | A supplied `llm.cost.total` is never overwritten by that conversion |

Storage, triggers, the OTLP endpoint, GraphQL views, and the UI's existing cost surfaces are
unchanged. The three new `SpanCostSummary` counts (`unpricedCount`, `partialCount`, `costedCount`),
the same three counts on each cost-time-series bin, and the span-level provenance fields are
additive GraphQL fields; the derived `completeness` field is `known`, `partial`, or `unknown`.

## Pins

| Pin | Value |
| --- | --- |
| Base image | `arizephoenix/phoenix:21.0@sha256:1f9a144a5357b4973e93a15718f050247cb7a4ee7b7c6c06ecb91b5eed3fbaad` |
| Upstream source | commit `6359d6388815d56f7795a3a8cd11e89a71793e01` (the 21.0.0 release, 2026-10-30) |
| Source tarball sha256 | `8c69a08de1fce99800926d982ba793e3d96095e6f280083e4e2c89fa03761375` |
| Patch | [`patches/0001-supplied-cost.patch`](patches/0001-supplied-cost.patch) |
| Patch sha256 | `b964d6d6d19f9b5c502c094089f261ec93e16ea4bad0a3d92f524b1a1c467290` |
| Provider labels | [`patches/0002-provider-labels.patch`](patches/0002-provider-labels.patch) |
| Provider-label patch sha256 | `0f43258db577a0aa0f372d8f87aad129916f5e18171f6222ac5a6d7919ffb2b0` |
| Image | `arno-phoenix-cost:21.0.0` |

The base image carries the released Python runtime; the patch replaces the files it changes and
rebuilds the compiled web app from the same commit, as upstream's own Dockerfile does. Every
`COPY` in [`Dockerfile`](Dockerfile) names one patched module, so no other installed file moves.

## Build

```bash
docker build -t arno-phoenix-cost:21.0.0 deploy/phoenix-cost
```

`compose.yaml` uses that tag and this directory as its build context, so `docker compose up -d
--build phoenix` produces the same image.

## Prove it before shipping a change

The contract test talks to a real instance; it never starts one and never touches the owner
deployment. Use a dedicated scratch stack, never the owner's Phoenix or its database on port 5433:

```bash
# Scratch Postgres, already used by the Phoenix contract run on 127.0.0.1:5439.
docker start arno-cost-test-db
docker exec arno-cost-test-db psql -U app -d postgres \
  -c "CREATE DATABASE phoenix_cost_test"   # once

docker run -d --name arno-phoenix-cost-test \
  -p 127.0.0.1:6007:6006 \
  -e PHOENIX_SQL_DATABASE_URL=postgresql://app:arno-cost-test-only@host.docker.internal:5439/phoenix_cost_test \
  -e PHOENIX_ENABLE_AUTH=false \
  -e PHOENIX_TELEMETRY_ENABLED=False \
  arno-phoenix-cost:21.0.0

PHOENIX_COST_URL=http://127.0.0.1:6007 uv run pytest tests/test_phoenix_cost_contract.py -q -n 0
```

The test posts synthetic OTLP spans into one project per case and reads the GraphQL totals back:
a supplied `$0.0000924`, a total-only observation with no prompt/completion amounts, an explicit
zero, unknown and partial observations, a nested logical request/attempt pair, a duplicate span, an
audio call, a `gen_ai.*`-only span, one model name served by two providers, an unpriced-only time
bin, a mixed bin, and the span/trace/session/project/time-range views agreeing. It also reads the
two model charts and the cost time series, so a change that drops supplied rows from the per-model
breakdown, merges two providers, or labels an unpriced bin as a free zero fails the contract.

Before bumping the base image, run that same test against the new release's stock build. If stock
passes, delete this patch and use the published image; otherwise port the patch onto the new
commit and re-run.

## Known limits

- The per-model breakdown groups a supplied observation by the pair `llm.model_name` and
  `llm.provider`. A supplied span with neither attribute is labeled `unknown`; no catalog or
  pseudo-model row is invented for it. Two providers serving one model name remain two rows;
  the chart labels and tooltips identify the provider.
- A cost-series bin with only unpriced calls has no total and therefore draws no bar. The chart
  says "Cost unknown for N calls in this range" instead of "No data", but the bin's position is not
  marked on the plot. A mixed bin draws its known subtotal and its tooltip names the unpriced calls.
- Per-token-type detail (cache reads, reasoning, and so on) exists only where a catalog model or
  supplied component prices it. A total-only supplied amount draws as one `Total (unsplit)` series
  in the model chart and the cost time series instead of being divided between prompt and
  completion.
- Supplied audio costs show in cost totals and as a span with no tokens; there is intentionally no
  token chart for audio.
- Phoenix still owns its own database, retention, and backups; Arno's `backup` service dumps only
  the ledger.

The model chart labels include the provider before the model name. Long names can still truncate
on the axis; the standard chart tooltip retains the complete label.
