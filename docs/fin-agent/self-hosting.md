# Self-hosting Fin-Agent

This is the install and operations guide for one person running one instance. It covers the
supported Docker + Tailscale path, model configuration, backups and the restore drill, upgrades,
and the privacy facts you need before choosing a provider.

`docker compose up` on an x86-64 Linux host, Docker Desktop, or a VPS is the supported path. A
native systemd install, arm64 images, and public-internet hosting are deliberately out of scope
([`parked.md`](parked.md)).

---

## 1. Before you start

| Need | Why |
|---|---|
| An x86-64 Linux host, Docker Desktop, or a VPS with Docker Compose | The shipped images are amd64 |
| About 2 GB of RAM free (4 GB with `--profile local-llm` or `--profile traces`) | Postgres, the API, and the optional services |
| Tailscale on the host, and the Tailscale app on every device you will use | The only network path to the app |
| Host disk encryption **on** | It is what protects the database at rest |
| A password manager, and somewhere off this machine for the restic password | Losing the restic password makes every backup unreadable |

### Host disk encryption is an install requirement

Fin-Agent does not encrypt the database from inside. The database files, the Docker volumes, and
the pre-migration dumps rely on the host's disk encryption. Turn it on before the first real
transaction:

- **Windows:** BitLocker on the system and data volumes.
  `manage-bde -status` shows the state; `manage-bde -on C: -RecoveryPassword` enables it.
- **Linux:** LUKS on the root or data volume. Confirm with `lsblk -f` (look for `crypto_LUKS`) and
  `cryptsetup status <name>`. A cloud VPS's "encrypted disk" option counts only if the provider
  really encrypts the block device; the provider can still read a running VPS (see §7).
- **macOS:** FileVault. `fdesetup status` must say `FileVault is On.`

If the disk is not encrypted, stop and fix that first. A stolen laptop is the most likely way this
data leaves the box, and no application setting replaces disk encryption.

---

## 2. Install

### 2.1 Get the files

```bash
git clone https://github.com/Joy9001/Fin-Agent.git
cd Fin-Agent
```

The release images are `ghcr.io/joy9001/fin-agent:<version>` and
`ghcr.io/joy9001/fin-agent-backup:<version>`. `compose.yaml` pins `FIN_AGENT_VERSION` to the
version in `pyproject.toml`. You can also build locally with `docker compose up -d --build`.

### 2.2 Create `.env`

```bash
cp .env.example .env
```

Set at least these. Use long random values; do not reuse them:

```dotenv
POSTGRES_PASSWORD=<long random>
AGENT_RO_PASSWORD=<long random>
BACKUP_PASSWORD=<long random>
RESTIC_PASSWORD=<long random, kept off this machine>
RESTIC_REPOSITORY=/backups/restic
FIN_AGENT_RP_ID=<machine>.<tailnet>.ts.net
FIN_AGENT_RP_ORIGIN=https://<machine>.<tailnet>.ts.net
```

Generate values with `openssl rand -base64 32` (or `python -c "import secrets;
print(secrets.token_urlsafe(32))"`). `RESTIC_PASSWORD` is the one secret that must live somewhere
other than this machine.

Find the tailnet name with `tailscale status --json` and read `Self.DNSName`, or open the
Tailscale admin console. It looks like `mini-pc.tail1234.ts.net`.

### 2.3 Start

```bash
docker compose up -d --build
docker compose logs -f api
```

The first start applies migrations, then prints a first-run enrollment link and a terminal QR
code. Open it on a phone on the tailnet, or paste the token printed under the link, and create the
passkey. A restart keeps a valid link; `docker compose exec api fin-agent setup` rotates it.

Check the API:

```bash
curl -s http://127.0.0.1:8000/api/health
```

It reports the database and schema version. The API publishes only on `127.0.0.1`, so nothing is
reachable from the LAN or the public internet.

### 2.4 HTTPS over Tailscale

On the host:

```bash
tailscale serve --bg 8000
tailscale serve status
```

That proxies `https://<machine>.<tailnet>.ts.net` to `http://127.0.0.1:8000`. Passkeys require
HTTPS and a stable hostname; the `*.ts.net` name gives both. Set `FIN_AGENT_RP_ID` and
`FIN_AGENT_RP_ORIGIN` to that name **before** registering a passkey, then
`docker compose up -d` if you changed them.

On Android, open the site in Chrome and install the PWA. The share target lets a UPI screenshot go
straight from GPay or PhonePe into Fin-Agent.

### 2.5 Ports on a shared host

If `5433` or `8000` is already taken, set `FIN_AGENT_DB_PORT` and `FIN_AGENT_API_PORT` in `.env`.
`tailscale serve --bg ${FIN_AGENT_API_PORT}` then points at the right port.

---

## 3. Configure models

Each role takes an OpenAI-compatible base URL, API key, and model ID. The same code path runs a
local Ollama/speaches endpoint and a cloud API. Model IDs must be exact, dated versions, never
floating `latest` aliases; re-run `fin-agent eval --model X` after changing one.

| Role | Variables | Used for |
|---|---|---|
| Chat | `CHAT_BASE_URL`, `CHAT_API_KEY`, `CHAT_MODEL` | The agent loop, scheduled questions, parse runs, categorizer and briefing prose |
| Vision | `VISION_BASE_URL`, `VISION_API_KEY`, `VISION_MODEL` | Photos and scanned statement pages with no text |
| Speech-to-text | `STT_BASE_URL`, `STT_API_KEY`, `STT_MODEL` | Voice notes |
| Chat fallback | `CHAT_FALLBACK_BASE_URL`, `CHAT_FALLBACK_API_KEY`, `CHAT_FALLBACK_MODEL` | Optional backup when the main chat endpoint fails |

### 3.1 Cloud chat

```dotenv
CHAT_BASE_URL=https://api.<provider>.com/v1
CHAT_API_KEY=<key>
CHAT_MODEL=<exact, dated model id>
LLM_CONTEXT_WINDOW=<the window the server really uses>
```

The default profile needs a cloud key. A free tier may train on or keep prompts: do not use one
for real statements.

### 3.2 Local chat with Ollama

```bash
docker compose --profile local-llm up -d
docker compose exec ollama ollama pull qwen3:4b
```

```dotenv
CHAT_BASE_URL=http://ollama:11434/v1
CHAT_API_KEY=ollama
CHAT_MODEL=qwen3:4b
LLM_CONTEXT_WINDOW=128000
```

`OLLAMA_CONTEXT_LENGTH` (set from `LLM_CONTEXT_WINDOW` in `compose.yaml`) and
`LLM_CONTEXT_WINDOW` must match what the server really uses. Ollama otherwise truncates a long
history silently. Local models are supported, but the eval scorecard records what each one
handles; the product is designed for a frontier model.

### 3.3 Local speech-to-text

```bash
docker compose --profile local-stt up -d
```

```dotenv
STT_BASE_URL=http://speaches:8000/v1
STT_MODEL=Systran/faster-whisper-small
STT_API_KEY=not-needed
STT_PAYEE_CONTEXT=1
```

The first voice note downloads the model into the `speaches-data` volume. A cloud
OpenAI-compatible transcription endpoint works too; with a cloud endpoint, the optional payee-name
prompt sends those names out as well. Set `STT_PAYEE_CONTEXT=0` to keep them local.

### 3.4 Vision

```dotenv
VISION_BASE_URL=https://api.<provider>.com/v1
VISION_API_KEY=<key>
VISION_MODEL=<exact, dated model id>
```

Vision sees a receipt or UPI screenshot. If no vision endpoint is set, statement pages with no
text and photos fail with a retry card; the rest of the app keeps working.

### 3.5 Fallback

```dotenv
CHAT_FALLBACK_BASE_URL=http://ollama:11434/v1
CHAT_FALLBACK_API_KEY=ollama
CHAT_FALLBACK_MODEL=qwen3:4b
CHAT_FALLBACK_CONTEXT_WINDOW=128000
```

`CHAT_FALLBACK_CONTEXT_WINDOW` must match the backup server's real window. A backup that cannot fit
the thread refuses the request instead of truncating it. Replies the backup wrote carry a badge.

---

## 4. What leaves the box

Checked **October 31, 2026**. Which provider runs each role is your choice. A local model for a
role means nothing leaves for that role.

| Role | Sends | When |
|---|---|---|
| Chat | Snapshot (balances, names, preferences), thread history, tool returns | Every turn |
| Parse run | Sample statement rows, including payee names and notes | New statement layouts only |
| Vision | The photo, or statement pages with no text | Each photo or such page |
| Speech-to-text | Your audio | Each voice note |
| Backups, push | Encrypted backups; contentless push pings | Nightly; on notification |
| Never | Statement passwords, API keys, session tokens, full account or card numbers from statements | |

There are no analytics. Traces stay on this machine.

## 5. What providers keep

Checked **October 31, 2026**. Provider terms change; re-check before trusting a new provider.

| Provider | Trains on it | Keeps it |
|---|---|---|
| [Anthropic API](https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training) | No, by default | [Up to 30 days](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data). [Fable 5 and 5.1, Mythos 5 and 5.1](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention) require 30 days, with no zero-retention option. Flagged content can stay up to 2 years |
| [OpenAI API](https://developers.openai.com/api/docs/guides/your-data) | No, by default | Abuse-monitoring logs, up to 30 days, longer if legally required or to prevent harm |
| [Gemini paid](https://ai.google.dev/gemini-api/terms) | No | Limited abuse-monitoring logs |
| [Gemini unpaid](https://ai.google.dev/gemini-api/terms) | **Yes, with human review.** Google's terms say not to send personal information | Don't use it for any role |
| Local models (Ollama, `speaches`) | Nothing leaves | |

**The one thing to avoid for every role is Gemini's unpaid tier.** It trains on your data with
human review.

---

## 6. Backups, restore, and upgrades

### 6.1 Backups

The `backup` sidecar checks hourly and runs when its last success is over 24 hours old, so a
sleeping laptop catches up. It runs `pg_dump`, writes `backup_manifest.json` (a row count and
content hash for every table), and hands both to restic. The dump and the manifest come from one
exported snapshot, so a write that commits mid-backup is in both or neither. The manifest is what
lets a restore prove the database came back, not just that a dump unpacked.

```dotenv
BACKUP_PASSWORD=<long random>
RESTIC_PASSWORD=<long random>          # THE ONE SECRET TO KEEP OFF THIS MACHINE
RESTIC_REPOSITORY=/backups/restic      # local volume; see below for off-site
```

**Keep `RESTIC_PASSWORD` somewhere other than this machine, such as a password manager or a printed
copy in a safe. Losing it makes every off-site copy unreadable. There is no recovery path.**

The default repository is the local `backup-data` volume, which is not off-site. For a real
off-site copy, use Google Drive through rclone, or S3-compatible storage such as Cloudflare R2,
AWS S3, or Backblaze B2's S3 endpoint. Both need only environment variables. `docker compose logs
backup` and Settings → Backup status show attempts; no success for 48 hours sends an inbox item and
a push.

Google Drive is the current recommended setup:

```dotenv
RESTIC_REPOSITORY=rclone:gdrive:fin-agent-backups
RCLONE_CONFIG_GDRIVE_TYPE=drive
RCLONE_CONFIG_GDRIVE_SCOPE=drive.file
RCLONE_CONFIG_GDRIVE_TOKEN='{"access_token":"...","token_type":"Bearer","refresh_token":"...","expiry":"..."}'
```

To get the token, install rclone on a machine with a browser (`scoop install rclone`, `winget
install Rclone.Rclone`, or [rclone.org/install](https://rclone.org/install/)) and run:

```bash
rclone authorize "drive" "eyJzY29wZSI6ImRyaXZlLmZpbGUifQ"
```

Sign in, then copy the JSON between the arrows into `RCLONE_CONFIG_GDRIVE_TOKEN`, inside single
quotes. The `drive.file` scope lets rclone see only the files it created, not the rest of your
Drive. The refresh token is a credential: treat it like `RESTIC_PASSWORD` and keep it out of git.

Use your own Google OAuth client. rclone's shared client ID is rate-limited (the first backup took
five minutes on `rateLimitExceeded`) and is being retired. In the Google Cloud console, enable the
Drive API, publish the OAuth consent screen to **In production** (a "Testing" app's refresh tokens
expire after 7 days; `drive.file` needs no verification), and create a **Desktop app** client.
Then authorize with a base64 blob that carries all three settings, and add the client to `.env`:

```bash
rclone authorize "drive" "$(printf '{"client_id":"%s","client_secret":"%s","scope":"drive.file"}' \
  "$CLIENT_ID" "$CLIENT_SECRET" | base64 -w0)"
```

```dotenv
RCLONE_CONFIG_GDRIVE_CLIENT_ID=<client id>
RCLONE_CONFIG_GDRIVE_CLIENT_SECRET=<client secret>
```

Under `drive.file`, a client sees only the files it created. Changing clients starts an empty
repository, so seed it again, as described below.

Cloudflare R2 needs an API token scoped to **Object Read & Write** on that bucket only:

```dotenv
RESTIC_REPOSITORY=s3:https://<account-id>.r2.cloudflarestorage.com/<bucket>
AWS_ACCESS_KEY_ID=<R2 API token access key id>
AWS_SECRET_ACCESS_KEY=<R2 API token secret>
AWS_DEFAULT_REGION=auto
```

Any other rclone remote works the same way, through `RCLONE_CONFIG_<NAME>_*` variables. SFTP does
not work: the images have no ssh client.

The sidecar runs `restic init` itself on first use. To switch or move storage later, change
`RESTIC_REPOSITORY` and its credentials, then seed the new repository right away. The age check
reads `backup_runs`, not the repository, so otherwise the new repository stays empty for up to 24
hours:

```bash
# `up -d` recreates the container; `docker restart` keeps the old environment.
docker compose up -d backup && docker compose run --rm -e BACKUP_ONCE=1 backup
docker compose run --rm --no-deps backup restic snapshots
```

To carry old snapshots across, `restic copy --from-repo <old repository>` exists.

Test the repository from the host:

```bash
docker compose run --rm --no-deps backup restic snapshots
```

### 6.2 Restore drill on a fresh machine

This is the milestone 37 check. Given only the restic repository and `RESTIC_PASSWORD`:

```bash
# 1. A fresh host has the repo. Start only the database.
docker compose up -d db

# 2. Restore and verify in one command. The API image owns the PostgreSQL client and restic.
#    The default local backup volume is root-owned, so read it with --user root. An off-site
#    repository does not need that flag.
docker compose run --rm --no-deps --user root api fin-agent restore

# 3. Start the app.
docker compose up -d
```

The command creates `fin_agent` if it is absent, refuses to touch a database that already has
tables, restores the newest snapshot, recomputes every table's row count and hash, and checks that
every transfer balances and every account balance equals its entries. It prints one `ok` line per
check and exits non-zero on any mismatch.

If the database already has tables and you really mean to replace it, add `--force`. That drops
and recreates the target database; it is the destructive path, so it is not the default.

To pick a snapshot or use files already extracted by `restic`:

```bash
fin-agent restore --repository "$RESTIC_REPOSITORY" --snapshot <id>
restic restore <id> --target /restore
fin-agent restore --from-dir /restore
```

The same command is covered by `tests/test_l8_ship.py`, which dumps a seeded database with
preferences, conversations, a message, an attachment, schedules, goals, and audit rows, pushes it
through a real restic repository, restores into a fresh database, and compares the manifest.

A mismatch on a table means the files and the database disagree, not that a write raced the
backup: the dump and manifest are taken from the same exported snapshot.

### 6.3 Pre-migration dump

Before applying pending migrations to a non-empty database, the API writes a plain `pg_dump` into
`/app/pre-migration` (the `pre-migration-data` volume). Retrieve one with:

```bash
docker compose cp api:/app/pre-migration/. ./pre-migration/
```

A fresh database has nothing to dump, so the first start skips it. The dump is a fallback, not a
replacement for the restic backup.

### 6.4 Update

```bash
docker compose pull
docker compose up -d
```

The API dumps before any pending migration and runs each migration in a transaction. If an update
fails, restore the pre-migration dump or the latest restic snapshot. Logs rotate at 10 MB, three
files per service.

### 6.5 Postgres major upgrade

Do not point a new major version at the old data volume. Do a dump and restore:

```bash
# 1. Dump from the old server.
docker compose exec -T db pg_dump -U app --format=custom fin_agent > fin_agent-before-upgrade.dump
docker compose down

# 2. Change the `db` image tag in compose.yaml to the new major, e.g. postgres:19.x.
# 3. Start only the new, empty database.
docker compose up -d db

# 4. Restore through the same verified path. Mount the folder with the dump.
docker compose run --rm --no-deps -v "$PWD:/restore:ro" api \
  fin-agent restore --from-dir /restore --allow-no-manifest

# 5. Start the app and check /api/health.
docker compose up -d
```

Keep the old volume until the new instance has answered a few real questions. The restore command
verifies the ledger and every table before it reports success.

---

## 7. Security and privacy posture

- **Network:** the API publishes on loopback only; `tailscale serve` is the only HTTPS path. No
  port forwarding, no public DNS, no CORS.
- **Auth:** one user, passkey login, opaque session tokens stored as SHA-256 hashes, cookies
  `HttpOnly; Secure; SameSite=Strict`. Export and passkey changes require a fresh passkey check
  within five minutes.
- **Headers:** strict CSP (`default-src 'self'; script-src 'self'; style-src 'self'
  'unsafe-inline'; img-src 'self' blob: data:; object-src 'none'; base-uri 'self'; form-action
  'self'; frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  no-referrer`, and a `TrustedHost` allowlist for the tailnet name.
- **Uploads:** 25 MB cap on statements, photos, and audio.
- **Database roles:** `app` owns the schema; `agent_ro` is SELECT-only on the ledger, plan,
  people, categorization, settings, and conversation tables, with no auth tables, no `TEMP`, and a
  two-second statement timeout; `backup` can SELECT for `pg_dump` and INSERT into `backup_runs`
  only. Phoenix, when enabled, owns only its own database and `agent_ro` cannot connect to it.
- **Logs:** only IDs, timings, and error types. Amounts and Postgres `DETAIL` lines are filtered
  before a handler writes them. Traces do contain content, which is why Phoenix stays local.
- **Not defended:** a VPS provider reading the running disk, and a live host compromise. Both are
  stated rather than papered over; in-database encryption is parked with the first as its trigger.

---

## 8. Traces with Phoenix (optional)

```bash
docker compose --profile traces up -d
```

Set this in the API environment, then restart the API:

```dotenv
PHOENIX_COLLECTOR_ENDPOINT=http://phoenix:6006/v1/traces
```

Phoenix uses its own database and role on the same Postgres server, keeps traces 90 days, and runs
with `PHOENIX_ALLOW_EXTERNAL_RESOURCES=false`. It is published on `127.0.0.1:6006` only. To view it
from another device, turn its login on and expose it with a separate
`tailscale serve --bg 6006`; do not put it on the public internet. The app works the same without
it.

---

## 9. Troubleshooting

| Symptom | Check |
|---|---|
| `docker compose up` exits on `POSTGRES_PASSWORD` | `.env` is missing a required value |
| `/api/health` says the database is unreachable | `docker compose ps`; the API waits for `pg_isready`; then `docker compose logs db` |
| The enrollment link does not work | Open it on the exact `FIN_AGENT_RP_ORIGIN`; a passkey made on `localhost` will not work on `*.ts.net` |
| Passkey login fails after a hostname change | Set the new `FIN_AGENT_RP_ID`/`FIN_AGENT_RP_ORIGIN`, then `docker compose exec api fin-agent passkeys reset` and register again |
| Backups stopped | Settings → Backup status; `docker compose logs backup`; test `restic snapshots` |
| A restore refuses | The target has tables; use a fresh database, or `--force` if replacement is intended |
| The UI is blank | Check the CSP console; the supported build has no inline scripts. Rebuild with `docker compose build api` |
| Traces are missing | `PHOENIX_COLLECTOR_ENDPOINT` is set and the `traces` profile is up; Phoenix binds loopback only |

Admin commands run on the host, never over HTTP:

```bash
docker compose exec api fin-agent migrate
docker compose exec api fin-agent passkeys list
docker compose exec api fin-agent passkeys reset
docker compose exec api fin-agent export --format json --out /tmp/ledger.json
docker compose exec api fin-agent restore --repository "$RESTIC_REPOSITORY"
```

`fin-agent passkeys reset` deletes every passkey and session and prints a fresh enrollment link.
Anyone with a shell on the host already has the data, so it opens no new door.
