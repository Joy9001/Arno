# Development operations

This guide owns the maintainer deployment file map, local development workflow, verification
commands, triage, and release checklist; use [self-hosting](self-hosting.md) for host installation,
backups, restore, and updates, [observability](observability.md) for Phoenix tracing and replay,
and [`AGENTS.md`](../../../AGENTS.md) for contributor rules.

Run all commands from the repository root. Shell examples use POSIX syntax; on Windows PowerShell,
translate environment assignments and use `curl.exe` instead of the `curl` alias. Never target an
owner database with scratch or test commands.

---

## Deployment files

The following table maps the files that define Arno's Docker Compose build, runtime, and CI/CD
pipelines.

| File | Responsibility |
|---|---|
| [`compose.yaml`](../../../compose.yaml) | Default stack (`db`, `api`, `backup`, `phoenix`), optional `local-llm` and `local-stt` profiles, volumes, health checks, and loopback ports |
| [`compose.gpu.yaml`](../../../compose.gpu.yaml) | NVIDIA CUDA 13 override for the optional `whisper` service |
| [`Dockerfile`](../../../Dockerfile) | Builds the static web bundle and Python 3.14 API runtime (including PostgreSQL 18 clients and `restic`) |
| [`backup/`](../../../backup/) | Backup sidecar image (`pg_dump`, `restic`, `rclone`) and schedule entrypoint |
| [`deploy/`](../../../deploy/) | Database role provisioning (`app-role.sh`, `phoenix-db.sh`), legacy conversion (`demote-app.sh`), patched Phoenix build (`phoenix-cost/`), and `whisper` image |
| [`.env.example`](../../../.env.example) | Configuration template; actual `.env` files stay local and gitignored |
| [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml) | Python, web, contract, dependency, license, and Compose validation checks |
| [`.github/workflows/release.yml`](../../../.github/workflows/release.yml) | Tag-triggered image builds, runtime/Phoenix contract checks, and GitHub Release publication |

The default stack binds `db` (`5433`), `api` (`8000`), and `phoenix` (`6006`) to loopback only;
`backup` waits for `api` to become healthy so startup sets the `backup` role password. Named
volumes (`db-data`, `backup-data`, `pre-migration-data`, `tasks-data`, and `phoenix-data`) hold
persistent state; never run `docker compose down -v` against an instance whose data matters. See
[install](self-hosting.md#install) for host setup and ports.

---

## Local development and verification

Arno uses Python 3.14 via `uv` and Node 24 with pnpm 11. Install dependencies, install Git hooks,
and start the development PostgreSQL container:

```bash
uv sync --dev
pnpm --dir web install --frozen-lockfile
uv tool install pre-commit && pre-commit install
docker compose up -d db
```

Create a dedicated scratch test database once (`tests/conftest.py` refuses any database name not
ending in `_test` and blocks non-loopback sockets and model calls):

```bash
docker compose exec db createdb -U app -O app arno_test
export DATABASE_URL='postgresql://app:YOUR_PASSWORD@127.0.0.1:5433/arno_test'
export AGENT_RO_PASSWORD='YOUR_AGENT_RO_PASSWORD'
export BACKUP_PASSWORD='YOUR_BACKUP_PASSWORD'
```

Run the host API (`uv run arno migrate && uv run arno serve`) alongside Vite (`pnpm --dir web dev`,
which proxies `/api`). Build the static bundle that `api` serves with `pnpm --dir web build`.

Scale verification to the changed area as defined in [`AGENTS.md`](../../../AGENTS.md):

```bash
# Python lint, formatting, and type checking (see typing.md)
uv run ruff check . && uv run ruff format --check .
uv run pyrefly check

# Targeted iteration (-n 0) vs full suite (-n 4 default in pyproject.toml; CI uses -n auto)
uv run pytest tests/test_AREA.py -q -n 0
uv run pytest -q

# Web lint, typecheck, answer-contract tests, interaction tests, and build
pnpm --dir web lint && pnpm --dir web typecheck
pnpm --dir web test:answers && pnpm --dir web test:web && pnpm --dir web build

# Financial invariants on a scratch _test database (after ledger/, tools/, importers/, engine/ edits)
uv run arno migrate && uv run arno demo seed && uv run python -m tests.invariants

# Dependencies, licenses, and Compose validation (including GPU override)
uv lock --check && uv run python scripts/license_audit.py
docker compose --env-file .env.example config -q
docker compose --env-file .env.example -f compose.yaml -f compose.gpu.yaml config -q
```

Never run two full `uv run pytest` invocations at once: the local default (`-n 4` in
`pyproject.toml`) spawns four xdist workers against the shared Postgres container (CI overrides
this with `-n auto` on two-core runners). See [type checking](typing.md) for Pyrefly policy and
diagnostic fixes.

For vulnerability audits (which require network access), run the same commands as CI and delete the
temporary file afterwards:

```bash
uv export --frozen --no-emit-project --format requirements-txt -q > requirements-audit.txt
uv run pip-audit --strict --disable-pip -r requirements-audit.txt
rm requirements-audit.txt
pnpm --dir web audit --audit-level high
```

### Real-model journeys, evals, and engine scores

- **HTTP end-to-end driver:** Exercise live API journeys on a scratch `_test` database using
  [`tests/e2e/README.md`](../../../tests/e2e/README.md).
- **Agent evals:** `uv run arno eval --dry-run` lists cases for free. Live `arno eval` spends money
  and requires an explicit request; see [`evals/README.md`](../../../evals/README.md).
- **Engine scores:** Tune forecasts with `uv run arno backtest --source personas --seeds tuning`,
  publish holdout scores with `--seeds holdout --publish`, or score a local dataset with
  `uv run arno hackerrank --dataset <folder>`.
- **Patched Phoenix image:** Test changes to `deploy/phoenix-cost/` against a scratch Phoenix
  database using [`deploy/phoenix-cost/README.md`](../../../deploy/phoenix-cost/README.md).

---

## Deliver a change

1. Read the owning specification in `docs/arno/` and update code, tests, and docs together. Add a
   new numbered SQL file in `src/arno/db/migrations/` for schema changes; never edit an applied
   migration. Grant `agent_ro` access only to ledger-side tables and views.
2. Run the verification suite for the affected area. For Dockerfile or Compose edits, also build
   and exercise the stack in a separate Compose project with scratch volumes and non-colliding
   ports ([shared-host ports](self-hosting.md#configure-ports-on-a-shared-host)).
3. Deploy the change. Source commits do not update running containers: rebuild locally with
   `docker compose up -d --build <service>` (rebuilding `api` also rebuilds the web bundle), or
   deploy a tagged release via [update Arno](self-hosting.md#update-arno). After editing `.env`,
   recreate affected containers with `docker compose up -d <service>`; `restart` keeps the old
   environment.
4. Check `docker compose ps`, logs, `/api/health`, and the affected user journey. Health verifies
   database reachability and schema version only—not backups, model access, or trace export.

Confirm a restorable off-site backup exists before deploying migrations. Downgrading an image does
not reverse a migrated schema; recover from a pre-migration dump or restic snapshot via
[backups, restore, and updates](self-hosting.md#backups-restore-and-updates).

---

## Routine operational checks

Run these checks periodically on a live host with the target Compose project selected:

```bash
docker compose ps
docker compose logs --tail=100 api backup phoenix
curl -fsS http://127.0.0.1:${ARNO_API_PORT:-8000}/api/health
docker compose run --rm --no-deps --entrypoint restic backup snapshots
```

Verify free disk space, the latest off-site snapshot age, and trace delivery in Phoenix
([find a trace](observability.md#find-a-trace)). Listing snapshots is not a restore test; run the
[restore drill](self-hosting.md#restore-drill-on-a-fresh-host) periodically on an isolated target.

---

## Triage

The following table lists first checks for common operational failures.

| Symptom | First checks |
|---|---|
| API unhealthy or unreachable | Check `docker compose ps`, `api` and `db` logs, and local `/api/health`. If local health works but a device fails, check `tailscale serve status`, `ARNO_RP_ID`, and `ARNO_RP_ORIGIN`. |
| Backup missing or stale | Check `backup` logs, disk space, and repository credentials. After changing repositories, seed immediately via [backups](self-hosting.md#backups). |
| Phoenix restart loop or missing traces | Check `phoenix` and `api` logs, required Phoenix secrets, `OTEL_SDK_DISABLED`, and [Phoenix database recovery](observability.md#phoenix-database-recovery). |
| Image build cannot reach a registry | Check builder DNS, proxy, VPN, and network connectivity before changing Dockerfiles. |
| Migration or startup fails | Preserve logs and backups, inspect the failed migration and `/app/pre-migration` dump, and test recovery on an isolated target first. |
| Pre-0.3.0 superuser warning on boot | Follow [demote the pre-0.3.0 superuser `app` role](legacy-upgrades.md#demote-the-pre-030-superuser-app-role). |

---

## Release checklist

1. **Bump version mirrors consistently.** Update `pyproject.toml`, `compose.yaml` (`api` and
   `backup` default tags), `web/package.json`, `src/arno/__init__.py`, `.env.example`, `uv.lock`,
   and the version badge in [`README.md`](../../../README.md). Note that `tests/test_release.py`
   enforces `pyproject.toml`, `compose.yaml`, `web/package.json`, `src/arno/__init__.py`, and
   `.env.example`, but does not check `uv.lock` or the `README.md` version badge—verify those two
   manually.
2. **Run pre-tag verification on `main`.** Confirm CI is green on the release commit and run:

   ```bash
   git status --short
   uv run pytest tests/test_release.py -q -n 0
   uv lock --check && uv run python scripts/license_audit.py
   docker compose --env-file .env.example config -q
   docker compose --env-file .env.example -f compose.yaml -f compose.gpu.yaml config -q
   ```

   For direct dependency additions, confirm `THIRD_PARTY_NOTICES.md` is updated. If changing the
   pinned Phoenix version, complete the contract verification in
   [`deploy/phoenix-cost/README.md`](../../../deploy/phoenix-cost/README.md) first.
3. **Tag and push.** Replace `vX.Y.Z` with the version in `pyproject.toml`:

   ```bash
   git tag vX.Y.Z
   git push origin main
   git push origin vX.Y.Z
   ```

4. **Monitor the release workflow.** Pushing a `v*` tag triggers
   [`.github/workflows/release.yml`](../../../.github/workflows/release.yml), which runs three
   sequential stages: `images` (verifies the tag against `pyproject.toml` and pushes
   `ghcr.io/joy9001/arno` and `ghcr.io/joy9001/arno-backup`), `contracts` (runs
   `tests/test_image_runtime.py` against the pushed images and builds `deploy/phoenix-cost` to run
   `tests/test_phoenix_cost_contract.py`), and `publish` (creates a GitHub Release if one does not
   already exist). Inspect status with `gh run list --workflow release.yml --limit 5`; never move a
   published tag.
5. **Publish hand-written release notes.** `release.yml`'s `--generate-notes` fallback lists merged
   PRs, which this repository does not use. Read `git log <previous-tag>..vX.Y.Z` and the diff,
   write plain prose in the applicable `##` sections (`Highlights`, `Fixes`, `Upgrade notes`,
   `Known limitations`, omitting chore, docs, and test-only commits and never quoting `private/` or
   real statements), and publish:

   ```bash
   gh release edit vX.Y.Z --notes-file release-notes.md
   gh release view vX.Y.Z
   ```

   Use `gh release create vX.Y.Z --verify-tag --title vX.Y.Z --notes-file release-notes.md` if
   publishing before the workflow creates the release. Publishing images does not update running
   hosts; update the host separately via [update Arno](self-hosting.md#update-arno) (which also
   rebuilds the local `phoenix` image).
