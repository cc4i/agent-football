"""Per-room, per-team profiles. Seeding and reading."""

import pytest

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


def test_a_valid_patch_moves_only_what_it_names(conn):
    room = rooms.create_room(conn, "solo")
    before = attributes.baseline_for("defender")
    result = profiles.patch(conn, room["id"], "blue", "defender", {"aggression": 0.2})
    assert result["attributes"]["aggression"] == 0.2
    assert result["attributes"]["speed"] == before["speed"]


def test_a_patch_reports_only_what_actually_changed(conn):
    # A coach that re-sends the same value should not light up the viewers.
    room = rooms.create_room(conn, "solo")
    unchanged = attributes.baseline_for("defender")["speed"]
    result = profiles.patch(conn, room["id"], "blue", "defender",
                            {"aggression": 0.2, "speed": unchanged})
    assert result["changed"] == {"aggression": 0.2}


def test_a_patch_survives_being_read_back(conn):
    room = rooms.create_room(conn, "solo")
    profiles.patch(conn, room["id"], "blue", "defender", {"aggression": 0.2})
    assert profiles.read_one(conn, room["id"], "blue", "defender")["aggression"] == 0.2


def test_a_patch_to_one_dugout_leaves_the_other_alone(conn):
    room = rooms.create_room(conn, "versus")
    profiles.patch(conn, room["id"], "blue", "defender", {"aggression": 0.2})
    assert (profiles.read_one(conn, room["id"], "red", "defender")
            == attributes.baseline_for("defender"))


def test_an_out_of_range_patch_is_refused_whole(conn):
    room = rooms.create_room(conn, "solo")
    before = profiles.read_one(conn, room["id"], "blue", "defender")
    with pytest.raises(profiles.Rejected) as refusal:
        profiles.patch(conn, room["id"], "blue", "defender",
                       {"aggression": 0.2, "speed": 99})
    assert refusal.value.problems == ["speed=99 is outside 0.0 to 1.0"]
    # Nothing lands: a half-applied patch is worse than a refused one.
    assert profiles.read_one(conn, room["id"], "blue", "defender") == before


def test_patching_a_dugout_the_room_does_not_have_is_refused(conn):
    room = rooms.create_room(conn, "solo")
    with pytest.raises(profiles.Rejected) as refusal:
        profiles.patch(conn, room["id"], "green", "defender", {"aggression": 0.2})
    assert refusal.value.problems == ["this room has no green defender"]


def test_an_unknown_role_never_reaches_storage(conn):
    room = rooms.create_room(conn, "solo")
    with pytest.raises(profiles.Rejected) as refusal:
        profiles.patch(conn, room["id"], "blue", "../defender", {})
    assert "unknown role" in refusal.value.problems[0]
