# The backup sidecar

`docker compose` starts one `backup` service. It checks hourly and runs when the last successful
backup is over 24 hours old, so a sleeping laptop catches up on wake. It dumps the app database,
writes `backup_manifest.json` (a row count and content hash for every table), hands both to
`restic`, prunes daily and weekly snapshots, and records the attempt in `backup_runs`.

The sidecar is not the API process, so backups keep working when the app is broken. It connects
as the `backup` role: `SELECT` on the database for `pg_dump`, and `INSERT` on `backup_runs` only.

## Configuration

```dotenv
BACKUP_PASSWORD=<long random value>          # the sidecar's database role
RESTIC_PASSWORD=<long random value>          # THE ONE SECRET TO KEEP OFF THIS MACHINE
RESTIC_REPOSITORY=/backups/restic            # or an S3/B2/R2/rclone target
# BACKUP_INTERVAL_SECONDS=3600
# BACKUP_MAX_AGE_SECONDS=86400
```

The default repository is a local Docker volume (`backup-data`). To ship off-site, set a restic
repository such as `s3:s3.amazonaws.com/<bucket>` and the `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, and `AWS_DEFAULT_REGION` variables restic expects. `RESTIC_PASSWORD` is
the one secret that must live somewhere other than this machine; losing it makes the off-site
copies unreadable. **Store it off this machine, in a password manager or a safe.**

## Restore

The milestone 37 drill is one command. On a fresh machine, with the repository and
`RESTIC_PASSWORD` in `.env`:

```bash
docker compose up -d db
docker compose run --rm --no-deps --user root api fin-agent restore
docker compose up -d
```

The default local `backup-data` volume is root-owned, so the one-off restore reads it as root. An
off-site repository does not need `--user root`. `fin-agent restore` pulls the dump and manifest out
of the newest snapshot, creates the target
database if it is absent, restores the dump, recomputes every table's row count and hash, and then
checks that every pgledger transfer sums to zero and every account balance equals its entries. It
prints one line per check and exits non-zero on a mismatch. It refuses a database that already has
tables unless `--force` is passed.

To choose a snapshot, or to use files extracted by `restic`:

```bash
fin-agent restore --repository "$RESTIC_REPOSITORY" --snapshot <id>
restic restore <id> --target /restore
fin-agent restore --from-dir /restore
```

A manual `pg_dump` has no manifest; pass `--allow-no-manifest` to accept its internal ledger checks
alone. The verified round-trip is `tests/test_l8_ship.py`: a seeded database with preferences,
conversations, a message, an attachment, schedules, goals, and audit rows goes through a real
restic repository and comes back into a fresh database, compared table by table.
