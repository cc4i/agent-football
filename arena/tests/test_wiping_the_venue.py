# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Emptying the venue, and the three ways that could go wrong quietly.

A workshop leaves a database behind it: managers, rooms, event logs, standings.
Between events that is history worth keeping and after a week of rehearsals it
is a board with nothing human on the first page. `tidy_rehearsals.py` takes the
rehearsals out by their email domain; this takes everything.

Which makes it the most destructive thing in the repository, so what these
tests are about is not that TRUNCATE empties a table. They are about the three
things a wipe can get wrong without saying so: leaving a table behind because
somebody added one, coming back without the workshop room the dugout needs, and
running while a match is being played on it.
"""

import importlib.util
import re

import psycopg
import pytest

import codes
import db
import rooms
from tests.test_images import ROOT

WIPE = ROOT / "deploy" / "wipe_the_venue.py"


def load_the_script():
    """`deploy/wipe_the_venue.py`, imported by path.

    It is not on any import path: it ships as a file the Cloud Run job base64s
    into a `python -c`, exactly as `tidy_rehearsals.py` does. Loading it by
    location is how a test gets at the same code the job runs rather than a
    copy of it.
    """
    spec = importlib.util.spec_from_file_location("wipe_the_venue", WIPE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Loaded once. Loading it per call gives every call its own module object, and
# with it its own `SomebodyIsPlaying` -- so `pytest.raises` would be watching
# for a class the code under test cannot raise, and the guard's test would fail
# while the guard worked.
wipe_the_venue = load_the_script()


def a_venue_with_something_in_it(conn, still_playing=False):
    """A manager, a room they played, and the log behind it.

    Finished by default, because a venue worth clearing is a venue of history.
    `still_playing` leaves the whistle unblown, which is what the guard is for.
    """
    player_id = rooms.upsert_player(conn, "Alex Rivera", "alex@example.com", "salt")
    room = rooms.create_room(conn, "solo")
    rooms.take_seat(conn, room["id"], "blue", player_id, "high press")
    rooms.set_ready(conn, room["id"], "blue", True)
    rooms.append_event(conn, room["id"], "kickoff", {})
    rooms.start_match(conn, room["id"])
    if not still_playing:
        rooms.finish_match(conn, room["id"])
    return player_id, room


def counts(conn):
    return {table: conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
            for table in db.TABLES}


def test_the_wipe_covers_every_table_the_schema_creates():
    """The one that catches the next person rather than this one.

    `db.TABLES` is what the wipe empties and what the suite truncates between
    tests. A table added to SCHEMA and not to that tuple survives both, so a
    wiped venue would come back carrying rows from the last one and the suite
    would start leaking state between tests -- neither of which announces
    itself.
    """
    created = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", db.SCHEMA))
    assert created == set(db.TABLES), (
        f"the schema creates {sorted(created - set(db.TABLES))} that nothing wipes, "
        f"and db.TABLES names {sorted(set(db.TABLES) - created)} that do not exist")


def test_a_venue_with_a_workshop_in_it_comes_back_with_only_that(conn):
    a_venue_with_something_in_it(conn)
    rooms.create_room(conn, "solo", codes.WORKSHOP)
    assert counts(conn)["player"] == 1

    wipe_the_venue.wipe(conn, apply=True)

    after = counts(conn)
    assert after["player"] == 0
    assert after["event"] == 0
    assert after["seat"] == 0
    assert after["result"] == 0
    # The workshop and its seeded profiles, which is what a fresh boot has.
    assert after["room"] == 1
    assert rooms.by_code(conn, codes.WORKSHOP)["status"] == "lobby"


def test_the_workshop_is_put_back_because_only_a_boot_makes_one(conn):
    """`lifespan` creates it if it is missing, and only there.

    So a wipe that took it out would leave the dugout's five stages with no room
    to run in until somebody thought to restart the arena, and nothing on the
    way would say that is what had happened.
    """
    rooms.create_room(conn, "solo", codes.WORKSHOP)
    wipe_the_venue.wipe(conn, apply=True)
    assert rooms.by_code(conn, codes.WORKSHOP) is not None


def test_the_next_manager_through_the_door_is_id_one_again(conn):
    # "From the beginning" rather than "empty": a venue whose first manager is
    # player 813 is one that remembers the last workshop.
    a_venue_with_something_in_it(conn)
    wipe_the_venue.wipe(conn, apply=True)
    again = rooms.upsert_player(conn, "Sam Okafor", "sam@example.com", "salt")
    assert again == 1


def test_a_dry_run_leaves_the_venue_exactly_as_it_was(conn):
    """The default, and the same contract `tidy_rehearsals.py` already has.

    One habit rather than two: the first run against a venue anybody cares
    about is a preview, and the flag is what says otherwise.
    """
    a_venue_with_something_in_it(conn)
    before = counts(conn)
    wipe_the_venue.wipe(conn, apply=False)
    assert counts(conn) == before


def test_it_refuses_while_a_match_is_being_played(conn):
    """A live room's rows vanishing is a crashed socket per match.

    The grounds is simulating it, and `_handle_from_host` reads the room on the
    way in: the row it gets back would be None, and the line after that asks it
    for a token.
    """
    a_venue_with_something_in_it(conn, still_playing=True)
    with pytest.raises(wipe_the_venue.SomebodyIsPlaying):
        wipe_the_venue.wipe(conn, apply=True)
    assert counts(conn)["room"] == 1


def test_a_venue_that_is_only_finished_matches_is_not_in_play(conn):
    # The guard is about matches in progress, not about history. A venue full of
    # finished rooms is the ordinary thing to want to clear.
    a_venue_with_something_in_it(conn)
    wipe_the_venue.wipe(conn, apply=True)
    assert counts(conn)["room"] == 1            # the workshop, put back


def test_it_can_be_told_to_wipe_a_venue_mid_match_anyway(conn):
    a_venue_with_something_in_it(conn, still_playing=True)
    wipe_the_venue.wipe(conn, apply=True, allow_live=True)
    assert counts(conn)["player"] == 0


def test_it_says_what_it_is_about_to_take(conn, capsys):
    # A wipe that printed nothing would be one nobody could check before
    # committing to it, which is the whole point of the dry run.
    a_venue_with_something_in_it(conn)
    wipe_the_venue.wipe(conn, apply=False)
    said = capsys.readouterr().out
    assert "player" in said
    assert "rolled back" in said


def test_importing_it_does_not_wipe_anything(dsn):
    """It is a script the job base64s into a `python -c`, so it has a guard.

    `tidy_rehearsals.py` has none and does its work at import, which is fine for
    something only ever exec'd. This one is imported by the tests above, and a
    module that emptied the venue on import would be a trap for whoever reads
    it next.
    """
    connection = db.connect(dsn)
    db.init_db(connection)
    rooms.upsert_player(connection, "Alex Rivera", "alex@example.com", "salt")
    load_the_script()
    assert connection.execute("SELECT count(*) AS n FROM player").fetchone()["n"] == 1
    connection.close()
