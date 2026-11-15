# Development operations

This guide is for the developer maintaining Arno's build, deployment, and runtime. It maps the
files to change and the checks to make; use [self-hosting](self-hosting.md) for installation,
credentials, backup configuration, restore, and upgrades. [Observability](observability.md) owns
the tracing and logging procedures. The decisions are in [plan.md](plan.md) §3–4 and the
deployment detail is in [platform.md](platform.md#deployment).

Commands in this guide run from the repository root. Shell examples use POSIX syntax; on Windows,
use a shell that supports it or translate environment assignments for PowerShell. Do not run
commands against the owner's instance when a scratch instance is intended.

## Deployment map

The supported deployment is Docker Compose on an x86-64 host. The following files define it.

| File | Maintainer responsibility |
|---|---|
| [`compose.yaml`](../../compose.yaml) | Services, ports, volumes, health checks, environment, and startup dependencies. |
| [`Dockerfile`](../../Dockerfile) | Builds the static web app and Python runtime, including PostgreSQL clients and restore tools. |
| [`backup/`](../../backup/) | Backup image and sidecar schedule, dump, manifest, and restic operations. |
| [`deploy/`](../../deploy/) | Database role provisioning, existing-cluster conversion, Phoenix build, and optional speech server. |
| [`.env.example`](../../.env.example) | Configuration template. Actual `.env` values are local secrets. |
| [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) | Automated Python, web, dependency, license, and Compose checks. |
| [`.github/workflows/release.yml`](../../.github/workflows/release.yml) | Tag-triggered API and backup image publication and GitHub Release. |

The default stack contains `db`, `api`, `backup`, and `phoenix`. The API serves the built web app
and runs the jobs loop; it waits for a healthy database. The backup service waits for the API to
be healthy because API startup provisions its role password. Ollama and whisper are optional
profiles. The API, database, and Phoenix host ports bind to loopback; Tailscale Serve supplies the
private HTTPS path to the API. See [installation](self-hosting.md#2-install) for origins, ports,
and first-run setup.

PostgreSQL holds the ledger, authentication, jobs, and other application state. `db-data` contains
both the Arno and Phoenix databases; the sidecar backs up only Arno's database. `backup-data` is
the default local restic repository, not an off-site backup. `pre-migration-data` contains plaintext
SQL dumps, and `tasks-data` contains parser task records. Phoenix and optional model services have
their own volumes. Protect the host disk and secrets as described in
[self-hosting](self-hosting.md#1-before-you-start); don't delete volumes as a routine reset.

## Development and verification

Use Python 3.14 through `uv` and Node 24 with pnpm 11. Start the development database, set the
database and role credentials locally, and run the API and Vite as described in the
[development instructions](../../README.md#develop). A host API connects to the published
database port; the Compose API connects to `db` inside the network.

Tests must point `DATABASE_URL` to a scratch database ending in `_test`; set `AGENT_RO_PASSWORD`
and `BACKUP_PASSWORD` too. Never substitute the owner's database. The suite blocks network model
calls. The repository's [contributor instructions](../../AGENTS.md#definition-of-done) specify
the required checks by changed area, including full Python tests for shared layers, web checks,
financial invariants, and dependency audits. CI is the source for its current exact commands;
passing CI alone does not verify a deployment or a real model journey. `arno eval` makes paid
model requests and must not be run unless requested.

For deployment changes, validate the configuration without printing the rendered secrets:

```bash
docker compose --env-file .env.example config -q
```

Use a separate Compose project, ports, and scratch data for container or restore experiments;
see [shared-host ports](self-hosting.md#25-ports-on-a-shared-host). For changes to the patched
Phoenix image, follow its [build and contract-test procedure](../../deploy/phoenix-cost/README.md)
against a dedicated scratch Phoenix database.

## Deliver a change

1. Review the relevant plan and satellite document, then change code and configuration together.
   Add a new numbered SQL migration for schema changes; never edit an applied migration. Grant
   `agent_ro` access only where appropriate and keep authentication and secrets tables private.
2. Run the checks required for the changed area. For an image or Compose change, also build and
   exercise the affected services in a scratch stack. Check migrations and recovery on a copy when
   changing database or backup behavior.
3. Choose the delivery path. A source commit does not update running containers. For a local
   source build, recreate the affected services with `docker compose up -d --build` and verify
   them. For published images, follow the [update procedure](self-hosting.md#64-update), which
   pulls the release images and builds the pinned Phoenix image locally. Confirm the checkout,
   `.env` version, and intended target before either operation.
4. Inspect service status and logs, call the health endpoint, and exercise the affected user
   journey. Health reports database connectivity and schema version; it does not prove backups,
   model access, traces, or the web journey work.

Before an upgrade, establish that a recent backup exists and can be restored. Startup creates a
pre-migration dump for a nonempty database and applies pending migrations transactionally. That
dump is plaintext and is not a substitute for an encrypted off-site backup. If an upgrade fails,
stop and use the [restore and update instructions](self-hosting.md#6-backups-restore-and-upgrades);
returning to an old image alone does not reverse a migrated schema. PostgreSQL major upgrades need
a planned dump and restore, not a new server pointed at the old volume.

## Routine checks

Run these from the host with the intended Compose project selected:

```bash
docker compose ps
docker compose logs --tail=100 api
docker compose logs --tail=100 backup
docker compose logs --tail=100 phoenix
curl -fsS http://127.0.0.1:8000/api/health
docker compose run --rm --no-deps --entrypoint restic backup snapshots
```

Replace `8000` with `ARNO_API_PORT` when changed. Inspect logs privately: they can contain sensitive
operational context. Compose rotates service logs at 10 MB with three files. Check host disk space,
the latest snapshot and its destination, and the app's backup alert. The sidecar checks hourly and
by default backs up when the last success is older than 24 hours; the alert fires after 48 hours
without a success. A running sidecar or a snapshot listing is not a restore test. Periodically
perform the [fresh-machine restore drill](self-hosting.md#62-restore-drill-on-a-fresh-machine)
on an isolated target and verify its manifest and financial checks. Keep the restic password off
the host and record where the repository and its access credentials can be recovered.

The Arno backup excludes Phoenix's database and telemetry. Decide separately whether to preserve
them. The cost journal is in Arno's database and supports limited cost-only replay; it cannot
reconstruct missing trace content. See [observability](observability.md#cost-journal-and-replay).

## Triage

Use the following checks to narrow down common failures.

| Symptom | First checks |
|---|---|
| API unhealthy or unreachable | Inspect `docker compose ps` and API/database logs, then test `/api/health` locally. If local health works but a device cannot connect, check Tailscale Serve, the configured origin, and passkey hostname. |
| Backup missing or stale | Read backup logs, check database and repository access, available disk, credentials, and snapshots. After changing repositories, seed the new one immediately using the [backup procedure](self-hosting.md#61-backups). |
| Phoenix restart loop or missing traces | Check Phoenix and API logs, required Phoenix secrets, database provisioning, and exporter configuration. Follow [observability](observability.md#find-a-run); a healthy API does not prove trace delivery. |
| Image build cannot reach a registry | Inspect the reported registry, builder DNS, proxy, VPN, and connectivity before changing application code. |
| Migration or startup fails | Preserve logs and backups; inspect the failed migration and pre-migration dump. Use the documented restore procedure on an isolated target first. |

Database initialization scripts run on a new volume. Existing clusters and Phoenix password
changes need their documented procedures, not a container restart: see
[existing-install conversion](self-hosting.md#67-upgrading-an-existing-install) and
[Phoenix database setup](observability.md#phoenix-database-setup-and-password-changes).

## Release maintenance

Before tagging, run the applicable checks and review the release and recovery implications. The
`pyproject.toml` version, the default API and backup image versions in `compose.yaml`, and the git
tag must agree; `tests/test_release.py` checks the release contract. A `v*` tag push starts the
release workflow, which verifies the tag against `pyproject.toml`, builds and pushes both images
to GHCR with version and `v` tags, and creates a GitHub Release. The workflow is separate from CI,
so inspect the CI result and the release run explicitly. Publishing does not update an installed
stack; apply and verify the update on the target host separately.

When changing dependencies, keep the lockfile and license audit current and update third-party
notices for direct additions. The Phoenix image is a separately built, pinned upstream image with
patches; review its
[upgrade procedure](../../deploy/phoenix-cost/README.md#prove-it-before-shipping-a-change) before
changing the pin. Do not assume the published API and backup images include Phoenix.

## Command conventions and prerequisites

The examples use a POSIX shell and Docker Compose v2. Run them from the checkout containing the
target `compose.yaml`. On Windows, use a shell with POSIX syntax or translate the commands to
PowerShell. In particular, POSIX `export`, command substitution, and `&&` are shell syntax, and
`curl` might be a PowerShell alias. Use `curl.exe` in Windows PowerShell.

Examples use `8000` for the API, `5433` for PostgreSQL, and `6006` for Phoenix. Replace those
ports when the instance uses different values. Compose reads `.env` for interpolation, but
does not export those values to your shell. A command such as `curl` cannot automatically see
`ARNO_API_PORT` from `.env`. A placeholder such as `YOUR_PASSWORD` is a value to replace, not a
literal password. Avoid putting secrets in shell history, command arguments, screenshots, or logs.

Check the installed tools and disk encryption before provisioning:

```bash
docker version
docker compose version
git --version
uv --version
node --version
pnpm --version
tailscale version
```

Tailscale is needed for access from another device. On Windows, `manage-bde -status` reports
BitLocker status from an elevated prompt; on macOS, use `fdesetup status`. On Linux, use
`lsblk -f` and `cryptsetup status DEVICE_NAME`. Keep database and backup volumes on encrypted
storage. See [host disk encryption](self-hosting.md#host-disk-encryption-is-an-install-requirement).

## Create and configure an instance

1. Obtain the checkout and create its local configuration:

   ```bash
   git clone https://github.com/Joy9001/Arno.git
   cd Arno
   cp .env.example .env
   ```

2. Edit `.env` privately. Set distinct long random values for `POSTGRES_PASSWORD`,
   `AGENT_RO_PASSWORD`, `BACKUP_PASSWORD`, `RESTIC_PASSWORD`, `PHOENIX_PASSWORD`,
   `PHOENIX_SECRET`, `PHOENIX_ADMIN_SECRET`, and `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD`.
   Generate each database password as hexadecimal using `openssl rand -hex 32`, because these
   passwords appear in database URLs. Save the restic password outside this host.
3. Set `ARNO_RP_ID` and `ARNO_RP_ORIGIN` to the stable Tailscale hostname and HTTPS origin, or
   use `localhost` and `http://localhost:8000` for host-only use. Configure a model and an
   off-site restic repository before using real data. The complete settings and provider examples
   are in [self-hosting](self-hosting.md#3-configure-models) and
   [`.env.example`](../../.env.example).
4. Validate the configuration, start the stack, and inspect status:

   ```bash
   docker compose config -q
   docker compose up -d --build
   docker compose ps
   docker compose logs --tail=100 api
   curl -fsS http://127.0.0.1:8000/api/health
   ```

5. For Tailscale access, configure the proxy and inspect its status. Replace `8000` with the
   configured API port:

   ```bash
   tailscale serve --bg 8000
   tailscale serve status
   ```

6. Open the enrollment link printed in the trusted API logs on the configured origin and
   register a passkey. If setup is still open and the token needs rotation, run:

   ```bash
   docker compose exec api arno setup
   ```

Compose settings and service `env_file` are distinct. After editing `.env`, recreate the affected
services with `docker compose up -d SERVICE`; `docker compose restart SERVICE` does not replace
the container's environment. Don't print `docker compose config` without `-q` into shared logs,
because the rendered configuration can contain secrets.

## Run the development environment

Install the locked dependencies and start PostgreSQL:

```bash
uv sync --dev
pnpm --dir web install --frozen-lockfile
docker compose up -d db
```

Set `DATABASE_URL` to the intended local database and set `AGENT_RO_PASSWORD` and
`BACKUP_PASSWORD` in your shell or local configuration. For example, the URL shape is
`postgresql://app:YOUR_PASSWORD@127.0.0.1:5433/arno`. Use URL-safe database passwords and do not
paste actual credentials into shared commands. Start the API and web development server in
separate terminals:

```bash
uv run arno migrate
uv run arno serve
```

```bash
pnpm --dir web dev
```

Vite proxies `/api` to the API. To build the static files that the API serves, run:

```bash
pnpm --dir web build
```

To install the repository's local Git hooks, run:

```bash
uv tool install pre-commit
pre-commit install
```

The hook configuration is [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml). Its
pre-push pytest hook skips the suite when `DATABASE_URL` does not end in `_test`; a skipped hook
is not a passing test run.

### Create a scratch test database

With the development `db` running, create a separate database once:

```bash
docker compose exec db createdb -U app -O app arno_test
```

Set the following variables in the test shell, replacing the placeholders privately:

```bash
export DATABASE_URL='postgresql://app:YOUR_PASSWORD@127.0.0.1:5433/arno_test'
export AGENT_RO_PASSWORD='YOUR_AGENT_RO_PASSWORD'
export BACKUP_PASSWORD='YOUR_BACKUP_PASSWORD'
```

The tests refuse a database name without the `_test` suffix. Check the target before running a
seed or a test that modifies data. Demo seed writes synthetic data; its `--reset` option clears
tables, so use a disposable database.

### Run checks

Run the checks appropriate to the change. The full commands are:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check
uv run pytest tests/test_AREA.py -q -n 0
uv run pytest -q
pnpm --dir web lint
pnpm --dir web typecheck
pnpm --dir web test:answers
pnpm --dir web build
uv lock --check
uv run python scripts/license_audit.py
docker compose --env-file .env.example config -q
```

Replace `test_AREA.py` with the affected test file. Python changes require Ruff, formatting,
Pyrefly, and affected tests. Run the full Python suite for changes in `ledger/`, `db/`, `tools/`,
`agent/`, or `api/`. Web changes require lint, typecheck, and build; answer and result card
changes also require `test:answers`. For dependency changes, run the lock and license checks and
update third-party notices for a direct addition. See
[definition of done](../../AGENTS.md#definition-of-done) for the complete rules.

For `ledger/`, `tools/`, `importers/`, or `engine/` changes, seed a disposable `_test` database
and run the financial invariant checks:

```bash
uv run arno migrate
uv run arno demo seed
uv run python -m tests.invariants
```

CI additionally runs Python and web vulnerability audits. To reproduce their commands, run:

```bash
uv export --frozen --no-emit-project --format requirements-txt -q > requirements-audit.txt
uv run pip-audit --strict --disable-pip -r requirements-audit.txt
pnpm --dir web audit --audit-level high
```

Remove the generated `requirements-audit.txt` after the audit. These audits need network access.
The tests themselves block external network and model calls. To list eval cases without model
requests, run `uv run arno eval --dry-run`. A real `arno eval` spends money and requires an
explicit request. For real-model HTTP journeys, use the scratch-only
[end-to-end driver](../../tests/e2e/README.md); its `--seed demo` and `--empty` options overwrite
scratch data. An HTTP driver check is not a browser UI acceptance test.

For a real-model journey, configure the scratch `_test` database and model credentials, then run:

```bash
uv run python -m tests.e2e.driver up --port 8123 --seed demo
uv run python -m tests.e2e.driver state
uv run python -m tests.e2e.driver invariants
uv run python -m tests.e2e.driver down
```

The driver starts a real API, and a `chat` command sends a real model request that can incur cost.
Use `up --keep` to restart without reseeding. See the driver guide for import, approval, and undo
commands.

## Operate containers and images

Use the following commands for the selected Compose project:

```bash
docker compose ps
docker compose logs --tail=100 db
docker compose logs --tail=100 api
docker compose logs --tail=100 backup
docker compose logs --tail=100 phoenix
docker compose logs -f api
docker compose stop api backup phoenix
docker compose start api backup phoenix
docker compose up -d
docker compose down
```

`stop` and `down` stop services; `down` also removes Compose containers and networks, but without
`-v` retains named volumes. Do not use `down -v` on an instance whose data must be kept. The
application jobs loop runs in `api`; stopping the API also stops those jobs. The database can be
left running for inspection or restore preparation.

After source changes, build and recreate the affected service. The API image includes the web
build, so rebuilding the API is necessary to serve changed web source in Compose:

```bash
docker compose up -d --build api
docker compose up -d --build backup
docker compose up -d --build phoenix
docker compose ps
```

For a published release, select the intended `ARNO_VERSION` in `.env`, then run:

```bash
docker compose pull
docker compose up -d
docker compose up -d --build phoenix
docker compose ps
curl -fsS http://127.0.0.1:8000/api/health
```

Inspect logs and exercise the changed functionality after either path. The Phoenix image is
built locally; the release workflow publishes only the API and backup images. Before updating,
check backup and migration readiness in [upgrade and rollback](#upgrade-and-rollback).

## Inspect and maintain PostgreSQL

The cluster contains the `arno` and `phoenix` databases. The `app` role owns the Arno database;
`agent_ro` is restricted to its granted ledger-side reads; `backup` can read for dumps and record
backup attempts; `phoenix` owns its database; and `postgres` is the local-socket bootstrap
superuser. See [database roles](platform.md#security) for exact privileges.

Use the following read-only commands to inspect a running cluster:

```bash
docker compose exec db pg_isready -h 127.0.0.1 -U app -d arno
docker compose exec db psql -U postgres -d postgres -c '\l'
docker compose exec db psql -U postgres -d postgres -c '\du'
docker compose exec db psql -U app -d arno -c 'SELECT * FROM schema_version ORDER BY version;'
```

To apply pending migrations, run `docker compose exec api arno migrate`. New migrations
are numbered SQL files in `src/arno/db/migrations/`. Applied files have recorded checksums: add a
new file rather than changing one. Test migrations and role grants against a scratch copy, then
verify the health endpoint after deployment. API startup makes a pre-migration dump before
pending migrations on a nonempty database.

For a cluster created before the non-superuser `app` role change, follow
[existing-install conversion](self-hosting.md#67-upgrading-an-existing-install). Its in-place
script changes roles and databases, so first preserve and verify a backup. The core commands are:

```bash
docker compose stop api backup phoenix
docker compose exec -T db bash -s -- arno < deploy/demote-app.sh
docker compose up -d
docker compose logs --tail=100 api
```

The script leaves `arno__pre_demote` as a rollback copy. Review the linked procedure for other
databases, interrupted runs, verification, and rollback before removing that copy.

## Back up and export data

The sidecar defaults to checking every hour and backing up after 24 hours without a recorded
success. Its dump and manifest share a database snapshot; restic encrypts the archive. It keeps
seven daily and four weekly snapshots. It records backup attempts in the Arno database, and the
application alerts after 48 hours without a successful backup. The default `/backups/restic`
repository is a local volume, so configure off-site storage separately.

Inspect, create, and verify snapshots with these commands:

```bash
docker compose logs --tail=100 backup
docker compose run --rm --no-deps --entrypoint restic backup snapshots
docker compose run --rm -e BACKUP_ONCE=1 backup
docker compose run --rm --no-deps --entrypoint restic backup check
```

`BACKUP_ONCE=1` creates a real backup and applies retention. `restic check` checks the repository;
it does not replace a restore drill. Avoid concurrent manual and scheduled operations when
investigating repository locks. For Google Drive or S3-compatible configuration and credentials,
see [backup storage setup](self-hosting.md#61-backups). When changing repositories, recreate
`backup` and run a one-time backup immediately: the due check uses database history, not whether
the new repository already has a snapshot.

For an additional manual export, choose a private output location:

```bash
docker compose exec api arno export --format json --out /tmp/arno-export.json
docker compose exec api arno export --format csv --out /tmp/arno-export.zip
docker compose exec api arno export --format sql --out /tmp/arno-export.sql
docker compose cp api:/tmp/arno-export.sql ./arno-export.sql
```

Use `arno export --help` for current options. JSON and CSV exports omit credential tables; the
database dump used for backup is complete and can contain credentials. Treat exports and
pre-migration dumps as plaintext sensitive files, store them on encrypted storage, and remove
temporary copies when no longer needed. See [data export](platform.md#api).

## Restore and verify

First identify the target database and the snapshot. A restore refuses a populated target unless
`--force` is supplied; `--force` drops and recreates that database. Do not use it for a drill.
Use an isolated host or a distinct Compose project with separate credentials, ports, and empty
volumes. When using another project, pass its project name and environment file consistently to
every command; make sure the repository is reachable from that project. `COMPOSE_PROJECT_NAME`
alone is insufficient if host ports collide.

On a fresh target configured with the matching repository and `RESTIC_PASSWORD`, run:

```bash
docker compose up -d db
docker compose run --rm --no-deps --user root api arno restore
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8000/api/health
docker compose logs --tail=100 backup
```

`--user root` is needed for the default root-owned local backup volume; an off-site repository
does not need it. The command restores the latest snapshot, checks table hashes and row counts,
verifies ledger invariants, and recreates the needed roles and grants. Review every reported
check and exercise the app and a subsequent backup. A backup made before grants were included can
restore without the `agent_ro` grants; use a backup made by the current image where possible.

To select a snapshot or restore previously extracted files, use the same isolated target and
choose one of these forms:

```bash
docker compose run --rm --no-deps --user root api arno restore --snapshot SNAPSHOT_ID
docker compose run --rm --no-deps --user root api arno restore --from-dir /RESTORE_DIRECTORY
```

For `--from-dir`, mount the directory into the container first, for example with
`-v "$PWD/restore:/restore:ro"` before `api`, and pass `--from-dir /restore`. Manual dumps
without a manifest require `--allow-no-manifest`; they cannot receive manifest comparison. See
`arno restore --help` and the
[fresh-machine procedure](self-hosting.md#62-restore-drill-on-a-fresh-machine).
The fresh-machine recovery kit includes the restic password, repository access, and, for Google
Drive with `drive.file`, the original OAuth client credentials or an accessible downloaded copy.

## Upgrade and rollback

Before an upgrade, record the running image version, verify an off-site snapshot, and perform a
restore drill on an isolated target. Review migrations and any version-specific upgrade steps.
Keep the previous image and the backup until verification is complete. The API writes plaintext
pre-migration dumps to its named volume; copy them to an encrypted private directory if needed:

```bash
docker compose cp api:/app/pre-migration/. ./pre-migration/
```

After updating, inspect `docker compose ps`, service logs, `/api/health`, the affected user
journey, and a new backup. If migration or verification fails, stop writes, preserve evidence,
and restore a verified compatible backup into an isolated target before any production recovery.
Do not assume an older image can read a newer schema. A destructive replacement requires deliberate
selection of the target and snapshot, and loses writes after that snapshot. The
[restore procedure](self-hosting.md#62-restore-drill-on-a-fresh-machine) describes `--force`.

For an older imported ledger that needs opening repair, inspect the proposal before applying it:

```bash
docker compose exec api arno repair-openings --dry-run
docker compose exec api arno repair-openings --yes
```

For a PostgreSQL major upgrade, plan a separate empty data volume, matching client tools, dump,
restore, role provisioning, and verification. Never start a new major server on the old data
volume. Retain the old volume and test the entire migration on a copy first. A manual dump can be
created with the following command; the resulting file is plaintext and sensitive:

```bash
docker compose exec -T db pg_dump -U postgres --format=custom arno > arno-before-upgrade.dump
```

The current [major-upgrade example](self-hosting.md#65-postgres-major-upgrade) requires an empty
new volume and a dump name accepted by `arno restore --from-dir`; do not run it unchanged against
the existing volume. A successful application restore does not restore the separate Phoenix
database. Plan and test its migration separately if its traces must be retained.

## Observe and recover tracing

Check service health and logs, then open Phoenix at `http://127.0.0.1:6006` on the host and
select the `arno` project:

```bash
docker compose ps
docker compose logs --tail=100 api
docker compose logs --tail=100 phoenix
curl -fsS http://127.0.0.1:6006/healthz
```

Send a test message and inspect the trace. A healthy API does not prove that Phoenix accepted
spans. Check the exporter endpoint, authentication, and `OTEL_SDK_DISABLED` if traces are absent.
Tracing can contain personal financial information; handle logs and Phoenix access accordingly.
The [observability guide](observability.md) describes retention, privacy, and trace interpretation.

For a missing Phoenix database or a changed `PHOENIX_PASSWORD`, update `.env` and run:

```bash
docker compose up -d db
docker compose exec db sh /docker-entrypoint-initdb.d/20-phoenix-db.sh
docker compose up -d phoenix
docker compose logs --tail=100 phoenix
```

To replay eligible finalized cost observations, run:

```bash
docker compose exec api arno cost-replay
docker compose exec api arno cost-replay --attempt ATTEMPT_ID
```

To finalize interrupted ordinary calls as unknown, first stop all API and CLI inference, then
run the one-off command in a separate container:

```bash
docker compose stop api
docker compose run --rm --no-deps api arno cost-replay --finalize-interrupted
docker compose up -d api
```

Keep PostgreSQL and Phoenix running for this operation. The one-off container reads the current
Compose configuration. Batch items remain pending, and replay cannot recover missing trace
content. Follow [cost journal and replay](observability.md#cost-journal-and-replay) before acting
on ambiguous calls.

## Maintain models and authentication

After changing a model or its endpoint in `.env`, recreate the API and check its logs:

```bash
docker compose up -d --no-deps api
docker compose logs --tail=100 api
```

Set the provider, billing mode, endpoint, model ID, and context window for each configured role.
Use a model and provider whose privacy and cost behavior you have reviewed; unknown prices can
stop paid inference. The full settings and fallback rules are in
[model configuration](self-hosting.md#3-configure-models).

To run the optional local model services:

```bash
docker compose --profile local-llm up -d ollama
docker compose exec ollama ollama pull qwen3:4b
docker compose --profile local-stt up -d --build whisper
docker compose logs -f whisper
```

Ollama and the API must agree on the context window. Whisper downloads models on first start;
wait for its health check before using transcription. Set the corresponding model settings and
recreate the API. For the NVIDIA override and its driver requirement, see
[local speech-to-text](self-hosting.md#34-local-speech-to-text).

For eligible ChatGPT plan authentication, use the container's manual callback flow:

```bash
docker compose exec api arno chatgpt-login --manual --no-browser --allow-background
docker compose exec api arno chatgpt-logout
```

The first command enables background inference for this sign-in; omit `--allow-background` if
that consent is not intended. The second disconnects the stored sign-in. Avoid sharing callback
URLs and credentials. Reset deletes every passkey and session and reopens enrollment. Run it only
when recovery is intended, from a trusted console, and register a new passkey at the configured
origin. To inspect passkeys and reset them, run:

```bash
docker compose exec api arno passkeys list
docker compose exec api arno passkeys reset --help
docker compose exec api arno passkeys reset
```

## Release a version

Release from the intended commit on `main`. Confirm `pyproject.toml`, both Compose image defaults,
and the release tag agree. Review the applicable tests, license notices, migration and rollback
plan, and CI result. Inspect the values without exposing `.env`:

```bash
git status --short
git log -1 --oneline
uv run pytest tests/test_release.py -q -n 0
uv lock --check
uv run python scripts/license_audit.py
docker compose --env-file .env.example config -q
```

After the version change is committed and checked, create and push the matching tag. Replace the
example version with the actual approved version:

```bash
git tag v0.2.0
git push origin main
git push origin v0.2.0
```

Pushing the tag publishes images and a GitHub Release; it is an external release action. Inspect
the workflow and published tags, for example:

```bash
gh run list --workflow release.yml --limit 5
gh release view v0.2.0
```

If publication fails, inspect the relevant run with `gh run view RUN_ID --log-failed` and repair
the cause. Do not move a published tag or assume an image was deployed. Update the host separately
using the [published release procedure](#operate-containers-and-images), then verify it.
