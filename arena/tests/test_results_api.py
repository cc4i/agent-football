"""The whistle, the results screen and the two boards, over HTTP.

`test_board.py` proves what the numbers are. This proves a match played the way
a match is really played -- a host on a socket, a phone on the API -- ends up
with those numbers in front of the manager.
"""

import pytest

import codes
import rooms
from tests.conftest import whistle, a_win


# ── The results screen ────────────────────────────────────────────────────

def test_a_match_still_being_played_has_no_result_yet(client, live_room):
    code, _ = live_room()
    answer = client.get(f"/api/rooms/{code}/result").json()
    assert answer["status"] == "live"
    assert answer["results"] == {}
    assert answer["standing"] == {}


def test_the_whistle_scores_the_match_without_anybody_asking(client, finished):
    answer = client.get(f"/api/rooms/{finished}/result").json()
    assert answer["status"] == "finished"
    assert answer["results"]["blue"]["points"] == 1000 + 300 + 500 + 300
    assert answer["results"]["blue"]["name"] == "Alex Rivera"


def test_the_results_screen_is_told_where_the_match_leaves_you(client, finished):
    answer = client.get(f"/api/rooms/{finished}/result").json()
    assert answer["standing"]["blue"] == {"rank": 1, "of": 1, "best": True}
    assert [row["name"] for row in answer["top"]] == ["Alex Rivera"]


def test_the_breakdown_reads_as_the_screen_shows_it(client, finished):
    breakdown = client.get(f"/api/rooms/{finished}/result").json()["results"]["blue"]["breakdown"]
    assert [row["label"] for row in breakdown] == [
        "Won the match", "1 goal", "First goal at 0:27", "Nothing conceded",
        "Clean sheet", "No shout led to a goal",
    ]


def test_asking_twice_gets_the_same_total(client, finished):
    first = client.get(f"/api/rooms/{finished}/result").json()
    assert client.get(f"/api/rooms/{finished}/result").json() == first


def test_the_result_of_a_room_that_does_not_exist_is_a_404(client):
    assert client.get("/api/rooms/ZZZZ/result").status_code == 404


def test_a_result_never_carries_an_unmasked_address(client, finished, phones):
    body = client.get(f"/api/rooms/{finished}/result").text
    assert "alex@example.com" not in body
    assert "a***x@example.com" in body


# ── Both boards ───────────────────────────────────────────────────────────

def test_the_board_is_empty_before_anybody_has_played(client):
    assert client.get("/api/board").json() == {"solo": [], "versus": [], "managers": 0}


def test_a_finished_run_is_on_the_solo_board(client, finished):
    board = client.get("/api/board").json()
    assert [row["name"] for row in board["solo"]] == ["Alex Rivera"]
    assert board["versus"] == []
    assert board["managers"] == 1


def test_the_board_ranks_the_better_run_first(client, conn, live_room, phones):
    first, first_host = live_room()
    whistle(client, first, first_host, a_win())

    phones.join("Sam Okafor", "sam@example.com")
    opened = client.post("/api/rooms", json={"mode": "solo"}).json()
    second = opened["code"]
    client.post(f"/api/rooms/{second}/seats/blue", json={"philosophy": "counter"})
    client.post(f"/api/rooms/{second}/seats/blue/ready", json={"ready": True})
    client.post(f"/api/rooms/{second}/start")
    whistle(client, second, rooms.by_code(conn, second)["host_client_id"],
            [("kickoff", 0, {}),
             ("goal", 20_000, {"team": "blue"}),
             ("goal", 40_000, {"team": "blue"}),
             ("full_time", 180_000, {})])

    assert [row["name"] for row in client.get("/api/board").json()["solo"]] \
        == ["Sam Okafor", "Alex Rivera"]


def test_the_workshop_is_not_on_the_board(client, phones):
    # It is the room the dugout tunes profiles in, with nobody in a seat, so a
    # match played there is a rehearsal and not a run.
    phones.join("Alex Rivera", "alex@example.com")
    assert client.get(f"/api/rooms/{codes.WORKSHOP}").json()["ranked"] is False
    assert client.get("/api/board").json()["solo"] == []


# ── What comes off the board ──────────────────────────────────────────────

def test_a_match_the_host_ran_fast_earns_a_breakdown_but_no_place(client, live_room):
    code, physics = live_room()
    whistle(client, code, physics, a_win(), speeds=[3.0])

    answer = client.get(f"/api/rooms/{code}/result").json()
    assert answer["ranked"] is False
    assert answer["results"]["blue"]["points"] > 0
    assert answer["standing"] == {}
    assert client.get("/api/board").json()["solo"] == []


def test_putting_the_slider_back_does_not_put_the_match_back_on_the_board(client, live_room):
    code, physics = live_room()
    whistle(client, code, physics, a_win(), speeds=[3.0, 1.0, 1.0])
    assert client.get(f"/api/rooms/{code}").json()["ranked"] is False


def test_a_match_played_at_one_speed_stays_ranked(client, live_room):
    code, physics = live_room()
    whistle(client, code, physics, a_win(), speeds=[1.0, 1, 1.0])
    assert client.get(f"/api/rooms/{code}").json()["ranked"] is True


def test_a_host_that_reports_no_speed_at_all_is_taken_at_one(client, live_room):
    # Nothing about the pitch is required to report a speed, and a host that
    # says nothing has said nothing suspicious.
    code, physics = live_room()
    whistle(client, code, physics, a_win(), speeds=[])
    assert client.get(f"/api/rooms/{code}").json()["ranked"] is True


def test_nonsense_where_the_speed_goes_does_not_unrank_a_match(client, live_room):
    code, physics = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as host:
        host.receive_json()
        for nonsense in ("3.0", True, None, [3], {"x": 1}):
            host.send_json({"type": "host.state", "payload": {"speed": nonsense}})
        host.send_json({"type": "host.event", "kind": "full_time",
                        "match_ms": 180_000, "payload": {}})
        host.receive_json()
    assert client.get(f"/api/rooms/{code}").json()["ranked"] is True


def test_an_abandoned_match_closes_the_room_and_scores_nothing(client, live_room):
    code, physics = live_room()
    whistle(client, code, physics,
            [("kickoff", 0, {}),
             ("goal", 27_400, {"team": "blue"}),
             ("abandoned", 45_000, {"why": "the host went away"})])

    answer = client.get(f"/api/rooms/{code}/result").json()
    assert answer["status"] == "abandoned"
    assert answer["results"] == {}
    assert client.get("/api/board").json() == {"solo": [], "versus": [], "managers": 0}


def test_an_abandoned_room_leaves_the_wall(client, live_room):
    code, physics = live_room()
    with client.websocket_connect("/ws/wall") as wall:
        assert [entry["code"] for entry in wall.receive_json()["rooms"]] == [code]
        whistle(client, code, physics, [("abandoned", 45_000, {})])
        assert wall.receive_json() == {"type": "wall", "rooms": []}


def test_nothing_more_is_logged_after_a_match_is_abandoned(client, live_room):
    code, physics = live_room()
    whistle(client, code, physics, [("abandoned", 45_000, {})])
    with client.websocket_connect(f"/ws/rooms/{code}?client_id={physics}") as host:
        host.receive_json()
        host.send_json({"type": "host.event", "kind": "goal", "match_ms": 50_000,
                        "payload": {"team": "blue"}})
    log = client.get(f"/api/rooms/{code}/events").json()["events"]
    assert log[-1]["kind"] == "abandoned"


def test_the_whistle_tells_the_room_it_can_go_and_read_the_result(client, live_room):
    # The phone learns the match is over from the socket and then asks for the
    # result, so the snapshot has to land after the result exists.
    code, physics = live_room()
    with client.websocket_connect(f"/ws/rooms/{code}") as phone:
        phone.receive_json()
        whistle(client, code, physics, a_win())
        seen = [phone.receive_json() for _ in range(4)]
    closed = [message for message in seen if message["type"] == "room"][-1]
    assert closed["status"] == "finished"
    assert client.get(f"/api/rooms/{code}/result").json()["results"]["blue"]["points"] > 0
