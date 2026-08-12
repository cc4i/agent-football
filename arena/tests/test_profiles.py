"""Per-room, per-team profiles. Seeding and reading."""

import attributes
import profiles
import rooms


def test_a_new_room_gets_a_full_set_of_profiles_for_both_dugouts(conn):
    room = rooms.create_room(conn, "versus")
    for team in ("blue", "red"):
        assert set(profiles.read_all(conn, room["id"], team)) == set(attributes.ROLES)


def test_a_solo_room_still_gets_a_red_dugout_ready_for_an_opponent(conn):
    # Seeding both is cheaper than seeding on demand, and step 5 lets a second
    # phone take the red seat in a room that opened solo.
    room = rooms.create_room(conn, "solo")
    assert set(profiles.read_all(conn, room["id"], "red")) == set(attributes.ROLES)


def test_a_seeded_profile_starts_at_the_shipped_baseline(conn):
    room = rooms.create_room(conn, "solo")
    assert (profiles.read_one(conn, room["id"], "blue", "defender")
            == attributes.baseline_for("defender"))


def test_two_rooms_do_not_share_a_defender(conn):
    first = rooms.create_room(conn, "solo")
    second = rooms.create_room(conn, "solo")
    conn.execute(
        "UPDATE profile SET attributes_json = '{\"aggression\": 0.1}' "
        "WHERE room_id = ? AND team = 'blue' AND role = 'defender'",
        (first["id"],),
    )
    conn.commit()
    assert profiles.read_one(conn, first["id"], "blue", "defender") == {"aggression": 0.1}
    assert (profiles.read_one(conn, second["id"], "blue", "defender")
            == attributes.baseline_for("defender"))


def test_reading_a_dugout_that_was_never_seeded_gives_nothing(conn):
    room = rooms.create_room(conn, "solo")
    assert profiles.read_all(conn, room["id"], "green") == {}
    assert profiles.read_one(conn, room["id"], "green", "defender") is None


def test_seeding_twice_leaves_the_first_values_alone(conn):
    # init_db is safe to re-run; so is this.
    room = rooms.create_room(conn, "solo")
    profiles.patch(conn, room["id"], "blue", "defender", {"aggression": 0.2})
    profiles.seed(conn, room["id"], ("blue", "red"))
    assert profiles.read_one(conn, room["id"], "blue", "defender")["aggression"] == 0.2
