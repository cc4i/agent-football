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

"""Who is available to run a match, and what happens when nobody is.

The registry is deliberately dumb: it knows how many matches each connected
instance said it would take and how many it has been given. It does not know
what a match is.
"""

import time

import pytest

from grounds import Grounds

SERVICE = "test-service-token"


class FakeSocket:
    """Stands in for a connected grounds. Only its identity matters here."""

    def __init__(self, name):
        self.name = name
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)

    def __repr__(self):
        return f"<grounds {self.name}>"


@pytest.fixture
def service(monkeypatch):
    """The shared secret between our own processes, and the header carrying it."""
    import app as arena_app

    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", SERVICE)
    return {"X-Arena-Service": SERVICE}


def settles(predicate, within=2.0):
    """Wait for something the socket's own task does, off the test's thread."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_nobody_connected_means_no_assignment():
    registry = Grounds()
    assert registry.assign("ABCD") is False


def test_an_assignment_goes_to_the_instance_with_room():
    registry = Grounds()
    one = FakeSocket("one")
    registry.joined(one, capacity=2)
    assert registry.assign("ABCD") is True
    assert registry.socket_for("ABCD") is one


def test_capacity_is_a_ceiling():
    registry = Grounds()
    registry.joined(FakeSocket("one"), capacity=1)
    assert registry.assign("ABCD") is True
    assert registry.assign("EFGH") is False


def test_releasing_frees_a_slot():
    registry = Grounds()
    one = FakeSocket("one")
    registry.joined(one, capacity=1)
    registry.assign("ABCD")
    assert registry.release("ABCD") is one
    assert registry.assign("EFGH") is True


def test_asking_twice_for_the_same_room_does_not_spend_two_slots():
    """`POST /start` is a button, and a button gets double-tapped."""
    registry = Grounds()
    one = FakeSocket("one")
    registry.joined(one, capacity=1)
    assert registry.assign("ABCD") is True
    assert registry.assign("ABCD") is True
    assert registry.running() == 1
    assert registry.assign("EFGH") is False


def test_the_emptiest_instance_takes_the_next_match():
    registry = Grounds()
    one, two = FakeSocket("one"), FakeSocket("two")
    registry.joined(one, capacity=4)
    registry.joined(two, capacity=4)
    codes = ("AAAA", "BBBB", "CCCC", "DDDD")
    for code in codes:
        registry.assign(code)

    where = [registry.socket_for(code) for code in codes]
    assert where.count(one) == 2
    assert where.count(two) == 2


def test_an_instance_leaving_takes_its_matches_with_it():
    registry = Grounds()
    one = FakeSocket("one")
    registry.joined(one, capacity=2)
    registry.assign("ABCD")
    registry.left(one)
    assert registry.socket_for("ABCD") is None
    assert registry.capacity() == 0
    assert registry.running() == 0


def test_one_instance_leaving_does_not_disturb_another():
    registry = Grounds()
    going, staying = FakeSocket("going"), FakeSocket("staying")
    registry.joined(going, capacity=1)
    registry.joined(staying, capacity=1)
    registry.assign("AAAA")
    registry.assign("BBBB")
    registry.left(going)

    kept = [code for code in ("AAAA", "BBBB") if registry.socket_for(code) is staying]
    assert len(kept) == 1
    assert registry.capacity() == 1
    assert registry.running() == 1


def test_releasing_a_room_nobody_holds_is_not_an_error():
    registry = Grounds()
    assert registry.release("ABCD") is None


def test_a_capacity_of_nonsense_is_a_capacity_of_none():
    """An instance that announces rubbish takes no work rather than all of it."""
    registry = Grounds()
    registry.joined(FakeSocket("one"), capacity=-4)
    assert registry.capacity() == 0
    assert registry.assign("ABCD") is False


def test_the_control_socket_refuses_an_unauthenticated_instance(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/grounds") as socket:
            socket.receive_json()


def test_the_control_socket_refuses_a_wrong_token(client, service):
    with pytest.raises(Exception):
        with client.websocket_connect(
                "/ws/grounds", headers={"X-Arena-Service": "guess"}) as socket:
            socket.receive_json()


def test_the_control_socket_takes_a_capacity(client, service):
    registry = client.app.state.grounds
    with client.websocket_connect("/ws/grounds", headers=service) as socket:
        socket.send_json({"type": "grounds.here", "capacity": 8})
        # The registry is the observable effect; there is no reply to wait on,
        # so ask the app rather than the wire.
        assert settles(lambda: registry.capacity() == 8), registry.capacity()

    assert settles(lambda: registry.capacity() == 0), registry.capacity()


def test_an_instance_that_hangs_up_is_forgotten_with_its_matches(client, service):
    registry = client.app.state.grounds
    with client.websocket_connect("/ws/grounds", headers=service) as socket:
        socket.send_json({"type": "grounds.here", "capacity": 4})
        assert settles(lambda: registry.capacity() == 4)
        registry.assign("ABCD")

    assert settles(lambda: registry.socket_for("ABCD") is None)
    assert registry.connected() == 0


def test_a_frame_the_socket_does_not_understand_is_ignored(client, service):
    registry = client.app.state.grounds
    with client.websocket_connect("/ws/grounds", headers=service) as socket:
        socket.send_json({"type": "grounds.what"})
        socket.send_json(["not even an object"])
        socket.send_json({"type": "grounds.here", "capacity": 2})
        assert settles(lambda: registry.capacity() == 2)
