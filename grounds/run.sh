#!/bin/bash
# run.sh - Runs the Grounds: one Chromium playing everything the arena assigns.

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

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv is not installed. See https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

echo "--> Syncing python environment with uv..."
uv sync --all-groups

# The browser is the thing this service is, and pip does not bring one. The
# download is ~150MB the first time and nothing on every run after, so it is
# unconditional rather than guarded by a check that would be the second answer
# to a question Playwright already answers correctly.
echo "--> Making sure Chromium is installed..."
uv run playwright install chromium

# The token is not checked here. This process reads its own configuration from
# the environment and from .env, and it warns on the way up about what it is
# missing; a copy of that check in this script would be the one that was wrong.
# The arena is not checked either: it is allowed to be down. This waits.

echo "--> Starting the Grounds on http://0.0.0.0:${PORT:-8004}, playing for ${ARENA_URL:-http://localhost:8003} ..."
exec uv run python main.py "$@"
