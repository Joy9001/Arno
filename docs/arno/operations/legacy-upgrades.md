# Legacy upgrades

This guide owns one-time migration procedures for older installations: moving from the
Fin-Agent project name, replacing the pre-0.3.0 superuser `app` database role, replacing passkey
sign-in, and moving to the baseline schema. For routine
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
for volume in db-data backup-data pre-migration-data phoenix-data; do
  docker volume create "arno_$volume"
  docker run --rm -v "fin-agent_$volume:/from" -v "arno_$volume:/to" \
    alpine sh -c "cp -a /from/. /to/"
done
```

Those four volumes hold the ledger, local restic repository, pre-migration dumps, and Phoenix
traces. `cp -a` preserves file ownership, permissions, and timestamps so
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

Confirm `api` and `db` are healthy. A pre-baseline database makes `arno migrate` and `api` refuse
to start; then follow [move to the baseline schema](#move-to-the-baseline-schema). Also follow
[replace passkey sign-in](#replace-passkey-sign-in) for the `.env` changes.

### 8. Retire the old volumes

Once the renamed install has answered real questions and completed a fresh backup, remove the old
volumes:

```bash
docker volume rm fin-agent_db-data fin-agent_backup-data fin-agent_pre-migration-data \
  fin-agent_phoenix-data
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

---

## Move to the baseline schema

The ledger tables changed shape: pgledger is gone, and movements now carry their legs directly.
Because Arno is a single-user app whose data you can recreate from your statements, there is no
in-place upgrade. Every install that ran migrations 1-50 (any release before the baseline schema)
moves to a fresh database instead.

On boot, `api` and `arno migrate` refuse such a database and print:

```text
This database uses the pre-baseline schema (migrations 1–50). Follow
docs/arno/operations/legacy-upgrades.md to move it to a fresh database.
```

Run every command below from the repository checkout containing `compose.yaml`.

### 1. Back up the old database

```bash
docker compose exec -T db pg_dump -U postgres --format=custom arno > arno-pre-baseline.dump
```

The `-T` flag keeps the terminal from corrupting the binary stream. The daily restic backup is a
second copy; see [backups](self-hosting.md#backups). Keep both until the new database answers real
questions.

### 2. Know what does not carry over

The new database starts empty. You re-add your accounts and re-upload your statements. Learned
category rules re-learn as you import. Chat history and undo history are not migrated. The dump
stays as the record of the old data.

### 3. Stop `api` and recreate the database

```bash
docker compose stop api
docker compose exec db psql -U postgres -d postgres   -c "DROP DATABASE arno WITH (FORCE)" -c "CREATE DATABASE arno OWNER app"
```

`psql` connects as `postgres` over the container's local socket without a password. The `app` role
and its password are untouched.

### 4. Update and start `api`

Run `git pull` (or pull the new image), then:

```bash
docker compose up -d api
docker compose logs api
```

`api` migrates the empty database on boot (`ARNO_AUTO_MIGRATE` defaults to on) and logs
`applied migrations [1]`. Check `curl -s http://127.0.0.1:${ARNO_API_PORT:-8000}/api/health`.

### 5. Re-add accounts and re-upload statements

Add your accounts in the app, then upload each statement again. The first imports re-teach the
categorizer.

---

## Replace passkey sign-in

Installs that registered passkeys, and later versions that dropped them, authenticate with your
Tailscale login or an optional passphrase instead. The baseline schema has no passkey, session, job
queue, parser, or categorizer-run tables and no shipped seed category rules, and your display name
lives in settings. Do this before you open Arno:

1. In `.env`, set `ARNO_OWNER_EMAIL` to your Tailscale login, set `ARNO_PASSPHRASE`, or set both.
   With neither set, every protected route returns `401`.
2. Remove the `ARNO_RP_ID` and `ARNO_RP_ORIGIN` lines. Arno ignores them.
3. Optionally set `ARNO_NOTIFY_WEBHOOK_URL` to receive each inbox item as a message. Browser push
   subscriptions are gone.
4. Recreate `api` with `docker compose up -d api`.

See [self-hosting](self-hosting.md) for each variable.
