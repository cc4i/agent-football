"""The reserved room the dugout works in, and who is allowed to shout there.

The workshop is not a match. Nobody joins it, nobody sits in it, and it never
kicks off: it is one blue squad in front of a pitch that is always running, so
the five stages of the dugout have something to tune. The agent holding the
service token is therefore the only voice in it, and these are about where that
authority begins and, more importantly, where it stops.
"""

import json

import codes
import rooms

SERVICE = "test-service-token"


def scripted(*events):
    """A squad that answers with these events and then stops."""
    async def run(text, state):
        for event in events:
            yield event
    return run


HUDDLE = {"author": "SynthesisCaptain", "content": {"parts": [{"text": json.dumps(
    {"status": "Tactics executed", "huddle": {"defender": "Holding the line."}})}]}}


def said(client, code):
    connection = client.app.state.conn
    entries = rooms.events(connection, rooms.by_code(connection, code)["id"])
    return [entry for entry in entries if entry["kind"] == "shout.sent"]


def test_the_agent_may_shout_in_the_workshop_with_no_seat_and_no_kick_off(
        client, monkeypatch):
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", SERVICE)

    response = client.post(f"/api/rooms/{codes.WORKSHOP}/shout",
                           json={"preset": "press high"},
                           headers={"X-Arena-Service": SERVICE})

    assert response.status_code == 200
    assert response.json()["team"] == "blue"


def test_the_workshop_log_says_antigravity_shouted_it(client, monkeypatch):
    # Nobody is sitting in that dugout. The manager is in a chat window and the
    # agent is the one on the touchline, so the agent's name goes on the shout.
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", SERVICE)
    client.post(f"/api/rooms/{codes.WORKSHOP}/shout", json={"preset": "sit deep"},
                headers={"X-Arena-Service": SERVICE})

    assert [entry["payload"]["actor"] for entry in said(client, codes.WORKSHOP)] \
        == [arena_app.WORKSHOP_ACTOR]


async def test_words_typed_at_the_dugout_reach_the_squad_the_same_way(arena, monkeypatch):
    # The dugout's shout tool sends prose, not chips, and waits on the relay.
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", SERVICE)
    arena.app.state.chain._run = scripted(HUDDLE)

    reply = await arena.post(f"/api/rooms/{codes.WORKSHOP}/shout",
                             json={"text": "push the defensive line up"},
                             headers={"X-Arena-Service": SERVICE})

    assert reply.status_code == 200
    assert reply.json()["actor"] == arena_app.WORKSHOP_ACTOR


def test_that_token_cannot_shout_into_a_match_a_stranger_is_playing(
        client, live_room, monkeypatch):
    # The token belongs to processes on the machine the arena runs on, and none
    # of them has any business in somebody else's match.
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", SERVICE)
    code, _ = live_room()
    client.cookies.clear()

    response = client.post(f"/api/rooms/{code}/shout", json={"preset": "press high"},
                           headers={"X-Arena-Service": SERVICE})

    assert response.status_code == 401
    assert said(client, code) == []


def test_a_phone_that_wandered_into_the_workshop_still_has_no_dugout(
        client, phones, monkeypatch):
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", SERVICE)
    phones.join("Sam Okafor", "sam@example.com")

    response = client.post(f"/api/rooms/{codes.WORKSHOP}/shout",
                           json={"preset": "press high"})

    assert response.status_code == 403
    assert said(client, codes.WORKSHOP) == []


def test_a_passer_by_with_neither_session_nor_token_may_not_shout_there(client):
    assert client.post(f"/api/rooms/{codes.WORKSHOP}/shout",
                       json={"preset": "press high"}).status_code == 401


def test_the_wrong_token_is_no_better_than_none_in_the_workshop(client, monkeypatch):
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", SERVICE)

    response = client.post(f"/api/rooms/{codes.WORKSHOP}/shout",
                           json={"preset": "press high"},
                           headers={"X-Arena-Service": "guess"})

    assert response.status_code == 401


def test_an_unset_token_lets_nobody_into_the_workshop_either(client, monkeypatch):
    # Forgetting to configure a secret must lock the agents out rather than
    # letting the whole network shout.
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", "")

    response = client.post(f"/api/rooms/{codes.WORKSHOP}/shout",
                           json={"preset": "press high"},
                           headers={"X-Arena-Service": ""})

    assert response.status_code == 401


def test_a_workshop_somebody_has_closed_is_not_shouted_at(client, monkeypatch):
    # Nothing in the product can put the workshop in this state, which is why
    # the state is written by hand here. The guard is there so that if anything
    # ever does, the agent is told rather than shouting into a corpse.
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", SERVICE)
    connection = client.app.state.conn
    connection.execute("UPDATE room SET status = 'abandoned' WHERE code = ?",
                       (codes.WORKSHOP,))
    connection.commit()

    response = client.post(f"/api/rooms/{codes.WORKSHOP}/shout",
                           json={"preset": "press high"},
                           headers={"X-Arena-Service": SERVICE})

    assert response.status_code == 409
