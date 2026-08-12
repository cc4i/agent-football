"""A preset is the chip on the phone: a named patch with no language model in it."""

import pytest

import attributes
import presets
import profiles
import rooms


def test_a_preset_is_valid_for_every_role_it_will_be_applied_to():
    for name in presets.NAMES:
        changes = presets.changes_for(name)
        assert changes, f"{name} moves nothing"
        for role in attributes.ROLES:
            assert attributes.validate(role, changes) == [], f"{name} is bad for {role}"


def test_an_unknown_preset_is_refused_by_name():
    with pytest.raises(presets.Unknown) as refusal:
        presets.changes_for("park the bus")
    assert "park the bus" in str(refusal.value)


def test_every_preset_carries_the_words_the_relay_will_show():
    # The phone shows the chip's label; the relay shows what the manager
    # "said", so a preset that had no phrase would post an empty shout.
    for name in presets.NAMES:
        described = presets.describe(name)
        assert described["label"].strip()
        assert described["phrase"].strip()


def test_the_catalogue_comes_back_in_the_order_the_phone_lays_it_out():
    assert [chip["name"] for chip in presets.catalogue()] == list(presets.NAMES)


def test_applying_a_preset_moves_all_four_roles(conn):
    room = rooms.create_room(conn, "solo")
    applied = presets.apply(conn, room["id"], "blue", "press high")
    assert [result["role"] for result in applied] == list(attributes.ROLES)
    for role in attributes.ROLES:
        current = profiles.read_one(conn, room["id"], "blue", role)
        for key, value in presets.changes_for("press high").items():
            assert current[key] == value


def test_a_preset_leaves_the_other_dugout_where_it_was(conn):
    room = rooms.create_room(conn, "versus")
    before = profiles.read_one(conn, room["id"], "red", "forward")
    presets.apply(conn, room["id"], "blue", "sit deep")
    assert profiles.read_one(conn, room["id"], "red", "forward") == before


def test_the_returned_changes_are_the_callers_to_keep():
    first = presets.changes_for("break wide")
    first["speed"] = 0.01
    assert presets.changes_for("break wide").get("speed") != 0.01
