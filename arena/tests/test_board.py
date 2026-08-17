"""Results written at the whistle, and the two boards they add up to."""

import pytest

import board
import rooms
import scoring

SALT = "test-salt"


@pytest.fixture
def alex(conn):
    return rooms.upsert_player(conn, "Alex Rivera", "alex@example.com", SALT)


@pytest.fixture
def sam(conn):
    return rooms.upsert_player(conn, "Sam Okafor", "sam@example.com", SALT)


@pytest.fixture
def priya(conn):
    return rooms.upsert_player(conn, "Priya Raman", "priya@example.com", SALT)


def played(conn, mode, seats, goals, shouts=()):
    """Play a whole match and score it. Returns the room, after the whistle.

    `goals` is (seconds, team) pairs and `shouts` is (seconds, team) pairs each
    of which the squad answers a moment later, which is what makes a shout
    capable of being effective.
    """
    room = rooms.create_room(conn, mode)
    for team, player_id in seats.items():
        rooms.take_seat(conn, room["id"], team, player_id, "high press")
        rooms.set_ready(conn, room["id"], team, True)
    rooms.start_match(conn, room["id"])

    rooms.append_event(conn, room["id"], "kickoff", {}, 0)
    for at, team in shouts:
        seq = rooms.append_event(conn, room["id"], "shout.sent",
                                 {"team": team, "text": "push up"}, int(at * 1000))
        rooms.append_event(conn, room["id"], "profile.patch",
                           {"team": team, "role": "forward", "shout_seq": seq,
                            "changed": {"aggression": 0.9}})
    for at, team in goals:
        rooms.append_event(conn, room["id"], "goal", {"team": team}, int(at * 1000))
    rooms.append_event(conn, room["id"], "full_time", {}, 180_000)

    rooms.finish_match(conn, room["id"])
    room = rooms.by_code(conn, room["code"])
    board.record(conn, room)
    return room


# ── Writing a result ──────────────────────────────────────────────────────

def test_a_finished_solo_match_pays_the_dugout_that_played_it(conn, alex):
    room = played(conn, "solo", {"blue": alex},
                  [(27.4, "blue"), (61.8, "red"), (112.3, "blue")])
    result = board.read(conn, room["id"])
    assert set(result) == {"blue"}
    assert result["blue"]["name"] == "Alex Rivera"
    assert result["blue"]["points"] == 1000 + 600 + 500 - 100
    assert result["blue"]["outcome"] == "won"
    assert result["blue"]["first_goal_ms"] == 27_400


def test_the_stored_breakdown_adds_up_to_the_stored_total(conn, alex):
    room = played(conn, "solo", {"blue": alex}, [(27.4, "blue")])
    result = board.read(conn, room["id"])["blue"]
    assert sum(row["points"] for row in result["breakdown"]) == result["points"]


def test_a_versus_match_pays_both_dugouts(conn, alex, sam):
    room = played(conn, "versus", {"blue": alex, "red": sam},
                  [(10, "blue"), (20, "blue"), (30, "red")])
    result = board.read(conn, room["id"])
    assert (result["blue"]["outcome"], result["red"]["outcome"]) == ("won", "lost")
    assert (result["blue"]["goals_for"], result["red"]["goals_for"]) == (2, 1)


def test_a_second_whistle_does_not_pay_anybody_twice(conn, alex):
    room = played(conn, "solo", {"blue": alex}, [(10, "blue")])
    first = board.read(conn, room["id"])
    assert board.record(conn, room) == first
    assert conn.execute("SELECT COUNT(*) AS rows FROM result").fetchone()["rows"] == 1


def test_a_match_still_being_played_has_no_result(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "high press")
    assert board.read(conn, room["id"]) == {}


def test_an_effective_shout_is_paid_for_out_of_the_log(conn, alex):
    room = played(conn, "solo", {"blue": alex},
                  goals=[(30, "blue")], shouts=[(10, "blue")])
    result = board.read(conn, room["id"])["blue"]
    assert (result["shouts"], result["effective"]) == (1, 1)
    assert any(row["label"] == "1 shout led to goals" for row in result["breakdown"])


# ── Elo ───────────────────────────────────────────────────────────────────

def test_a_solo_run_earns_nobody_a_rating(conn, alex):
    room = played(conn, "solo", {"blue": alex}, [(10, "blue")])
    assert board.read(conn, room["id"])["blue"]["rating"] is None
    assert board.rating(conn, alex) == scoring.START_RATING


def test_winning_a_duel_takes_rating_off_the_other_manager(conn, alex, sam):
    room = played(conn, "versus", {"blue": alex, "red": sam}, [(10, "blue")])
    result = board.read(conn, room["id"])
    assert result["blue"]["rating"] > scoring.START_RATING > result["red"]["rating"]
    assert result["blue"]["rating"] + result["red"]["rating"] \
        == pytest.approx(2 * scoring.START_RATING)


def test_a_second_duel_starts_from_where_the_first_left_off(conn, alex, sam):
    played(conn, "versus", {"blue": alex, "red": sam}, [(10, "blue")])
    after_one = board.rating(conn, alex)
    second = played(conn, "versus", {"blue": alex, "red": sam}, [(10, "blue")])
    assert board.read(conn, second["id"])["blue"]["rating"] > after_one


def test_a_rating_is_a_matchs_own_record_and_the_player_row_holds_none(conn, alex, sam):
    # The whole ladder can be replayed from the results that made it, which is
    # why nothing is stored on the player.
    played(conn, "versus", {"blue": alex, "red": sam}, [(10, "blue")])
    columns = {row["name"] for row in conn.execute(
        "SELECT column_name AS name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'player'")}
    assert "rating" not in columns


# ── Score attack ──────────────────────────────────────────────────────────

def test_the_solo_board_ranks_on_points(conn, alex, sam, priya):
    played(conn, "solo", {"blue": alex}, [(10, "blue")])                    # 1 - 0
    played(conn, "solo", {"blue": sam}, [(10, "blue"), (20, "blue")])       # 2 - 0
    played(conn, "solo", {"blue": priya}, [(10, "red")])                    # 0 - 1
    assert [row["name"] for row in board.solo(conn)] \
        == ["Sam Okafor", "Alex Rivera", "Priya Raman"]


def test_the_solo_board_keeps_a_managers_best_run_and_not_their_last(conn, alex):
    best = played(conn, "solo", {"blue": alex}, [(10, "blue"), (20, "blue")])
    played(conn, "solo", {"blue": alex}, [(10, "red")])
    assert [row["room"] for row in board.solo(conn)] == [best["code"]]


def test_the_solo_board_names_the_shape_a_manager_opened_with(conn, alex):
    played(conn, "solo", {"blue": alex}, [(10, "blue")])
    assert board.solo(conn)[0]["philosophy"] == "high press"


def test_the_solo_board_never_shows_an_unmasked_address(conn, alex):
    """The board carries no address, masked or otherwise. Under E1, addresses
    stopped being published on any unauthenticated endpoint; before that, this
    asserted the masked form was present."""
    played(conn, "solo", {"blue": alex}, [(10, "blue")])
    assert "email" not in board.solo(conn)[0]


def test_a_duel_is_not_on_the_solo_board(conn, alex, sam):
    played(conn, "versus", {"blue": alex, "red": sam}, [(10, "blue")])
    assert board.solo(conn) == []


# ── Head to head ──────────────────────────────────────────────────────────

def test_the_versus_board_ranks_on_wins_before_goal_difference(conn, alex, sam, priya):
    # Alex wins once by one; Priya draws twice, scoring more than anybody.
    played(conn, "versus", {"blue": alex, "red": sam}, [(10, "blue")])
    for _ in range(2):
        played(conn, "versus", {"blue": priya, "red": sam},
               [(10, "blue"), (20, "blue"), (30, "red"), (40, "red")])
    table = board.versus(conn)
    assert [row["name"] for row in table][0] == "Alex Rivera"
    assert (table[0]["won"], table[0]["difference"]) == (1, 1)


def test_the_versus_board_ranks_on_goal_difference_when_wins_are_level(conn, alex, sam, priya):
    played(conn, "versus", {"blue": alex, "red": sam}, [(10, "blue")])
    played(conn, "versus", {"blue": priya, "red": sam},
           [(10, "blue"), (20, "blue"), (30, "blue")])
    assert [row["name"] for row in board.versus(conn)][:2] == ["Priya Raman", "Alex Rivera"]


def test_the_versus_board_does_not_rank_on_rating(conn, alex, sam, priya):
    # Priya beats Sam while Sam is still at his starting rating; Alex beats him
    # afterwards, when he is worth less, and so earns less Elo for the better
    # win. The board is the record, so Alex is still above her.
    played(conn, "versus", {"blue": sam, "red": priya}, [(10, "red")])
    played(conn, "versus", {"blue": alex, "red": sam},
           [(10, "blue"), (20, "blue"), (30, "blue")])
    table = {row["name"]: row for row in board.versus(conn)}
    assert table["Priya Raman"]["rating"] > table["Alex Rivera"]["rating"]
    assert [row["name"] for row in board.versus(conn)][:2] == ["Alex Rivera", "Priya Raman"]


def test_the_versus_board_adds_up_a_managers_whole_evening(conn, alex, sam):
    played(conn, "versus", {"blue": alex, "red": sam}, [(10, "blue"), (20, "blue")])
    played(conn, "versus", {"blue": alex, "red": sam}, [(10, "red")])
    played(conn, "versus", {"blue": alex, "red": sam}, [(10, "blue"), (20, "red")])
    mine = {row["name"]: row for row in board.versus(conn)}["Alex Rivera"]
    assert (mine["played"], mine["won"], mine["drew"], mine["lost"]) == (3, 1, 1, 1)
    assert (mine["goals_for"], mine["goals_against"], mine["difference"]) == (3, 2, 1)


def test_the_versus_board_remembers_the_last_match_and_who_it_was_against(conn, alex, sam):
    played(conn, "versus", {"blue": alex, "red": sam}, [(10, "blue")])
    last = played(conn, "versus", {"blue": alex, "red": sam}, [(10, "red"), (20, "red")])
    mine = {row["name"]: row for row in board.versus(conn)}["Alex Rivera"]
    assert mine["last"] == {"outcome": "lost", "room": last["code"], "goals_for": 0,
                            "goals_against": 2, "against": "Sam Okafor"}


def test_a_solo_run_is_not_on_the_versus_board(conn, alex):
    played(conn, "solo", {"blue": alex}, [(10, "blue")])
    assert board.versus(conn) == []


# ── What a board is made of ───────────────────────────────────────────────

def test_an_unranked_room_earns_a_result_but_no_place(conn, alex):
    room = played(conn, "solo", {"blue": alex}, [(10, "blue")])
    rooms.unrank(conn, room["id"])
    assert board.read(conn, room["id"])["blue"]["points"] > 0
    assert board.solo(conn) == []
    assert board.placing(conn, rooms.by_code(conn, room["code"]), {}) == {}


def test_an_abandoned_match_is_scored_by_nobody(conn, alex):
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", alex, "high press")
    rooms.set_ready(conn, room["id"], "blue", True)
    rooms.start_match(conn, room["id"])
    rooms.append_event(conn, room["id"], "goal", {"team": "blue"}, 10_000)
    rooms.finish_match(conn, room["id"], "abandoned")
    assert board.solo(conn) == []


def test_the_header_counts_the_people_with_a_place(conn, alex, sam):
    played(conn, "solo", {"blue": alex}, [(10, "blue")])
    played(conn, "solo", {"blue": alex}, [(10, "blue")])
    played(conn, "versus", {"blue": alex, "red": sam}, [(10, "blue")])
    assert board.managers(conn) == 2


# ── Where a match leaves you ──────────────────────────────────────────────

def test_a_result_says_where_it_leaves_you_on_the_board(conn, alex, sam):
    played(conn, "solo", {"blue": sam}, [(10, "blue"), (20, "blue")])
    room = played(conn, "solo", {"blue": alex}, [(10, "blue")])
    standing = board.placing(conn, room, board.read(conn, room["id"]))
    assert standing["blue"] == {"rank": 2, "of": 2, "best": True}


def test_a_run_worse_than_your_own_best_is_not_your_best(conn, alex):
    played(conn, "solo", {"blue": alex}, [(10, "blue"), (20, "blue")])
    room = played(conn, "solo", {"blue": alex}, [(10, "blue")])
    standing = board.placing(conn, room, board.read(conn, room["id"]))
    assert standing["blue"] == {"rank": 1, "of": 1, "best": False}


def test_a_duel_says_where_both_managers_stand_and_claims_no_best_run(conn, alex, sam):
    room = played(conn, "versus", {"blue": alex, "red": sam}, [(10, "blue")])
    standing = board.placing(conn, room, board.read(conn, room["id"]))
    assert standing["blue"] == {"rank": 1, "of": 2}
    assert standing["red"] == {"rank": 2, "of": 2}


def test_the_head_of_the_board_comes_back_with_a_result(conn, alex, sam, priya):
    for player, count in ((alex, 3), (sam, 2), (priya, 1)):
        played(conn, "solo", {"blue": player}, [(at * 10, "blue") for at in range(count)])
    assert [row["name"] for row in board.top(conn, "solo")] \
        == ["Alex Rivera", "Sam Okafor", "Priya Raman"]


def test_the_head_of_the_board_is_only_ever_three(conn, alex, sam, priya):
    for player in (alex, sam, priya):
        played(conn, "solo", {"blue": player}, [(10, "blue")])
    assert len(board.top(conn, "solo")) == 3
    assert len(board.top(conn, "solo", most=2)) == 2
