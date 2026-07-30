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


from google.adk.a2a.utils.agent_to_a2a import to_a2a
import uvicorn
from agents.captain import captain_agent

HOST = os.environ.get("CAPTAIN_HOST", "localhost")
PORT = int(os.environ.get("CAPTAIN_PORT", "8001"))


app = to_a2a(captain_agent, host=HOST, port=PORT)

if __name__ == "__main__":
    print(f"Serving Team Captain over A2A at http://{HOST}:{PORT}")
    print(f"Agent card: http://{HOST}:{PORT}/.well-known/agent-card.json")
    uvicorn.run(app, host=HOST, port=PORT)
