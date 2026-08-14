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

"""The last thing in a match that bypassed the arena.

A knock was written into a JSON file beside the pitch and polled every two
seconds by whichever browser happened to be hosting. That worked exactly as
long as a browser was hosting, and it stopped being true when physics moved to
the farm -- so rather than teach the farm to write files nobody would read, it
goes where everything else that happens in a match already goes.
"""

import codes
import rooms


def _log(client, code, kind=None):
    connection = client.app.state.conn
    entries = rooms.events(connection, rooms.by_code(connection, code)["id"])
    return [entry for entry in entries if kind is None or entry["kind"] == kind]


KNOCK = {"team": "blue", "role": "forward", "action": "injury", "detail": "hamstring"}


def test_a_substitution_is_refused_without_the_service_token(client, live_room):
    code, _ = live_room()
    answer = client.post(f"/api/rooms/{code}/substitution", json=KNOCK)
    assert answer.status_code == 403


def test_a_manager_cannot_injure_the_other_squad(client, live_room, phones):
    """A session is not the authority here, and neither is a seat.

    Only a player agent reports its own condition, and the only thing in the
    building holding the service token is a process we run.
    """
    code, _ = live_room()
    phones.join("Sam Okafor", "sam@example.com")
    answer = client.post(f"/api/rooms/{code}/substitution",
                         json={**KNOCK, "team": "red"})
    assert answer.status_code == 403


def test_a_substitution_lands_in_the_log(client, live_room, service_headers):
    code, _ = live_room()
    answer = client.post(f"/api/rooms/{code}/substitution", json=KNOCK,
                         headers=service_headers)
    assert answer.status_code == 200

    said = _log(client, code, "substitution")
    assert len(said) == 1
    assert said[0]["payload"] == {"team": "blue", "role": "forward",
                                  "action": "injury", "detail": "hamstring"}
    assert answer.json()["seq"] == said[0]["seq"]


def test_a_substitution_reaches_the_room_socket(client, live_room, service_headers):
    code, _ = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as socket:
        socket.receive_json()  # the opening snapshot
        client.post(f"/api/rooms/{code}/substitution",
                    json={"team": "blue", "role": "goalkeeper",
                          "action": "substitution", "detail": "tired"},
                    headers=service_headers)
        message = socket.receive_json()

    assert message["type"] == "event"
    assert message["kind"] == "substitution"
    assert message["payload"]["role"] == "goalkeeper"
    assert message["payload"]["action"] == "substitution"


def test_it_survives_a_cut_between_matches(client, live_room, service_headers):
    """In the log rather than in a file somebody is polling, which is the point.

    A wall that cuts to this match a minute later reads the log on the way in,
    so it catches up on the knock instead of only ever showing knocks that
    happened while it was already looking.
    """
    code, _ = live_room()
    client.post(f"/api/rooms/{code}/substitution", json=KNOCK, headers=service_headers)

    caught_up = client.get(f"/api/rooms/{code}/events?since=0").json()["events"]
    assert [entry["payload"]["detail"] for entry in caught_up
            if entry["kind"] == "substitution"] == ["hamstring"]


def test_a_knock_in_one_match_does_not_reach_another(client, live_room, service_headers):
    """The bug the file had, kept fixed by the shape of the route.

    One file for the venue subbed a player off in whichever match was open.
    A room event cannot do that, and this is the test that says so out loud.
    """
    first, _ = live_room()
    second, _ = live_room()
    client.post(f"/api/rooms/{first}/substitution", json=KNOCK, headers=service_headers)

    assert len(_log(client, first, "substitution")) == 1
    assert _log(client, second, "substitution") == []


def test_an_unknown_dugout_role_or_action_is_refused(client, live_room, service_headers):
    code, _ = live_room()
    for wrong in ({"team": "green"}, {"role": "striker"}, {"action": "sacked"}):
        answer = client.post(f"/api/rooms/{code}/substitution", json={**KNOCK, **wrong},
                             headers=service_headers)
        assert answer.status_code in (404, 422), wrong
    assert _log(client, code, "substitution") == []


def test_a_match_that_is_over_takes_no_more_reports(client, live_room, service_headers,
                                                    conn):
    """A specialist still thinking when the whistle went writes nothing.

    The log of a finished match is what it was scored against, and an agent
    that answered late has no business adding to it.
    """
    code, _ = live_room()
    rooms.finish_match(conn, rooms.by_code(conn, code)["id"])
    answer = client.post(f"/api/rooms/{code}/substitution", json=KNOCK,
                         headers=service_headers)
    assert answer.status_code == 409
    assert _log(client, code, "substitution") == []


def test_the_workshop_takes_them_too(client, service_headers):
    """The lab is where the condition tools are demonstrated, and it never kicks off.

    Gating on `live` would make the one room the MCP server defaults to the one
    room it could not report in.
    """
    answer = client.post(f"/api/rooms/{codes.WORKSHOP}/substitution", json=KNOCK,
                         headers=service_headers)
    assert answer.status_code == 200
    assert len(_log(client, codes.WORKSHOP, "substitution")) == 1
