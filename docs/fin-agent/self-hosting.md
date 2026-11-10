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
| About 2 GB of RAM free, plus Phoenix (4 GB with `--profile local-llm`) | Postgres, the API, Phoenix, and the optional services |
| Tailscale on the host, and the Tailscale app on every device you will use | The only network path to the app from another device. A host-only setup needs none (§2.2) |
| Host disk encryption **on** | It is what protects the database at rest |
| A password manager, and somewhere off this machine for the restic password | Losing the restic password makes every backup unreadable |

### Host disk encryption is an install requirement

Fin-Agent does not encrypt the database from inside. The database files, the Docker volumes, and
the pre-migration dumps rely on the host's disk encryption. Turn it on before the first real
transaction. Run these from an **elevated** prompt on Windows (`manage-bde` denies a normal one):

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

On Windows, keep the `*.sh text eol=lf` rule in `.gitattributes` and do not check out with CRLF.
The backup image's entrypoint is a shell script, and a CRLF checkout makes it crash-loop
(`exec …/fin-agent-backup: no such file or directory`), which leaves the instance with no backups.

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

Generate the three database passwords with `openssl rand -hex 32` (hex, not base64: these land
inside the `DATABASE_URL` in `compose.yaml`, where `/` and `+` break the URI). Any generator is
fine for `RESTIC_PASSWORD` and the Phoenix values.
`RESTIC_PASSWORD` is the one secret that must live somewhere other than this machine.

Without Tailscale, use `FIN_AGENT_RP_ID=localhost` and
`FIN_AGENT_RP_ORIGIN=http://localhost:8000` and open the app on the host itself. Passkeys work on
`localhost`, but a phone cannot reach it.

The normal start also runs Phoenix. Set these on the host, with long random values; never paste
them into chat:

```dotenv
PHOENIX_PASSWORD=<long random; the Phoenix database role>
PHOENIX_SECRET=<long random; Phoenix auth>
PHOENIX_ADMIN_SECRET=<long random; Phoenix admin>
PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD=<long random; Phoenix admin login>
```

`PHOENIX_ADMIN_SECRET` must be at least 32 characters, contain a digit and a lowercase letter, and
differ from `PHOENIX_SECRET`. It also serves as the default local bearer credential for trace
export, so the normal start needs no Phoenix API key. A missing Phoenix value keeps Phoenix in a
restart loop, because its entrypoint exits and Compose restarts it; stop it with
`docker compose stop phoenix`, and the app and its database are unaffected. For existing volumes
and password changes, follow the
[Phoenix database setup instructions](observability.md#phoenix-database-setup-and-password-changes).

Find the tailnet name with `tailscale status --json` and read `Self.DNSName`, or open the
Tailscale admin console. It looks like `mini-pc.tail1234.ts.net`.

### 2.3 Start

```bash
docker compose up -d --build
docker compose logs -f api
```

The first start applies migrations, then prints a first-run enrollment link and a terminal QR
code. Finish §2.4 first: the enrollment link is served over the origin you set in §2.2, so
`tailscale serve` must already be running. Then open it on a phone on the tailnet, or paste the
token printed under the link, and create the passkey. A restart keeps a valid link;
`docker compose exec api fin-agent setup` rotates it while setup is still open, and afterwards it
says setup is complete.

Check the API:

```bash
curl -s http://127.0.0.1:${FIN_AGENT_API_PORT:-8000}/api/health
```

It reports the database and schema version. The API publishes only on `127.0.0.1`, so nothing is
reachable from the LAN or the public internet.

### 2.4 HTTPS over Tailscale

On the host:

```bash
tailscale serve --bg ${FIN_AGENT_API_PORT:-8000}
tailscale serve status
```

That proxies `https://<machine>.<tailnet>.ts.net` to `http://127.0.0.1:8000`. Passkeys require
HTTPS and a stable hostname; the `*.ts.net` name gives both. Set `FIN_AGENT_RP_ID` and
`FIN_AGENT_RP_ORIGIN` to that name **before** registering a passkey, then
`docker compose up -d` if you changed them.

On Android, open the site in Chrome and install the PWA. The share target lets a UPI screenshot go
straight from GPay or PhonePe into Fin-Agent.

### 2.5 Ports on a shared host

If `5433`, `8000`, or `6006` is already taken, set `FIN_AGENT_DB_PORT`, `FIN_AGENT_API_PORT`, or
`PHOENIX_UI_PORT` in `.env`. `tailscale serve --bg ${FIN_AGENT_API_PORT}` then points at the right
port. Running a second stack on one host also needs `COMPOSE_PROJECT_NAME` (or `-p <name>` on every
command): `compose.yaml` pins `name: fin-agent`, so a second stack collides on container names even
with different ports. The optional Ollama and speech-to-text ports are fixed at `11434` and
`8001`, so only one stack on a host can start each profile.

---

## 3. Configure models

Each role takes an OpenAI-compatible base URL, API key, and model ID. The same code path runs a
local Ollama/speaches endpoint and a cloud API. Model IDs must be exact, dated versions, never
floating `latest` aliases; re-run `fin-agent eval --model X` after changing one.

| Role | Variables | Used for |
|---|---|---|
| Chat | `CHAT_BASE_URL`, `CHAT_API_KEY`, `CHAT_MODEL` | The agent loop, scheduled questions, the categorizer agent, and briefing prose |
| Batch | `BATCH_PROVIDER`, `BATCH_API_KEY`, `BATCH_MODEL` | Optional, cloud-only. A model with a batch API—Anthropic, OpenAI, or Gemini—for categorizing a big backfill at about half the price. Leave it unset for a fully local setup |
| Parse | `PARSE_BASE_URL`, `PARSE_API_KEY`, `PARSE_MODEL` | Optional. The parser subagent; unset, it uses Chat |
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

speaches installs `STT_MODEL` (`PRELOAD_MODELS`) into the `speaches-data` volume on
first start (speaches returns 404 until a model is installed). A cloud
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
| Parser subagent | Sample statement rows, including payee names and notes | Each import; a few rows when a saved parser replays cleanly |
| Vision | The photo, or statement pages with no text | Each photo or such page |
| Batch (only when `BATCH` is set) | Payee names, a sample narration for each, and your category names | Big categorizing backfills. Batch APIs are cloud-only; a local setup leaves `BATCH` unset and categorizes with the chat model |
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

On Google Drive, the recovery kit also includes the OAuth client ID and secret. Under `drive.file`,
only the client that created the backups can see them. You can re-download the client from the
Google Cloud console while you can still sign in to Google. If the client is gone, download the
`fin-agent-backups` folder from the Drive website and point restic at the local copy. That still
needs `RESTIC_PASSWORD`.

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
RCLONE_CONFIG_GDRIVE_CLIENT_ID=<client id>
RCLONE_CONFIG_GDRIVE_CLIENT_SECRET=<client secret>
RCLONE_CONFIG_GDRIVE_TOKEN='{"access_token":"...","token_type":"Bearer","refresh_token":"...","expiry":"..."}'
```

Use your own Google OAuth client. rclone's shared client ID is rate-limited (a first backup took
five minutes on `rateLimitExceeded`) and is being retired. In the Google Cloud console, create a
project, enable the **Google Drive API**, publish the OAuth consent screen to **In production** (a
"Testing" app's refresh tokens expire after 7 days; `drive.file` needs no verification), and create
a **Desktop app** client. Then install rclone on a machine with a browser (`scoop install rclone`,
`winget install Rclone.Rclone`, or [rclone.org/install](https://rclone.org/install/)) and run:

```bash
BLOB=$(printf '{"client_id":"%s","client_secret":"%s","scope":"drive.file"}' "$CLIENT_ID" "$CLIENT_SECRET" | base64 -w0)
rclone authorize "drive" "$BLOB"
```

Sign in, then copy the token JSON into `RCLONE_CONFIG_GDRIVE_TOKEN`, inside single quotes. rclone
1.75 prints a base64 blob between the arrows. Decode it, and the `token` field is the value. The
`drive.file` scope lets rclone see only the files this client created, not the rest of your Drive,
so changing clients starts an empty repository; seed it again as described below. The client
secret and refresh token are credentials: keep them out of git.

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
docker compose run --rm --no-deps --entrypoint restic backup snapshots
```

To carry old snapshots across, `restic copy --from-repo <old repository>` exists.

Test the repository from the host:

```bash
docker compose run --rm --no-deps --entrypoint restic backup snapshots
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
check and exits non-zero on any mismatch. It also recreates the `agent_ro` and `backup` login roles,
restores the dump's own table grants, and re-grants the backup role from the image, so `run_query`
and the backup sidecar work on the restored database. Restore from a backup the current image made.
An older dump also restores and the app works, but a dump written without its table grants carries
none for `agent_ro`, so `run_query` stays unprivileged until a new backup replaces it.

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

**Upgrading from an older ledger.** If it imported statements before this release, run the opening
repair once, after the new version has migrated:

```bash
docker compose exec api fin-agent repair-openings --dry-run   # prints what would change; writes nothing
docker compose exec api fin-agent repair-openings --yes       # applies it
```

It books each import's printed opening at the statement's first day and supersedes the older
balance adjustments the statement now explains, so every date the statement covered reads its real
balance. It is idempotent, and undoing an import reverses its repair too. A restore needs the same
care: restore from a backup the current image made, because a dump written without its table grants
leaves `run_query` unprivileged (§6.2).

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
  only. Phoenix owns only its own database and `agent_ro` cannot connect to it.
- **Logs:** standard Python/Uvicorn stdout, kept on the host. They are not shipped to a central
  log store. Traces include prompts, replies, and tool data because Pydantic AI's default
  instrumentation is used, which is why Phoenix stays local. There is no custom metadata-only mode.
- **Not defended:** a VPS provider reading the running disk, and a live host compromise. Both are
  stated rather than papered over; in-database encryption is parked with the first as its trigger.

---

## 8. Observability

The normal start runs [Phoenix](system-design.md#traces-phoenix) beside the app. Native Pydantic AI
agent, model, and tool spans export directly over OTLP/HTTP; there is no Collector, Loki,
Prometheus, Grafana, custom metric, or log shipper. Standard Python/Uvicorn logs stay on Docker
stdout, capped at three 10 MB files per service. Phoenix publishes only on loopback at
<http://127.0.0.1:6006> and requires authentication; its own database and role stay separate from
the ledger.

Compose sets `PHOENIX_COLLECTOR_ENDPOINT=http://phoenix:6006/v1/traces` and
`PHOENIX_PROJECT_NAME=fin-agent`. It also maps `PHOENIX_API_KEY` to
`PHOENIX_ADMIN_SECRET` by default, so the normal start exports with no key creation and no second
restart. Set `PHOENIX_API_KEY` only to use a real Phoenix API key; it wins over the fallback. A
host CLI or eval that should export needs `PHOENIX_COLLECTOR_ENDPOINT=http://127.0.0.1:6006/v1/traces`
and a bearer credential in `PHOENIX_API_KEY`; a container command already has the Compose values.
Tracing is opt-in: with `PHOENIX_COLLECTOR_ENDPOINT` unset, nothing is registered or instrumented,
so a host CLI or eval run without it exports nothing.

The app starts and answers when Phoenix is down. Export is fail-open. The standard
`OTEL_SDK_DISABLED=true` kill switch disables export and agent instrumentation; there is no other
tracing switch. Keep the volumes and never use `docker compose down -v` or `docker system prune`
during rollback.

Phoenix keeps its built-in retention default of **0 days (infinite)** unless the operator sets a
policy in Phoenix or through `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS`. The app database is backed up;
Phoenix data is not, and a ledger restore leaves it untouched. Pydantic AI's default instrumentation
records prompts, replies, and tool data, so Phoenix stays local and loopback-only.

The operator journey, settings, recovery, and owner commands are in
[`observability.md`](observability.md).

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
| Telemetry is missing or degraded | `docker compose ps`; `docker compose logs api` for an exporter error; confirm `PHOENIX_COLLECTOR_ENDPOINT` and `PHOENIX_PROJECT_NAME`; a 401 fails export only; the default bearer credential is `PHOENIX_ADMIN_SECRET`, and `PHOENIX_API_KEY` overrides it; Phoenix is loopback-only |

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
