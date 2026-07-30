#!/bin/bash
# run.sh - Runs the Frontend, Captain Server, and Coach Server in parallel.

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

# Resolve root directory
CWD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$CWD"

# Default to running the reference solution. Pass "task" as an argument to run student templates instead.
MODE="solution"
if [ "$1" == "task" ]; then
    MODE="task"
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv is not installed. See https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

# Create/update .venv from pyproject.toml + uv.lock
echo "--> Syncing python environment with uv..."
uv sync || exit 1

# Track child PIDs for cleanup
PIDS=()

cleanup() {
    echo -e "\nStopping all services..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
        fi
    done
    # Restore agent.py if it was swapped
    if [ "$MODE" == "task" ] && [ -f "agents/agent.py.bak" ]; then
        echo "--> Restoring original agent.py..."
        mv agents/agent.py.bak agents/agent.py
    fi
    exit 0
}

# Trap SIGINT (Ctrl+C) and SIGTERM to clean up all spawned processes
trap cleanup EXIT SIGHUP SIGINT SIGTERM



# Clean up any stale local ADK SQLite databases / session cache to prevent lock/corruption errors.
# `adk web .` writes the session db next to the agent app, i.e. agents/.adk.
for stale in .adk agents/.adk; do
    if [ -d "$stale" ]; then
        echo "--> Cleaning up stale $stale session storage..."
        rm -rf "$stale"
    fi
done

echo "Starting Futsal WorldCup services in MODE: $MODE..."

# 1. Start Captain Server
echo "--> Starting Team Captain A2A Server..."
uv run python -m agents.captain_server &
CAPTAIN_PID=$!
PIDS+=($CAPTAIN_PID)

# Wait a brief moment for A2A port registration
sleep 2

# 2. Start Coach Server (ADK Web)
# Running adk web on '.' (the game root folder) scans the subdirectory 'agents'
# and registers it as the agent application 'agents'.
echo "--> Starting Head Coach Server (adk web)..."
uv run adk web . --allow_origins='*' &
PIDS+=($!)

# Wait a brief moment for Coach server port registration
sleep 2

# 2.5 Run sample test query on Captain Server if it is active
if kill -0 "$CAPTAIN_PID" 2>/dev/null; then
    echo "--> Sending test query 'all the best! stick to current plan' to Team Captain A2A Server..."
    curl -s -X POST http://localhost:8001/ \
      -H "Content-Type: application/json" \
      -d '{
        "jsonrpc": "2.0",
        "method": "message/send",
        "id": 1,
        "params": {
          "message": {
            "message_id": "test-shout",
            "role": "user",
            "parts": [{"kind": "text", "text": "all the best! stick to current plan"}]
          }
        }
      }' | uv run python -m json.tool || echo "--> [WARNING] Test query to Captain Server failed."
fi

# 3. Start Frontend
echo "--> Starting Frontend server (Vite)..."
cd frontend
npm install &&
npm run dev &
PIDS+=($!)
cd ..

echo "=========================================================="
echo "All services running! Press Ctrl+C to stop all of them."
echo "=========================================================="

# Wait on all background processes
wait
