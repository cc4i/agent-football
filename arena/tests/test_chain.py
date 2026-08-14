"""What a manager sees between shouting and the squad answering.

The chain is three language-model hops and takes tens of seconds, so almost
everything here is about the wait: what the relay says during it, what happens
to a shout that arrives while another is still going out, and what the squad's
silence looks like when one of them never answers at all.

The squad is scripted rather than reached over HTTP. Gemini's actual words are
not what these tests are about; the shape of the stream is.
"""

import asyncio
import json

import pytest

import chain
import coach
import rooms
from bus import Bus, room_topic
from tests.standins import connect_grounds

ROOM = {"id": 1, "code": "ABCD"}


def transfer():
    """The coach handing the shout to the captain over A2A."""
    return {"author": "ManagerAgent", "actions": {"transferToAgent": "team_captain"}}


def quip(role, words):
    return {"author": f"{role.capitalize()}Specialist",
            "content": {"parts": [{"text": words}]}}


def huddle(**lines):
    body = json.dumps({"status": "Tactics executed", "huddle": lines})
    return {"author": "SynthesisCaptain", "content": {"parts": [{"text": body}]}}


FULL_HUDDLE = huddle(defender="Holding the line.", midfielder="Winning it back.",
                     forward="Going for goal!", goalkeeper="Nothing gets past.")


def scripted(*events):
    """A squad that answers with these events and then stops."""
    async def run(text, state):
        for event in events:
            yield event
    return run


async def until(subscription, kind, budget=5):
    """Everything the room hears up to and including a message of this kind."""
    heard = []
    async with asyncio.timeout(budget):
        while True:
            message = await anext(subscription)
            heard.append(message)
            if message["type"] == kind:
                return heard


def kinds(heard):
    return [message["type"] for message in heard]


@pytest.fixture
def bus():
    return Bus()


@pytest.fixture
def listening(bus):
    with bus.subscribe(room_topic(ROOM["code"]), maxsize=256) as subscription:
        yield subscription


async def test_the_relay_lights_the_trunk_before_it_lights_a_branch(bus, listening):
    # The wiring diagram is the point: a manager watching it should be able to
    # tell where in the chain their words have got to.
    running = chain.Chain(bus, run=scripted(transfer(), quip("forward", "Going for goal!"),
                                            FULL_HUDDLE))
    running.submit(ROOM, "blue", 7, "get after them", "Alex Rivera")
    heard = await until(listening, "relay.huddle")

    trunk = [message for message in heard if message["type"] != "relay.specialist"]
    assert kinds(trunk) == ["relay.coach", "relay.coach", "relay.captain",
                            "relay.captain", "relay.huddle"]
    assert [message["state"] for message in trunk] == [
        "thinking", "done", "thinking", "done", "done"]
    assert all(message["seq"] == 7 and message["team"] == "blue" for message in heard)


async def test_a_specialist_speaks_for_itself_as_soon_as_it_answers(bus, listening):
    running = chain.Chain(bus, run=scripted(transfer(), quip("goalkeeper", "Nothing gets past."),
                                            FULL_HUDDLE))
    running.submit(ROOM, "blue", 1, "stay back", "Alex Rivera")
    heard = await until(listening, "relay.huddle")

    spoke = [message for message in heard
             if message["type"] == "relay.specialist" and message["state"] == "done"]
    assert [(message["role"], message["text"]) for message in spoke] == [
        ("goalkeeper", "Nothing gets past.")]


async def test_a_specialist_that_never_answers_goes_grey_rather_than_pending(bus, listening):
    # A branch left spinning at full time reads as a broken page. The huddle
    # completes on three, so silence has to be drawn as silence.
    running = chain.Chain(bus, run=scripted(transfer(),
                                            quip("forward", "Going for goal!"),
                                            huddle(forward="Going for goal!")))
    running.submit(ROOM, "blue", 1, "shoot on sight", "Alex Rivera")
    heard = await until(listening, "relay.huddle")

    quiet = {message["role"] for message in heard
             if message["type"] == "relay.specialist" and message["state"] == "missing"}
    assert quiet == {"defender", "midfielder", "goalkeeper"}


async def test_a_specialist_the_captain_quoted_counts_as_having_answered(bus, listening):
    # The captain's JSON is the only sighting of a specialist whose own event
    # was lost, and it is proof enough that the player was there.
    running = chain.Chain(bus, run=scripted(transfer(), FULL_HUDDLE))
    running.submit(ROOM, "blue", 1, "press", "Alex Rivera")
    heard = await until(listening, "relay.huddle")

    assert not [message for message in heard if message.get("state") == "missing"]
    assert heard[-1]["huddle"]["defender"] == "Holding the line."


async def test_a_chain_that_runs_past_its_budget_is_cut_off_and_says_so(bus, listening):
    async def dawdle(text, state):
        yield transfer()
        await asyncio.sleep(30)

    running = chain.Chain(bus, budget=0.05, run=dawdle)
    running.submit(ROOM, "blue", 1, "hold the ball", "Alex Rivera")
    heard = await until(listening, "relay.huddle")

    trouble = [message for message in heard if message["type"] == "relay.trouble"]
    assert "time" in trouble[0]["text"]
    assert heard[-1]["state"] == "failed"


async def test_a_coach_nobody_can_reach_is_reported_in_the_managers_words(bus, listening):
    async def refuse(text, state):
        raise coach.Unreachable("the coach at http://127.0.0.1:8000 did not answer")
        yield  # pragma: no cover - makes this an async generator

    running = chain.Chain(bus, run=refuse)
    running.submit(ROOM, "blue", 1, "press high", "Alex Rivera")
    heard = await until(listening, "relay.huddle")

    assert heard[1]["type"] == "relay.trouble"
    assert "did not answer" in heard[1]["text"]


async def test_a_frame_the_coach_could_not_finish_does_not_lose_the_squad(bus, listening):
    running = chain.Chain(bus, run=scripted({"errorMessage": "MALFORMED_FUNCTION_CALL"},
                                            quip("defender", "Holding the line."),
                                            FULL_HUDDLE))
    running.submit(ROOM, "blue", 1, "sit deep", "Alex Rivera")
    heard = await until(listening, "relay.huddle")

    assert "MALFORMED_FUNCTION_CALL" in heard[1]["text"]
    assert heard[-1]["state"] == "done"


async def test_the_room_and_the_dugout_reach_the_agents_in_session_state(bus):
    # `update_profile` reads these four keys. Without them a specialist writes
    # to the workshop room, which is another match entirely.
    seen = {}

    async def remember(text, state):
        seen.update(state)
        seen["said"] = text
        yield FULL_HUDDLE

    running = chain.Chain(bus, run=remember)
    with bus.subscribe(room_topic(ROOM["code"])) as subscription:
        running.submit(ROOM, "red", 4, "get it wide", "Sam Okafor")
        await until(subscription, "relay.huddle")

    assert seen["room_code"] == "ABCD"
    assert seen["team"] == "red"
    assert seen["actor"] == "Sam Okafor"
    assert seen["reason"] == "get it wide"
    assert seen["said"] == "get it wide"


async def test_the_dugout_names_the_shout_it_is_carrying(bus, listening):
    # A specialist writes attributes through the same route a manager uses and
    # cannot say which instruction it is acting on. This is how the arena knows.
    seen = []

    async def look(text, state):
        seen.append(running.caused_by(ROOM["id"], "blue"))
        yield FULL_HUDDLE

    running = chain.Chain(bus, run=look)
    assert running.caused_by(ROOM["id"], "blue") is None
    running.submit(ROOM, "blue", 12, "press", "Alex Rivera")
    await until(listening, "relay.huddle")

    assert seen == [12]
    assert running.caused_by(ROOM["id"], "blue") is None


async def test_a_second_shout_waits_behind_the_first_rather_than_racing_it(bus, listening):
    # Two chains moving the same squad at once would leave the profiles in
    # whichever order the two language models happened to finish in.
    order = []

    async def note(text, state):
        order.append(f"start {text}")
        await asyncio.sleep(0)
        order.append(f"end {text}")
        yield FULL_HUDDLE

    running = chain.Chain(bus, run=note)
    assert running.submit(ROOM, "blue", 1, "first", "Alex Rivera") == 0
    assert running.submit(ROOM, "blue", 2, "second", "Alex Rivera") == 1
    await until(listening, "relay.huddle")
    await until(listening, "relay.huddle")

    assert order == ["start first", "end first", "start second", "end second"]


async def test_a_third_shout_is_refused_out_loud_rather_than_dropped(bus):
    parked = asyncio.Event()

    async def park(text, state):
        await parked.wait()
        yield FULL_HUDDLE

    running = chain.Chain(bus, run=park)
    running.submit(ROOM, "blue", 1, "first", "Alex Rivera")
    running.submit(ROOM, "blue", 2, "second", "Alex Rivera")
    assert running.has_room(ROOM["id"], "blue") is False
    with pytest.raises(chain.Busy):
        running.submit(ROOM, "blue", 3, "third", "Alex Rivera")

    parked.set()
    await running.close()


async def test_the_other_dugout_is_not_held_up_by_this_one(bus):
    parked = asyncio.Event()

    async def park(text, state):
        await parked.wait()
        yield FULL_HUDDLE

    running = chain.Chain(bus, run=park)
    running.submit(ROOM, "blue", 1, "first", "Alex Rivera")
    running.submit(ROOM, "blue", 2, "second", "Alex Rivera")
    assert running.has_room(ROOM["id"], "red") is True

    parked.set()
    await running.close()


async def test_a_manager_held_by_the_venue_limit_is_told_their_place(bus):
    # The Gemini quota belongs to the venue, not to a room. Somebody waiting on
    # it should see a queue rather than a spinner they cannot explain.
    other = {"id": 2, "code": "EFGH"}
    parked = asyncio.Event()

    async def park(text, state):
        await parked.wait()
        yield FULL_HUDDLE

    running = chain.Chain(bus, limit=1, run=park)
    with bus.subscribe(room_topic(other["code"])) as elsewhere:
        running.submit(ROOM, "blue", 1, "first", "Alex Rivera")
        await asyncio.sleep(0)
        running.submit(other, "blue", 1, "second", "Sam Okafor")
        held = await until(elsewhere, "relay.waiting")

    assert held[-1]["ahead"] == 1
    parked.set()
    await running.close()


async def test_closing_the_arena_drops_a_chain_still_talking(bus):
    async def park(text, state):
        await asyncio.Event().wait()
        yield FULL_HUDDLE

    running = chain.Chain(bus, run=park)
    running.submit(ROOM, "blue", 1, "first", "Alex Rivera")
    await asyncio.sleep(0)
    await running.close()

    assert running.has_room(ROOM["id"], "blue") is True


# ── Through the arena, as a phone reaches it ────────────────────────────────

SERVICE = "test-service-token"


async def seated(arena, mode="solo"):
    """Open a room, sit Alex in the blue dugout and kick off. Returns the code.

    A pitch first, because kicking off acquires one now. Nothing below reads
    the assignment - these tests are about what the dugout says, not about who
    is simulating - so the stand-in is enough.
    """
    connect_grounds(arena.app)
    await arena.post("/api/players",
                     json={"display_name": "Alex Rivera", "email": "alex@example.com"})
    code = (await arena.post("/api/rooms", json={"mode": mode})).json()["code"]
    await arena.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    await arena.post(f"/api/rooms/{code}/seats/blue/ready", json={"ready": True})
    await arena.post(f"/api/rooms/{code}/start")
    return code


def logged(arena, code, kind=None):
    connection = arena.app.state.conn
    entries = rooms.events(connection, rooms.by_code(connection, code)["id"])
    return [entry for entry in entries if kind is None or entry["kind"] == kind]


async def test_words_typed_into_the_box_reach_the_log_as_a_shout(arena):
    arena.app.state.chain._run = scripted(transfer(), FULL_HUDDLE)
    code = await seated(arena)

    reply = await arena.post(f"/api/rooms/{code}/shout", json={"text": "  press  them   high "})
    assert reply.status_code == 200
    assert reply.json()["ahead"] == 0

    said = logged(arena, code, "shout.sent")
    assert len(said) == 1
    # A phone keyboard's spacing is not part of the instruction.
    assert said[0]["payload"]["text"] == "press them high"
    assert said[0]["payload"]["preset"] is None
    assert said[0]["payload"]["actor"] == "Alex Rivera"


async def test_every_attribute_a_chain_moves_names_the_shout_behind_it(arena, monkeypatch):
    # Scoring pays for a shout that led to a goal, so it has to be able to walk
    # from the goal back to the words. The specialists write through the same
    # route a manager does and cannot say which shout they are answering.
    import app as arena_app
    monkeypatch.setattr(arena_app, "SERVICE_TOKEN", SERVICE)

    async def specialist_writes(text, state):
        yield transfer()
        await arena.patch(
            f"/api/rooms/{state['room_code']}/teams/{state['team']}/profiles/forward",
            json={"changes": {"aggression": 0.9}, "actor": "ForwardSpecialist",
                  "reason": text},
            headers={"X-Arena-Service": SERVICE})
        yield quip("forward", "Going for goal!")
        yield FULL_HUDDLE

    arena.app.state.chain._run = specialist_writes
    code = await seated(arena)
    seq = (await arena.post(f"/api/rooms/{code}/shout",
                            json={"text": "shoot on sight"})).json()["seq"]

    async with asyncio.timeout(5):
        while not [entry for entry in logged(arena, code, "profile.patch")
                   if entry["payload"].get("shout_seq") == seq]:
            await asyncio.sleep(0.01)

    caused = [entry for entry in logged(arena, code, "profile.patch")
              if entry["payload"].get("shout_seq") == seq]
    assert [entry["payload"]["role"] for entry in caused] == ["forward"]
    assert caused[0]["payload"]["actor"] == "ForwardSpecialist"
    assert caused[0]["payload"]["reason"] == "shoot on sight"


async def test_a_manager_who_will_not_stop_shouting_is_told_so(arena):
    parked = asyncio.Event()

    async def park(text, state):
        await parked.wait()
        yield FULL_HUDDLE

    arena.app.state.chain._run = park
    code = await seated(arena)
    for words in ("first", "second"):
        assert (await arena.post(f"/api/rooms/{code}/shout",
                                 json={"text": words})).status_code == 200
    refused = await arena.post(f"/api/rooms/{code}/shout", json={"text": "third"})

    assert refused.status_code == 429
    # Refused before anything was written: two shouts said, two shouts logged.
    assert len(logged(arena, code, "shout.sent")) == 2
    parked.set()


async def test_the_second_shout_tells_the_manager_it_is_waiting(arena):
    parked = asyncio.Event()

    async def park(text, state):
        await parked.wait()
        yield FULL_HUDDLE

    arena.app.state.chain._run = park
    code = await seated(arena)
    await arena.post(f"/api/rooms/{code}/shout", json={"text": "first"})
    reply = await arena.post(f"/api/rooms/{code}/shout", json={"text": "second"})

    assert reply.json()["ahead"] == 1
    parked.set()


async def test_a_shout_that_is_both_a_chip_and_words_is_refused(arena):
    code = await seated(arena)
    both = await arena.post(f"/api/rooms/{code}/shout",
                            json={"preset": "press high", "text": "press high"})
    assert both.status_code == 422
    assert not logged(arena, code, "shout.sent")


async def test_a_shout_that_is_neither_is_refused(arena):
    code = await seated(arena)
    assert (await arena.post(f"/api/rooms/{code}/shout", json={})).status_code == 422


async def test_a_shout_of_nothing_but_spaces_is_refused(arena):
    code = await seated(arena)
    assert (await arena.post(f"/api/rooms/{code}/shout",
                             json={"text": "   "})).status_code == 422


async def test_the_phone_hears_its_own_words_before_the_squad_answers(arena):
    # The whole reason the words are logged before the chain starts: a manager
    # should never wonder whether the arena heard them.
    arena.app.state.chain._run = scripted(transfer(), FULL_HUDDLE)
    code = await seated(arena)
    reply = await arena.post(f"/api/rooms/{code}/shout", json={"text": "get after them"})

    said = logged(arena, code, "shout.sent")[0]
    assert reply.json()["seq"] == said["seq"]
    assert said["seq"] < max(entry["seq"] for entry in logged(arena, code)) + 1
