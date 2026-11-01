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

FROM python:3.14-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY README.md THIRD_PARTY_NOTICES.md ./
RUN uv sync --frozen --no-dev --no-editable

COPY --from=web /web/dist ./web/dist

RUN useradd --create-home --uid 10001 fin && chown -R fin:fin /app
USER fin

EXPOSE 8000
CMD ["fin-agent", "serve", "--host", "0.0.0.0", "--port", "8000"]
