# The arena, with the pitch built into it. Two stages so that Node is a build
# dependency rather than something running on the internet.
#
# Built from the repository root, because the pitch lives in game/ and the
# arena serves it:
#   docker build -f arena/Dockerfile -t arena .

FROM node:22-slim AS pitch
WORKDIR /build
COPY game/frontend/package*.json ./
RUN npm ci
COPY game/frontend/ ./
# The bundle is served from a subdirectory of the arena, so every hashed asset
# URL has to be written with that prefix at build time.
ENV PITCH_BASE=/pitch/
RUN npm run build

FROM python:3.14-slim
WORKDIR /app

# Pinned, because a lockfile is only as reproducible as the thing reading it:
# `uv sync --frozen` fixes every dependency and a floating uv would leave the
# tool doing the pinning as the one part of the image free to drift.
COPY --from=ghcr.io/astral-sh/uv:0.11.23 /uv /usr/local/bin/uv

COPY arena/pyproject.toml arena/uv.lock ./
RUN uv sync --frozen --no-dev

COPY arena/ ./
COPY --from=pitch /build/dist /srv/pitch

# ARENA_ENV=production is deliberate, and so is what it does: the arena refuses
# to start without ARENA_SECRET, ARENA_EMAIL_SALT and ARENA_SERVICE_TOKEN. A
# container that will not boot without its secrets is this file working.
ENV ARENA_PITCH_DIR=/srv/pitch \
    ARENA_ENV=production \
    PATH="/app/.venv/bin:$PATH"

# Cloud Run names the port. 8080 is the default for running the image by hand.
ENV PORT=8080
EXPOSE 8080

# One worker, on purpose. The bus, host liveness and the chain's semaphore are
# all in process, and a second worker is a second arena that shares none of
# them. See the spec: this is the same constraint as max-instances: 1.
#
# The wide bind is the one that stays. Cloud Run's container contract requires
# the ingress container to listen on 0.0.0.0:$PORT, so this is not the same
# widening as the two sidecars had and tidying it to match them is a revision
# that never serves a request.
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT} --workers 1
