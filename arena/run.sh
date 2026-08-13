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

# Nothing is checked here. The arena reads its own configuration from the
# environment and from .env, so it is the only thing that knows whether the
# salt, the secret and the service token were set, and it warns about each of
# them on the way up. A copy of that check in this script would be a second
# answer to the same question, and it would be the one that was wrong.

echo "--> Syncing python environment with uv..."
uv sync --all-groups

echo "--> Starting Arena on http://$HOST:$PORT ..."
exec uv run uvicorn app:app --host "$HOST" --port "$PORT" "$@"
