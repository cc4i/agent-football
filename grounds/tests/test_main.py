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

"""The two pieces of the entry point that can be looked at on their own.

The browser and the socket are not tested here - a test that launches Chromium
and expects an arena is the end-to-end run in the plan, not a unit test. What is
here is what the socket hands the supervisor, and what the platform reads.
"""

import json
import types

import main


def test_a_message_that_is_not_json_is_nothing():
    """The control plane is ours on both ends, so this is a bug rather than an
    attack. Either way it must not take the socket down."""
    assert main._loads("") == {}
    assert main._loads("host AAAA please") == {}


def test_a_message_that_is_json_but_not_a_message_is_nothing():
    assert main._loads("[1, 2, 3]") == {}
    assert main._loads("null") == {}
    assert main._loads('"host"') == {}


def test_a_message_comes_through_whole():
    assert main._loads('{"type": "host", "code": "AAAA"}') == {
        "type": "host", "code": "AAAA"}


def _asked(state):
    request = types.SimpleNamespace(app=types.SimpleNamespace(
        state=types.SimpleNamespace(grounds=state)))
    answer = main.healthz(request)
    return answer.status_code, json.loads(answer.body)


def test_an_instance_with_no_page_is_not_ok():
    """Cloud Run replaces this one, and it replaces it on the status code alone.

    A liveness probe reads the code and never the body, so an instance whose
    browser has gone answering `200 {"ok": false}` would keep passing its health
    check forever while playing nothing. Saying no has to be the status.
    """
    assert _asked({"open": False, "running": 0, "capacity": 12}) == (
        503, {"ok": False, "running": 0, "capacity": 12})


def test_an_instance_that_is_playing_says_how_much():
    assert _asked({"open": True, "running": 3, "capacity": 12}) == (
        200, {"ok": True, "running": 3, "capacity": 12})
