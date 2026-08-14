"""The arena's window onto the coach, which is exactly two paths wide.

The pitch opens an ADK session and posts a shout to /run_sse. In development
Vite proxies both to :8000. Deployed there is no :8000 to reach, because the
coach is a sidecar with no published port, so the arena carries those two
calls and refuses everything else. An open proxy in front of an
unauthenticated ADK server is a free language model for whoever finds it.
"""

import asyncio
import json
import threading

import httpx
import pytest

import coach


@pytest.mark.parametrize("path", [
    "/api-apps/agents/users/user/sessions/abc",       # a session by id
    "/api-apps/agents/users/user/sessions/abc/events",
    "/api-apps/",
    "/api-apps/agents/eval_sets",
    "/api-apps/list-apps",
])
def test_everything_but_the_two_allowed_paths_is_a_404(client, path):
    assert client.post(path, json={}).status_code == 404
    assert client.get(path).status_code == 404


def test_run_sse_only_answers_post(client):
    assert client.get("/run_sse").status_code == 405


def test_the_session_path_only_answers_post(client):
    assert client.get("/api-apps/agents/users/user/sessions").status_code == 405


def test_a_coach_that_is_not_there_is_a_502_not_a_500(client, monkeypatch):
    monkeypatch.setattr("coach.COACH_URL", "http://127.0.0.1:9")
    reply = client.post("/run_sse", json={"appName": "agents"})
    assert reply.status_code == 502
    assert "coach" in reply.json()["detail"]


def test_malformed_user_segment_is_400_not_500(client, monkeypatch):
    """A user segment that httpx cannot parse raises InvalidURL, not HTTPError."""
    # Monkeypatch session_path to generate an invalid URL by including a
    # literal newline (which quote() would normally prevent).
    def broken_session_path(user):
        return f"/apps/agents/users/{user}\n/sessions"

    monkeypatch.setattr("coach.session_path", broken_session_path)

    reply = client.post("/api-apps/agents/users/test/sessions", json={"state": {}})
    assert reply.status_code == 400
    assert "malformed" in reply.json()["detail"]


def test_an_encoded_dotdot_user_segment_is_refused_with_a_400(client):
    """Starlette decodes the segment, so the fence sees `..` and says no."""
    reply = client.post("/api-apps/agents/users/%2e%2e/sessions", json={"state": {}})
    assert reply.status_code == 400
    assert "invalid" in reply.json()["detail"]


def test_an_encoded_question_mark_user_segment_is_refused_with_a_400(client):
    """Same fence, same decoding: the handler is handed `foo?bar` and says no."""
    reply = client.post("/api-apps/agents/users/foo%3Fbar/sessions", json={"state": {}})
    assert reply.status_code == 400
    assert "invalid" in reply.json()["detail"]


@pytest.mark.parametrize("user, path", [
    ("..", "/apps/agents/users/%2E%2E/sessions"),
    ("foo?bar", "/apps/agents/users/foo%3Fbar/sessions"),
    ("arena", "/apps/agents/users/arena/sessions"),
])
def test_the_session_path_encodes_the_user_segment(user, path):
    """The belt behind the two 400s above, asserted where the fence cannot mask it.

    Task 8 encoded this segment so httpx's dot-segment removal could not
    collapse the upstream path to /apps/agents/sessions, and a query string
    could not be smuggled into it. The validator refuses both of those inputs
    at the door now, which is exactly why the encoding needs a test of its own:
    routed through a handler, dropping `quote` would change nothing anybody
    could see. The `arena` row is here so that encoding cannot be quietly
    dropped for the values that actually occur either.
    """
    assert coach.session_path(user) == path


# Happy path: both routes carry requests and return responses


def test_open_session_carries_body_and_returns_session_id(client, monkeypatch):
    """The session body arrives unaltered and the coach's id comes back."""
    received = []

    async def fake_transport(request):
        received.append({"path": str(request.url.path), "content": request.content})
        return httpx.Response(
            status_code=200,
            content=b'{"id": "test-session-123"}',
            headers={"content-type": "application/json"},
        )

    def fake_client(base_url, timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(fake_transport), base_url=base_url)

    monkeypatch.setattr("proxy._make_client", fake_client)

    body = {"state": {"room_code": "ABC123", "team": "blue"}}
    reply = client.post("/api-apps/agents/users/testuser/sessions", json=body)

    assert reply.status_code == 200
    assert reply.json()["id"] == "test-session-123"
    assert len(received) == 1
    assert received[0]["path"] == "/apps/agents/users/testuser/sessions"
    assert json.loads(received[0]["content"]) == body


def test_run_sse_carries_body_and_frames_come_back(client, monkeypatch):
    """The shout body reaches the coach and SSE frames come back."""
    received = []

    async def fake_transport(request):
        from httpx._content import AsyncIteratorByteStream
        received.append({"method": request.method, "url": str(request.url), "content": request.content})

        async def stream_body():
            yield b'data: {"event": "first"}\n\n'
            yield b'data: {"event": "second"}\n\n'

        return httpx.Response(
            status_code=200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncIteratorByteStream(stream_body()),
        )

    def fake_client(base_url, timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(fake_transport), base_url=base_url)

    monkeypatch.setattr("proxy._make_client", fake_client)

    body = {"appName": "agents", "newMessage": {"role": "user", "parts": [{"text": "attack"}]}}
    response = client.post("/run_sse", json=body)
    assert response.status_code == 200
    assert b"first" in response.content
    assert b"second" in response.content
    assert len(received) == 1
    assert received[0]["method"] == "POST"
    assert received[0]["url"] == f"{coach.COACH_URL}/run_sse"
    assert json.loads(received[0]["content"]) == body


def test_coach_error_status_passes_through_not_502(client, monkeypatch):
    """A coach that answers with 4xx or 5xx passes that status, not 502."""

    async def fake_transport(request):
        return httpx.Response(
            status_code=429,
            content=b'{"error": "rate limited"}',
            headers={"content-type": "application/json"},
        )

    def fake_client(base_url, timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(fake_transport), base_url=base_url)

    monkeypatch.setattr("proxy._make_client", fake_client)

    reply = client.post("/api-apps/agents/users/user/sessions", json={"state": {}})
    assert reply.status_code == 429
    assert b"rate limited" in reply.content


# Streaming: incremental delivery over a real socket


def test_run_sse_streams_incrementally_not_buffered(real_arena_server, monkeypatch):
    """Frames arrive as they are produced, not after the coach finishes.

    If the response were buffered anywhere in the stack, the first read would
    not return until the generator had finished. The fake coach blocks after
    the first frame, and only the test releasing it allows the second frame
    to be produced.
    """
    release = threading.Event()

    async def fake_transport(request):
        from httpx._content import AsyncIteratorByteStream

        async def stream_chunks():
            yield b"data: {\"event\": \"first\"}\n\n"
            # Block until the test says otherwise. The bounded wait means a
            # regression fails the assertion rather than hanging the suite.
            await asyncio.to_thread(release.wait, 2.0)
            yield b"data: {\"event\": \"second\"}\n\n"

        return httpx.Response(
            status_code=200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncIteratorByteStream(stream_chunks()),
        )

    def fake_client(base_url, timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(fake_transport), base_url=base_url)

    monkeypatch.setattr("proxy._make_client", fake_client)

    body = {"appName": "agents", "newMessage": {"role": "user", "parts": [{"text": "attack"}]}}

    # Use a real httpx.Client over the socket to see incremental delivery.
    with httpx.Client() as client:
        with client.stream("POST", f"{real_arena_server}/run_sse", json=body) as response:
            assert response.status_code == 200
            iter_raw = response.iter_raw()
            # If buffered, this read never returns because the fake is blocked.
            first = next(iter_raw)
            assert b"first" in first
            assert b"second" not in first
            # Release the fake coach to send the second frame.
            release.set()
            second = next(iter_raw)
            assert b"second" in second


# Body size limits: declared and actual length


def test_oversized_declared_body_refused_without_reading_session(client, monkeypatch):
    """An oversized Content-Length is refused before the coach is called."""
    coach_called = []

    async def fake_transport(request):
        coach_called.append(True)
        return httpx.Response(status_code=200, content=b'{"id": "session-123"}')

    def fake_client(base_url, timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(fake_transport), base_url=base_url)

    monkeypatch.setattr("proxy._make_client", fake_client)

    # Declare a body larger than MAX_BODY_BYTES.
    reply = client.post(
        "/api-apps/agents/users/user/sessions",
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": str(65 * 1024)},
    )
    assert reply.status_code == 413
    assert "too much to say" in reply.json()["detail"]
    assert not coach_called


def test_oversized_declared_body_refused_without_reading_run_sse(client, monkeypatch):
    """An oversized Content-Length is refused before the coach is called."""
    coach_called = []

    async def fake_transport(request):
        coach_called.append(True)
        from httpx._content import AsyncIteratorByteStream

        async def stream_body():
            yield b'data: {"event": "test"}\n\n'

        return httpx.Response(
            status_code=200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncIteratorByteStream(stream_body()),
        )

    def fake_client(base_url, timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(fake_transport), base_url=base_url)

    monkeypatch.setattr("proxy._make_client", fake_client)

    reply = client.post(
        "/run_sse",
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": str(65 * 1024)},
    )
    assert reply.status_code == 413
    assert "too much to say" in reply.json()["detail"]
    assert not coach_called


def test_oversized_chunked_body_refused_without_calling_coach(client, monkeypatch):
    """A chunked body with no Content-Length but over 64 KiB is refused."""
    coach_called = []

    async def fake_transport(request):
        coach_called.append(True)
        return httpx.Response(status_code=200, content=b'{"id": "session-123"}')

    def fake_client(base_url, timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(fake_transport), base_url=base_url)

    monkeypatch.setattr("proxy._make_client", fake_client)

    # Use a generator to make httpx send transfer-encoding: chunked.
    def oversized_chunks():
        yield b"x" * 33 * 1024
        yield b"x" * 33 * 1024

    response = client.post(
        "/api-apps/agents/users/user/sessions",
        content=oversized_chunks(),
        headers={"Content-Type": "application/json"},
    )

    # Verify no Content-Length header was sent (chunked transfer encoding).
    # Note: TestClient processes the request, so we check the response.
    assert response.status_code == 413
    assert "too much to say" in response.json()["detail"]
    assert not coach_called
