import json
import pytest

import codes
import rooms

SALT = "test-salt"


@pytest.fixture
def alex(conn):
    return rooms.create_player(conn, "Alex Rivera", "alex@example.com", SALT)


@pytest.fixture
def sam(conn):
    return rooms.create_player(conn, "Sam Okafor", "sam@example.com", SALT)


def live_solo(conn, player_id):
    """A solo room already kicked off, with `phone-7` holding physics."""
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", player_id, "high press")
    rooms.set_ready(conn, room["id"], "blue", True)
    rooms.start_match(conn, room["id"], "phone-7")
    return room


def test_a_new_player_keeps_a_masked_email_and_no_address(conn, alex):
    row = rooms.get_player(conn, alex)
    assert row["display_name"] == "Alex Rivera"
    assert row["email_masked"] == "a***x@example.com"
    assert "alex@example.com" not in " ".join(str(value) for value in tuple(row))


def test_the_same_email_comes_back_as_the_same_player(conn, alex):
    again = rooms.create_player(conn, "Alex R", "ALEX@example.com", SALT)
    assert again == alex
    # They typed a shorter name this time, and the board follows the latest.
    assert rooms.get_player(conn, alex)["display_name"] == "Alex R"


def test_a_new_room_opens_in_the_lobby_with_a_typable_code(conn):
    room = rooms.create_room(conn, "solo")
    assert room["status"] == "lobby"
    assert room["mode"] == "solo"
    assert room["host_client_id"] is None
    assert room["ranked"] == 1
    assert codes.is_valid(room["code"])


def test_the_workshop_room_is_never_ranked(conn):
    room = rooms.create_room(conn, "solo", code=codes.WORKSHOP)
    assert room["code"] == codes.WORKSHOP
    assert room["ranked"] == 0


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
    seat = conn.execute("SELECT * FROM seat WHERE room_id = ?", (room["id"],)).fetchone()
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


def test_an_unknown_philosophy_is_refused(conn, alex):
    room = rooms.create_room(conn, "solo")
    with pytest.raises(rooms.RoomError, match="philosophy must be"):
        rooms.take_seat(conn, room["id"], "blue", alex, "park the bus")


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


def test_starting_a_match_records_the_host(conn, alex):
    room = live_solo(conn, alex)
    started = rooms.by_code(conn, room["code"])
    assert started["status"] == "live"
    assert started["host_client_id"] == "phone-7"


def test_a_match_cannot_start_before_everyone_is_ready(conn, alex):
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    rooms.set_ready(conn, room["id"], "blue", True)
    with pytest.raises(rooms.RoomError, match="not every dugout is ready"):
        rooms.start_match(conn, room["id"], "phone-7")


def test_a_match_cannot_start_without_somebody_holding_physics(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "counter")
    rooms.set_ready(conn, room["id"], "blue", True)
    with pytest.raises(rooms.RoomError, match="needs a host"):
        rooms.start_match(conn, room["id"], "")


def test_a_live_match_cannot_kick_off_a_second_time(conn, alex):
    room = live_solo(conn, alex)
    with pytest.raises(rooms.RoomError, match="not every dugout is ready"):
        rooms.start_match(conn, room["id"], "phone-9")


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
    rooms.start_match(conn, second["id"], "phone-8")

    assert rooms.append_event(conn, first["id"], "kickoff", {}) == 1
    assert rooms.append_event(conn, first["id"], "goal", {"team": "blue"}) == 2
    assert rooms.append_event(conn, second["id"], "kickoff", {}) == 1


def test_an_event_payload_comes_back_the_way_it_went_in(conn, alex):
    room = live_solo(conn, alex)
    rooms.append_event(conn, room["id"], "goal",
                       {"team": "blue", "scorer": "forward"}, match_ms=27400)
    assert rooms.events(conn, room["id"]) == [
        {"seq": 1, "kind": "goal", "match_ms": 27400,
         "payload": {"team": "blue", "scorer": "forward"}}
    ]


def test_a_room_with_nothing_logged_has_an_empty_log(conn, alex):
    assert rooms.events(conn, live_solo(conn, alex)["id"]) == []


def test_a_lobby_snapshot_names_the_seat_still_open(conn, alex):
    room = rooms.create_room(conn, "versus")
    rooms.take_seat(conn, room["id"], "blue", alex, "high press")
    snapshot = rooms.snapshot(conn, room["id"])
    assert snapshot["code"] == room["code"]
    assert snapshot["status"] == "lobby"
    assert snapshot["ranked"] is True
    assert snapshot["open_seats"] == ["red"]
    assert snapshot["seats"]["blue"] == {
        "name": "Alex Rivera",
        "email": "a***x@example.com",
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
    rooms.start_match(conn, room["id"], "screen-1")

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
