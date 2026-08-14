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

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


from google.adk.a2a.converters.part_converter import convert_a2a_part_to_genai_part
from google.adk.a2a.converters.request_converter import convert_a2a_request_to_agent_run_request
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
from google.adk.a2a.executor.config import A2aAgentExecutorConfig
from google.adk.a2a.utils.agent_to_a2a import to_a2a
import uvicorn
from agents.captain import captain_agent
from agents.specialist_agents.arena_client import DUGOUT_KEYS

# The address the coach dials, which is not the interface this binds. `to_a2a`
# writes HOST into the agent card's rpc_url, and `RemoteA2aAgent` fetches the
# card and then sends every request to the url it found there - so HOST has to
# be an address something can connect to, and the wildcard is not one.
HOST = os.environ.get("CAPTAIN_HOST", "localhost")
PORT = int(os.environ.get("CAPTAIN_PORT", "8001"))
# The interface uvicorn binds. Wide in the image, because Cloud Run's startup
# probe does not dial from inside the instance's network namespace and a
# loopback bind is unreachable to it. Defaults to HOST so that running this by
# hand is unchanged: one address, and it is the one in the card.
BIND = os.environ.get("CAPTAIN_BIND", HOST)


def take_the_dugout(request, part_converter=convert_a2a_part_to_genai_part):
    """The usual conversion, plus the room the coach is shouting about.

    A2A gives this server a session per conversation and opens it empty, so
    without this the specialists downstream have no idea which match they are
    playing in and write to the fallback room. The coach sends the keys as
    request metadata; they go into the session as a state delta, which is where
    `update_profile` already looks for them.

    Only the keys the arena is known to send are copied across. A metadata bag
    from somewhere else cannot use this to set session state of its own.
    """
    run_request = convert_a2a_request_to_agent_run_request(request, part_converter)
    carried = request.metadata or {}
    run_request.state_delta = {key: carried[key] for key in DUGOUT_KEYS if carried.get(key)}
    return run_request


app = to_a2a(
    captain_agent, host=HOST, port=PORT,
    agent_executor_factory=lambda runner: A2aAgentExecutor(
        runner=runner,
        config=A2aAgentExecutorConfig(request_converter=take_the_dugout),
    ),
)

if __name__ == "__main__":
    print(f"Serving Team Captain over A2A at http://{HOST}:{PORT}")
    print(f"Agent card: http://{HOST}:{PORT}/.well-known/agent-card.json")
    uvicorn.run(app, host=BIND, port=PORT)
