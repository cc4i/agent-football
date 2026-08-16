"""When "one request is one unit of work" actually ends.

The arena puts its one shared connection back in a `finally` around every HTTP
request. That was written as `@app.middleware("http")`, which is Starlette's
`BaseHTTPMiddleware`, and `BaseHTTPMiddleware` hands `call_next` back as soon
as the response *headers* exist -- the body is still being produced. So for the
one route that streams, `/run_sse`, the `finally` was running at the start of a
response that goes on for tens of seconds, and the invariant the middleware
exists to state was not true of the request it matters most for.

The second reason to be rid of it is the shape of the thing: it wraps every
response in a memory stream inside a task group of its own, which is a known
way for a response to hang or arrive truncated when the client goes away
mid-body. Cloud Run reports that as `the HTTP response was malformed or
connection to the instance had an error`, which is what production logged at
10:19:01 on 2026-08-16, sixty seconds before it stopped serving entirely.
"""

import asyncio
import threading

import httpx
import psycopg
import pytest

import codes
import db


def a_coach_that_streams_in_two_parts(monkeypatch, release):
    """A fake coach that sends one frame, waits to be let go, then sends another."""
    async def fake_transport(request):
        from httpx._content import AsyncIteratorByteStream

        async def stream_chunks():
            yield b'data: {"event": "first"}\n\n'
            await asyncio.to_thread(release.wait, 5.0)
            yield b'data: {"event": "second"}\n\n'

        return httpx.Response(
            status_code=200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncIteratorByteStream(stream_chunks()))

    monkeypatch.setattr(
        "proxy._make_client",
        lambda base_url, timeout: httpx.AsyncClient(
            transport=httpx.MockTransport(fake_transport), base_url=base_url))


def test_the_connection_goes_back_after_the_body_not_after_the_headers(
        real_arena_server, monkeypatch):
    """The `finally` must not fire while the response is still being written.

    Told as an order of events rather than a timestamp: the last thing put on
    the wire for this request has to happen before the connection is handed
    back, or the unit of work ended somewhere in the middle of itself.
    """
    release = threading.Event()
    a_coach_that_streams_in_two_parts(monkeypatch, release)

    happened = []
    put_back = threading.Event()
    real_finish = db.finish

    def note(conn):
        happened.append("put back")
        put_back.set()
        return real_finish(conn)

    monkeypatch.setattr(db, "finish", note)

    body = {"appName": "agents", "newMessage": {"role": "user", "parts": [{"text": "go"}]}}
    with httpx.Client() as caller:
        with caller.stream("POST", f"{real_arena_server}/run_sse", json=body) as answer:
            assert answer.status_code == 200
            chunks = answer.iter_raw()
            assert b"first" in next(chunks)
            happened.append("first frame read")
            # Nothing may have gone back yet: the response is one frame in and
            # the request that owns the connection is still being answered.
            assert "put back" not in happened, (
                f"the connection went back one frame into the body: {happened}")
            release.set()
            assert b"second" in next(chunks)
            happened.append("second frame read")
        happened.append("response closed")

    # The server hands the connection back on its own thread, so this waits for
    # it rather than reading the list the instant the client is done with the
    # socket. A regression fails on the assertion above, not on this timeout.
    assert put_back.wait(5.0), f"the connection never went back at all: {happened}"
    monkeypatch.undo()
    assert happened.index("second frame read") < happened.index("put back"), (
        f"the connection went back before the body finished: {happened}")


def test_a_caller_that_hangs_up_mid_stream_leaves_the_arena_usable(
        real_arena_server, monkeypatch):
    """The disconnect that `BaseHTTPMiddleware` is bad at.

    A phone that locks itself part way through a chain is the ordinary case,
    not the rare one. Whatever the middleware does about it, the next caller
    has to find an arena that works and a connection that is idle.
    """
    release = threading.Event()
    a_coach_that_streams_in_two_parts(monkeypatch, release)

    body = {"appName": "agents", "newMessage": {"role": "user", "parts": [{"text": "go"}]}}
    with httpx.Client() as caller:
        with caller.stream("POST", f"{real_arena_server}/run_sse", json=body) as answer:
            assert b"first" in next(answer.iter_raw())
            # And walk away without reading the rest.
    release.set()
    monkeypatch.undo()

    with httpx.Client() as after:
        assert after.get(f"{real_arena_server}/api/rooms/{codes.WORKSHOP}").status_code == 200
        assert after.get(f"{real_arena_server}/health").status_code == 200


def test_the_middleware_is_not_the_streaming_one(client):
    """A structural check, because the failure it guards is a hang.

    `BaseHTTPMiddleware` is the class the two tests above are about. Behaviour
    covers what it did wrong on the routes there are today; this covers the day
    somebody adds `@app.middleware("http")` back for the next thing, and
    quietly puts every response back inside a memory stream.
    """
    from starlette.middleware.base import BaseHTTPMiddleware

    installed = [layer.cls for layer in client.app.user_middleware]
    assert BaseHTTPMiddleware not in installed, (
        f"BaseHTTPMiddleware is back in the stack: {installed}")


def test_every_ordinary_route_still_ends_idle(client):
    # The whole point of the middleware, unchanged by how it is written.
    assert client.get(f"/api/rooms/{codes.WORKSHOP}").status_code == 200
    assert _idle(client)
    assert client.post("/api/players",
                       json={"display_name": "Alex Rivera", "email": ""}).status_code == 200
    assert _idle(client)
    assert client.get("/api/rooms/ZZZZ/teams/blue/profiles").status_code == 404
    assert _idle(client)


def test_a_route_that_raised_still_ends_idle(client, monkeypatch):
    import rooms

    def a_write_nobody_hardened(conn, *arguments, **keywords):
        conn.execute("INSERT INTO seat (room_id, team, player_id, philosophy, ready, "
                     "joined_at) VALUES (424242, 'blue', 424242, 'counter', 0, 0)")

    monkeypatch.setattr(rooms, "create_room", a_write_nobody_hardened)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        client.post("/api/rooms", json={"mode": "solo"})
    monkeypatch.undo()
    assert _idle(client)
    assert client.get(f"/api/rooms/{codes.WORKSHOP}").status_code == 200


def test_a_websocket_is_not_wrapped_by_it(client, live_room):
    # Sockets put the connection back per message, because one message is one
    # unit of work and a socket that lives all evening is not.
    code, _ = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as viewer:
        viewer.receive_json()
        assert _idle(client)


def _idle(client):
    return client.app.state.conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
