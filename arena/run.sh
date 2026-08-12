#!/bin/bash
# run.sh - Runs the Arena FastAPI server.

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
PORT="${PORT:-8003}"

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv is not installed. See https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

# The salt fixes every email hash for good and the secret signs every phone's
# session, so both must survive a restart. Dev defaults let the arena start
# without them; an event that reuses them loses everyone's board history.
if [ -z "${ARENA_EMAIL_SALT:-}" ] || [ -z "${ARENA_SECRET:-}" ]; then
    echo "WARNING: ARENA_EMAIL_SALT and/or ARENA_SECRET are unset." >&2
    echo "         Running with dev defaults. Set both before a real event." >&2
fi

echo "--> Syncing python environment with uv..."
uv sync --all-groups

echo "--> Starting Arena on http://$HOST:$PORT ..."
exec uv run uvicorn app:app --host "$HOST" --port "$PORT" "$@"
