# Self-hosting Arno

This guide owns installation, model setup, privacy and provider retention, backups, restore,
updates, security posture, and troubleshooting for a single-user Arno instance; see
[legacy upgrades](legacy-upgrades.md) for pre-0.3.0 migrations,
[observability](observability.md) for optional Phoenix tracing, and
[development operations](development-operations.md) for maintainer workflows.

`docker compose up` on an x86-64 Linux host, Docker Desktop, or a virtual private server (VPS) is
the supported deployment path. Native systemd installs, arm64 images, and public-internet hosting
remain out of scope ([`parked.md`](../planning/parked.md)).

---

## Prerequisites

The following table lists the host requirements for running Arno.

| Requirement | Reason |
|---|---|
| An x86-64 Linux host, Docker Desktop, or a VPS with Docker Compose | The published images target `amd64` |
| About 2 GB of free RAM, plus memory for optional profiles | PostgreSQL, the API, and any optional Phoenix or local models |
| Tailscale on the host and on every client device | Provides private HTTPS access from other devices; a host-only setup needs none |
| Host disk encryption enabled | Protects the database, Docker volumes, and plaintext dumps at rest |
| A password manager and off-machine storage for `RESTIC_PASSWORD` | Losing `RESTIC_PASSWORD` makes every encrypted backup unreadable |

### Enable host disk encryption

Arno does not encrypt the database internally. The database files, Docker volumes, and
pre-migration dumps rely on host disk encryption. Enable encryption before recording real
transactions. On Windows, run `manage-bde` from an elevated prompt.

- **Windows:** Enable BitLocker on the system and data volumes. Check state with
  `manage-bde -status` and enable it with `manage-bde -on C: -RecoveryPassword`.
- **Linux:** Enable LUKS on the root or data volume. Confirm with `lsblk -f` (look for
  `crypto_LUKS`) and `cryptsetup status <name>`. A cloud VPS disk-encryption option counts only
  when the provider encrypts the block device; the provider can still read a running VPS (see
  [Security and privacy posture](#security-and-privacy-posture)).
- **macOS:** Enable FileVault. Confirm that `fdesetup status` reports `FileVault is On.`

Stop and enable disk encryption first if it is off. No application setting replaces full-disk
encryption on a stolen device.

---

## Install

### Get the files

Clone the repository onto the host:

```bash
git clone https://github.com/Joy9001/Arno.git
cd Arno
```

Release images publish to `ghcr.io/joy9001/arno:<version>`. [`compose.yaml`](../../../compose.yaml) pins
`ARNO_VERSION` to the version in [`pyproject.toml`](../../../pyproject.toml). You can also build
locally with `docker compose up -d --build`.

On Windows, preserve the `*.sh text eol=lf` rule in `.gitattributes` and avoid CRLF checkouts. The
database init script is a shell script, and CRLF line endings break it.

### Create `.env`

Copy the template and set the required values privately:

```bash
cp .env.example .env
```

Set distinct long random values for at least the following variables:

```dotenv
POSTGRES_PASSWORD=<long random hex>
AGENT_RO_PASSWORD=<long random hex>
BACKUP_PASSWORD=<long random hex>
RESTIC_PASSWORD=<long random, stored off this machine>
RESTIC_REPOSITORY=/backups/restic
ARNO_RP_ID=<machine>.<tailnet>.ts.net
ARNO_RP_ORIGIN=https://<machine>.<tailnet>.ts.net
```

Generate the three database passwords with `openssl rand -hex 32`. Use hexadecimal rather than
base64 because `/` and `+` break the `DATABASE_URL` URI in `compose.yaml`. `POSTGRES_PASSWORD` sets
the password for `app` (the non-superuser database owner); the `postgres` bootstrap superuser has no
network password and accepts connections over the container's local socket only. Any strong
generator works for `RESTIC_PASSWORD` and the Phoenix secrets. Store `RESTIC_PASSWORD` off this
machine.

Without Tailscale, set `ARNO_RP_ID=localhost` and `ARNO_RP_ORIGIN=http://localhost:8000` to open
Arno on the host itself. Passkeys work on `localhost`, though phones cannot reach it.

Tracing is optional. If you enable the `obs` profile (see [Observability](#observability)),
generate three distinct long random values on the host and never paste them into chat:

```dotenv
PHOENIX_SECRET=<long random; Phoenix auth secret>
PHOENIX_ADMIN_SECRET=<long random; Phoenix admin secret>
PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD=<long random; initial Phoenix login>
```

`PHOENIX_ADMIN_SECRET` must be at least 32 characters long, contain a digit and a lowercase letter,
and differ from `PHOENIX_SECRET`. Compose also uses it as the default local bearer credential for
trace export so first start needs no Phoenix API key. If any Phoenix variable is empty, Phoenix
restart-loops while the API and PostgreSQL keep running; stop it with `docker compose stop phoenix`.

Find your tailnet hostname with `tailscale status --json` (read `Self.DNSName`) or in the Tailscale
admin console. It has the form `mini-pc.tail1234.ts.net`.

### Additional instance settings

The following table lists optional runtime variables from [`.env.example`](../../../.env.example)
that control security headers, sessions, jobs, backups, and UI links.

| Variable | Default | Purpose |
|---|---|---|
| `ARNO_MATCH_KEY` | Unset | Secret key for one-way hashes of third-party account numbers in statement narrations. Set a long random value once; changing it causes older statement lines to stop matching future imports. |
| `ARNO_TRUSTED_HOSTS` | `localhost,127.0.0.1,testserver,*.ts.net` | Comma-separated `Host` header allowlist enforced by `TrustedHostMiddleware`. |
| `ARNO_SESSION_TTL_DAYS` | `30` | Opaque session cookie lifetime in days. |
| `ARNO_SETUP_TOKEN_TTL_HOURS` | `24` | Enrollment link lifetime in hours. |
| `ARNO_COOKIE_SECURE` | `1` when `ARNO_RP_ORIGIN` starts with `https://`, else `0` | Controls the `Secure` attribute on `arno_session`. Leave unset in production. |
| `ARNO_JOBS_ENABLED` | `1` | Runs the background job loop in `api`. Set to `0` for read-only processes or tests. |
| `ARNO_VAPID_SUBJECT` | `mailto:arno@localhost` | Contact `mailto:` or `https:` URL sent with web push VAPID headers. |
| `ARNO_PHOENIX_UI_URL` | Unset | Browser URL for Phoenix (for example, `http://127.0.0.1:6006`). When set, **Settings** displays a link to open Phoenix; Arno never derives this URL from the collector endpoint. |
| `ARNO_ENV_FILE` | `.env` | Environment file read by `api` in Compose. Override only for isolated checks. |
| `PHOENIX_CSRF_TRUSTED_ORIGINS` | `http://127.0.0.1:6006` | Trusted CSRF origin list for the Phoenix UI. |

### Start the stack

Pull the published images, start the services, and inspect the API logs:

```bash
docker compose pull api db
docker compose up -d
docker compose logs -f api
```

First boot applies migrations and prints a one-time enrollment link and terminal QR code.
Configure `tailscale serve` first so the origin in `ARNO_RP_ORIGIN` is reachable, then open the
link (or paste its token) on a tailnet device and register your passkey. Restarts keep an active
enrollment link; run `docker compose exec api arno setup` to rotate it while setup is open.

Verify the API health endpoint:

```bash
curl -s http://127.0.0.1:${ARNO_API_PORT:-8000}/api/health
```

The endpoint returns database connectivity and schema version. Because the API publishes on
`127.0.0.1` only, it is unreachable from the LAN or public internet.

### Enable HTTPS over Tailscale

Proxy your tailnet HTTPS hostname to the loopback API port on the host:

```bash
tailscale serve --bg ${ARNO_API_PORT:-8000}
tailscale serve status
```

This routes `https://<machine>.<tailnet>.ts.net` to `http://127.0.0.1:8000`. Passkeys require HTTPS
and a stable hostname, which `*.ts.net` provides. Set `ARNO_RP_ID` and `ARNO_RP_ORIGIN` to that
hostname before registering a passkey, and run `docker compose up -d` if you changed `.env`.

On Android, open Arno in Chrome and install the PWA. The PWA share target lets you send UPI
screenshots directly from GPay or PhonePe into Arno.

### Configure ports on a shared host

If `5433`, `8000`, or `6006` is in use on the host, override `ARNO_DB_PORT`, `ARNO_API_PORT`, or
`PHOENIX_UI_PORT` in `.env` and point `tailscale serve --bg` at `${ARNO_API_PORT}`. Running a
second Arno stack on the same host also requires setting `COMPOSE_PROJECT_NAME` (or passing
`-p <name>` to every command), because `compose.yaml` sets `name: arno`. The optional `ollama`
profile binds a fixed loopback port (`11434`), so only one stack per host can run it.

---

## Configure models

Each model role accepts a provider, billing mode, base URL, API key, and exact model ID. Local
Ollama endpoints use the same code path as cloud APIs. `*_BILLING_MODE` accepts
`metered` (default), `local`, or `reported`; there is no subscription billing mode. Paid inference
refuses to run when a model price is unknown rather than treating it as free, so paid roles must
name a verified provider or `reported` when the endpoint supplies `usage.cost`. Pin exact, dated
model IDs instead of floating `latest` aliases. Evals have no command-line entry point; see
[development operations](development-operations.md#real-model-journeys-and-evals).

The following table lists every model role and its environment variables.

| Role | Variables | Used for |
|---|---|---|
| Chat | `CHAT_PROVIDER`, `CHAT_BILLING_MODE`, `CHAT_BASE_URL`, `CHAT_API_KEY`, `CHAT_MODEL`, `LLM_CONTEXT_WINDOW` | Main agent loop, scheduled questions, categorizer agent, and summary prose |
| Batch | `BATCH_PROVIDER`, `BATCH_API_KEY`, `BATCH_MODEL` | Optional cloud-only batch API (`anthropic`, `openai`, or `google`/`gemini`) for large categorization backfills at lower cost; leave unset for local-only setups |
| Parse | `PARSE_PROVIDER`, `PARSE_BILLING_MODE`, `PARSE_BASE_URL`, `PARSE_API_KEY`, `PARSE_MODEL` | Optional parser subagent model; falls back to Chat when unset |
| Vision | `VISION_PROVIDER`, `VISION_BILLING_MODE`, `VISION_BASE_URL`, `VISION_API_KEY`, `VISION_MODEL` | Receipt or UPI photos and statement pages with no text layer |
| Speech-to-text | `STT_PROVIDER`, `STT_BILLING_MODE`, `STT_BASE_URL`, `STT_API_KEY`, `STT_MODEL`, `STT_LANGUAGE`, `STT_PAYEE_CONTEXT`, `STT_TIMEOUT_SECONDS` | Voice-note transcription |
| Chat fallback | `CHAT_FALLBACK_PROVIDER`, `CHAT_FALLBACK_BILLING_MODE`, `CHAT_FALLBACK_BASE_URL`, `CHAT_FALLBACK_API_KEY`, `CHAT_FALLBACK_MODEL`, `CHAT_FALLBACK_CONTEXT_WINDOW` | Optional backup endpoint when the primary chat endpoint fails |

Every paid path shares a $20 UTC-day ceiling and a $5 interactive-run ceiling; each scheduled
question has a $1 default ceiling. These limits act as runaway brakes rather than an invoice cap;
see [cost tracking](../specs/cost-tracking.md).

### Cloud chat providers

Arno supports OpenAI-compatible Chat Completions (`openai`, `opencode`), OpenAI Responses
(`openai-responses`), OpenRouter (`openrouter`), and native Anthropic Messages (`anthropic`).

For an OpenAI-compatible endpoint (or OpenAI Responses with `CHAT_PROVIDER=openai-responses`):

```dotenv
CHAT_PROVIDER=openai
CHAT_BILLING_MODE=metered
CHAT_BASE_URL=https://api.<provider>.com/v1
CHAT_API_KEY=<key>
CHAT_MODEL=<exact, dated model id>
LLM_CONTEXT_WINDOW=<server context window>
```

For OpenCode Go (`CHAT_PROVIDER=opencode`), point `CHAT_BASE_URL` at the OpenCode endpoint. Chat
conversations send a stable per-conversation `x-opencode-session` header automatically; set
optional `OPENCODE_SESSION_ID` to pin the routing session for calls outside a chat thread.

For OpenRouter (`CHAT_PROVIDER=openrouter`), leave `CHAT_BASE_URL` unset or set it to
`https://openrouter.ai/api/v1` to use the native OpenRouter adapter.

For Anthropic, leave `CHAT_BASE_URL` unset to use the default Messages API endpoint. Both standard
Console API keys and eligible plan-funded Console credits use this route:

```dotenv
CHAT_PROVIDER=anthropic
CHAT_BILLING_MODE=metered
CHAT_API_KEY=<Console API key>
CHAT_MODEL=<exact Claude model id>
LLM_CONTEXT_WINDOW=200000
```

Do not use free-tier endpoints for real statements: free tiers often train on or retain prompts.

### Local chat with Ollama

Start the optional `local-llm` profile and pull a model:

```bash
docker compose --profile local-llm up -d
docker compose exec ollama ollama pull qwen3:4b
```

```dotenv
CHAT_PROVIDER=ollama
CHAT_BILLING_MODE=local
CHAT_BASE_URL=http://ollama:11434/v1
CHAT_API_KEY=ollama
CHAT_MODEL=qwen3:4b
LLM_CONTEXT_WINDOW=128000
```

`OLLAMA_CONTEXT_LENGTH` (derived from `LLM_CONTEXT_WINDOW` in `compose.yaml`) must match the
context window the server uses, or Ollama truncates long histories silently. Arno works with local
models, though it is designed for frontier models.

### Speech-to-text

Arno has no bundled speech-to-text server. Point `STT_BASE_URL` at any OpenAI-compatible
transcription endpoint: a cloud API or a server you run yourself.

```dotenv
STT_PROVIDER=openai
STT_BILLING_MODE=metered
STT_BASE_URL=https://api.<provider>.com/v1
STT_API_KEY=<key>
STT_MODEL=<exact model id>
```

Use `STT_BILLING_MODE=local` for a server on your own hardware, or `reported` when the endpoint
returns `usage.cost`.

- **`STT_LANGUAGE`:** Pins the spoken language (for example, `en`, which also handles Hinglish
  written in Latin script). Leave unset to auto-detect; short clips often misdetect when unset.
- **`STT_PAYEE_CONTEXT`:** Sends up to 80 active payee names as a recognition hint (`1` by default).
  Set to `0` when using a cloud transcription endpoint if you want to keep payee names local.
- **`STT_TIMEOUT_SECONDS`:** Request timeout in seconds (`120` by default).

### Vision

```dotenv
VISION_PROVIDER=openai
VISION_BILLING_MODE=metered
VISION_BASE_URL=https://api.<provider>.com/v1
VISION_API_KEY=<key>
VISION_MODEL=<exact, dated model id>
```

Vision reads receipts, UPI screenshots, and statement pages that lack a text layer. If `VISION_*`
is unset, photos and textless pages return an actionable retry card while the rest of Arno keeps
working.

### Fallback

```dotenv
CHAT_FALLBACK_PROVIDER=ollama
CHAT_FALLBACK_BILLING_MODE=local
CHAT_FALLBACK_BASE_URL=http://ollama:11434/v1
CHAT_FALLBACK_API_KEY=ollama
CHAT_FALLBACK_MODEL=qwen3:4b
CHAT_FALLBACK_CONTEXT_WINDOW=128000
```

`CHAT_FALLBACK_CONTEXT_WINDOW` must match the backup server's real window. If a thread exceeds that
window, Arno refuses the fallback request rather than silently truncating history. Replies written
by the fallback display a badge.

---

## What leaves the host

Checked October 31, 2026. Choosing a local model for any role keeps that role's traffic on the host.
The following table summarizes what leaves the host for each configured role or service.

| Role or service | Data sent | When |
|---|---|---|
| Chat | Financial snapshot (balances, account names, preferences), thread history, and tool returns | Every chat turn or scheduled question |
| Parser subagent | Sample statement rows, including payee names and narrations | Each statement import; only sample rows when a saved parser replays cleanly |
| Vision | Receipt or UPI photo, or statement pages with no text layer | Each photo or textless statement page |
| Batch (`BATCH_*` set) | Payee names, one sample narration per payee, and category names | Large categorization backfills (cloud-only; unset uses Chat) |
| Speech-to-text | Recorded audio and, when `STT_PAYEE_CONTEXT=1`, active payee names | Each voice note |
| Backups and push | Encrypted restic archive; contentless web push pings | Scheduled backup; on notification |
| Never sent | Statement passwords, API keys, session tokens, full account or card numbers from statements | |

Arno collects no analytics, and Phoenix traces (when enabled) stay on the host.

---

## What providers keep

Checked October 31, 2026. Provider policies change; verify current terms before sending real
financial data to any provider. The following table summarizes documented training and retention
defaults.

| Provider | Trains on API data | Retention |
|---|---|---|
| [Anthropic API](https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training) | No, by default | [Up to 30 days](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data); flagged content up to 2 years |
| [OpenAI API](https://developers.openai.com/api/docs/guides/your-data) | No, by default | Abuse-monitoring logs up to 30 days, longer if legally required |
| [Gemini paid](https://ai.google.dev/gemini-api/terms) | No | Limited abuse-monitoring logs |
| [Gemini unpaid](https://ai.google.dev/gemini-api/terms) | **Yes, with human review.** Google's terms state not to send personal information | Do not use for any role |
| Local models (Ollama or a server you run) | Nothing leaves the host | |

**Warning:** Never use Gemini's unpaid tier for any role. It trains on submitted prompts with human
review.

---

## Backups, restore, and updates

### Backups

The `api` service runs the backup. A daily scheduled job runs when the last success is missing or at
least 22 hours old, so a sleeping laptop catches up automatically. It streams
`pg_dump --no-owner --format=custom` into `restic backup --stdin` (tagged `arno`, stored as
`/fin_agent.dump`), then prunes with `restic forget --keep-daily 7 --keep-weekly 4 --prune`.
Each run writes a `backup_runs` row.

The dump filename (`fin_agent.dump`) predates the Arno rename and stays unchanged so older archives
restore cleanly.

```dotenv
BACKUP_PASSWORD=<long random hex; the `backup` database role>
RESTIC_PASSWORD=<long random, stored off this machine>
RESTIC_REPOSITORY=/backups/restic
```

**Important:** Store `RESTIC_PASSWORD` off this machine (for example, in a password manager or
printed in a safe). Losing it makes every off-site backup permanently unreadable.

The default `/backups/restic` target writes to the local `backup-data` Docker volume, which `api`
mounts and which is not off-site. Configure Google Drive through rclone or S3-compatible storage (such as Cloudflare R2,
AWS S3, or Backblaze B2) using environment variables alone. Check attempts in
`docker compose logs api` or **Settings > Backup status**. If no backup succeeds for 48 hours,
Arno creates an inbox alert and sends a push notification.

#### Off-site storage on Google Drive

Google Drive is the recommended off-site target:

```dotenv
RESTIC_REPOSITORY=rclone:gdrive:arno-backups
RCLONE_CONFIG_GDRIVE_TYPE=drive
RCLONE_CONFIG_GDRIVE_SCOPE=drive.file
RCLONE_CONFIG_GDRIVE_CLIENT_ID=<client id>
RCLONE_CONFIG_GDRIVE_CLIENT_SECRET=<client secret>
RCLONE_CONFIG_GDRIVE_TOKEN='{"access_token":"...","token_type":"Bearer","refresh_token":"...","expiry":"..."}'
```

Older installs may still point at `rclone:gdrive:fin-agent-backups`; keep the existing folder name
or migrate snapshots with `restic copy`.

Create your own Google OAuth **Desktop app** client rather than relying on rclone's shared client
ID, which is heavily rate-limited:

1. In the Google Cloud console, create a project and enable the **Google Drive API**.
2. Set the OAuth consent screen publishing status to **In production** (apps in **Testing** expire
   refresh tokens after 7 days; the `drive.file` scope requires no verification).
3. Create a **Desktop app** OAuth client and record its client ID and secret in your recovery kit.
4. On a machine with a browser, install rclone (`scoop install rclone`,
   `winget install Rclone.Rclone`, or [rclone.org/install](https://rclone.org/install/)) and run:

   ```bash
   BLOB=$(printf '{"client_id":"%s","client_secret":"%s","scope":"drive.file"}' "$CLIENT_ID" "$CLIENT_SECRET" | base64 -w0)
   rclone authorize "drive" "$BLOB"
   ```

5. Sign in and copy the resulting JSON token into `RCLONE_CONFIG_GDRIVE_TOKEN` inside single
   quotes. (Host rclone 1.75 prints a base64 blob between arrows; decode it and extract the `token`
   object.)

Because `drive.file` restricts access to files created by that OAuth client, keep the client ID and
secret in your off-machine recovery kit. If the OAuth client is lost, download the backup folder
from Google Drive manually and restore locally with `RESTIC_PASSWORD`.

#### Off-site storage on Cloudflare R2 or S3

Create an API token scoped to **Object Read & Write** on one bucket:

```dotenv
RESTIC_REPOSITORY=s3:https://<account-id>.r2.cloudflarestorage.com/<bucket>
AWS_ACCESS_KEY_ID=<R2 API token access key id>
AWS_SECRET_ACCESS_KEY=<R2 API token secret>
AWS_DEFAULT_REGION=auto
```

Other rclone backends work through `RCLONE_CONFIG_<NAME>_*` variables. SFTP is not supported
because the `api` image includes no SSH client.

#### Switch or test repositories

The backup job initializes a new repository automatically with `restic init`. After changing
`RESTIC_REPOSITORY` or credentials in `.env`, recreate `api`. The due check reads `backup_runs` in
Postgres rather than inspecting the new repository, so the next backup runs within a day. To check
the repository now, list its snapshots:

```bash
docker compose up -d api
docker compose exec api restic snapshots
```

Use `restic copy --from-repo <old repository>` if you want to copy older snapshots across.

### Restore drill on a fresh host

This procedure recovers the ledger given only the restic repository and `RESTIC_PASSWORD`:

```bash
# 1. On a fresh host with the repo and .env configured, start only PostgreSQL.
docker compose up -d db

# 2. Restore and verify in one command. Pass --user root only when reading the
#    root-owned default local backup volume; off-site repositories do not need it.
docker compose run --rm --no-deps --user root api arno restore

# 3. Start the rest of the stack.
docker compose up -d
```

`arno restore` creates `arno` if missing, refuses to overwrite a database that already has tables,
checks that the snapshot holds `/fin_agent.dump` (or `/tmp/fin_agent.dump` in snapshots from the old sidecar), and pipes `restic dump` into
`pg_restore --no-owner`. It then restores grants and checks that every transfer balances and every
account balance matches its entries. It exits nonzero on any mismatch. Snapshots from older
versions restore too.

The restore runs as `app` (using `CREATEDB`, `CREATEROLE`, and `pg_signal_backend`, never
`SUPERUSER`), recreates the `agent_ro` and `backup` login roles, restores table grants from the
dump, and reapplies backup grants. Older dumps created without table grants still restore the app,
though `agent_ro` remains unprivileged for `run_query` until a backup from the current image
replaces it.

The command takes `--target` (a database URL; defaults to `DATABASE_URL`), `--repository`
(defaults to `RESTIC_REPOSITORY`), `--snapshot` (an ID; defaults to `latest`), and `--force`. Pass
`--force` only when intentionally replacing a populated database (it drops and recreates the target
database):

```bash
arno restore --repository "$RESTIC_REPOSITORY" --snapshot <id>
```

### Pre-migration dump

Before applying pending migrations to a nonempty database, `api` writes a plaintext `pg_dump` to
`/app/pre-migration` (`pre-migration-data` volume). Copy it out with:

```bash
docker compose cp api:/app/pre-migration/. ./pre-migration/
```

Treat pre-migration dumps as sensitive plaintext (they include stored credentials
and are not encrypted like restic archives). They are a rollback aid, not a backup replacement.

### Update Arno

Verify a recent off-site backup exists, update `ARNO_VERSION` in `.env` (or pull the updated git
checkout), and run:

```bash
docker compose pull api db
docker compose up -d
```

The API writes a pre-migration dump before applying pending migrations transactionally. If an update
fails, restore the pre-migration dump or latest restic snapshot.

- **Pre-0.3.0 installs (Fin-Agent rename or superuser `app` role):** Follow
  [legacy upgrades](legacy-upgrades.md) before applying routine updates.

### PostgreSQL major upgrade

Never start a new PostgreSQL major version against an existing `db-data` volume. Dump and restore
into a fresh volume instead. `arno restore` reads from restic, so first load the manual dump into
the repository under the name it expects:

```bash
# 1. Dump the ledger from the running old server and store it in restic.
docker compose exec -T db pg_dump -U postgres --no-owner --format=custom arno \
  | docker compose exec -T api restic backup --stdin --stdin-filename fin_agent.dump \
      --tag arno --host arno
docker compose down

# 2. Rename or back up the old db-data volume so Compose creates a fresh volume,
#    then update the `db` image tag in compose.yaml to the new major version.
# 3. Start only the empty new database.
docker compose up -d db

# 4. Restore and verify through the standard restore command.
docker compose run --rm --no-deps --user root api arno restore

# 5. Start the stack and verify /api/health.
docker compose up -d
```

Keep the old volume until the upgraded instance answers real questions cleanly. If you enabled
Phoenix, its traces live in the separate `phoenix-data` volume, which a ledger restore does not
touch.

---

## Security and privacy posture

- **Network:** The API binds to `127.0.0.1` only; `tailscale serve` supplies the sole HTTPS path.
  No port forwarding, public DNS, or CORS is required.
- **Authentication:** Single-user passkey login with opaque session tokens stored as SHA-256 hashes
  in `HttpOnly; Secure; SameSite=Strict` cookies. Data exports, full deletion, and passkey changes
  require a fresh passkey check within five minutes.
- **HTTP headers:** Strict Content Security Policy (`default-src 'self'; script-src 'self';
  style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; object-src 'none'; base-uri 'self';
  form-action 'self'; frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, and `TrustedHostMiddleware`.
- **Uploads:** 25 MB maximum size on statements, photos, and audio.
- **Database roles:** `postgres` is a local-socket-only bootstrap superuser with `PASSWORD NULL`;
  `app` owns Arno databases without `SUPERUSER` (`LOGIN CREATEDB CREATEROLE`, `pg_signal_backend`,
  and `ADMIN OPTION` with `INHERIT FALSE, SET FALSE` on `agent_ro` and `backup`); `agent_ro` is
  `SELECT`-only on ledger-side tables and views with no auth or secrets tables, no `TEMP`, and a
  two-second statement timeout; `backup` has `SELECT` for `pg_dump` and `INSERT` on `backup_runs`
  only; `phoenix` owns only the `phoenix` database.
- **Logs and traces:** Standard Python/Uvicorn stdout stays on the host (10 MB × 3 files per
  service). Phoenix traces (when enabled) include prompts, replies, and tool returns and stay loopback-only.
- **Not defended:** A cloud VPS provider inspecting a running host or a live host compromise.
  In-database encryption remains parked with VPS hosting as its trigger
  ([`parked.md`](../planning/parked.md#encryption-inside-the-database)).

---

## Observability

Tracing is off by default: `PHOENIX_COLLECTOR_ENDPOINT` is empty, so Arno registers and
instruments nothing. To turn it on, set the Phoenix secrets in `.env`, set
`PHOENIX_COLLECTOR_ENDPOINT=http://phoenix:6006/v1/traces`, and start the `obs` profile:

```bash
docker compose --profile obs up -d
```

The profile runs stock Phoenix (Elastic License 2.0, outside the Arno image) on loopback at
`http://127.0.0.1:6006` with authentication enabled and traces stored in SQLite on the
`phoenix-data` volume. Compose sets `PHOENIX_PROJECT_NAME=arno` and falls back `PHOENIX_API_KEY` to
`PHOENIX_ADMIN_SECRET` so trace export works on first start. Set `ARNO_PHOENIX_UI_URL` to surface
the Phoenix link in **Settings**. Export is fail-open: Arno serves normally when Phoenix is down,
and `OTEL_SDK_DISABLED=true` disables tracing completely.

Phoenix prices calls from its own catalog, so its cost figures can differ from Arno's. The
PostgreSQL spend ledger (`llm_calls`) is the source of truth for cost, and ledger backups include it
but exclude Phoenix's data. See [observability](observability.md) and
[cost tracking](../specs/cost-tracking.md).

---

## Troubleshooting

The following table lists common failure symptoms and first diagnostic checks.

| Symptom | Check |
|---|---|
| `docker compose up` exits on `POSTGRES_PASSWORD` | `.env` is missing a required secret. |
| `/api/health` reports the database unreachable | Run `docker compose ps` and `docker compose logs db`; `api` waits for `pg_isready`. |
| Enrollment link fails | Open it on the exact `ARNO_RP_ORIGIN`; a passkey created on `localhost` fails on `*.ts.net`. |
| Passkey login fails after a hostname change | Update `ARNO_RP_ID` and `ARNO_RP_ORIGIN`, recreate `api`, run `docker compose exec api arno passkeys reset`, and enroll again. |
| Backups stopped | Check **Settings > Backup status**, `docker compose logs api`, and `docker compose exec api restic snapshots`. |
| `arno restore` refuses | The target database has tables. Use a fresh database, or pass `--force` to replace it. |
| Web UI is blank | Check browser console CSP errors; the production build has no inline scripts. Rebuild with `docker compose build api`. |
| Traces missing | Tracing is off unless `PHOENIX_COLLECTOR_ENDPOINT` is set and the `obs` profile runs. Check `docker compose ps`, `docker compose logs api`, `OTEL_SDK_DISABLED`, and `PHOENIX_API_KEY` / `PHOENIX_ADMIN_SECRET`. |
| Paid inference refuses with unknown price | Set `*_PROVIDER` to a verified provider or `*_BILLING_MODE=reported` when the endpoint returns `usage.cost`; use `local` only for local servers. |

### Host CLI reference

Administrative commands run on the host or inside the `api` container (`docker compose exec api
arno <command>`), never over HTTP. The following table lists every `arno` subcommand.

| Command | Purpose |
|---|---|
| `arno migrate` | Write a pre-migration dump (when nonempty), apply pending SQL migrations, and sync `agent_ro` and `backup` passwords |
| `arno serve` | Start the FastAPI server and static web build |
| `arno setup` / `arno setup-token` | Rotate and print a fresh first-run enrollment link and QR code while setup is open |
| `arno passkeys list` | List registered passkeys and last-used timestamps |
| `arno passkeys reset` | Delete all passkeys and sessions, then print a fresh enrollment link |
| `arno export` | Export the ledger as `json`, `csv` (zip), or `sql` (`pg_dump`) |
| `arno restore` | Restore and verify a restic snapshot (`--target`, `--repository`, `--snapshot`, `--force`) |
| `arno demo seed` | Seed the synthetic demo persona into a scratch database (`demo template` clones an eval template) |
