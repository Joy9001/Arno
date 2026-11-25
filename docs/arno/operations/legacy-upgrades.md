# Legacy upgrades

This guide owns one-time migration procedures for pre-0.3.0 installations: moving from the
Fin-Agent project name and replacing the pre-0.3.0 superuser `app` database role. For routine
version updates and PostgreSQL major upgrades, see [self-hosting](self-hosting.md#update-arno).

---

## Upgrade from Fin-Agent

Arno was named Fin-Agent until November 2026. Because the old name appears in the Compose project
name, Docker volume names, and PostgreSQL database name, an existing Fin-Agent installation moves
its data into the `arno` project rather than upgrading in place. Run every command below from the
repository checkout containing `compose.yaml`.

### 1. Back up and verify a snapshot

Run this on the old checkout before pulling changes. It uses the old `backup` sidecar:

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
PostgreSQL can read the copied cluster. The optional model cache (`ollama-data`) does not need copying; its profile re-downloads it.

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

Confirm `api` and `db` are healthy and `arno migrate` reports no pending
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

## Replace the pre-0.3.0 superuser `app` role

PostgreSQL volumes initialized before November 14, 2026 made `app` the bootstrap superuser.
PostgreSQL cannot demote the bootstrap superuser in place, so `api` logs a startup warning telling
you to restore a backup into a fresh database volume. The supported path is:

1. Confirm a recent backup exists with `docker compose exec api restic snapshots`.
2. Stop the stack and rename or back up the old `db-data` volume so Compose creates an empty one.
   Never run `docker compose down -v`.
3. Start only the new database with `docker compose up -d db`. Its init script creates `app` as a
   non-superuser owner.
4. Restore with `docker compose run --rm --no-deps --user root api arno restore`. See
   [restore drill](self-hosting.md#restore-drill-on-a-fresh-host).
5. Start the stack with `docker compose up -d` and confirm the warning is gone in
   `docker compose logs api`.

Keep the old volume until the restored instance answers real questions cleanly.
