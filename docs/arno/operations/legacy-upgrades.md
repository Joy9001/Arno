# Legacy upgrades

This guide owns one-time migration procedures for pre-0.3.0 installations: moving from the
Fin-Agent project name and demoting the pre-0.3.0 superuser `app` database role. For routine
version updates and PostgreSQL major upgrades, see [self-hosting](self-hosting.md#update-arno).

---

## Upgrade from Fin-Agent

Arno was named Fin-Agent until November 2026. Because the old name appears in the Compose project
name, Docker volume names, and PostgreSQL database name, an existing Fin-Agent installation moves
its data into the `arno` project rather than upgrading in place. Run every command below from the
repository checkout containing `compose.yaml`.

### 1. Back up and verify a snapshot

Run this on the old checkout before pulling changes:

```bash
docker compose run --rm -e BACKUP_ONCE=1 backup
docker compose run --rm --no-deps --entrypoint restic backup snapshots
```

Confirm the second command lists the snapshot created by the first. Keep `RESTIC_PASSWORD` stored
off this machine.

### 2. Stop the old stack while keeping volumes

```bash
docker compose -p fin-agent down
```

Never pass `-v`: that deletes the volumes this migration carries over. Passing `-p fin-agent`
targets the old project consistently before and after updating the checkout.

### 3. Update the checkout

Run `git pull` to fetch the `arno` project name, `ARNO_*` environment variables, and updated image
references.

### 4. Copy the old volumes into `arno_*` volumes

Run this before any `docker compose up`, because starting the stack first initializes an empty
database inside new volumes:

```bash
for volume in db-data backup-data pre-migration-data tasks-data phoenix-data; do
  docker volume create "arno_$volume"
  docker run --rm -v "fin-agent_$volume:/from" -v "arno_$volume:/to" \
    alpine sh -c "cp -a /from/. /to/"
done
```

Those five volumes hold the ledger, local restic repository, pre-migration dumps, parser task
records, and Phoenix traces. `cp -a` preserves file ownership, permissions, and timestamps so
PostgreSQL can read the copied cluster. Optional model caches (`ollama-data`, `whisper-data`, and
`whisper-cache`) do not need copying; their profiles re-download or rebuild them.

### 5. Rename `.env` variables and the database path

On Linux or in Git Bash, rename the prefix with:

```bash
sed -i 's/^FIN_AGENT_/ARNO_/' .env
```

On other shells, rename every `FIN_AGENT_` key to `ARNO_` manually. Then edit `DATABASE_URL` so its
database name is `arno` instead of `fin_agent`, and update `COMPOSE_PROJECT_NAME` to `arno` if
`.env` sets one. Keep all secret values unchanged.

### 6. Start PostgreSQL and rename the database

```bash
docker compose up -d db
docker compose exec db psql -U postgres -d postgres -c "ALTER DATABASE fin_agent RENAME TO arno"
```

Initialization scripts run only on an empty volume, so the existing database keeps its old name until
you run `ALTER DATABASE`. `psql` connects as `postgres` over the container's local socket without a
password, and no other service is connected yet.

### 7. Start the stack and verify

```bash
docker compose up -d
docker compose ps
curl -s http://127.0.0.1:${ARNO_API_PORT:-8000}/api/health
docker compose exec api arno migrate
```

Confirm `api`, `db`, `backup`, and `phoenix` are healthy and `arno migrate` reports no pending
migrations. Open Arno in your browser and sign in once on each device: the session cookie name
changed from `fin_agent_session` to `arno_session`, while existing passkeys keep working because
`ARNO_RP_ID` did not change.

### 8. Retire the old volumes

Once the renamed install has answered real questions and completed a fresh backup, remove the old
volumes:

```bash
docker volume rm fin-agent_db-data fin-agent_backup-data fin-agent_pre-migration-data \
  fin-agent_tasks-data fin-agent_phoenix-data
```

Backups created before the rename continue to restore unchanged; see
[backups](self-hosting.md#backups) for archive compatibility details.

---

## Demote the pre-0.3.0 superuser `app` role

PostgreSQL volumes initialized before November 14, 2026 made `app` the bootstrap superuser. Until
the cluster is converted, `api` logs a startup warning referencing
[`deploy/demote-app.sh`](../../../deploy/).

`deploy/demote-app.sh` converts the existing cluster in place without deleting volumes, keeps the
`app` password unchanged, verifies every table, content hash, `schema_version` row, and grant
before swapping databases, and leaves a rollback copy named `arno__pre_demote`.

### 1. Stop clients while leaving PostgreSQL running

```bash
docker compose stop api backup phoenix
```

### 2. Run the conversion script inside `db`

```bash
docker compose exec -T db bash -s -- arno < deploy/demote-app.sh
```

If the cluster holds additional Arno databases (for example, a scratch or demo database), list all
of them after `arno` or pass `--leave-others` to leave unlisted databases owned by `postgres`. If
the script is interrupted, run the exact same command again to resume.

### 3. Start the stack and confirm the warning is gone

```bash
docker compose up -d
curl -s http://127.0.0.1:${ARNO_API_PORT:-8000}/api/health
docker compose logs api | tail -n 20
```

### 4. Drop the rollback copy

Once you verify the instance works cleanly, remove the rollback database:

```bash
docker compose exec db psql -U postgres -d postgres -c 'DROP DATABASE "arno__pre_demote";'
```

### Roll back a conversion

- **Before the database swap:** Remove the staging database and restart the stack:

  ```bash
  docker compose exec db psql -U postgres -d postgres -c 'DROP DATABASE IF EXISTS "arno__converted" WITH (FORCE);'
  docker compose up -d
  ```

- **After the swap (while `arno__pre_demote` still exists):** Stop clients, restore the original
  database name and superuser privilege, and start the stack:

  ```bash
  docker compose stop api backup phoenix
  docker compose exec db psql -U postgres -d postgres \
    -c 'ALTER DATABASE arno RENAME TO arno__failed; ALTER DATABASE arno__pre_demote RENAME TO arno;'
  docker compose exec db psql -U postgres -d postgres -c 'ALTER ROLE app WITH SUPERUSER;'
  docker compose up -d
  ```
