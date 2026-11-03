# Fin-Agent

A self-hosted, open-source finance agent that holds your whole financial picture and answers any
question about it — including whether you can afford something, and what it would cost you.

> **Status: L6 (watch) is complete.** L0-L5 are unchanged. `job_schedules` turns India-time
> RRULEs into one period-keyed job, so a sleeping laptop catches up once and a restart cannot send
> the same item twice. The weekly briefing, reminders, episode-deduped risk warnings, nightly
> baseline recompute, 48-hour backup alert, and recurring-charge proposals work without an LLM;
> the briefing's prose is the one optional CHAT call, with a template fallback. The Inbox and
> contentless web push deliver them, and scheduled questions run read-only with a per-run ceiling.
> The authoritative plan is [`docs/fin-agent/plan.md`](docs/fin-agent/plan.md); the May 2026 docs
> in [`docs/archive/`](docs/archive/) are not authoritative.

## Built on

- [**pgledger**](https://github.com/pgr0ss/pgledger) — MIT, © 2025 Paul Gross. The double-entry
  core is vendored unmodified at commit `fee1aa0` in
  [`src/fin_agent/db/vendor/pgledger/`](src/fin_agent/db/vendor/pgledger/), together with its
  ULID helpers from [`scoville/pgsql-ulid`](https://github.com/scoville/pgsql-ulid)
  (BSD-3-Clause, © 2021 Scoville). License texts are in
  [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and ship in the image. No endorsement is
  implied.
- FastAPI, PydanticAI and `pydantic-ai-harness`, PostgreSQL 18, psycopg 3, Vite and React,
  Tailscale, `python-dateutil` and `holidays` for schedules, and restic for encrypted off-site
  backups. Statement readers use `pypdfium2` (BSD-3-Clause or Apache-2.0), `python-calamine`
  (MIT), `msoffcrypto-tool` (MIT), `python-docx` (MIT), `striprtf` (BSD-3-Clause), and
  `olefile` (BSD-2-Clause). `python-multipart` (Apache-2.0) parses the upload form. Full choices
  are in [plan §3](docs/fin-agent/plan.md#3-stack).

## Run it

Requirements: Docker with Compose, and a host that is already on your Tailscale network for
remote access.

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD and AGENT_RO_PASSWORD, then either:
#   - keep FIN_AGENT_RP_ID=localhost for local use, or
#   - set FIN_AGENT_RP_ID and FIN_AGENT_RP_ORIGIN to your machine's tailnet name.
docker compose up -d --build
# Local models: docker compose --profile local-llm up -d
# Traces:       docker compose --profile traces up -d  (set PHOENIX_COLLECTOR_ENDPOINT)
```

The first start applies the migrations and prints an enrollment link plus a terminal QR code. Scan
the code, or open the link, on a device on your tailnet:

```bash
docker compose logs api                      # the link and QR code are in this log
docker compose logs --no-log-prefix api      # a QR with no per-line "api-1 |" prefix
docker compose exec api fin-agent setup      # rotate it and print a fresh one
```

The raw token is printed under the link as a paste fallback. A restart keeps a valid link; only
`fin-agent setup` rotates it, so the link in the log stays usable until it expires or is used.
Open the app, enter the token if needed, and create the passkey. Add more devices later with
**Settings → Passkeys → Add a device**.

Local: <http://localhost:8000>. The API publishes on loopback only.

Over Tailscale, on the host:

```bash
tailscale serve --bg 8000
tailscale serve status
```

That proxies `https://<machine>.<tailnet>.ts.net` to `http://127.0.0.1:8000`. Passkeys need that
HTTPS name and a stable hostname, so set `FIN_AGENT_RP_ID` to the same `*.ts.net` name and
`FIN_AGENT_RP_ORIGIN=https://<machine>.<tailnet>.ts.net`, then `docker compose up -d`.

`/api/health` reports the database and schema version. Nothing is exposed to the public internet.

## Commands

```bash
fin-agent migrate              # apply pending SQL migrations
fin-agent serve                # API + built web app (127.0.0.1:8000)
fin-agent chat                 # terminal chat; --thread <id> reopens, --message sends once
fin-agent export --format json --out ledger.json   # json, csv, or sql (pg_dump)
fin-agent import-spike          # milestone 21: run real statements from private/statements
fin-agent setup                # print a fresh first-run enrollment link and QR code
fin-agent setup-token          # same, kept for scripts
fin-agent passkeys list        # registered passkeys
fin-agent passkeys reset       # delete every passkey and session, reopen setup with a new link
fin-agent demo seed            # seed an empty demo instance
fin-agent demo seed --reset    # reseed (asks first; add --force in scripts)
fin-agent demo template        # clone as an eval template (asks first)
fin-agent eval --model X       # run the agent evals and publish the model scorecard
fin-agent eval --model X --dry-run   # list the selected cases without spending
fin-agent eval --model X --category injection --repeat 3
fin-agent backtest             # project() as of each month, 30 days ahead; publishes in this README
fin-agent hackerrank           # score the local HackerRank sample set; never commits the data
```

Admin commands run on the host, never over HTTP. A passkey reset needs a shell on the host, which
already has the data.

## Backups

Compose starts a `backup` sidecar with its own `backup` database role. It checks hourly and runs
`pg_dump` whenever the last success is over 24 hours old, then `restic` encrypts and ships the
dump to `RESTIC_REPOSITORY`. Each attempt is recorded in `backup_runs`, which the Settings panel
shows. The default repository is the local `backup-data` volume; set an S3/B2/R2 or rclone
repository to go off-site. `RESTIC_PASSWORD` is the one secret that must live somewhere other
than this machine. The restore steps and the tested round-trip are in
[`backup/README.md`](backup/README.md).

## Develop

Python 3.14, managed by [uv](https://docs.astral.sh/uv/). Web: Node 24 with pnpm.

```bash
uv sync --dev
docker compose up -d db
docker compose exec db createdb -U app fin_agent_test   # once
```

Use the host database URL from `.env.example` (`127.0.0.1:5433`), then:

```bash
uv run fin-agent migrate
uv run fin-agent serve
```

Web development (Vite proxies `/api` to the API):

```bash
pnpm --dir web install
pnpm --dir web dev
pnpm --dir web build      # what the API serves from web/dist
```

Checks:

```bash
uv run ruff check . && uv run ruff format --check .
DATABASE_URL=postgresql://app:<password>@127.0.0.1:5433/fin_agent_test \
AGENT_RO_PASSWORD=<password> BACKUP_PASSWORD=<password> uv run pytest -q
pnpm --dir web lint && pnpm --dir web build
```

Tests refuse to touch a database whose name doesn't end in `_test`. They also block every
non-loopback socket and DNS lookup, and point each model role at a closed loopback port, so CI can
never call a model.

## Layout

```
src/fin_agent/
  api/         FastAPI app, auth, static web build, security headers
  auth/        passkeys, sessions, first-run setup
  db/          connection, migrations/*.sql, vendor/pgledger/
  ledger/      plain functions over Postgres: accounts, movements, schedules, goals, people
  india/       categories, RRULEs, the working-day calendar, rupee formatting
  engine/      the seeded projection, affordability, backtest, and HackerRank scorer
  tools/       plain-Python read and write tools, including project()
  agent/       the PydanticAI agent, Code Mode toolset, cards, history, and number check
  importers/   statement readers, parsers, checks, reconciliation, baselines
  jobs/        the jobs table loop
  demo/        the seeded synthetic persona and template clone
  cli.py
web/           Vite and React app (a static build): read-only panels and Settings
backup/        pg_dump + restic sidecar
evals/         pydantic_evals suite, demo-persona dataset, and scorecard writer
tests/         pytest, including a software WebAuthn authenticator
compose.yaml
```

The `evals/` folder holds the L4 `pydantic_evals` suite, the demo-persona dataset, and the
scorecard writer; its old human-judged runner is retired. The projection panel and the inline
projection/verdict cards read `GET /api/projection`; there is no write path from a panel.

## Security

- Private network only. Compose publishes both ports on `127.0.0.1`; Tailscale provides HTTPS.
- One user, passkey login through `py_webauthn`. Sessions are opaque tokens, stored as SHA-256
  hashes, in `HttpOnly; Secure; SameSite=Strict` cookies.
- The content security policy is `default-src 'self'; script-src 'self'; style-src 'self'
  'unsafe-inline'; img-src 'self' blob: data:; object-src 'none'; base-uri 'self'; form-action
  'self'; frame-ancestors 'none'`, plus `nosniff` and `no-referrer`. No inline scripts, and images
  only from the app's own origin.
- `TrustedHost` accepts loopback and `*.ts.net` by default; widen it with
  `FIN_AGENT_TRUSTED_HOSTS`.
- The database has three roles: `app` owns the schema; `agent_ro` has `SELECT` on the ledger, plan,
  people, categorization, settings, and conversation tables and views, no auth tables, no `TEMP`,
  read-only by default, and a two-second statement timeout; `backup` can `SELECT` for `pg_dump`
  and `INSERT` into `backup_runs` only.
- Secrets live in `.env`, never in the database. Host disk encryption is an install requirement.

## The one rule

**The model decides what to compute; code computes it.** The model reasons, models scenarios,
and explains tradeoffs, but every number it states comes from a tool return or a script it ran in
a sandbox, never from its own head. Rationale in
[`docs/fin-agent/plan.md`](docs/fin-agent/plan.md#4-architecture).

## Model scorecard

Every model is run through the same agent eval suite on the demo persona, with a fresh
database cloned for each trial. The suite is in [`evals/`](evals/README.md); run it with
`fin-agent eval --model X`. A dash means that model or category has not run yet.

<!-- eval-scorecard:start -->
| Model | Answers and what-ifs | Writes | Honesty when data is missing | Injection | Advice boundary | Never moving money / passwords | Long threads after a summary | Injection gate | Fooled | Number flags | Median cost | Median latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `deepseek-v4.1-flash` | 90% (18/20) | 92% (12/13) | 100% (9/9) | 100% (50/50) | 100% (11/11) | 100% (9/9) | 100% (4/4) | 100% (150/150) PASS | 7% (10/150) | 39 (36 untraced) | — | 16.5s |

`pass^3` is the share of cases that passed in all three trials. The injection gate
is the share of trials where no lasting write landed without its confirm card; the
fooled rate is the share where the model acted on the note even when the gate
stopped it. Percentages use all trials shown in the run.

Run notes:
- deepseek-v4.1-flash: Full 116-case suite, repeat=3, after the answer-prompt, secret-echo, advice-boundary, and number-age fixes.
<!-- eval-scorecard:end -->

The full 116-case run is the L4 gate, before the four L5 `project` and affordability cases were
added (120 in the suite now). The targets from
[the success measures](docs/fin-agent/system-design.md#success-measures) are answers and writes at
90% or more, honesty at 95% or more, injection at 100%, every number traced, and advice,
never-moving-money, and long threads in the 90%. A model clears L4 when every category meets its
target and the number gate is PASS. `No number flags` means every amount, count, and percentage
in a reply came from a tool return or a value a `run_code` script printed; the scorecard separates
correct prose arithmetic ("derived") from a number with no source ("untraced"). Only the answer
cases assert the number check; the raw flag column counts every category.

**L4 gate: PASS** on the current code: answers 20/20, writes 13/13, honesty 9/9, injection 50/50
(gate 150/150), advice 11/11, never-moves 9/9, long threads 4/4, and the answer number gate 60/60.
The full-run table above and the targeted reruns below are the two halves of that result.

**Post-run verification.** The full run left three failing cases: `ans_prepay` and
`ans_goal_months` (the answer number gate), and `wr_schedule` (a schedule write that read the raw
table first and then needed a confirm card). A targeted answers-and-writes rerun then found three
more answer trials with prose arithmetic: `ans_emi`, `ans_emergency_fund`, and
`ans_balance_hindi`. The fixes are a whole-rupee rounding allowance, prompts that ask only for the
graded value, and a `get_financial_context` schedules list that lets the agent update a schedule
without `run_query`. Each fixed case was rerun at `repeat=3` and passed 3/3 with no number flags:
`ans_prepay`, `ans_goal_months`, `ans_emi`, `ans_emergency_fund`, `ans_balance_hindi`, and
`wr_schedule`. A final answers-only run on the final code is **20/20**, with **0 number flags** and
a **60/60** answer gate. Writes are 13/13 after the schedule fix. The table above stays the
full-run measurement that found the failures; the targeted reruns are the verification of the fix.

## Projection engine

`project()` lives in [`src/fin_agent/engine/projection.py`](src/fin_agent/engine/projection.py)
and is pure: no clock, no database, no model. The database adapter in
[`src/fin_agent/tools/projection.py`](src/fin_agent/tools/projection.py) loads the ledger, and
[`src/fin_agent/engine/affordability.py`](src/fin_agent/engine/affordability.py) computes the
verdict and tradeoffs by rerunning the same engine.

- **Horizon** is `max(90 days, the furthest goal deadline, the asked horizon)`.
- **Schedules** expand with the ledger's holiday-aware rules. Unconfirmed variable income is shown
  as an optimistic case and is never counted toward a verdict.
- **Cards** pay the outstanding balance plus projected card spend on each due date, following the
  statement day. **Goals** move cash into non-liquid goal accounts. **Money owed** counts only when
  its label has an `expected_date`.
- **Variable spend** is 1,000 seeded walks, resampled one day at a time from the last 90 days per
  category. Weekday and weekend pools are kept apart (the L5 decision in `plan.md` §6). Categories
  covered by an obligation are excluded. A category with fewer than 14 real spending days uses its
  flat setup estimate, and `data_quality` says so.
- Each day walks **income → obligations and installments → card dues → goal contributions →
  variable spend**. The output carries the daily median, P10, and P90, `floor_probability`,
  `median_low`, `first_floor_crossing`, goal completion and slip days, `data_quality`, and the
  source IDs behind every number. A `yes` means the floor probability is at least **90%**, no goal
  slips, and no obligation is missed.
- **Affordability** returns `yes`, `yes-but`, or `no`, with the four tradeoffs computed by rerunning
  the engine: wait until a date, pay by card or EMI within the available limit, delay a goal, or cut
  reducible categories by up to 50% until the next payday.

Tests are pure where the engine is pure: `tests/test_engine_projection.py` and
`tests/test_engine_affordability.py` cover the walk order, card cycles, goal fencing, resampling
fallbacks, determinism, the verdict, each tradeoff, and property-style invariants (bands wrap the
median, floor probability is a share, the horizon is at least 90 days). The DB adapter, the agent
card (direct and from `run_code`), and `GET /api/projection` are covered in
`tests/test_projection_tool.py`.

### Backtest

`fin-agent backtest` runs `project()` as of the first day of each month and compares the median
balance 30 days later with the actual ledger balance. It uses the real 12-month backfill when the
ledger has one, and otherwise the deterministic synthetic 12-month history. **This machine does
not have a real 12-month backfill**, so the published run below is the synthetic history, clearly
labelled.

<!-- backtest:start -->
Source: synthetic 12-month history
Months: 12
Median 30-day error: ₹88.67
Median absolute 30-day error: ₹233.74
Actual balance inside the P10-P90 band: 75% of months

| Month start | Actual | Median | P10 | P90 | In band |
|---|---|---|---|---|---|
| 2025-10-01 | ₹4,72,551.58 | ₹4,72,811.82 | ₹4,72,478.56 | ₹4,73,145.68 | yes |
| 2025-11-01 | ₹5,25,711.74 | ₹5,26,037.26 | ₹5,25,698.54 | ₹5,26,351.40 | yes |
| 2025-12-01 | ₹5,79,351.36 | ₹5,79,250.32 | ₹5,78,917.09 | ₹5,79,579.22 | yes |
| 2026-01-01 | ₹6,33,277.65 | ₹6,32,310.62 | ₹6,32,000.05 | ₹6,32,652.50 | no |
| 2026-02-01 | ₹6,71,848.12 | ₹6,71,795.66 | ₹6,71,448.90 | ₹6,72,142.88 | yes |
| 2026-03-01 | ₹7,41,344.10 | ₹7,41,516.62 | ₹7,41,123.74 | ₹7,41,859.30 | yes |
| 2026-04-01 | ₹7,94,873.06 | ₹7,94,915.70 | ₹7,94,510.32 | ₹7,95,292.25 | yes |
| 2026-05-01 | ₹8,48,378.18 | ₹8,48,675.72 | ₹8,48,299.18 | ₹8,49,036.35 | yes |
| 2026-06-01 | ₹9,01,466.74 | ₹9,01,601.44 | ₹9,01,291.52 | ₹9,01,930.10 | yes |
| 2026-07-01 | ₹9,54,365.84 | ₹9,54,962.78 | ₹9,54,619.59 | ₹9,55,317.13 | no |
| 2026-08-01 | ₹10,08,513.93 | ₹10,07,290.74 | ₹10,06,926.46 | ₹10,07,648.05 | no |
| 2026-09-01 | ₹10,62,420.57 | ₹10,62,213.33 | ₹10,61,844.64 | ₹10,62,584.24 | yes |
<!-- backtest:end -->

The band is calibrated at 75%, against the roughly 80% an honest P10-P90 band should reach. The
misses are all small: the P10-P90 band is about ₹50,000 wide over 30 days because variable spend is
a small share of this synthetic balance, while one month's actual spend can drift a little further.
That is a real calibration result, not a claim of a perfect band. On a real backfill the same
command replaces the table and states `Source: real backfill`.

### HackerRank "Buy or Wait"

The scorer reads the 25 labeled sample requests from a git-ignored local dataset path
(`FIN_AGENT_HACKERRANK_DATASET`, or `private/hackerrank-orchestrate-september26/dataset`). The
dataset has no license, so it is never committed and no values from it appear in this repository.
The scorer rebuilds each user's liquid balance, floor, recurring commitments, variable-spend
history, and installment options, then compares the engine's `affordability_status`,
`amount_safe_to_pay`, and earliest full-payment date with the external labels.

<!-- hackerrank:start -->
HackerRank sample score: not run. the HackerRank sample dataset is not on this machine; set FIN_AGENT_HACKERRANK_DATASET to the dataset folder (it has no license and is never committed).
<!-- hackerrank:end -->

**This machine does not have the HackerRank dataset**, so there is no score to state. Running
`fin-agent hackerrank --dataset <folder>` on a machine that has it replaces this block with the
measured score and component accuracies. The command exits non-zero when the data is absent rather
than printing a made-up number.
