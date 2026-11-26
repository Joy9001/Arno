# Observability

This guide owns optional Phoenix tracing, span attributes, observability configuration, and Docker
logs; see [cost tracking](../specs/cost-tracking.md) for the usage log and daily spend cap, which
are independent of tracing, and [self-hosting](self-hosting.md) for full host setup.

Arno uses the stock OpenTelemetry SDK with the OTLP/HTTP exporter, the FastAPI and asyncpg
instrumentors, and Pydantic AI's native tracing. Tracing is off by default. Arno ships no custom
span, metric, sampler, exporter, log bridge, Collector, Loki, Prometheus, Grafana, or alerting
pipeline. Python and Uvicorn logs stay on Docker stdout.

---

## Services

The following table lists the services that matter for tracing and their host bindings.

| Service | Role | Host access |
|---|---|---|
| `api` | FastAPI server, agent, backup, and minute loop; exports native spans to Phoenix when tracing is on | `127.0.0.1:8000` |
| `db` | PostgreSQL 18 cluster hosting the `arno` database | `127.0.0.1:5433` |
| `phoenix` | Optional trace backend and UI (`obs` profile): stock `arizephoenix/phoenix:21.0`, pinned by digest | `127.0.0.1:6006` |

Phoenix is Elastic License 2.0 and runs outside the Arno image. It stores traces in SQLite on the
`phoenix-data` volume, so it has no access to the Postgres ledger. It enforces authentication
because its container binds `::`; the published host port stays loopback-only.

### Turn tracing on

Tracing is off while `OTEL_EXPORTER_OTLP_ENDPOINT` is empty. To enable it, set the three Phoenix
secrets in `.env` (see [Configuration](#configuration)), set
`OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006`, and run:

```bash
docker compose --profile obs up -d
```

---

## Tracing behavior

`arno.observability.bootstrap_observability(app)` runs once per process. It builds a stock
`TracerProvider` with a `BatchSpanProcessor` and `OTLPSpanExporter`, then turns on three sources of
spans:

- `FastAPIInstrumentor` traces every HTTP request.
- `AsyncPGInstrumentor` traces every SQL query.
- `Agent.instrument_all()` traces agent runs, model requests, and tool calls with native `gen_ai.*`
  attributes, which Phoenix converts to OpenInference on ingestion.

The FastAPI lifespan calls `shutdown_observability()` on shutdown to flush queued spans cleanly.

- **Message content:** Follows Pydantic AI's default instrumentation: prompts, replies, tool
  arguments, and tool results appear in spans. Because traces contain financial data, Phoenix stays
  local and loopback-only.
- **Vision isolation:** Textless statement transcription (`src/arno/importers/vision.py`) sets
  `agent.instrument = False` so raw images never enter traces. `Agent.instrument_all` also sets `include_binary_content=False`.
- **Root span view attributes:** `RootSpanIO` in `src/arno/agent/capabilities.py` writes
  `input.value` and `output.value` (both `text/plain`) onto the native `invoke_agent` span so
  Phoenix's trace list displays the user prompt and final reply without the full history. Failed,
  cancelled, or approval-pending runs omit `output.value`. Nothing redacts `input.value`, so a
  credential typed into chat appears in the trace; Phoenix stays loopback-only.
- **Attribution and approval counts:** Outside the content switch, `RootSpanIO` sets `user.id` (the
  owner's Tailscale login in web chat when present, or `local-user` otherwise) plus numeric
  `arno.approvals.offered` and `arno.approvals.approved` tap counts. `InferenceContext` in
  `src/arno/agent/capabilities.py` sets `session.id`, `gen_ai.conversation.id`, and
  `arno.cost.workload`.
- **Span status:** Follows Pydantic AI's native status: successful runs stay `UNSET`, and failed or
  cancelled runs report `ERROR`.
- **Cost:** Spans carry no Arno cost data. Stock Phoenix prices spans from its own model catalog, so
  its cost totals are indicative. The `llm_usage` table is the source of truth for cost and the
  daily cap.
- **Scope and fail-open behavior:** Background tasks, imports, and notifications emit no custom
  spans. A Phoenix outage or invalid bearer token fails trace export only; Arno continues serving
  requests. SQL spans include statement text, so they carry financial data like the rest of the
  trace.
- **Kill switch:** Setting `OTEL_SDK_DISABLED=true` installs no provider and no
  instrumentation.

### Project and endpoint

The exporter reads the standard OpenTelemetry environment variables and appends `/v1/traces` to
`OTEL_EXPORTER_OTLP_ENDPOINT`, so set the Phoenix base URL: `http://phoenix:6006` inside Compose and
`http://127.0.0.1:6006` for a host process. A host process also needs a bearer credential in
`OTEL_EXPORTER_OTLP_HEADERS`. Compose sets
`OTEL_RESOURCE_ATTRIBUTES=openinference.project.name=arno` (the Phoenix project) and
`OTEL_SERVICE_NAME=arno`. When the endpoint is empty, tracing stays off so processes never block on
an unreachable collector.

---

## Configuration

The following table lists every observability environment variable.

| Variable | Default | Purpose |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Empty | Phoenix base URL. Empty disables tracing. Use `http://phoenix:6006` in Compose and `http://127.0.0.1:6006` on the host. |
| `OTEL_EXPORTER_OTLP_HEADERS` | Compose sets `Authorization=Bearer%20${PHOENIX_ADMIN_SECRET}` | Bearer credential for trace export. Set explicitly to use a Phoenix API key instead of the local admin secret. |
| `OTEL_RESOURCE_ATTRIBUTES` | `openinference.project.name=arno` in Compose | Phoenix project receiving exported spans. |
| `ARNO_PHOENIX_UI_URL` | Unset | Browser URL shown as a link in **Settings** (for example, `http://127.0.0.1:6006`). |
| `OTEL_SDK_DISABLED` | `false` | OpenTelemetry SDK kill switch; `true` installs no provider and no instrumentation. |
| `PHOENIX_SECRET` | Required for `phoenix` | Phoenix authentication signing secret. |
| `PHOENIX_ADMIN_SECRET` | Required for `phoenix` | Phoenix admin secret (at least 32 characters, with a digit and lowercase letter, distinct from `PHOENIX_SECRET`); default local export bearer token. |
| `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD` | Required for `phoenix` | Initial Phoenix UI admin login password. |
| `PHOENIX_UI_PORT` | `6006` | Loopback host port for the Phoenix UI. |
| `PHOENIX_CSRF_TRUSTED_ORIGINS` | `http://127.0.0.1:6006` | Trusted CSRF origin list for the Phoenix UI. |

Compose strips `PHOENIX_SECRET`, `PHOENIX_ADMIN_SECRET`, and
`PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD` from the `api` container environment; the `api` gets only
the single bearer credential in `OTEL_EXPORTER_OTLP_HEADERS`.

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

Arno uses no central log store or log-trace correlation field. 

---

## Find a trace

1. Confirm tracing is on (see [Turn tracing on](#turn-tracing-on)), then confirm all services are
   running and healthy with `docker compose ps` and
   `curl -fsS http://127.0.0.1:${PHOENIX_UI_PORT:-6006}/healthz`.
2. Open `http://127.0.0.1:6006`, sign in, and select the `arno` project.
3. Send one chat message and open the newest trace. The root `invoke_agent` span shows the
   prompt and reply in Phoenix's trace list, with model-request and `execute_tool` child spans. The
   Phoenix also shows the `GET`/`POST` request spans and `SELECT`/`INSERT` query spans.
4. If no trace arrives, inspect `docker compose logs api` for exporter errors, confirm
   `OTEL_EXPORTER_OTLP_ENDPOINT` is reachable from `api`, confirm `OTEL_SDK_DISABLED` is not `true`,
   and verify the bearer credential (`OTEL_EXPORTER_OTLP_HEADERS` or `PHOENIX_ADMIN_SECRET`) on
   `401` errors.

### Integration references

- [Phoenix OTLP ingestion and tracing setup](https://arize.com/docs/phoenix/tracing/llm-traces-1)
- [Native GenAI attribute conversion](https://arize.com/docs/phoenix/release-notes/05-2026/05-15-2026-otel-semconv-conversion)
- [Phoenix retention defaults](https://arize.com/docs/phoenix/self-hosting/misc/frequently-asked-questions)
