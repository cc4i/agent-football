"""The shared connection has to come back idle from every unit of work.

psycopg opens a transaction on the first statement of any kind, reads included,
and the whole arena runs on one connection. Left open, a read pins the vacuum
horizon on a board meant to last weeks; left aborted, a write that hit a
constraint fails every later statement in the process.
"""

import psycopg
import pytest

import codes
import identity
import rooms


def idle(client):
    status = client.app.state.conn.info.transaction_status
    return status == psycopg.pq.TransactionStatus.IDLE


def test_a_read_leaves_no_transaction_open(client):
    assert client.get(f"/api/rooms/{codes.WORKSHOP}").status_code == 200
    assert idle(client)


def test_a_write_leaves_no_transaction_open(client):
    assert client.post("/api/players",
                       json={"display_name": "Alex Rivera",
                             "email": "alex@example.com"}).status_code == 200
    assert idle(client)


def test_a_refused_request_leaves_no_transaction_open(client):
    # The 404 is raised after the lookup that opened the transaction.
    assert client.get("/api/rooms/ZZZZ/teams/blue/profiles").status_code == 404
    assert idle(client)


def test_a_socket_that_only_listens_leaves_no_transaction_open(client, live_room):
    # The snapshot on connect is a read like any other, and a wall screen holds
    # its socket open for the whole evening.
    code, _ = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        assert idle(client)
    with client.websocket_connect("/ws/wall") as wall:
        wall.receive_json()
        assert idle(client)


def test_a_socket_message_leaves_no_transaction_open(client, live_room):
    code, host_token = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={host_token}") as host:
        host.receive_json()
        host.send_json({"type": "host.event", "kind": "goal",
                        "match_ms": 100, "payload": {"team": "blue"}})
        # The frame comes back down once the event is logged, so by the time it
        # arrives the write has happened and the connection should be back.
        host.receive_json()
        assert idle(client)


def test_a_write_that_lost_a_race_does_not_brick_the_arena(client, dsn, monkeypatch):
    """A rollout runs two instances at once, so check-then-write stops being safe.

    `create_player` looks for the address, finds nothing, and inserts. Another
    instance inserting the same address in between turns that INSERT into a
    UNIQUE violation. On one shared connection an aborted transaction fails
    every later statement in the process, so without a rollback one unlucky
    request takes the arena down with it. `mask_email` is called exactly once,
    between the check and the insert, which makes it the seam to let the other
    instance in through.
    """
    import app as arena

    email = "alex@example.com"
    real_mask = identity.mask_email
    already = []

    def the_other_instance_gets_there_first(address):
        if not already:
            already.append(address)
            with psycopg.connect(dsn, autocommit=True) as rival:
                rival.execute(
                    "INSERT INTO player (display_name, email_hash, email_masked, "
                    "created_at) VALUES (%s, %s, %s, %s)",
                    ("Alex Rivera", identity.hash_email(address, arena.EMAIL_SALT),
                     real_mask(address), 0),
                )
        return real_mask(address)

    monkeypatch.setattr(rooms.identity, "mask_email",
                        the_other_instance_gets_there_first)
    with pytest.raises(psycopg.errors.UniqueViolation):
        client.post("/api/players", json={"display_name": "Alex Rivera", "email": email})

    # The whole point: everybody else's next request still works.
    assert idle(client)
    assert client.get(f"/api/rooms/{codes.WORKSHOP}").status_code == 200
    assert client.post("/api/players",
                       json={"display_name": "Sam Okafor",
                             "email": "sam@example.com"}).status_code == 200
