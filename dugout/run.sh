#!/bin/bash
# run.sh - Runs the Avatar Creator FastAPI server.

# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail

# Resolve root directory
CWD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$CWD"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8002}"

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv is not installed. See https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

# app.py builds the genai client at import time, so a missing .env fails the
# server on startup rather than at generation time. Catch it here instead.
if [ ! -f ".env" ]; then
    echo "ERROR: no .env found in $CWD"
    echo "       cp .env.example .env   # then set GOOGLE_CLOUD_PROJECT"
    exit 1
fi

# The game's ADK servers read their own .env. Without it they still start and
# the pitch still renders, so the only symptom is baseline backup and restore
# failing with "No API key was provided" deep in the browser console. Warn
# rather than exit: the dugout itself works fine, only stage 9 breaks.
if [ ! -f "../game/.env" ]; then
    echo "WARNING: no game/.env found." >&2
    echo "         cp game/.env.example game/.env" >&2
    echo "         Without it the game's coach and captain fail every call," >&2
    echo "         and the squad will not reset when you refresh the pitch." >&2
fi

# Create/update .venv from pyproject.toml + uv.lock
echo "--> Syncing python environment with uv..."
uv sync --all-groups

# Preflight: verify agy is on PATH. We deliberately do NOT shell out to
# `agy status`, which opens a TTY-based UI and fails outright in a
# non-interactive shell. Login state is verified at runtime by GET /health,
# which is where the UI surfaces it.
if ! command -v agy >/dev/null 2>&1; then
  echo "ERROR: the 'agy' CLI is not on PATH." >&2
  echo "  Install Antigravity, then run: agy login" >&2
  exit 1
fi

# Install chromium for the Playwright script that drives the game.
# Idempotent: skipped if the browser is already present.
echo "--> Installing Playwright chromium (skipped if already present)..."
uv run playwright install chromium

echo "--> Starting Avatar Creator on http://$HOST:$PORT ..."
exec uv run uvicorn app:app --host "$HOST" --port "$PORT" "$@"
