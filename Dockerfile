# syntax=docker/dockerfile:1

# The web build is static: no Node server in the final image (system design, step 10).
FROM node:24-alpine AS web
WORKDIR /web
RUN corepack enable && corepack prepare pnpm@11.1.2 --activate
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm_config_fetch_timeout=600000 \
    pnpm_config_fetch_retries=5 \
    pnpm_config_fetch_retry_mintimeout=10000 \
    pnpm_config_fetch_retry_maxtimeout=60000 \
    pnpm_config_network_concurrency=8 \
    pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.14-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ARNO_REPO_ROOT=/app \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# restic and rclone are in the runtime image so `arno restore` can read local, S3/R2, or
# rclone (Google Drive) repositories with one command, without a second tool on the host.
# pg_dump needs a client matching the server's major version, which Debian does not ship, so the
# Postgres 18 client comes from the PGDG apt repository.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates postgresql-common restic \
    && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rclone/rclone:1.71.2 /usr/local/bin/rclone /usr/local/bin/rclone

RUN useradd --create-home --uid 10001 fin \
    && mkdir -p /app/pre-migration /app/tasks \
    && chown fin:fin /app /app/pre-migration /app/tasks
COPY --chown=fin:fin pyproject.toml uv.lock ./
COPY --chown=fin:fin src ./src
COPY --chown=fin:fin LICENSE README.md THIRD_PARTY_NOTICES.md ./
USER fin
RUN --mount=type=bind,from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --no-cache --frozen --no-dev --no-editable

COPY --chown=fin:fin --from=web /web/dist ./web/dist

EXPOSE 8000
CMD ["arno", "serve", "--host", "0.0.0.0", "--port", "8000"]
