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

"""Empty the venue. Every manager, every match, every result, back to nothing.

The sibling of `tidy_rehearsals.py` and the blunt one. That takes the rehearsal
managers out by the email domain they stamp; this takes everybody, and it is
the most destructive thing in the repository. Same defaults for that reason:
it counts, it prints, and it rolls back unless it is told to apply. One habit
rather than two.

Runs as a Cloud Run job on the arena image, inside the VPC, because the Cloud
SQL instance has a private address and a laptop has no route to it. On a laptop
it is just a script -- everything it needs is `ARENA_DB`.

    deploy/wipe.sh              # counts, and leaves the venue alone
    deploy/wipe.sh --apply      # and this empties it

Three things it does that a `TRUNCATE` typed by hand would not.

It empties `db.TABLES`, imported rather than retyped. That tuple is already
what the suite truncates between tests, so a table added to the schema and not
to it is a table that survives a wipe and leaks between tests, and neither of
those announces itself. `tests/test_wiping_the_venue.py` compares the two.

It restarts the identities. "From the beginning" rather than "empty": a venue
whose first manager is player 813 is one that still remembers the last
workshop, in the one place anybody would look.

And it puts the workshop room back. `app.lifespan` creates that if it is
missing and only there, so a wipe without this leaves the dugout's five stages
with no room to run in until somebody thinks to restart the arena, with nothing
anywhere saying so.

What it cannot put back is the arena's memory: the match bus, the chain's
seats, the rooms a socket is holding. None of it survives a wipe meaningfully
and most of it corrects itself -- the sweep prunes what it owns, and a socket
owns the rest -- so the job says to bounce the arena rather than doing it.
"""

import os
import sys
from pathlib import Path

import psycopg


def _reach_the_arena():
    """Make `codes`, `db` and `rooms` importable, wherever this was started.

    The Cloud Run job needs none of this: it runs `python -c` with the arena
    directory as its working directory, and `-c` puts that on the path. A
    laptop running the file directly gets the *script's* directory instead,
    which is `deploy/`, so the imports below would fail on a machine where the
    modules are one directory over.
    """
    arena = Path(__file__).resolve().parent.parent / "arena"
    if arena.is_dir() and str(arena) not in sys.path:
        sys.path.insert(0, str(arena))


class SomebodyIsPlaying(Exception):
    """There are live matches, and their rows are about to be deleted."""


def wipe(conn, apply=False, allow_live=False):
    """Count the venue, empty it, and commit only if asked. Returns the counts.

    `conn` is an open connection rather than a DSN so that a test can hand over
    its own throwaway database and read the result back on the same one.
    """
    _reach_the_arena()
    import codes
    import db
    import rooms

    counts = {table: conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
              for table in db.TABLES}
    for table, many in counts.items():
        print(f"{many:8d}  {table}")

    # Before anything is deleted, and refused rather than warned about. A live
    # room is one a grounds is simulating right now: `_handle_from_host` reads
    # the room on the way in and asks the row it gets back for a token, so the
    # row going out from under it is a crashed socket for every match in play.
    playing = conn.execute(
        "SELECT count(*) AS n FROM room WHERE status = 'live'").fetchone()["n"]
    if playing and not allow_live:
        raise SomebodyIsPlaying(
            f"{playing} match{'es are' if playing > 1 else ' is'} being played right "
            f"now. Wait for the whistle, or pass --live to take them with it.")
    if playing:
        print(f"  ({playing} live, going anyway)")

    # One statement: the foreign keys point every which way between these and
    # CASCADE with RESTART IDENTITY is the whole job. Transactional in Postgres,
    # which is what lets the dry run below be a real one rather than a guess.
    conn.execute(f"TRUNCATE {', '.join(db.TABLES)} RESTART IDENTITY CASCADE")

    if apply:
        # The workshop, exactly as `app.lifespan` would make it on the next
        # boot. Inside this branch and not above it, because `create_room`
        # commits on its own -- put before the decision, it committed the
        # TRUNCATE with it and the dry run emptied the venue it was previewing.
        rooms.create_room(conn, "solo", codes.WORKSHOP)
        conn.commit()
        print(f"emptied. {codes.WORKSHOP} is back; bounce the arena to clear its memory too")
    else:
        conn.rollback()
        print(f"rolled back - nothing was deleted. {codes.WORKSHOP} would be put back.")
        print("--apply, or WIPE_APPLY=1, to mean it")
    return counts


if __name__ == "__main__":
    # Guarded, unlike `tidy_rehearsals.py`, which does its work at import and is
    # fine because it is only ever exec'd. This one is imported by its tests,
    # and a module that emptied the venue on import would be a trap.
    #
    # The Cloud Run job reaches here too: `python -c 'exec(...)'` runs in the
    # __main__ namespace, so the same file serves the job and the laptop.
    with psycopg.connect(os.environ["ARENA_DB"], row_factory=psycopg.rows.dict_row) as conn:
        try:
            wipe(conn,
                 apply=os.environ.get("WIPE_APPLY") == "1",
                 allow_live=os.environ.get("WIPE_LIVE") == "1")
        except SomebodyIsPlaying as playing:
            print(f"refused: {playing}")
            sys.exit(1)
