import json
import time

import pytest

import codes
import rooms

SALT = "test-salt"


@pytest.fixture
def alex(conn):
    return rooms.upsert_player(conn, "Alex Rivera", "alex@example.com", SALT)


@pytest.fixture
def sam(conn):
    return rooms.upsert_player(conn, "Sam Okafor", "sam@example.com", SALT)


def live_solo(conn, player_id):
    """A solo room already kicked off, physics held by whoever opened it."""
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", player_id, "high press")
    rooms.set_ready(conn, room["id"], "blue", True)
    rooms.start_match(conn, room["id"])
    return room


def test_a_new_player_keeps_a_masked_email_and_no_address(conn, alex):
    row = rooms.get_player(conn, alex)
    assert row["display_name"] == "Alex Rivera"
    assert row["email_masked"] == "a***x@example.com"
    # The raw address must not appear in the row. dict_row iterates keys when
    # converted to tuple, so check the values explicitly.
    assert "alex@example.com" not in " ".join(str(value) for value in row.values())
    # And the recovery code must not appear in the clear either.
    if row.get("recovery_code"):
        assert row["recovery_code"] not in {"alex@example.com", "a***x@example.com"}


def test_the_same_email_comes_back_as_the_same_player(conn, alex):
    """Changed under E1: address resolves the row, authenticated with the code."""
    code = rooms.get_player(conn, alex)["recovery_code"]
    again = rooms.upsert_player(conn, "Alex R", "ALEX@example.com", SALT, recovery_code=code)
    assert again == alex
    # They typed a shorter name this time, and the board follows the latest.
    assert rooms.get_player(conn, alex)["display_name"] == "Alex R"


def test_a_player_who_gave_no_email_is_still_a_player(conn):
    anonymous = rooms.upsert_player(conn, "Taylor Quinn", "", SALT)
    row = rooms.get_player(conn, anonymous)
    assert row["display_name"] == "Taylor Quinn"
    assert row["email_hash"] is None


def test_a_name_another_player_holds_is_refused(conn, alex):
    with pytest.raises(rooms.RoomError, match="already managing as Alex Rivera"):
        rooms.upsert_player(conn, "alex rivera", "sam@example.com", SALT)
    # The refusal left nothing behind: a name clash is not half a join.
    assert conn.execute("SELECT count(*) AS n FROM player").fetchone()["n"] == 1


def test_a_session_keeps_its_own_name_across_a_second_join(conn, alex):
    assert rooms.upsert_player(conn, "Alex Rivera", "", SALT, player_id=alex) == alex


def test_a_session_may_not_take_a_name_another_player_holds(conn, alex, sam):
    with pytest.raises(rooms.RoomError, match="already managing as Alex Rivera"):
        rooms.upsert_player(conn, "Alex Rivera", "", SALT, player_id=sam)


def test_an_address_outranks_the_session_it_arrived_with(conn, alex, sam):
    """Changed under E1: now requires the recovery code."""
    # Alex on Sam's phone. The address is the deliberate claim of the two.
    code = rooms.get_player(conn, alex)["recovery_code"]
    assert rooms.upsert_player(conn, "Alex Rivera", "alex@example.com", SALT,
                               recovery_code=code, player_id=sam) == alex


def test_a_name_nobody_holds_has_no_holder(conn, alex):
    assert rooms.name_holder(conn, "Priya Raman") is None


def test_a_name_is_held_however_it_is_typed(conn, alex):
    held = rooms.name_holder(conn, "  ALEX   rivera ")
    assert held["id"] == alex
    # The spelling its holder chose, which is what a refusal is worded with.
    assert held["display_name"] == "Alex Rivera"


def test_a_new_room_opens_in_the_lobby_with_a_typable_code(conn):
    room = rooms.create_room(conn, "solo")
    assert room["status"] == "lobby"
    assert room["mode"] == "solo"
    assert room["host_client_id"], "the creator holds physics from the start"
    assert room["ranked"] == 1
    assert codes.is_valid(room["code"])


def test_the_workshop_room_is_never_ranked(conn):
    room = rooms.create_room(conn, "solo", code=codes.WORKSHOP)
    assert room["code"] == codes.WORKSHOP
    assert room["ranked"] == 0


def test_opening_a_room_gives_it_profiles(conn):
    room = rooms.create_room(conn, "versus")
    seeded = conn.execute(
        "SELECT COUNT(*) AS n FROM profile WHERE room_id = %s", (room["id"],)
    ).fetchone()["n"]
    assert seeded == 8  # four roles, two dugouts


def test_the_workshop_room_cannot_be_opened_twice(conn):
    rooms.create_room(conn, "solo", code=codes.WORKSHOP)
    with pytest.raises(rooms.RoomError, match="already exists"):
        rooms.create_room(conn, "solo", code=codes.WORKSHOP)


def test_an_unknown_mode_is_refused(conn):
    with pytest.raises(rooms.RoomError, match="mode must be"):
        rooms.create_room(conn, "battle-royale")


def test_a_solo_room_needs_only_the_blue_dugout(conn):
    assert rooms.required_teams("solo") == ("blue",)
    assert rooms.required_teams("versus") == ("blue", "red")


def test_taking_a_seat_records_the_philosophy_and_leaves_them_not_ready(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "high press")
    seat = conn.execute("SELECT * FROM seat WHERE room_id = %s", (room["id"],)).fetchone()
    assert (seat["team"], seat["player_id"]) == ("blue", alex)
    assert seat["philosophy"] == "high press"
    assert seat["ready"] == 0


def test_the_red_dugout_does_not_exist_in_a_solo_room(conn, alex):
    room = rooms.create_room(conn, "solo")
    with pytest.raises(rooms.RoomError, match="only a blue dugout"):
        rooms.take_seat(conn, room["id"], "red", alex, "counter")


def test_a_team_that_is_not_a_team_is_refused(conn, alex):
    room = rooms.create_room(conn, "versus")
    with pytest.raises(rooms.RoomError, match="team must be"):
        rooms.take_seat(conn, room["id"], "green", alex, "counter")


def test_a_taken_dugout_cannot_be_taken_again(conn, alex, sam):
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    with pytest.raises(rooms.RoomError, match="the blue dugout is taken"):
        rooms.take_seat(conn, room["id"], "blue", sam, "low block")


def test_one_player_cannot_manage_both_sides(conn, alex):
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    with pytest.raises(rooms.RoomError, match="already have a dugout"):
        rooms.take_seat(conn, room["id"], "red", alex, "counter")


def test_a_seated_player_can_be_asked_which_dugout_is_theirs(conn, alex, sam):
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "red", alex, "counter")
    assert rooms.team_of(conn, room["id"], alex) == "red"
    assert rooms.team_of(conn, room["id"], sam) is None


def test_a_dugout_in_one_room_is_not_a_dugout_in_another(conn, alex):
    mine = rooms.create_room(conn, "solo")
    theirs = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, mine["id"], "blue", alex, "counter")
    assert rooms.team_of(conn, theirs["id"], alex) is None


def test_an_unknown_philosophy_is_refused(conn, alex):
    room = rooms.create_room(conn, "solo")
    with pytest.raises(rooms.RoomError, match="philosophy must be"):
        rooms.take_seat(conn, room["id"], "blue", alex, "park the bus")


def test_a_waiting_room_can_be_turned_head_to_head_and_back(conn):
    room = rooms.create_room(conn, "solo")
    rooms.set_mode(conn, room["id"], "versus")
    assert rooms.by_code(conn, room["code"])["mode"] == "versus"
    rooms.set_mode(conn, room["id"], "solo")
    assert rooms.by_code(conn, room["code"])["mode"] == "solo"


def test_changing_the_mode_keeps_the_code_and_the_token(conn):
    room = rooms.create_room(conn, "solo")
    rooms.set_mode(conn, room["id"], "versus")
    after = rooms.by_code(conn, room["code"])
    assert after["code"] == room["code"]
    assert after["host_client_id"] == room["host_client_id"]


def test_the_manager_already_in_the_blue_dugout_keeps_their_seat(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "high press")
    rooms.set_ready(conn, room["id"], "blue", True)
    rooms.set_mode(conn, room["id"], "versus")
    assert rooms.team_of(conn, room["id"], alex) == "blue"
    # And the room now waits for the dugout it did not have a moment ago.
    assert not rooms.can_kick_off(conn, room["id"])


def test_a_room_opened_solo_can_seat_a_red_manager_once_it_is_versus(conn, alex, sam):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "high press")
    with pytest.raises(rooms.RoomError, match="only a blue dugout"):
        rooms.take_seat(conn, room["id"], "red", sam, "counter")
    rooms.set_mode(conn, room["id"], "versus")
    rooms.take_seat(conn, room["id"], "red", sam, "counter")
    assert rooms.team_of(conn, room["id"], sam) == "red"


def test_going_solo_will_not_evict_the_red_dugout(conn, alex, sam):
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "red", sam, "counter")
    with pytest.raises(rooms.RoomError, match="somebody is in the red dugout"):
        rooms.set_mode(conn, room["id"], "solo")
    assert rooms.by_code(conn, room["code"])["mode"] == "versus"
    assert rooms.team_of(conn, room["id"], sam) == "red"


def test_a_match_already_kicked_off_keeps_the_mode_it_was_scored_against(conn, alex):
    room = live_solo(conn, alex)
    with pytest.raises(rooms.RoomError, match="already started"):
        rooms.set_mode(conn, room["id"], "versus")


def test_a_mode_that_is_not_a_mode_is_refused(conn):
    room = rooms.create_room(conn, "solo")
    with pytest.raises(rooms.RoomError, match="mode must be"):
        rooms.set_mode(conn, room["id"], "penalties")


def test_setting_the_mode_a_room_already_has_is_nothing_at_all(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "high press")
    rooms.set_mode(conn, room["id"], "solo")
    assert rooms.by_code(conn, room["code"])["mode"] == "solo"
    assert rooms.team_of(conn, room["id"], alex) == "blue"


def test_a_solo_room_kicks_off_once_its_one_manager_is_ready(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "tiki-taka")
    assert not rooms.can_kick_off(conn, room["id"])
    rooms.set_ready(conn, room["id"], "blue", True)
    assert rooms.can_kick_off(conn, room["id"])


def test_a_versus_room_waits_for_both_managers(conn, alex, sam):
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "blue", alex, "high press")
    rooms.set_ready(conn, room["id"], "blue", True)
    assert not rooms.can_kick_off(conn, room["id"])
    rooms.take_seat(conn, room["id"], "red", sam, "low block")
    rooms.set_ready(conn, room["id"], "red", True)
    assert rooms.can_kick_off(conn, room["id"])


def test_a_manager_can_change_their_mind_about_being_ready(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    rooms.set_ready(conn, room["id"], "blue", True)
    rooms.set_ready(conn, room["id"], "blue", False)
    assert not rooms.can_kick_off(conn, room["id"])


def test_marking_an_empty_dugout_ready_is_an_error(conn):
    room = rooms.create_room(conn, "versus")
    with pytest.raises(rooms.RoomError, match="nobody is in the red dugout"):
        rooms.set_ready(conn, room["id"], "red", True)


def test_starting_a_match_leaves_physics_where_it_was(conn, alex):
    room = live_solo(conn, alex)
    started = rooms.by_code(conn, room["code"])
    assert started["status"] == "live"
    assert started["host_client_id"] == room["host_client_id"]


def test_two_rooms_never_share_a_physics_token(conn):
    # A shared token would let either room's host drive the other one.
    first = rooms.create_room(conn, "solo")
    second = rooms.create_room(conn, "solo")
    assert first["host_client_id"] != second["host_client_id"]


def test_a_match_cannot_start_before_everyone_is_ready(conn, alex):
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    rooms.set_ready(conn, room["id"], "blue", True)
    with pytest.raises(rooms.RoomError, match="not every dugout is ready"):
        rooms.start_match(conn, room["id"])


def test_a_match_cannot_start_without_somebody_holding_physics(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    rooms.set_ready(conn, room["id"], "blue", True)
    conn.execute("UPDATE room SET host_client_id = NULL WHERE id = %s", (room["id"],))
    with pytest.raises(rooms.RoomError, match="needs a host"):
        rooms.start_match(conn, room["id"])


def test_a_live_match_cannot_kick_off_a_second_time(conn, alex):
    room = live_solo(conn, alex)
    with pytest.raises(rooms.RoomError, match="not every dugout is ready"):
        rooms.start_match(conn, room["id"])


def test_nobody_can_sit_down_after_kick_off(conn, alex, sam):
    # The status check runs before the seat check, so a latecomer is told the
    # match started rather than that the dugout is taken.
    room = live_solo(conn, alex)
    with pytest.raises(rooms.RoomError, match="already started"):
        rooms.take_seat(conn, room["id"], "blue", sam, "counter")


def test_a_live_match_can_be_abandoned(conn, alex):
    room = live_solo(conn, alex)
    rooms.finish_match(conn, room["id"], "abandoned")
    ended = rooms.by_code(conn, room["code"])
    assert ended["status"] == "abandoned"
    assert ended["finished_at"] is not None


def test_a_match_in_the_lobby_cannot_finish(conn):
    room = rooms.create_room(conn, "solo")
    with pytest.raises(rooms.RoomError, match="only a live match"):
        rooms.finish_match(conn, room["id"])


def test_a_match_cannot_end_in_a_status_that_is_not_an_ending(conn, alex):
    room = live_solo(conn, alex)
    with pytest.raises(rooms.RoomError, match="finished or abandoned"):
        rooms.finish_match(conn, room["id"], "lobby")


def test_a_room_that_is_not_there_says_so(conn):
    assert rooms.by_code(conn, "ZZZZ") is None
    with pytest.raises(rooms.RoomError, match="there is no room"):
        rooms.take_seat(conn, 999, "blue", 1, "counter")


def test_events_are_numbered_from_one_within_each_room(conn, alex, sam):
    first = live_solo(conn, alex)
    second = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, second["id"], "blue", sam, "counter")
    rooms.set_ready(conn, second["id"], "blue", True)
    rooms.start_match(conn, second["id"])

    assert rooms.append_event(conn, first["id"], "kickoff", {}) == 1
    assert rooms.append_event(conn, first["id"], "goal", {"team": "blue"}) == 2
    assert rooms.append_event(conn, second["id"], "kickoff", {}) == 1


def test_an_event_payload_comes_back_the_way_it_went_in(conn, alex):
    room = live_solo(conn, alex)
    opened = time.time()
    rooms.append_event(conn, room["id"], "goal",
                       {"team": "blue", "scorer": "forward"}, match_ms=27400)
    [entry] = rooms.events(conn, room["id"])
    # The stamp is the one thing the caller did not supply. Scoring measures the
    # window after a shout with it, so it has to come back out.
    assert entry.pop("wall_ts") >= opened
    assert entry == {"seq": 1, "kind": "goal", "match_ms": 27400,
                     "payload": {"team": "blue", "scorer": "forward"}}


def test_a_room_with_nothing_logged_has_an_empty_log(conn, alex):
    assert rooms.events(conn, live_solo(conn, alex)["id"]) == []


def test_a_lobby_snapshot_names_the_seat_still_open(conn, alex):
    """Snapshots carry name and philosophy but not email. Under E1, snapshots
    stopped echoing addresses; before that, this asserted the masked form was
    present."""
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "blue", alex, "high press")
    snapshot = rooms.snapshot(conn, room["id"])
    assert snapshot["code"] == room["code"]
    assert snapshot["status"] == "lobby"
    assert snapshot["ranked"] is True
    assert snapshot["open_seats"] == ["red"]
    assert snapshot["seats"]["blue"] == {
        "name": "Alex Rivera",
        "philosophy": "high press",
        "ready": False,
    }


def test_a_full_solo_room_has_no_open_seats(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    assert rooms.snapshot(conn, room["id"])["open_seats"] == []


def test_a_snapshot_never_carries_an_unmasked_address(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    assert "alex@example.com" not in json.dumps(rooms.snapshot(conn, room["id"]))


def test_the_wall_lists_live_rooms_with_both_managers(conn, alex, sam):
    waiting = rooms.create_room(conn, "solo")
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "blue", alex, "high press")
    rooms.take_seat(conn, room["id"], "red", sam, "low block")
    rooms.set_ready(conn, room["id"], "blue", True)
    rooms.set_ready(conn, room["id"], "red", True)
    rooms.start_match(conn, room["id"])

    assert rooms.live(conn) == [
        {"code": room["code"], "mode": "versus", "blue": "Alex Rivera", "red": "Sam Okafor"}
    ]
    assert waiting["code"] not in [entry["code"] for entry in rooms.live(conn)]


def test_a_solo_room_on_the_wall_has_no_red_manager(conn, alex):
    # Most matches are solo, so "no dugout here" is the common case, not an edge.
    live_solo(conn, alex)
    assert rooms.live(conn)[0]["red"] is None


def test_a_finished_room_leaves_the_wall(conn, alex):
    room = live_solo(conn, alex)
    rooms.finish_match(conn, room["id"])
    assert rooms.live(conn) == []


def test_exhausted_codes_returns_503(client, monkeypatch):
    def always_exhausted(taken):
        raise codes.CodesExhausted("no free room code after 200 tries")
    monkeypatch.setattr(codes, "generate", always_exhausted)
    response = client.post("/api/rooms", json={"mode": "solo"})
    assert response.status_code == 503
    assert "no free room code" in response.text.lower()


def test_email_over_254_chars_is_refused(client):
    huge_email = "a" * 250 + "@example.com"
    response = client.post("/api/players", json={"display_name": "Test", "email": huge_email})
    assert response.status_code == 422



def test_room_codes_are_matched_case_insensitively(client, phones, monkeypatch):
    phones.join("Alex Rivera", "alex@example.com")
    # Use a fixed code with letters to test case-insensitive lookup reliably.
    import codes
    def fixed_code(taken):
        return "AB23"
    monkeypatch.setattr(codes, "generate", fixed_code)
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    assert code == "AB23"
    lowercase = code.lower()
    assert lowercase == "ab23"
    response = client.get(f"/api/rooms/{lowercase}")
    assert response.status_code == 200
    assert response.json()["code"] == code
