"""A philosophy is a named profile patch, applied to all four roles at kick-off."""

import pytest

import attributes
import philosophies
import profiles
import rooms


def test_every_philosophy_the_join_form_offers_has_a_patch():
    # The form and the patches are two files; a name in one and not the other
    # would seat a manager whose choice quietly did nothing.
    assert set(rooms.PHILOSOPHIES) == set(philosophies.NAMES)


def test_a_philosophy_is_valid_for_every_role_it_will_be_applied_to():
    for name in philosophies.NAMES:
        changes = philosophies.changes_for(name)
        assert changes, f"{name} moves nothing"
        for role in attributes.ROLES:
            assert attributes.validate(role, changes) == [], f"{name} on the {role}"


def test_an_unknown_philosophy_is_refused_by_name():
    with pytest.raises(philosophies.Unknown) as refusal:
        philosophies.changes_for("gegenpressing")
    assert "gegenpressing" in str(refusal.value)


def test_the_returned_changes_cannot_be_edited_by_the_caller():
    first = philosophies.changes_for("high press")
    first["speed"] = 0.01
    assert philosophies.changes_for("high press").get("speed") != 0.01


def test_applying_one_moves_all_four_roles(conn):
    room = rooms.create_room(conn, "solo")
    applied = philosophies.apply(conn, room["id"], "blue", "low block")
    assert sorted(result["role"] for result in applied) == sorted(attributes.ROLES)
    for result in applied:
        stored = profiles.read_one(conn, room["id"], "blue", result["role"])
        for key, value in philosophies.changes_for("low block").items():
            assert stored[key] == value


def test_applying_one_leaves_the_other_dugout_alone(conn):
    room = rooms.create_room(conn, "versus")
    philosophies.apply(conn, room["id"], "blue", "low block")
    red = profiles.read_one(conn, room["id"], "red", "defender")
    assert red == attributes.baseline_for("defender")


def test_applying_one_reports_only_what_actually_moved(conn):
    room = rooms.create_room(conn, "solo")
    philosophies.apply(conn, room["id"], "blue", "high press")
    again = philosophies.apply(conn, room["id"], "blue", "high press")
    assert all(result["changed"] == {} for result in again)
