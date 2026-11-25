# Observability

This guide owns optional Phoenix tracing, span attributes, cost journaling and replay,
observability configuration, and Docker logs; see
[cost tracking](../specs/cost-tracking.md) for pricing and spend-budget contracts and
[self-hosting](self-hosting.md) for full host setup.

Arno uses Pydantic AI's native OpenTelemetry tracing and the standard Phoenix OpenTelemetry SDK.
Tracing is off by default. Apart from the `MeteredModel` cost attempt span and cost-only replay, Arno ships no custom span,
metric, sampler, log bridge, Collector, Loki, Prometheus, Grafana, or alerting pipeline. Python and
Uvicorn logs stay on Docker stdout.

---

## Services

The following table lists the services that matter for tracing and their host bindings.

| Service | Role | Host access |
|---|---|---|
| `api` | FastAPI server, agent, backup, and job loop; exports native spans to Phoenix when tracing is on | `127.0.0.1:8000` |
| `db` | PostgreSQL 18 cluster hosting the `arno` database | `127.0.0.1:5433` |
| `phoenix` | Optional trace backend and UI (`obs` profile): stock `arizephoenix/phoenix:21.0`, pinned by digest | `127.0.0.1:6006` |

Phoenix is Elastic License 2.0 and runs outside the Arno image. It stores traces in SQLite on the
`phoenix-data` volume, so it has no access to the Postgres ledger. It enforces authentication
because its container binds `::`; the published host port stays loopback-only.

### Turn tracing on

Tracing is off while `PHOENIX_COLLECTOR_ENDPOINT` is empty. To enable it, set the three Phoenix
secrets in `.env` (see [Configuration](#configuration)), set
`PHOENIX_COLLECTOR_ENDPOINT=http://phoenix:6006/v1/traces`, and run:

```bash
docker compose --profile obs up -d
```

---

## Tracing behavior

`arno.observability.bootstrap_observability()` runs once per process, calling
`phoenix.otel.register()` (with `batch=True`) followed by `Agent.instrument_all()`. Pydantic AI
emits native `gen_ai.*` spans for agent runs, model requests, and tool calls, and Phoenix converts
those attributes to OpenInference on ingestion.

The FastAPI lifespan calls `shutdown_observability()` on shutdown to flush queued spans cleanly.

- **Message content:** Follows Pydantic AI's default instrumentation: prompts, replies, tool
  arguments, and tool results appear in spans. Because traces contain financial data, Phoenix stays
  local and loopback-only.
- **Vision isolation:** Receipt and UPI photo extraction (`src/arno/capture/photo.py`) and
  textless statement transcription (`src/arno/importers/vision.py`) set `agent.instrument = False`
  so raw images never enter traces. Only `MeteredModel` emits a metadata-only cost attempt span for
  those calls.
- **Root span view attributes:** `RootSpanIO` in `src/arno/agent/capabilities.py` writes
  `input.value` and `output.value` (both `text/plain`) onto the native `invoke_agent` span so
  Phoenix's trace list displays the user prompt and final reply without the pinned snapshot or full
  history. Failed, cancelled, or approval-pending runs omit `output.value`. Because `RootSpanIO`
  runs before the input guardrail, `input.value` passes through
  `arno.agent.redaction.redact_text` first so pasted credentials appear as `[redacted:...]`.
- **Attribution and approval counts:** Outside the content switch, `RootSpanIO` sets `user.id` (the
  signed-in passkey principal's opaque `users.id` in web chat, or `local-user` in scheduled runs) plus numeric `arno.approvals.offered` and `arno.approvals.approved` tap counts.
  `InferenceContext` in `src/arno/agent/capabilities.py` sets `session.id`,
  `gen_ai.conversation.id`, and `arno.cost.workload`.
- **Span status:** Follows Pydantic AI's native status: successful runs stay `UNSET`, and failed or
  cancelled runs report `ERROR`.
- **Supplied cost observations:** `MeteredModel` writes `llm.cost.total` and `arno.cost.*` onto a
  child attempt span and marks the logical request parent so totals never double-count. Stock
  Phoenix ignores these supplied `llm.cost.*` values and prices spans from its own model catalog, so
  its cost totals can differ from Arno's. The PostgreSQL spend ledger (`llm_calls`) is the source of
  truth for cost and the sole budget authority.
- **Scope and fail-open behavior:** HTTP routes, SQL queries, jobs, imports, and notifications emit
  no custom spans or metrics. A Phoenix outage or invalid bearer token fails trace export only;
  Arno continues serving requests.
- **Kill switch:** Setting `OTEL_SDK_DISABLED=true` skips exporter registration and agent
  instrumentation completely and turns cost replay into a no-op.

### Project and endpoint

`arno.observability` passes `PHOENIX_COLLECTOR_ENDPOINT` directly to `register()`, so a URL ending
in `/v1/traces` selects OTLP/HTTP. Inside Compose, use `http://phoenix:6006/v1/traces`. A host
process that exports traces must set `PHOENIX_COLLECTOR_ENDPOINT=http://127.0.0.1:6006/v1/traces`
and a bearer credential in `PHOENIX_API_KEY`. When `PHOENIX_COLLECTOR_ENDPOINT` is empty, tracing
stays off so processes never block on an unreachable collector.

---

## Cost journal and replay

Every model or speech-to-text dispatch writes a metadata-only intent row to `llm_calls` before
touching the network and finalizes that row once the response or error is known. Rows store cost
and identity fields only (no prompts, replies, documents, credentials, or provider bodies), have no
`agent_ro` grant, and are backed up with the `arno` database. They are the source of truth for cost.

When tracing is on, the `cost_replay` schedule is checked every five minutes and re-exports finalized rows older than
five minutes whose recovery export has not succeeded, reusing the original project, trace ID, and
span ID. A pass with nothing waiting adds no job.
Because standard batched OTLP export has no per-span acknowledgement, the first replay pass may
re-export spans that already arrived; Phoenix ignores duplicate span IDs and keeps the full span
when present. Replayed spans carry `arno.trace.recovered = true` and
`arno.trace.content_unavailable = true` because prompt and tool text cannot be reconstructed from
`llm_calls`. Exporter failures leave rows pending for the next pass.

Provider batch jobs stay pending for collection even while `api` is stopped. Replay never rebills a
call or modifies a spend-budget counter.

---

## Configuration

The following table lists every observability environment variable.

| Variable | Default | Purpose |
|---|---|---|
| `PHOENIX_COLLECTOR_ENDPOINT` | Empty | OTLP/HTTP trace endpoint. Empty disables tracing. Use `http://phoenix:6006/v1/traces` in Compose and `http://127.0.0.1:6006/v1/traces` on the host. |
| `PHOENIX_PROJECT_NAME` | `arno` | Phoenix project receiving exported spans. |
| `PHOENIX_API_KEY` | Compose falls back to `PHOENIX_ADMIN_SECRET` | Bearer credential for trace export. Set explicitly to use a Phoenix API key instead of the local admin secret. |
| `ARNO_PHOENIX_UI_URL` | Unset | Browser URL shown as a link in **Settings** (for example, `http://127.0.0.1:6006`). |
| `OTEL_SDK_DISABLED` | `false` | OpenTelemetry SDK kill switch; `true` disables registration, instrumentation, and cost replay. |
| `PHOENIX_SECRET` | Required for `phoenix` | Phoenix authentication signing secret. |
| `PHOENIX_ADMIN_SECRET` | Required for `phoenix` | Phoenix admin secret (at least 32 characters, with a digit and lowercase letter, distinct from `PHOENIX_SECRET`); default local export bearer token. |
| `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD` | Required for `phoenix` | Initial Phoenix UI admin login password. |
| `PHOENIX_UI_PORT` | `6006` | Loopback host port for the Phoenix UI. |
| `PHOENIX_CSRF_TRUSTED_ORIGINS` | `http://127.0.0.1:6006` | Trusted CSRF origin list for the Phoenix UI. |

Compose resolves `PHOENIX_API_KEY` as `${PHOENIX_API_KEY:-${PHOENIX_ADMIN_SECRET:-}}` and strips
`PHOENIX_SECRET`, `PHOENIX_ADMIN_SECRET`, and
`PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD` from the `api` container environment. The Phoenix SDK also
reads `.env.phoenix` by walking up from the working directory, with process environment variables
taking precedence; never commit `.env.phoenix`.

### Retention

Phoenix defaults to **0 days (infinite retention)** and does not purge traces automatically. Set a
retention policy in the Phoenix UI or through `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS` if your host
storage requires one. Phoenix stores data in the `phoenix-data` volume, which Arno's
ledger backups do not include.

---

## Logs

`api` configures `logging.basicConfig(level=logging.INFO)` while Uvicorn retains its standard
access and server loggers. Docker's `json-file` driver caps logs at three 10 MB files per service.

```bash
docker compose logs --tail=100 api
docker compose logs -f api
```

Arno uses no central log store or log-trace correlation field. The first-run enrollment link prints
to local stdout only; read it from a trusted host terminal.

---

## Find a trace

1. Confirm tracing is on (see [Turn tracing on](#turn-tracing-on)), then confirm all services are
   running and healthy with `docker compose ps` and
   `curl -fsS http://127.0.0.1:${PHOENIX_UI_PORT:-6006}/healthz`.
2. Open `http://127.0.0.1:6006`, sign in, and select the `arno` project.
3. Send one chat message and open the newest trace. The root `invoke_agent` span shows the redacted
   prompt and reply in Phoenix's trace list, with model-request and `execute_tool` child spans. The
   attempt span's cost tooltip shows source, rate revision, and completeness, while unpriced or
   partial counts appear beside aggregate totals.
4. If no trace arrives, inspect `docker compose logs api` for exporter errors, confirm
   `PHOENIX_COLLECTOR_ENDPOINT` is reachable from `api`, confirm `OTEL_SDK_DISABLED` is not `true`,
   and verify the bearer credential (`PHOENIX_API_KEY` or `PHOENIX_ADMIN_SECRET`) on `401` errors.

### Integration references

- [Phoenix SDK setup and defaults](https://arize.com/docs/phoenix/sdk-api-reference/python/arize-phoenix-otel)
- [Native GenAI attribute conversion](https://arize.com/docs/phoenix/release-notes/05-2026/05-15-2026-otel-semconv-conversion)
- [Phoenix retention defaults](https://arize.com/docs/phoenix/self-hosting/misc/frequently-asked-questions)
