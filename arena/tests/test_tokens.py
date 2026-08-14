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

"""Two secrets that used to be one, and the wall between them.

The screen token says "I opened this lobby". The physics token says "I am
simulating this match". They were one string for as long as one tab did both
jobs, which stopped being true when the grounds took the second one.

The point of the split is what each side cannot do. A screen holding the
physics token would hold a credential for work it does not do; the grounds
holding the screen token could reshape a lobby they have no business in.
"""
import rooms


def test_a_room_mints_two_different_tokens(conn):
    room = rooms.create_room(conn, "versus")
    assert room["host_client_id"]
    assert room["screen_client_id"]
    assert room["host_client_id"] != room["screen_client_id"]


def test_two_rooms_never_share_a_screen_token(conn):
    first = rooms.create_room(conn, "solo")
    second = rooms.create_room(conn, "solo")
    assert first["screen_client_id"] != second["screen_client_id"]


def test_opening_a_room_returns_the_screen_token(client):
    body = client.post("/api/rooms", json={"mode": "versus"}).json()
    assert body["screen_token"]
    assert len(body["screen_token"]) > 10


def test_opening_a_room_never_returns_the_physics_token(client, conn):
    """The one response that used to carry it, and now must not.

    The physics token leaves the arena over the grounds' control socket and
    nowhere else. A browser that never receives it cannot leak it.
    """
    body = client.post("/api/rooms", json={"mode": "versus"}).json()
    assert "host_token" not in body
    room = rooms.by_code(conn, body["code"])
    assert room["host_client_id"] not in repr(body)


def test_the_mode_switch_takes_the_screen_token(client):
    opened = client.post("/api/rooms", json={"mode": "solo"}).json()
    answer = client.post(f"/api/rooms/{opened['code']}/mode",
                         json={"mode": "versus", "screen_token": opened["screen_token"]})
    assert answer.status_code == 200
    assert answer.json()["mode"] == "versus"


def test_the_mode_switch_refuses_the_physics_token(client, conn):
    """The grounds have no business reshaping a lobby, and cannot."""
    opened = client.post("/api/rooms", json={"mode": "solo"}).json()
    room = rooms.by_code(conn, opened["code"])
    answer = client.post(f"/api/rooms/{opened['code']}/mode",
                         json={"mode": "versus", "screen_token": room["host_client_id"]})
    assert answer.status_code == 403
    assert rooms.by_code(conn, opened["code"])["mode"] == "solo"


def test_the_mode_switch_refuses_another_room_s_screen_token(client, conn):
    mine = client.post("/api/rooms", json={"mode": "solo"}).json()
    theirs = client.post("/api/rooms", json={"mode": "solo"}).json()
    answer = client.post(f"/api/rooms/{mine['code']}/mode",
                         json={"mode": "versus", "screen_token": theirs["screen_token"]})
    assert answer.status_code == 403
    assert rooms.by_code(conn, mine["code"])["mode"] == "solo"


def test_the_screen_token_does_not_leak_into_the_snapshot(client, phones):
    """Everything on the bus is read by every phone watching the room."""
    opened = client.post("/api/rooms", json={"mode": "versus"}).json()
    code, screen_token = opened["code"], opened["screen_token"]

    assert screen_token not in str(client.get(f"/api/rooms/{code}").json())

    with client.websocket_connect(f"/ws/rooms/{code}") as watcher:
        assert screen_token not in str(watcher.receive_json())
        client.post(f"/api/rooms/{code}/mode",
                    json={"mode": "solo", "screen_token": screen_token})
        assert screen_token not in str(watcher.receive_json())
