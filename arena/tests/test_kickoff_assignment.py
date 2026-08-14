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

"""Kick-off is where a match acquires somewhere to be played.

Until now this could not fail: the screen that opened the room was the host, so
`start_match`'s "a match needs a host" has never once been a real check. It
becomes one here, and it is the only genuinely new way kicking off can go
wrong.

These drive a real control socket rather than the stand-in the rest of the
suite uses, because what is under test is what goes down the wire.
"""

import contextlib
import time

import rooms


def seat(client, code, team="blue", philosophy="high press"):
    client.post(f"/api/rooms/{code}/seats/{team}", json={"philosophy": philosophy})
    client.post(f"/api/rooms/{code}/seats/{team}/ready", json={"ready": True})


def a_room_ready_to_go(client, phones, mode="solo"):
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": mode}).json()["code"]
    seat(client, code)
    return code


def wait_for(condition, seconds=2.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition never became true")


@contextlib.contextmanager
def a_pitch(client, service_headers, capacity=4):
    """A real grounds instance, connected and announced, for the block below."""
    with client.websocket_connect("/ws/grounds", headers=service_headers) as socket:
        socket.send_json({"type": "grounds.here", "capacity": capacity})
        wait_for(lambda: client.app.state.grounds.capacity() == capacity)
        yield socket


def test_kickoff_refuses_with_no_grounds_connected(client, conn, phones):
    code = a_room_ready_to_go(client, phones)

    answer = client.post(f"/api/rooms/{code}/start")

    assert answer.status_code == 503
    assert "pitch" in answer.json()["detail"]
    assert rooms.by_code(conn, code)["status"] == "lobby"


def test_a_refused_kickoff_leaves_the_room_startable(client, conn, phones,
                                                     service_headers):
    """The lobby is intact, so the same button works once a pitch turns up."""
    code = a_room_ready_to_go(client, phones)
    assert client.post(f"/api/rooms/{code}/start").status_code == 503

    with a_pitch(client, service_headers) as socket:
        assert client.post(f"/api/rooms/{code}/start").status_code == 200
        assert rooms.by_code(conn, code)["status"] == "live"


def test_kickoff_sends_the_physics_token_to_the_grounds(client, conn, phones,
                                                        service_headers):
    with a_pitch(client, service_headers) as socket:
        code = a_room_ready_to_go(client, phones)
        assert client.post(f"/api/rooms/{code}/start").status_code == 200

        assignment = socket.receive_json()
        room = rooms.by_code(conn, code)
        assert assignment["type"] == "host"
        assert assignment["code"] == code
        assert assignment["token"] == room["host_client_id"]
        assert assignment["seed"] == f"{code}-{room['id']}"
        assert room["status"] == "live"


def test_the_physics_token_reaches_nobody_but_the_grounds(client, conn, phones,
                                                          service_headers):
    """The one place it leaves the arena, and it leaves it exactly once."""
    with a_pitch(client, service_headers) as socket:
        code = a_room_ready_to_go(client, phones)
        with client.websocket_connect(f"/ws/rooms/{code}") as watcher:
            watcher.receive_json()
            answer = client.post(f"/api/rooms/{code}/start")
            physics = rooms.by_code(conn, code)["host_client_id"]
            assert physics not in str(answer.json())
            assert physics not in str(watcher.receive_json())
        socket.receive_json()


def test_the_seed_is_stable_for_one_room(client, conn, phones, service_headers):
    """Two grounds handed the same room would play the same match.

    Nothing does that today - a room is assigned once - but the seed is what
    makes it true, and it costs nothing to make it true now.
    """
    with a_pitch(client, service_headers) as socket:
        code = a_room_ready_to_go(client, phones)
        client.post(f"/api/rooms/{code}/start")
        seed = socket.receive_json()["seed"]

    room = rooms.by_code(conn, code)
    assert seed == f"{code}-{room['id']}"


def test_two_rooms_never_share_a_seed(client, phones, service_headers):
    with a_pitch(client, service_headers) as socket:
        first = a_room_ready_to_go(client, phones)
        client.post(f"/api/rooms/{first}/start")
        second = a_room_ready_to_go(client, phones)
        client.post(f"/api/rooms/{second}/start")

        seeds = {socket.receive_json()["seed"], socket.receive_json()["seed"]}
    assert len(seeds) == 2


def test_a_full_farm_refuses_the_next_match(client, conn, phones, service_headers):
    with a_pitch(client, service_headers, capacity=1) as socket:
        first = a_room_ready_to_go(client, phones)
        assert client.post(f"/api/rooms/{first}/start").status_code == 200
        socket.receive_json()

        second = a_room_ready_to_go(client, phones)
        answer = client.post(f"/api/rooms/{second}/start")
        assert answer.status_code == 503
        assert rooms.by_code(conn, second)["status"] == "lobby"


def test_a_match_that_ended_gives_its_pitch_back(client, conn, phones,
                                                 service_headers):
    """One pitch, two matches in a row, which is what an evening looks like."""
    with a_pitch(client, service_headers, capacity=1) as socket:
        first = a_room_ready_to_go(client, phones)
        client.post(f"/api/rooms/{first}/start")
        socket.receive_json()

        physics = rooms.by_code(conn, first)["host_client_id"]
        with client.websocket_connect(
                f"/ws/rooms/{first}?client_id={physics}") as grounds:
            grounds.receive_json()
            grounds.send_json({"type": "host.event", "kind": "full_time",
                               "at_ms": 180_000, "payload": {}})
        wait_for(lambda: rooms.by_code(conn, first)["status"] == "finished")
        wait_for(lambda: client.app.state.grounds.running() == 0)

        second = a_room_ready_to_go(client, phones)
        assert client.post(f"/api/rooms/{second}/start").status_code == 200
        socket.receive_json()
