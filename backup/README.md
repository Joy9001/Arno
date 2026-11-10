# The backup sidecar

`docker compose` starts one `backup` service. It checks hourly and runs when the last successful
backup is over 24 hours old, so a sleeping laptop catches up on wake. It dumps the app database,
writes `backup_manifest.json` (a row count and content hash for every table), hands both to
`restic`, prunes daily and weekly snapshots, and records the attempt in `backup_runs`. The dump
and the manifest come from one exported snapshot, so a write that commits mid-backup is in both
or neither.

The sidecar is not the API process, so backups keep working when the app is broken. It connects
as the `backup` role: `SELECT` on the database for `pg_dump`, and `INSERT` on `backup_runs` only.

## Configuration

```dotenv
BACKUP_PASSWORD=<long random value>          # the sidecar's database role
RESTIC_PASSWORD=<long random value>          # THE ONE SECRET TO KEEP OFF THIS MACHINE
RESTIC_REPOSITORY=/backups/restic            # local volume; see below for off-site
# BACKUP_INTERVAL_SECONDS=3600
# BACKUP_MAX_AGE_SECONDS=86400
```

The default repository is a local Docker volume (`backup-data`), which is not off-site. To ship
off-site, use Google Drive through rclone, or S3-compatible storage such as Cloudflare R2, AWS S3,
or Backblaze B2's S3 endpoint. Both need only environment variables. `RESTIC_PASSWORD` is the one
secret that must live somewhere other than this machine; losing it makes the off-site copies
unreadable. **Store it off this machine, in a password manager or a safe.**

Google Drive is the current recommended setup, with your own Google OAuth client (rclone's shared
client ID is rate-limited and being retired):

```dotenv
RESTIC_REPOSITORY=rclone:gdrive:arno-backups
RCLONE_CONFIG_GDRIVE_TYPE=drive
RCLONE_CONFIG_GDRIVE_SCOPE=drive.file
RCLONE_CONFIG_GDRIVE_CLIENT_ID=<client id>
RCLONE_CONFIG_GDRIVE_CLIENT_SECRET=<client secret>
RCLONE_CONFIG_GDRIVE_TOKEN='{"access_token":"...","token_type":"Bearer","refresh_token":"...","expiry":"..."}'
```

Get the token with `rclone authorize "drive"` and a base64 blob that carries the client and the
`drive.file` scope; `docs/arno/self-hosting.md` §6.1 has the console steps and the command.
`drive.file` covers only the files this client creates, not the rest of your Drive. The client
secret and refresh token are credentials: keep them out of git.

Cloudflare R2 needs an API token scoped to **Object Read & Write** on that bucket only:

```dotenv
RESTIC_REPOSITORY=s3:https://<account-id>.r2.cloudflarestorage.com/<bucket>
AWS_ACCESS_KEY_ID=<R2 API token access key id>
AWS_SECRET_ACCESS_KEY=<R2 API token secret>
AWS_DEFAULT_REGION=auto
```

Other rclone remotes work through `RCLONE_CONFIG_<NAME>_*` variables. SFTP does not work: the image
has no ssh client.

The sidecar runs `restic init` itself on first use. After you change `RESTIC_REPOSITORY`, the age
check reads `backup_runs` and does not know which repository a success went to, so the new
repository stays empty for up to 24 hours. Run one backup immediately and check it:

```bash
# `up -d` recreates the container; `docker restart` keeps the old environment.
docker compose up -d backup && docker compose run --rm -e BACKUP_ONCE=1 backup
docker compose run --rm --no-deps backup restic snapshots
```

To carry old snapshots across, `restic copy --from-repo <old repository>` exists.

## Restore

The milestone 37 drill is one command. On a fresh machine, with the repository and
`RESTIC_PASSWORD` in `.env`:

```bash
docker compose up -d db
docker compose run --rm --no-deps --user root api arno restore
docker compose up -d
```

The default local `backup-data` volume is root-owned, so the one-off restore reads it as root. An
off-site repository does not need `--user root`. `arno restore` pulls the dump and manifest out
of the newest snapshot, creates the target
database if it is absent, restores the dump, recomputes every table's row count and hash, and then
checks that every pgledger transfer sums to zero and every account balance equals its entries. It
prints one line per check and exits non-zero on a mismatch. It refuses a database that already has
tables unless `--force` is passed.

To choose a snapshot, or to use files extracted by `restic`:

```bash
arno restore --repository "$RESTIC_REPOSITORY" --snapshot <id>
restic restore <id> --target /restore
arno restore --from-dir /restore
```

A manual `pg_dump` has no manifest; pass `--allow-no-manifest` to accept its internal ledger checks
alone. The verified round-trip is `tests/test_l8_ship.py`: a seeded database with preferences,
conversations, a message, an attachment, schedules, goals, and audit rows goes through a real
restic repository and comes back into a fresh database, compared table by table.

After building the images, `tests/test_image_runtime.py` can also check the tools and writable
paths in the actual containers. Set `ARNO_API_TEST_IMAGE` and `ARNO_BACKUP_TEST_IMAGE`
to the image tags, and set `DATABASE_URL` to a disposable database ending in `_test` before
running `uv run pytest tests/test_image_runtime.py`. Like the rest of the test suite, these tests
reset that database.
