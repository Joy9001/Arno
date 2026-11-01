# Fin-Agent

A self-hosted, open-source finance agent that holds your whole financial picture and answers any
question about it — including whether you can afford something, and what it would cost you.

> **Status: L1 (ledger) is built.** Postgres in Compose with numbered SQL migrations, schema
> v1 on a vendored pgledger, a FastAPI shell serving a Vite/React build under a strict content
> security policy, passkey login, and CI. L1 adds accounts, balanced movements, schedules,
> goals, people, categorization rules, group undo, full export, the restic backup sidecar, and
> the seeded demo persona. The agent and projection engine land in later layers. The
> authoritative plan is [`docs/fin-agent/plan.md`](docs/fin-agent/plan.md); the May 2026 docs in
> [`docs/archive/`](docs/archive/) are not authoritative.

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
  backups. Full choices are in [plan §3](docs/fin-agent/plan.md#3-stack).

## Run it

Requirements: Docker with Compose, and a host that is already on your Tailscale network for
remote access.

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD and AGENT_RO_PASSWORD, then either:
#   - keep FIN_AGENT_RP_ID=localhost for local use, or
#   - set FIN_AGENT_RP_ID and FIN_AGENT_RP_ORIGIN to your machine's tailnet name.
docker compose up -d --build
```

The first start applies the migrations and prints a setup token. Every start while setup is open
prints a fresh token, so read the current log line:

```bash
docker compose logs api | Select-String "setup token"   # PowerShell
docker compose logs api | grep "setup token"            # bash
```

Open the app, enter the token, and create the passkey. The token is also available with
`docker compose exec api fin-agent setup-token`.

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
fin-agent setup                 # issue a first-run setup token
fin-agent setup-token          # same, kept for scripts
fin-agent passkeys list        # registered passkeys
fin-agent passkeys reset       # delete every passkey and session, reopen setup
fin-agent demo seed            # seed an empty demo instance
fin-agent demo seed --reset    # reseed (asks first; add --force in scripts)
fin-agent demo template        # clone as an eval template (asks first)
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
  demo/        the seeded synthetic persona and template clone
  cli.py
web/           Vite and React app (a static build): read-only panels and Settings
backup/        pg_dump + restic sidecar
tests/         pytest, including a software WebAuthn authenticator
compose.yaml
```

Folders for later layers (`engine/`, `importers/`, `agent/`, `jobs/`) are created when their layer
starts. The old eval harness in `evals/` predates this plan and retires at L4.

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
