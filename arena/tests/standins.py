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

"""Doubles the suite hands the arena in place of another of our processes."""


class StandInGrounds:
    """A pitch that is connected as far as the registry is concerned.

    Kick-off now needs somewhere to play, and most tests in this suite kick a
    match off to get at what happens after. Registered straight into the
    registry rather than over `/ws/grounds`, because holding a real control
    socket open in every one of those tests would buy nothing: they never read
    the assignment, and a test that never reads is a queue that never drains.

    `test_kickoff_assignment.py` drives the real socket. This is for everyone
    who only needs a pitch to exist.
    """

    def __init__(self):
        self.assignments = []
        self.drops = []

    async def send_json(self, message):
        if message.get("type") == "drop":
            self.drops.append(message)
        else:
            self.assignments.append(message)

    def __repr__(self):
        return f"<stand-in grounds, {len(self.assignments)} assigned>"


def connect_grounds(fastapi_app, capacity=64):
    """Give this app a pitch to hand matches to. Returns the stand-in."""
    farm = StandInGrounds()
    fastapi_app.state.grounds.joined(farm, capacity)
    return farm
