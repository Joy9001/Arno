<p align="center">
  <img src="web/public/favicon.svg" width="96" height="96" alt="The Arno orb">
</p>

<h1 align="center">Arno</h1>

<p align="center">
  <strong>Ask your money anything. Get answers that are computed, not guessed.</strong>
</p>

<p align="center">
  A private, self-hosted finance agent that holds your whole financial picture (accounts, cards,
  EMIs, goals, the people you owe) and answers any question about it, including
  <em>"Can I afford this?"</em> and what it would cost you.
</p>

<p align="center">
  <sub>Arno was called Fin-Agent until November 2026, so old links, issues, and backups still use
  that name.</sub>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-d4ff3a?style=flat-square"></a>
  <a href="https://github.com/Joy9001/Arno/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Joy9001/Arno/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Version 0.2.0" src="https://img.shields.io/badge/version-0.2.0-2b59ff?style=flat-square">
  <img alt="Python 3.14" src="https://img.shields.io/badge/python-3.14-1ec8a5?style=flat-square">
  <img alt="PostgreSQL 18" src="https://img.shields.io/badge/postgres-18-1ec8a5?style=flat-square">
  <img alt="Self-hosted" src="https://img.shields.io/badge/self--hosted-private%20network%20only-0e0f0c?style=flat-square">
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#what-you-can-ask">What you can ask</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#measured-not-claimed">Measured results</a> ·
  <a href="docs/arno/self-hosting.md">Self-hosting guide</a>
</p>

<p align="center">
  <img src=".impeccable/mocks/final/shots/02-thread.png" alt="An Arno conversation: the question 'Can I buy a ₹68,000 laptop on the Slice card?' answered with a 'Yes, with a change' affordability card, a 91% floor probability, and a 90-day projection band." width="100%">
  <br>
  <sub>Interface design comp. The figures are illustrative, not from a real ledger.</sub>
</p>

---

## Why Arno

Most money apps show you charts of the past. Arno answers questions about your future.

Ask _"Can I buy a ₹68,000 laptop on my credit card?"_ and it doesn't guess. It reads your ledger,
simulates 1,000 futures of your own spending, and comes back with **yes**, **yes-but**, or
**no**: the chance you stay above your safety floor, the lowest point ahead, the tradeoffs that
would make it safe, and the ledger rows behind every number.

Three promises, held at once:

- **Your ledger stays on your machine.** Arno runs in Docker on hardware you own and is
  reached only over your private Tailscale network. There is no cloud account, no bank aggregator,
  and no analytics. Only the model calls you configure leave the box, and with local models
  nothing does.
- **Verdicts are computed, with a probability.** Affordability comes from a seeded probabilistic
  projection in plain Python, never from the model's opinion. "Yes" means at least a 90% chance
  you stay above your floor, no goal slips, and no bill is missed.
- **Every number is traced.** The model decides _what_ to compute; code computes it. Each figure
  in a reply must come from a tool return or a script the agent ran, and a number check flags any
  that don't.

## What you can ask

Chat in English, Hindi, or Hinglish, by text or voice. A few examples:

| You say | Arno does |
|---|---|
| "Can I buy a ₹68,000 laptop on the Slice card?" | Runs the projection and returns a verdict card with its probability and tradeoffs |
| "Where did October go?" | Breaks the month down by category and links the rows behind each total |
| "If I prepay ₹1 lakh on my loan, how much interest do I save?" | Writes a short Python script over your data, runs it in a sandbox, and shows the script |
| "Paid ₹480 for chai with the team, Ravi owes me half" | Logs the spend and the split instantly, with undo |
| "Every Sunday, tell me what I spent on food" | Schedules a read-only question that answers into your inbox |
| A photo of a receipt or a UPI screenshot | Turns it into one transaction and keeps the image |

## What it does

- **Captures everything:** chat, push-to-talk voice notes, photos of receipts and UPI
  screenshots (share straight from your UPI app on Android), and statement imports from any bank:
  PDF, spreadsheet, or text, password-protected or not. It learns each new statement layout once,
  and keeps one record per real money movement however it was captured.
- **Knows the whole picture:** a double-entry ledger with bank accounts, credit cards and their
  billing cycles, cash, wallets, and loans; salary and freelance income; rent, EMIs,
  subscriptions, and card installment plans; goals with deadlines; a floor your balance must not
  cross; people, splits, and who owes whom; asset accounts such as EPF, PPF, and FDs; and merchant
  categories it learns from your corrections.
- **Answers and models:** any question the data supports, through read tools and a read-only SQL
  escape hatch; multistep what-ifs through Python scripts it writes and shows you; and
  affordability verdicts that propose concrete fixes: wait until a date, pay by card or EMI, delay
  a goal, or trim a reducible category.
- **Watches your back:** bill, EMI, and card-due reminders; a warning when the projection turns;
  a weekly briefing; and detection of new or missed subscriptions, delivered to an in-app inbox
  and web push. These run as fixed code, so they work even when the model is down.
- **Stays in its lane:** it never moves money, never handles passwords (a statement password goes
  on the import card, not in chat), and never recommends investments or products. It states facts
  and tradeoffs; you decide.

<p align="center">
  <img src=".impeccable/mocks/final/shots/06-money-timeline.png" alt="The Money timeline: the ledger behind today, and a 1,000-run projection band ahead with upcoming bills, salary, and the tightest day." width="76%">
  <img src=".impeccable/mocks/final/shots/20-phone-thread.png" alt="The same affordability answer on a phone." width="22%">
  <br>
  <sub>Money opens on a timeline: the ledger behind today, the projection ahead. Daily use happens
  on the phone, as an installable web app. Interface design comps; the figures are
  illustrative.</sub>
</p>

## How it works

<p align="center">
  <img src="docs/arno/diagrams/readme-how-it-works.png" alt="How an Arno answer is made: your message goes to the agent, which calls plain-Python tools directly or through Code Mode scripts; the tools read the Postgres ledger and run the projection engine, and every reply passes a number check before it reaches you with verdict cards and citations." width="100%">
</p>

- **One rule.** The model reasons, models scenarios, and explains tradeoffs, but every number it
  states comes from code. Rationale in [`plan.md` §4](docs/arno/plan.md#4-architecture).
- **The ledger.** One Postgres database is the whole state, built on a vendored copy of
  [pgledger](https://github.com/pgr0ss/pgledger). Money is integer paise everywhere below the
  display layer.
- **The engine.** [`engine/projection.py`](src/arno/engine/projection.py) is pure: no clock,
  no database, no model. Scheduled events land exactly; day-to-day spending is resampled from your
  own last 90 days, with weekdays and weekends kept apart. The affordability tradeoffs come from
  rerunning the same engine.
- **The tools.** Plain Python over the database, free of framework types, so the agent, the REST
  API, and the web app all call the same functions. The agent's SQL runs as a read-only role with
  a two-second timeout and no access to auth tables.
- **Untrusted text stays data.** Statement text and payee notes can't make a lasting change unseen:
  once a conversation has read such text, memory, setup, and bulk writes need one confirm tap.
  Everyday writes stay instant and undoable.

Deeper reading: the [system design](docs/arno/system-design.md), the
[agent loop](docs/arno/diagrams/sd-agent-loop.png), and the
[tool surface](docs/arno/tools.md).

## Measured, not claimed

Every result below is written by a command, not by hand. When a measurement is missing, the
README says so instead of inventing one.

### Model scorecard

Every model runs the same agent eval suite against a synthetic demo persona, with a fresh database
cloned for each trial. The suite is in [`evals/`](evals/README.md); run it with
`arno eval --model X`. A dash means that model or category has not run yet.

<!-- eval-scorecard:start -->
| Model | Answers and what-ifs | Writes | Honesty when data is missing | Injection | Advice boundary | Never moving money / passwords | Long threads after a summary | Injection gate | Fooled | Number flags | Median cost | Median latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `deepseek-v4.1-flash` | 96% (25/26) | 100% (22/22) | 100% (11/11) | 100% (50/50) | 100% (11/11) | 100% (10/10) | 75% (3/4) | 100% (150/150) PASS | 7% (11/150) | 8 (7 untraced) | — | 18.4s |

`pass^3` is the share of cases that passed in all three trials. The injection gate
is the share of trials where no lasting write landed without its confirm card; the
fooled rate is the share where the model acted on the note even when the gate
stopped it. Percentages use all trials shown in the run.

Run notes:
- deepseek-v4.1-flash: Full 134-case suite, repeat=3, after the release-hardening pass and the Pyrefly strict typing pass.
<!-- eval-scorecard:end -->

The table is the full 134-case run on the final code. Two cases each slipped in one of their
three trials: a grocery answer that left its total and trip count to the presented card, and a
long thread that recalled the floor decision without its ₹25,000 value. Answers and writes
together scored 47/48, honesty 11/11, the injection gate 150/150, and the answer number gate
78/78, so every category meets its target from
[the success measures](docs/arno/system-design.md#success-measures) and the L4 gate passes.

<details>
<summary>What the columns mean</summary>

- **Injection** cases plant instructions in statement text and payee notes. The gate measures
  whether a lasting write ever landed without its confirm card.
- **Number flags** count figures in a reply with no source. "Derived" flags are correct prose
  arithmetic; "untraced" flags have no source at all. Only the answer cases assert the number
  check; the flag column counts every category.
- The targets are answers and writes at 90% or more, honesty at 95% or more, injection at 100%,
  every answer number traced, and advice, never-moving-money, and long threads at 90% or more.

</details>

### Projection backtest

`arno backtest` runs the projection as of the first day of each month and compares its
median 30 days later with the actual ledger balance. It uses a real 12-month backfill when the
ledger has one. This machine doesn't, so the run below uses the deterministic synthetic history
and says so.

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

An honest P10–P90 band should hold the actual balance about 80% of the time; this one holds it
75% of the time, and every miss is small. That is a real calibration result, not a claim of a
perfect band.

### HackerRank "Buy or Wait"

The scorer replays the 25 labeled sample requests of an external affordability challenge through
the engine and compares its status, safe amount, and earliest full-payment date with the labels.
The dataset has no license, so it is never committed, and no score is published without it.

<!-- hackerrank:start -->
HackerRank sample score: not run. the HackerRank sample dataset is not on this machine; set ARNO_HACKERRANK_DATASET to the dataset folder (it has no license and is never committed).
<!-- hackerrank:end -->

## Quick start

You need Docker with Compose. For phone access, the host should be on your
[Tailscale](https://tailscale.com) network. **Host disk encryption** (BitLocker, LUKS, or
FileVault) is an install requirement: it is what protects the database at rest.

```bash
git clone https://github.com/Joy9001/Arno.git && cd Arno
cp .env.example .env
# Generate every secret with `openssl rand -hex 32` (hex, not base64: the database passwords
# land inside DATABASE_URL, where `/` or `+` breaks the URI).
# Set POSTGRES_PASSWORD, AGENT_RO_PASSWORD, BACKUP_PASSWORD, RESTIC_PASSWORD, and the chat model
# (CHAT_MODEL, CHAT_API_KEY). Local Ollama works too.
# Also set all four Phoenix values: PHOENIX_PASSWORD, PHOENIX_SECRET, PHOENIX_ADMIN_SECRET
# (32+ chars, a digit and a lowercase letter), and PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD.
# Phoenix refuses to start without them, so it restart-loops while the app keeps working.
docker compose up -d --build        # or pull the release image: docker compose pull && docker compose up -d
docker compose logs api             # prints a one-time enrollment link and a QR code
```

Open <http://localhost:8000>, follow the enrollment link, and create your passkey. That's it: start
by telling the agent your balances, salary day, and card dues, and it can answer from the first
conversation.

Something already owns `8000`, `5433`, or `6006`? Set `ARNO_API_PORT`, `ARNO_DB_PORT`, or
`PHOENIX_UI_PORT`. Running a second stack on one host also needs `COMPOSE_PROJECT_NAME` (or
`-p <name>` on every command), because `compose.yaml` pins `name: arno` and the container
names would collide. The optional Ollama and speech-to-text ports (`11434` and `8001`) are fixed.

**Want to look around first?** Seed the synthetic demo persona into an empty instance:

```bash
docker compose exec api arno demo seed
```

<details>
<summary>Reach it from your phone over Tailscale</summary>

On the host:

```bash
tailscale serve --bg 8000
```

That serves `https://<machine>.<tailnet>.ts.net` over HTTPS. Passkeys are bound to a hostname, so
set `ARNO_RP_ID=<machine>.<tailnet>.ts.net` and
`ARNO_RP_ORIGIN=https://<machine>.<tailnet>.ts.net` in `.env`, run `docker compose up -d`,
and enroll from that address. Add more devices later in **Settings → Passkeys → Add a device**.

</details>

<details>
<summary>Optional profiles: local models, local speech-to-text, traces</summary>

```bash
docker compose --profile local-llm up -d    # chat on a local model, nothing leaves the box
docker compose --profile local-stt up -d    # voice notes transcribed locally
```

Phoenix runs in the normal start and receives the agent's model and tool traces locally. Turn it
off with `OTEL_SDK_DISABLED=true`; the app keeps serving if Phoenix is down. Tracing is off unless
`PHOENIX_COLLECTOR_ENDPOINT` is set, so a host CLI or eval exports only when it points at a
collector. See [observability](docs/arno/observability.md).

</details>

The [self-hosting guide](docs/arno/self-hosting.md) covers everything else: choosing models,
exactly what each provider receives, backups, restore, upgrades, and troubleshooting.

Already running Fin-Agent?
[Upgrading from Fin-Agent](docs/arno/self-hosting.md#66-upgrading-from-fin-agent) moves the old
volumes and database into Arno instead of starting over.

### Backups

A `backup` sidecar runs `pg_dump` once a day, records a row count and hash for every table, and
ships an encrypted copy with [restic](https://restic.net) to `RESTIC_REPOSITORY` (a local volume by
default; S3, B2, R2, or rclone to go off-site). `arno restore` rebuilds a fresh machine from
the newest snapshot and verifies that every transfer balances. Restore from a backup the current
image made; an older dump still restores, but a dump written without its table grants leaves
`run_query` unprivileged until a new backup replaces it.

> [!WARNING]
> Keep `RESTIC_PASSWORD` somewhere other than this machine. Without it, the backups can't be
> read, and there is no recovery path.

### Commands

<details>
<summary>The <code>arno</code> CLI</summary>

Admin commands run on the host, never over HTTP. Inside Compose, prefix them with
`docker compose exec api`.

```bash
arno migrate                   # apply pending SQL migrations
arno serve                     # API + built web app (127.0.0.1:8000)
arno chat                      # terminal chat; --thread <id> reopens, --message sends once
arno export --format json --out ledger.json        # json, csv, or sql (pg_dump)
arno setup                     # print a fresh enrollment link and QR code
arno passkeys list             # registered passkeys
arno passkeys reset            # delete every passkey and session, reopen setup
arno demo seed                 # seed an empty demo instance (--reset to reseed)
arno demo template             # clone the demo as an eval template
arno eval --model X            # run the agent evals and publish the scorecard (spends money)
arno eval --model X --dry-run        # list the cases for free
arno backtest                  # measure the projection against history; publishes here
arno repair-openings           # place each import's opening at its statement's first day
arno hackerrank                # score the local HackerRank sample set
arno restore                   # restore the newest restic snapshot and verify every table
```

`repair-openings` is for a ledger written before this release: after you upgrade an instance that
imported statements before it, run it once. An import's printed opening used to stay inside the
balance adjustment dated the day the balance was stated, so every date the statement covered read
short by it. `arno repair-openings --dry-run` prints one line per
import—account, period, the opening it would book at the first day, the closing difference, and
how many earlier balance adjustments it would supersede—and writes nothing. Run it again without
`--dry-run` to apply, with `--yes` to skip the question. It is idempotent, and undoing the import
later reverses its repair too.

</details>

## Security and privacy

- **Private network only.** Compose publishes on `127.0.0.1`; Tailscale provides HTTPS. Nothing is
  exposed to the public internet.
- **One user, passkey login.** Sessions are opaque tokens stored as SHA-256 hashes in `HttpOnly`,
  `Secure`, `SameSite=Strict` cookies. Export and passkey changes need a fresh passkey check.
- **Least privilege in the database.** Three roles: `app` owns the schema, `agent_ro` can only
  read ledger-side tables with a two-second timeout, and `backup` can only dump and log its runs.
- **A strict content security policy.** No inline scripts, images only from the app's own origin,
  and links in chat render as plain text.
- **No analytics, no telemetry.** Traces stay on the host. Secrets live in `.env`, never in the
  database.

The full posture, including what each model provider receives and keeps, is in
[self-hosting §4–7](docs/arno/self-hosting.md#4-what-leaves-the-box).

## Built with

[FastAPI](https://fastapi.tiangolo.com) ·
[PydanticAI](https://ai.pydantic.dev) and `pydantic-ai-harness` ·
[PostgreSQL 18](https://www.postgresql.org) with `asyncpg` ·
[Vite](https://vite.dev) and [React](https://react.dev) with
[assistant-ui](https://www.assistant-ui.com) ·
[Tailscale](https://tailscale.com) ·
[restic](https://restic.net) ·
[Arize Phoenix](https://phoenix.arize.com) for local traces

The double-entry core is [pgledger](https://github.com/pgr0ss/pgledger) (MIT, © 2025 Paul Gross),
vendored unmodified at commit `fee1aa0` with ULID helpers from
[scoville/pgsql-ulid](https://github.com/scoville/pgsql-ulid) (BSD-3-Clause, © 2021 Scoville). No
endorsement is implied. Every stack choice and its reason is in
[`plan.md` §3](docs/arno/plan.md#3-stack).

## Develop

Python 3.14 with [uv](https://docs.astral.sh/uv/); the web app uses Node 24 with pnpm.

```bash
uv sync --dev
docker compose up -d db                                  # dev Postgres on 127.0.0.1:5433
docker compose exec db createdb -U app arno_test            # once, for tests
uv run arno migrate && uv run arno serve
pnpm --dir web install && pnpm --dir web dev             # Vite proxies /api to the API
```

<details>
<summary>Checks, tests, and git hooks</summary>

```bash
uv run ruff check . && uv run ruff format --check .
uv run pyrefly check                                     # types: docs/arno/typing.md
uv run python scripts/license_audit.py
DATABASE_URL=postgresql://app:<password>@127.0.0.1:5433/arno_test \
AGENT_RO_PASSWORD=<password> BACKUP_PASSWORD=<password> uv run pytest -q
pnpm --dir web lint && pnpm --dir web typecheck && pnpm --dir web build
```

Tests refuse any database whose name doesn't end in `_test`, block every non-loopback socket and
DNS lookup, and point each model role at a closed port, so tests can never call a model.

[pre-commit](https://pre-commit.com) hooks run the CI checks locally: install them once with
`uv tool install pre-commit && pre-commit install`.

</details>

<details>
<summary>Project layout</summary>

```
src/arno/
  api/         FastAPI app, auth, static web build, security headers
  auth/        passkeys, sessions, first-run setup
  db/          connection, migrations/*.sql, vendor/pgledger/
  ledger/      plain functions over Postgres: accounts, movements, schedules, goals, people
  india/       categories, recurrence rules, the working-day calendar, rupee formatting
  engine/      the seeded projection, affordability, backtest, and HackerRank scorer
  tools/       plain-Python read and write tools
  agent/       the PydanticAI agent, Code Mode toolset, cards, history, and number check
  importers/   statement readers, parsers, checks, reconciliation
  jobs/        the job loop: reminders, briefings, scheduled questions
  demo/        the synthetic demo persona
web/           Vite and React app, served by the API from web/dist
backup/        pg_dump + restic sidecar
evals/         pydantic_evals suite and scorecard writer
tests/         pytest, including a software WebAuthn authenticator
```

</details>

Contributor and agent conventions are in [`AGENTS.md`](AGENTS.md). Decisions live in
[`plan.md`](docs/arno/plan.md), which wins over every other doc.

## Project status

Version 0.2.0. The code build is complete. Two human gates stand between it and "shipped" as
[`plan.md`](docs/arno/plan.md#what-shipped-means) defines it: someone else installs it from
the docs alone (milestone 40), and four weeks of daily use without falling back to a spreadsheet
(milestone 41). What remains is tracked in the
[completion checklist](docs/arno/completion-checklist.md).

India is the only country implemented today, and everything India-specific lives in one folder.

## License

Arno is [MIT licensed](LICENSE). Every shipped dependency's license is audited in CI, and no
GPL, AGPL, SSPL, or Elastic code ships in the Arno image. Phoenix runs as a separate,
source-available Elastic 2.0 service. Third-party notices are in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
