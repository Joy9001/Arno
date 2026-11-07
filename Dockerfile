# syntax=docker/dockerfile:1

# The web build is static: no Node server in the final image (system design, step 10).
FROM node:24-alpine AS web
WORKDIR /web
RUN corepack enable && corepack prepare pnpm@11.1.2 --activate
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

# pg_dump 18 lives in the Postgres image. The API's export endpoint needs a client matching the
# server's major version, and Python's slim image does not ship one.
FROM postgres:18.6 AS pgclient

FROM python:3.14-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FIN_AGENT_REPO_ROOT=/app \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# restic and rclone are in the runtime image so `fin-agent restore` can read local, S3/R2, or
# rclone (Google Drive) repositories with one command, without a second tool on the host.
RUN apt-get update \
    && apt-get install -y --no-install-recommends restic \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rclone/rclone:1.71.2 /usr/local/bin/rclone /usr/local/bin/rclone

COPY --from=pgclient /usr/lib/postgresql/18/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=pgclient /usr/lib/postgresql/18/bin/pg_restore /usr/local/bin/pg_restore
COPY --from=pgclient /usr/lib/postgresql/18/bin/psql /usr/local/bin/psql
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libpq.so.5 /usr/local/lib/libpq.so.5
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libgssapi_krb5.so.2 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libldap.so.2 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libsasl2.so.2 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libkrb5.so.3 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libk5crypto.so.3 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libkrb5support.so.0 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/liblber.so.2 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libkeyutils.so.1 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libcom_err.so.2 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libzstd.so.1 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/liblzma.so.5 /usr/local/lib/
ENV LD_LIBRARY_PATH=/usr/local/lib

RUN useradd --create-home --uid 10001 fin \
    && mkdir -p /app/pre-migration /app/tasks \
    && chown fin:fin /app /app/pre-migration /app/tasks
COPY --chown=fin:fin pyproject.toml uv.lock ./
COPY --chown=fin:fin src ./src
COPY --chown=fin:fin evals ./evals
COPY --chown=fin:fin LICENSE README.md THIRD_PARTY_NOTICES.md ./
USER fin
RUN --mount=type=bind,from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --no-cache --frozen --no-dev --no-editable

COPY --chown=fin:fin --from=web /web/dist ./web/dist

EXPOSE 8000
CMD ["fin-agent", "serve", "--host", "0.0.0.0", "--port", "8000"]
