# Observability

This guide describes the working tree, which is not yet deployed to the owner instance. The
[cost tracking and budgets feature](cost-tracking.md) adds supplied costs to the pinned Phoenix
build (`arno-phoenix-cost:21.0.0`), a small cost journal, and same-ID recovery export. The
[acceptance log](cost-tracking-acceptance.md) separates the synthetic checks from the pending
paid-provider and owner-deployment checks.

Arno uses the native tracing of Pydantic AI and the standard Phoenix OpenTelemetry SDK.
Apart from the cost attempt span and its cost-only replay, there is no custom span, metric, log
bridge, sampler, redaction layer, Collector, Loki, Prometheus, Grafana, dashboard, or alerting
pipeline. Python and Uvicorn logs stay standard on
Docker stdout.

## What runs

| Service | Role | Host access |
|---|---|---|
| `api` | FastAPI, agent, jobs; exports native spans to Phoenix | `127.0.0.1:8000` |
| `db` | Postgres, including Phoenix's own database and role | `127.0.0.1:5433` |
| `backup` | pg_dump + restic sidecar | none |
| `phoenix` | Trace backend and agent UI; Arno's pinned supplied-cost build | `127.0.0.1:6006` |

Phoenix has authentication on because its container binds `::`; the host publish is
loopback-only. It keeps its own Postgres database and role and does not use the ledger tables.

## Tracing behavior

`arno.observability.bootstrap_observability()` runs once per process. It calls
`phoenix.otel.register()` and then `Agent.instrument_all()`. Pydantic AI emits its own
`gen_ai.*` spans for the agent run, each model request, and each tool call. Phoenix converts
those native attributes to OpenInference on ingestion.

- Content follows Pydantic AI's default instrumentation: prompts, replies, tool arguments, and
  tool results are recorded. There is no Arno content switch.
- The native `invoke_agent` span also carries `input.value` and `output.value` (both
  `text/plain`) for Phoenix's trace list. They hold only the current user message and the final
  assistant reply, never the pinned financial snapshot or the full history; a failed, cancelled,
  or approval-pending run has no output. This is two attributes on the existing span, not a
  second trace or a wrapper root. The full history and `final_result` remain on the same span.
  The capability is `RootSpanIO` in `arno.agent.capabilities`; it follows the run's
  `include_content` setting. The input preview goes through
  `arno.agent.redaction.redact_text`, the same policy the input guardrail and stored history
  use, so a pasted credential appears as its `[redacted:...]` placeholder rather than raw text.
  This matters because `RootSpanIO` runs before the guardrail rewrites the prompt.
- The same span carries `user.id`, outside the content switch: it is attribution, not message
  text. Chat runs use the opaque `users.id` of the signed-in passkey principal, so every
  conversation by that user gets the same value. CLI and scheduled runs have no session and use
  the fixed local-actor label `local-user`. It is never derived from a token, email, account
  name, financial row, conversation ID, or hostname.
- Status is Pydantic AI's native status. A successful run stays `UNSET`; a failed or cancelled
  run is `ERROR`. Arno adds no status writer, so it cannot turn an SDK-recorded error into
  success. The trace list shows the same `UNSET`/`ERROR` values Phoenix receives.
- Cost comes from one observation, never from Phoenix's own catalog. `MeteredModel` writes the
  `llm.cost.total` and `arno.cost.*` attributes on a child attempt span; a logical request
  parent is marked and excluded from totals. A measured zero stays zero, a partial subtotal is
  labeled partial, and an unknown has no amount and appears in the unpriced count. No price is
  invented. The Postgres spend ledger remains the only budget authority.
- HTTP requests, database statements, jobs, imports, notifications, and business functions have
  no custom spans or metrics. Their real errors and guardrails are unchanged.
- A Phoenix outage or bad key fails export only; the app still answers.
- `OTEL_SDK_DISABLED=true` is the standard SDK kill switch. The process registers no exporter and
  instruments no agent. There is no other switch.
- The API exposes no `X-Trace-Id` header and does not write new trace IDs to `messages.trace_id`.
  Historical rows keep their old values. `jobs.trace_context` remains nullable for the same
  reason and is no longer read or written.

### Project and endpoint

`arno.observability` passes `PHOENIX_COLLECTOR_ENDPOINT` through to `register()` so a URL
with `/v1/traces` selects OTLP/HTTP. Compose points the API at
`http://phoenix:6006/v1/traces`. A host CLI or eval must use
`http://127.0.0.1:6006/v1/traces` if it should export. With no endpoint set, tracing is off:
nothing is registered or instrumented, so a host command never waits on an exporter that has
nowhere to send.

## Cost journal and replay

Every real dispatch writes a cost-and-identity-only row to `llm_calls` before the network call and
finalizes it once the response or failure is known. The row holds no prompt, answer, document,
credential, or raw provider body, has no `agent_ro` grant, and is not a budget authority. It is
included in Arno's backups; Phoenix's own database is not.

The `cost_replay` job runs every five minutes. It re-exports finalized rows older than five
minutes whose recovery export has not been accepted, using the original project, trace, and span
IDs. Normal batched export has no per-span acknowledgement, so the first recovery pass also
re-exports observations whose full span may already be present. Phoenix ignores duplicate span
IDs and retains that full span; replay does not overwrite it. A recovered span is labeled
`arno.trace.content_unavailable` because prompt and tool content cannot be reconstructed. An
exporter failure leaves the rows for the next pass. `OTEL_SDK_DISABLED=true` or no endpoint makes
replay a no-op.

After stopping API and CLI inference, `docker compose exec api arno cost-replay
--finalize-interrupted` marks interrupted ordinary calls unknown, then replays after the normal
grace. Provider batches remain pending for collection, even while the API is stopped. The CLI's
`pending` count includes unfinished and not-yet-exported rows; it does not imply they are safe to
finalize. Recovery never rebills a call or changes a budget counter.

For uncertain OTLP acceptance, `arno cost-replay --attempt <attempt-id>` retries one immutable
observation even if its earlier export was accepted. Find the opaque ID in the span's
`arno.cost.attempt_id` attribute. The same five-minute grace and duplicate-ID protection apply.
The normal processor remains the standard batch processor; no custom delivery service is involved.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `PHOENIX_COLLECTOR_ENDPOINT` | Compose: `http://phoenix:6006/v1/traces`; host: `http://127.0.0.1:6006/v1/traces` | Phoenix OTLP/HTTP endpoint. |
| `PHOENIX_PROJECT_NAME` | `arno` | Phoenix project that receives the spans. |
| `PHOENIX_API_KEY` | Compose: falls back to `PHOENIX_ADMIN_SECRET` | Bearer credential for Phoenix export. Set it to use a real Phoenix API key; the fallback keeps the normal start working without one. An unauthenticated external Phoenix does not need one. |
| `OTEL_SDK_DISABLED` | `false` | Standard OpenTelemetry kill switch. `true` disables export and agent instrumentation. |
| `PHOENIX_SECRET` | required for Phoenix | Phoenix auth secret. |
| `PHOENIX_ADMIN_SECRET` | required for Phoenix start | Phoenix admin secret; at least 32 characters with a digit and a lowercase letter, different from `PHOENIX_SECRET`. It is also the default local bearer credential for trace export. |
| `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD` | required for Phoenix start | Initial Phoenix UI login password. |
| `PHOENIX_PASSWORD` | required for Phoenix | Password for the `phoenix` Postgres role. |
| `PHOENIX_UI_PORT` | `6006` | Loopback host port for the Phoenix UI. |
| `PHOENIX_CSRF_TRUSTED_ORIGINS` | `http://127.0.0.1:6006` | Phoenix CSRF origin. |

Compose resolves `PHOENIX_API_KEY` as `${PHOENIX_API_KEY:-${PHOENIX_ADMIN_SECRET:-}}`. With the
normal four Phoenix values, the app exports using the local admin secret as a bearer token, so a
first start needs no API-key creation and no second restart. The API container still receives
none of `PHOENIX_SECRET`, `PHOENIX_ADMIN_SECRET`, `PHOENIX_PASSWORD`, or the initial admin
password under those names. Set `PHOENIX_API_KEY` only to use a real Phoenix API key; it wins
over the fallback. This is a local, loopback-only admin credential, not a second credential to
manage.

The Phoenix SDK also discovers a `.env.phoenix` file by walking up from the working directory;
process environment variables win. Do not commit that file.

### Phoenix database setup and password changes

On a fresh Postgres volume, `db` runs `deploy/phoenix-db.sh` once to create the separate
Phoenix role and database. If `PHOENIX_PASSWORD` is empty, it skips that setup so the app can
still start; Phoenix refuses to start until all four Phoenix credentials are present.

Postgres does not rerun initialization scripts on an existing volume. If the Phoenix database
or role is missing, or if you change `PHOENIX_PASSWORD`, set the desired value in `.env` and run:

```bash
docker compose up -d db
docker compose exec db sh /docker-entrypoint-initdb.d/phoenix-db.sh
docker compose up -d phoenix
```

The first command recreates `db` if its environment changed. The script is safe to rerun: it
creates missing objects and updates the role password without deleting data. Existing Phoenix
databases and traces remain in place. The final command recreates Phoenix with its new password.
This also recovers a fresh volume initialized while `PHOENIX_PASSWORD` was empty. If the script
reports that setup was skipped, check the value and repeat the commands. Do not delete the
Postgres volume to run initialization again.

### Retention

Phoenix's supported default is **0 days, meaning infinite retention**: it does not delete traces
automatically. This repository no longer sets a custom retention value. Existing project-specific
policies are preserved. Set a policy in the Phoenix UI, or
`PHOENIX_DEFAULT_RETENTION_POLICY_DAYS` deliberately, if the deployment needs one.
Phoenix data lives in the `phoenix-data` volume and in the `phoenix` database; neither is part of
the ledger backup.

## Logs

The app calls `logging.basicConfig(level=logging.INFO)`. Uvicorn keeps its own loggers and access
line. Docker's `json-file` driver keeps stdout, capped at three 10 MB files per service. Read logs
with:

```bash
docker compose logs api
docker compose logs -f api
```

There is no Loki, no central log store, and no log correlation field. The first-run enrollment
link is printed to that local stdout and should be read from the trusted console.

## Find a run

1. Run `docker compose up -d` and `docker compose ps`. A running container is configured, not
   delivering.
2. Open <http://127.0.0.1:6006>, sign in, and select the `arno` project. The normal start
   already exports with the local admin secret. Set `PHOENIX_API_KEY` only to use a real Phoenix
   API key, then recreate the API after changing its environment.
3. Send one chat message. Open the newest trace. The root is Pydantic AI's `invoke_agent` span,
   with model-request and `execute_tool` children. Its input and output columns show the message
   you sent and the reply, so the trace list is readable before opening the span. The model
   attempt's cost tooltip shows the source, rate revision, and completeness; a partial or
   unknown count sits beside aggregate cost totals.
4. If no trace arrives, check `docker compose logs api` for an exporter error, confirm
   `PHOENIX_COLLECTOR_ENDPOINT` is reachable from the API container, confirm
   `OTEL_SDK_DISABLED` is not `true`, and confirm the bearer credential if Phoenix returns 401.

## Owner commands

These rebuild or recreate the normal deployment without deleting volumes, telemetry history, or
ledger data. Run them on the host, in the repository root.

```bash
docker compose config --quiet          # validate the source change
# Stop the removed services in the existing default arno project; retain their data.
docker stop arno-otel-collector-1 arno-loki-1 arno-prometheus-1 arno-grafana-1
docker compose up -d --build api       # rebuild and recreate the API with the new tracing
docker compose up -d --build phoenix   # build the pinned supplied-cost image
docker compose ps                      # verify api, db, backup, and phoenix
docker compose logs api                # standard app and Uvicorn logs
docker compose exec api arno cost-replay   # manual cost-only recovery, if needed
```

Do not run `docker compose down -v`, `docker volume rm`, or `docker rm -v`. Source changes remove
the old Collector/Loki/Prometheus/Grafana service definitions; they do not remove existing
containers or volumes. The old services keep running until stopped; the command above uses the
container names of the current `arno` project. Adjust names if using another Compose project.
Leave `phoenix-data`, `db-data`, and `backup-data` in place. Stopped telemetry containers and volumes
may remain as orphans until the owner chooses to remove the containers without their volumes.

## Standard integration references

- [Phoenix SDK setup and defaults](https://arize.com/docs/phoenix/sdk-api-reference/python/arize-phoenix-otel)
- [Native GenAI attribute conversion](https://arize.com/docs/phoenix/release-notes/05-2026/05-15-2026-otel-semconv-conversion)
- [Phoenix retention defaults](https://arize.com/docs/phoenix/self-hosting/misc/frequently-asked-questions)
