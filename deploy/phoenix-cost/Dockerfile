# syntax=docker/dockerfile:1
#
# Arize Phoenix 21.0.0 with Arno's supplied-cost patch.
#
# The published image ignores `llm.cost.*` and prices only from its own model
# catalog, so a supplied estimate, an explicit zero, and an unknown cost are
# indistinguishable and aggregates report unknown as $0. This build keeps the
# released Phoenix runtime and compiled assets' upstream behavior, and replaces
# only the modules named in `patches/` plus the rebuilt UI.
#
# Phoenix is licensed under the Elastic License 2.0 and is NOT part of the Arno
# image. Its source and notices stay in this separate image.
#
# Build (from the repository root):
#   docker build -t arno-phoenix-cost:21.0.0 deploy/phoenix-cost
#
# Pins:
#   PHOENIX_IMAGE  arizephoenix/phoenix:21.0 (the release this patch qualifies)
#   PHOENIX_COMMIT the upstream commit that release was built from
#   PHOENIX_TARBALL_SHA256  sha256 of the GitHub source tarball for that commit

ARG PHOENIX_IMAGE=arizephoenix/phoenix:21.0@sha256:1f9a144a5357b4973e93a15718f050247cb7a4ee7b7c6c06ecb91b5eed3fbaad
ARG NODE_IMAGE=node:24-slim

# Source, patch application, and the frontend build. The vite build writes into
# src/phoenix/server/static, exactly as upstream's Dockerfile does.
FROM ${NODE_IMAGE} AS source

ARG PHOENIX_COMMIT=6359d6388815d56f7795a3a8cd11e89a71793e01
ARG PHOENIX_TARBALL_SHA256=8c69a08de1fce99800926d982ba793e3d96095e6f280083e4e2c89fa03761375

RUN apt-get update \
  && apt-get install -y --no-install-recommends patch \
  && rm -rf /var/lib/apt/lists/* \
  && printf '\nprecedence ::ffff:0:0/96  100\n' >> /etc/gai.conf

WORKDIR /src
ADD --checksum=sha256:${PHOENIX_TARBALL_SHA256} https://codeload.github.com/Arize-ai/phoenix/tar.gz/${PHOENIX_COMMIT} /tmp/phoenix.tar.gz
# A digest check, not TLS alone: the patch is only valid against this tree.
RUN echo "${PHOENIX_TARBALL_SHA256}  /tmp/phoenix.tar.gz" | sha256sum -c - \
  && tar -xzf /tmp/phoenix.tar.gz -C /src --strip-components=1 \
  && rm /tmp/phoenix.tar.gz

ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
ENV PHOENIX_ENABLE_SOURCE_MAP=True
WORKDIR /src/js
RUN corepack enable \
  && corepack install
# Phoenix 21.0 pins pnpm@12.0.0, where fetchTimeout bounds the entire HTTP
# request (changed to a progress timeout in 12.4.0). Raise fetchTimeout, cap
# networkConcurrency, persist the pnpm store in a BuildKit cache mount, and
# install all workspace packages in one step so later `run build` and
# `run typecheck` calls do not auto-install remaining workspace projects
# without these settings.
RUN --mount=type=cache,id=phoenix-pnpm-store,target=/pnpm/store \
  pnpm_config_store_dir=/pnpm/store \
  pnpm_config_fetch_timeout=600000 \
  pnpm_config_fetch_retries=5 \
  pnpm_config_fetch_retry_mintimeout=10000 \
  pnpm_config_fetch_retry_maxtimeout=60000 \
  pnpm_config_network_concurrency=8 \
  pnpm install --frozen-lockfile

COPY patches/ /patches/
RUN for p in /patches/*.patch; do patch -p1 -d /src --no-backup-if-mismatch < "$p"; done

RUN pnpm --filter 'phoenix-ui...' run build
# A compiled build is not a type-checked one; fail the image on either.
RUN pnpm --filter 'phoenix-ui...' run typecheck

# The released Phoenix runtime, unchanged except for the patched Python modules
# and the static assets built above from the same upstream commit.
FROM ${PHOENIX_IMAGE}

COPY --from=source /src/LICENSE /phoenix/licenses/phoenix/LICENSE
COPY --from=source /src/IP_NOTICE /phoenix/licenses/phoenix/IP_NOTICE

COPY --from=source /src/src/phoenix/db/insertion/helpers.py /phoenix/.venv/lib/python3.13/site-packages/phoenix/db/insertion/helpers.py
COPY --from=source /src/src/phoenix/server/cost_tracking/supplied_cost.py /phoenix/.venv/lib/python3.13/site-packages/phoenix/server/cost_tracking/supplied_cost.py
COPY --from=source /src/src/phoenix/server/cost_tracking/span_cost_calculator.py /phoenix/.venv/lib/python3.13/site-packages/phoenix/server/cost_tracking/span_cost_calculator.py
COPY --from=source /src/src/phoenix/server/api/dataloaders/types.py /phoenix/.venv/lib/python3.13/site-packages/phoenix/server/api/dataloaders/types.py
COPY --from=source /src/src/phoenix/server/api/dataloaders/cost_status.py /phoenix/.venv/lib/python3.13/site-packages/phoenix/server/api/dataloaders/cost_status.py
COPY --from=source /src/src/phoenix/server/api/dataloaders/span_cost_summary_by_project.py /phoenix/.venv/lib/python3.13/site-packages/phoenix/server/api/dataloaders/span_cost_summary_by_project.py
COPY --from=source /src/src/phoenix/server/api/dataloaders/span_cost_summary_by_project_session.py /phoenix/.venv/lib/python3.13/site-packages/phoenix/server/api/dataloaders/span_cost_summary_by_project_session.py
COPY --from=source /src/src/phoenix/server/api/dataloaders/span_cost_summary_by_trace.py /phoenix/.venv/lib/python3.13/site-packages/phoenix/server/api/dataloaders/span_cost_summary_by_trace.py
COPY --from=source /src/src/phoenix/server/api/types/SpanCostSummary.py /phoenix/.venv/lib/python3.13/site-packages/phoenix/server/api/types/SpanCostSummary.py
COPY --from=source /src/src/phoenix/server/api/types/Span.py /phoenix/.venv/lib/python3.13/site-packages/phoenix/server/api/types/Span.py
COPY --from=source /src/src/phoenix/server/api/types/Trace.py /phoenix/.venv/lib/python3.13/site-packages/phoenix/server/api/types/Trace.py
COPY --from=source /src/src/phoenix/server/api/types/ProjectSession.py /phoenix/.venv/lib/python3.13/site-packages/phoenix/server/api/types/ProjectSession.py
COPY --from=source /src/src/phoenix/server/api/types/Project.py /phoenix/.venv/lib/python3.13/site-packages/phoenix/server/api/types/Project.py

COPY --from=source /src/src/phoenix/server/static/ /phoenix/.venv/lib/python3.13/site-packages/phoenix/server/static/
