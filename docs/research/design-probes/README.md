# Design probes

These throwaway scripts tested the claims behind [system design](../../fin-agent/system-design.md) steps
7, 8, 9, and 10. They run no real LLM, and every value in them is synthetic. They become the first tests at
L2, so they're kept here until then.

| Scripts | Tested | Results |
|---|---|---|
| `step7_probe*.py`, `step7_pg*` | Confirm answers, redaction, truncation, the injection defender, outside connections, and `agent_ro` on real Postgres | [Step 7](../../fin-agent/system-design.md#the-safety-tests-october-31-2026) |
| `step8_probe1` to `step8_probe3` | Disconnects, Stop, failures, a card across a restart, provider failures against a local stub, and a real server | [Step 8](../../fin-agent/system-design.md#the-reliability-tests-november-1-2026) |
| `step8_probe4`, `step8_probe5`, `step8_own_worker`, `step8_pgtask_worker` | pgtask and a plain `jobs` table under a killed worker | Step 8 |
| `step8_probe6`, `step8_probe7` | Failure saves with the full config, and a connection pool across a Postgres restart | Step 8 |
| `step8_probe8` to `step8_probe11` | The summary call under `ModelFallback`, a window-limited backup, and how each ending finds your message | Step 8 |
| `test_guide_sample.py` | [The testing guide](https://pydantic.dev/docs/ai/guides/testing/) applied to our agent's shape | [Step 9](../../fin-agent/system-design.md#the-measurement-tests-november-1-2026) |
| `step9_probe1` | `pydantic_evals` with `repeat`, a cloned database per trial, and grading on database state | Step 9 |
| `numcheck.py`, `step9_probe2` | The number check on synthetic replies | Step 9 |
| `step9_probe3` to `step9_probe5` | A trace row from messages (dropped for Phoenix), a full turn exported to a tracing tool, and our own facts on a parent span | Step 9 |
| `step9_probe7`, `step9_probe8` | The daily spend ceiling across a restart, and the spend store while Postgres stops and restarts | Step 9 |

| `web-spike/`, `step10_probe2` | A real `useChat` client against the detached turn, the same app as a Next.js static export under a strict content security policy, and where a card's data survives a reload | [Step 10](../../fin-agent/system-design.md#the-interface-tests-november-1-2026) |

To run the web spike, run `pnpm install` and `pnpm build` in `vite-app` or `next-app`, then
`python server.py <its dist or out folder> 7 18800`, and open `http://127.0.0.1:18800/?thread=a`.

To run the others, you need these packages in one environment: `pydantic-ai`, `pydantic-ai-harness`,
`pydantic-monty`, `pydantic-evals`, `psycopg`, `psycopg-pool`, `asyncpg`, `pgtask`, `uvicorn`,
`opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`,
`openinference-instrumentation-pydantic-ai`, `pytest`, `anyio`, `inline-snapshot`, and `dirty-equals`.
The Postgres scripts read `DSN`, pointed at a throwaway server. On Windows, `step8_probe4` fails at
pgtask's migration.
