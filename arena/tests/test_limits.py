"""What the arena will take from one address before it says no."""

import types

import httpx
import pytest
from httpx._content import AsyncIteratorByteStream

import limits
from app import COACH_BURST, PLAYER_BURST

SHOUT = {"appName": "agents"}


@pytest.fixture
def coach_calls(monkeypatch):
    """A coach that answers both proxied routes, and the list of what reached it.

    Both routes go through one fake because the tests below care about the same
    thing on each: whether the call arrived at all. A session create wants JSON
    back and /run_sse wants a stream, which is the only difference.
    """
    reached = []

    async def transport(request):
        reached.append(request.url.path)
        if request.url.path == "/run_sse":
            async def frames():
                yield b'data: {}\n\n'
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=AsyncIteratorByteStream(frames()))
        return httpx.Response(200, content=b'{"id": "session-123"}')

    monkeypatch.setattr("proxy._make_client", lambda base_url, timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url=base_url))
    return reached


class _Caller:
    """The two attributes `client_ip` reads, and nothing else.

    A TestClient cannot put an arbitrary chain in front of itself, and the
    interesting half of `client_ip` is what it does with one.
    """

    def __init__(self, forwarded=None, socket_host=None):
        self.headers = {"x-forwarded-for": forwarded} if forwarded else {}
        self.client = types.SimpleNamespace(host=socket_host) if socket_host else None


def test_a_burst_goes_through_and_the_next_one_does_not():
    bucket = limits.Bucket(rate=1.0, burst=3)
    assert [bucket.take("1.2.3.4", now=0.0) for _ in range(3)] == [True, True, True]
    assert bucket.take("1.2.3.4", now=0.0) is False


def test_it_refills_over_time():
    bucket = limits.Bucket(rate=1.0, burst=3)
    for _ in range(3):
        bucket.take("1.2.3.4", now=0.0)
    assert bucket.take("1.2.3.4", now=1.0) is True


def test_it_never_refills_past_the_burst():
    bucket = limits.Bucket(rate=1.0, burst=3)
    bucket.take("1.2.3.4", now=0.0)
    assert [bucket.take("1.2.3.4", now=1000.0) for _ in range(3)] == [True, True, True]
    assert bucket.take("1.2.3.4", now=1000.0) is False


def test_one_address_cannot_spend_another_address_budget():
    bucket = limits.Bucket(rate=1.0, burst=1)
    assert bucket.take("1.2.3.4", now=0.0) is True
    assert bucket.take("5.6.7.8", now=0.0) is True


def test_idle_addresses_are_forgotten_rather_than_accumulated():
    # A venue's worth of phones over an evening should not become a dict that
    # only ever grows.
    bucket = limits.Bucket(rate=1.0, burst=1)
    bucket.take("1.2.3.4", now=0.0)
    bucket.take("5.6.7.8", now=100_000.0)
    assert "1.2.3.4" not in bucket._seen


def test_a_spoofed_chain_is_keyed_on_its_last_entry():
    # Everything before the last entry is whatever the caller claimed.
    assert limits.client_ip(_Caller("1.2.3.4, 5.6.7.8", "9.9.9.9")) == "5.6.7.8"


def test_a_last_entry_that_is_not_an_address_is_not_trusted_as_a_key():
    # Otherwise a caller names its own bucket, which is the same as having none.
    assert limits.client_ip(_Caller("1.2.3.4, not-an-address", "9.9.9.9")) == "9.9.9.9"


def test_no_forwarded_header_falls_back_to_the_socket():
    # Running without a proxy, which is every developer's laptop.
    assert limits.client_ip(_Caller(socket_host="9.9.9.9")) == "9.9.9.9"


def test_no_socket_either_is_unknown_rather_than_a_crash():
    # One shared bucket is a poor limit and a much better answer than a 500.
    assert limits.client_ip(_Caller()) == "unknown"


def test_opening_rooms_too_fast_is_a_429(client):
    # The bucket is this app's own, so shrinking it cannot leak into the next
    # test the way a module-level one would.
    client.app.state.rooms_opened = limits.Bucket(rate=0.0, burst=2)
    assert client.post("/api/rooms", json={"mode": "solo"}).status_code == 200
    assert client.post("/api/rooms", json={"mode": "solo"}).status_code == 200
    refused = client.post("/api/rooms", json={"mode": "solo"})
    assert refused.status_code == 429


def test_joining_too_fast_is_a_429(client):
    # The endpoint this task is named for, shrunk the same way and for the same
    # reason. Each join is a different email so that nothing but the bucket can
    # be what refuses the third.
    client.app.state.players = limits.Bucket(rate=0.0, burst=2)
    joining = {"display_name": "Alex Rivera"}
    assert client.post("/api/players", json={**joining, "email": "a@example.com"}
                       ).status_code == 200
    assert client.post("/api/players", json={**joining, "email": "b@example.com"}
                       ).status_code == 200
    refused = client.post("/api/players", json={**joining, "email": "c@example.com"})
    assert refused.status_code == 429


def test_the_default_budget_is_bigger_than_a_test_run_needs():
    # 50 people is the spec's first line and a venue is one address, so the
    # shipped burst has to carry all fifty of those joins. A number chosen
    # against a requirement should fail here when somebody lowers it past it.
    assert PLAYER_BURST >= 50


def test_a_full_venue_is_a_503_with_a_sentence_in_it(client, live_room, monkeypatch):
    # The cap is what is under test, so one match is already live and the cap is
    # set to exactly that. A handler that refused unconditionally would pass
    # this and fail its twin below.
    live_room()
    monkeypatch.setattr("app.MAX_LIVE_ROOMS", 1)
    refused = client.post("/api/rooms", json={"mode": "solo"})
    assert refused.status_code == 503
    assert "full" in refused.json()["detail"].lower()


def test_a_venue_with_room_left_in_it_opens_another_match(client, live_room, monkeypatch):
    # The same live match and the same call, one higher cap.
    live_room()
    monkeypatch.setattr("app.MAX_LIVE_ROOMS", 2)
    assert client.post("/api/rooms", json={"mode": "solo"}).status_code == 200


def test_two_phones_spend_the_same_coach_budget(client, phones, coach_calls):
    # The coach's budget belongs to the instance, not to whoever is holding a
    # session. The pitch is what calls these routes and a venue is one address,
    # so a session buys no budget of its own and two of them cannot have one
    # each - which is also what stops 120 anonymous joins buying 120 budgets.
    client.app.state.coach = limits.Bucket(rate=0.0, burst=1)
    phones.use(phones.join("Alex Rivera", "alex@example.com"))
    assert client.post("/run_sse", json=SHOUT).status_code == 200
    phones.use(phones.join("Taylor Quinn", "taylor@example.com"))
    assert client.post("/run_sse", json=SHOUT).status_code == 429
    assert len(coach_calls) == 1


def test_a_caller_with_no_cookie_spends_that_same_budget(client, phones, coach_calls):
    # No session is not a bucket of its own either.
    client.app.state.coach = limits.Bucket(rate=0.0, burst=1)
    phones.use(phones.join("Alex Rivera", "alex@example.com"))
    assert client.post("/run_sse", json=SHOUT).status_code == 200
    client.cookies.clear()
    assert client.post("/run_sse", json=SHOUT).status_code == 429
    assert len(coach_calls) == 1


def test_the_two_coach_routes_spend_the_one_bucket(client, coach_calls):
    # A shout is two requests, because the pitch opens a fresh session for each
    # one, so both charge points have to be real. Exhaust the budget through the
    # session route and /run_sse must already be refused.
    client.app.state.coach = limits.Bucket(rate=0.0, burst=1)
    opened = client.post("/api-apps/agents/users/arena/sessions", json={"state": {}})
    assert opened.status_code == 200
    refused = client.post("/run_sse", json=SHOUT)
    assert refused.status_code == 429
    assert coach_calls == ["/apps/agents/users/arena/sessions"]


def test_the_shipped_coach_burst_carries_a_venue_opening_shouts():
    # 50 people is the spec, which is twenty-five matches, and every shout costs
    # two requests. The burst is what carries them all shouting at once; the
    # rate is what refuses a loop afterwards.
    assert COACH_BURST >= 2 * 25


def test_user_segment_must_match_pattern(client):
    # The segment must match [A-Za-z0-9_-]+ or it is a 400 before anything is
    # proxied. A percent sign is in that list of refusals on purpose: the only
    # way one reaches the validator is a caller double-encoding it, and the
    # legitimate values are `arena` and `user`.
    assert client.post("/api-apps/agents/users/invalid space/sessions",
                       json={}).status_code == 400
    assert client.post("/api-apps/agents/users/invalid@char/sessions",
                       json={}).status_code == 400
    assert client.post("/api-apps/agents/users/%2525/sessions",
                       json={}).status_code == 400
    assert client.post("/api-apps/agents/users/slash/path/sessions",
                       json={}).status_code == 404


def test_valid_user_segments_are_allowed(client, coach_calls):
    # arena, user, alphanumerics, underscores and hyphens are all fine, and each
    # of them reaches the coach at the path it names.
    for user in ["arena", "user", "test_user", "test-user", "user123"]:
        coach_calls.clear()
        reply = client.post(f"/api-apps/agents/users/{user}/sessions", json={})
        assert reply.status_code == 200, f"user={user} should be allowed"
        assert coach_calls == [f"/apps/agents/users/{user}/sessions"]
