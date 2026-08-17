"""Rooms, seats and the moves between them.

Every function takes a connection. The rules here are the ones a reviewer
should be able to read in one sitting: who may sit down, when a match may kick
off, and which status may follow which. Nothing in this file knows about HTTP.
"""

import hmac
import json
import secrets
import time

import psycopg

import codes
import identity
import profiles

MODES = ("solo", "versus")
STATUSES = ("lobby", "live", "finished", "abandoned")
TEAMS = ("blue", "red")

# Named profile patches applied to all four roles at kick-off. Applying them is
# step 2's job; the join form collects the choice and the seat records it.
PHILOSOPHIES = ("high press", "tiki-taka", "counter", "low block")


class RoomError(Exception):
    """A move the rules do not allow. The text is fit to show a player as-is."""


class NeedsRecoveryCode(Exception):
    """A claim without the code. Located on recovery_code, not display_name."""


def required_teams(mode):
    """The dugouts that must be filled before this mode can kick off."""
    return ("blue",) if mode == "solo" else TEAMS


def upsert_player(conn, display_name, email, salt, recovery_code="", player_id=None):
    """Insert or find a player, and give them the name they just typed.

    Which player this is, in order: the address if it names one, then the
    session the phone arrived with, then nobody and a new row. The address goes
    first even though it is the optional half of the form, because it is the
    deliberate claim of the two -- somebody typing their own address on a
    borrowed phone is asking for their own place on the board back, and a
    cookie its owner left behind must not take it from them.

    A recovery code is required whenever the address resolves to a row the
    caller's cookie does not already name. That one sentence is the whole of E1,
    and it leaves the common paths untouched: same phone playing again needs no
    code, first registration needs no code.

    A name belongs to one manager, so a name another player already holds is a
    RoomError worded for a phone rather than a second row on the board that
    nobody can tell from the first. `player_id` is a session the caller has
    already verified; a stale or absent one is simply somebody new.
    """
    display_name = identity.normalise_name(display_name)
    email_hash = identity.hash_email(email, salt) if email else None
    by_address = _player_by_email(conn, email_hash)
    if by_address is not None and by_address != player_id:
        # A claim: the address names a row this phone's cookie does not.
        if not _code_matches(conn, by_address, recovery_code):
            raise NeedsRecoveryCode()
    mine = by_address if by_address is not None else player_id

    holder = name_holder(conn, display_name)
    if holder is not None and holder["id"] != mine:
        # Worded with the name as its holder spells it rather than as this
        # caller typed it, so somebody who tried `alex rivera` is shown the
        # `Alex Rivera` that is actually on the board and can see why.
        raise RoomError(_already_managing(holder["display_name"]))

    try:
        return _write_player(conn, mine, display_name, email, email_hash)
    except psycopg.errors.UniqueViolation as clash:
        # The check above is a read and this is a write, and between the two
        # another request can take the name: two phones typing it at the same
        # moment, or one tapped twice during a rollout that has both instances
        # serving. The loser is told what the check would have told them rather
        # than handed a 500, and the rollback is what lets everybody else carry
        # on -- a refused statement leaves the transaction in error, and every
        # later one fails until it is ended.
        conn.rollback()
        if clash.diag.constraint_name != "one_player_per_name":
            raise
        raise RoomError(_already_managing(display_name)) from clash


def _already_managing(display_name):
    """What a phone is told when the name it typed is somebody else's."""
    return f"somebody at this event is already managing as {display_name} - pick another name"


def _write_player(conn, player_id, display_name, email, email_hash):
    """Insert this manager, or move the row they already have. Returns the id."""
    if player_id is None:
        # A fresh player with an address gets a code. One without an address gets
        # none, because a row that no address resolves to can never be claimed.
        code = identity.new_recovery_code() if email_hash else None
        row = conn.execute(
            "INSERT INTO player (display_name, email_hash, email_masked, recovery_code, created_at) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (display_name, email_hash, identity.mask_email(email) if email else None,
             code, time.time()),
        ).fetchone()
        conn.commit()
        return row["id"]

    # An address is added or replaced, never cleared. A manager coming back who
    # leaves the optional box empty this time has not asked to give up the
    # thing that has been keeping their one place on the board.
    if email_hash is None:
        conn.execute("UPDATE player SET display_name = %s WHERE id = %s",
                     (display_name, player_id))
    else:
        # Adding an address for the first time, or changing it. Either way, mint
        # a new code if they do not have one.
        conn.execute(
            "UPDATE player SET display_name = %s, email_hash = %s, email_masked = %s, "
            "recovery_code = COALESCE(recovery_code, %s) WHERE id = %s",
            (display_name, email_hash, identity.mask_email(email),
             identity.new_recovery_code(), player_id))
    conn.commit()
    return player_id


def _player_by_email(conn, email_hash):
    """Whoever gave this address before, or None -- including for no address."""
    if email_hash is None:
        return None
    row = conn.execute("SELECT id FROM player WHERE email_hash = %s",
                       (email_hash,)).fetchone()
    return row["id"] if row else None


def _code_matches(conn, player_id, submitted):
    """True if the submitted code matches this player's, constant-time.

    A row with a NULL code matches nothing. The comparison is on bytes using
    hmac.compare_digest, like every other secret in this codebase.
    """
    row = conn.execute("SELECT recovery_code FROM player WHERE id = %s",
                       (player_id,)).fetchone()
    if not row or row["recovery_code"] is None:
        return False
    return hmac.compare_digest(
        submitted.encode("utf-8", "surrogatepass"),
        row["recovery_code"].encode("utf-8", "surrogatepass")
    )


def name_holder(conn, display_name):
    """The player holding this name, or None if it is free.

    Matched the way `db.ONE_NAME_EACH` is built -- lower-cased, over a name
    whose whitespace has been collapsed -- so what this reports and what the
    database will accept are the same question asked twice. The row rather than
    the id, because a refusal is worded with the spelling its holder chose.
    """
    return conn.execute(
        "SELECT id, display_name FROM player WHERE lower(display_name) = lower(%s)",
        (identity.normalise_name(display_name),),
    ).fetchone()


def get_player(conn, player_id):
    return conn.execute("SELECT * FROM player WHERE id = %s", (player_id,)).fetchone()


def by_code(conn, code):
    return conn.execute("SELECT * FROM room WHERE code = %s", (code,)).fetchone()


def create_room(conn, mode, code=None):
    """Open a room in the lobby. Pass `code` only for the reserved workshop room."""
    if mode not in MODES:
        raise RoomError(f"mode must be one of {', '.join(MODES)}")
    if code is None:
        code = codes.generate(lambda candidate: by_code(conn, candidate) is not None)
    elif by_code(conn, code) is not None:
        raise RoomError(f"room {code} already exists")

    # Two secrets, because there are two claims to make and they stopped being
    # one claim when physics left the browser.
    #
    # `host_client_id` is physics. It is handed to the grounds at kick-off and
    # to nothing else, ever -- no HTTP response carries it and no browser sees
    # it. `screen_client_id` goes back to whoever opened the room and proves
    # only that: this lobby is mine to reshape, and my socket being open is why
    # it is still on every phone's list.
    #
    # Both minted here rather than at kick-off, because that is what makes the
    # rule true: neither is ever handed to a second client, so nobody can take
    # either of them from the one who has it.
    conn.execute(
        "INSERT INTO room (code, mode, status, ranked, host_client_id, "
        "screen_client_id, created_at) VALUES (%s, %s, 'lobby', %s, %s, %s, %s)",
        (code, mode, 0 if code == codes.WORKSHOP else 1,
         secrets.token_urlsafe(16), secrets.token_urlsafe(16), time.time()),
    )
    conn.commit()
    room = by_code(conn, code)
    # Both dugouts, even in a solo room: seeding is cheap, and a room that
    # opened solo can still gain a red manager later.
    profiles.seed(conn, room["id"], TEAMS)
    return room


def take_seat(conn, room_id, team, player_id, philosophy):
    """Sit a player in a dugout."""
    room = _room(conn, room_id)
    if room["status"] != "lobby":
        raise RoomError("that match has already started")
    if team not in TEAMS:
        raise RoomError(f"team must be one of {', '.join(TEAMS)}")
    if team not in required_teams(room["mode"]):
        raise RoomError("a solo room has only a blue dugout")
    if philosophy not in PHILOSOPHIES:
        raise RoomError(f"philosophy must be one of {', '.join(PHILOSOPHIES)}")
    if conn.execute("SELECT 1 FROM seat WHERE room_id = %s AND team = %s",
                    (room_id, team)).fetchone():
        raise RoomError(f"the {team} dugout is taken")
    if conn.execute("SELECT 1 FROM seat WHERE room_id = %s AND player_id = %s",
                    (room_id, player_id)).fetchone():
        raise RoomError("you already have a dugout in this match")

    try:
        conn.execute(
            "INSERT INTO seat (room_id, team, player_id, philosophy, ready, joined_at) "
            "VALUES (%s, %s, %s, %s, 0, %s)",
            (room_id, team, player_id, philosophy, time.time()),
        )
    except psycopg.errors.UniqueViolation as clash:
        # The two checks above are the common path and word their refusals from
        # the room's own state, but they are reads and this is a write, and
        # between the two another request can seat somebody: a rival instance
        # during a rollout, or the same phone tapped twice. Whichever of the two
        # rules the database caught, the loser is told the same thing the check
        # would have told them rather than handed a 500. The rollback is what
        # lets everybody else carry on: a statement the server refused leaves
        # the transaction in error, and every later one fails until it is ended.
        conn.rollback()
        if clash.diag.constraint_name == "one_dugout_per_player":
            raise RoomError("you already have a dugout in this match") from clash
        # `seat_pkey`, which is also what Postgres reports when a request breaks
        # both rules at once - the same one the checks above pick first. Named
        # here because a third unique constraint on `seat` would otherwise land
        # in this branch and tell a phone something confident and wrong.
        raise RoomError(f"the {team} dugout is taken") from clash
    conn.commit()


def set_ready(conn, room_id, team, ready):
    changed = conn.execute(
        "UPDATE seat SET ready = %s WHERE room_id = %s AND team = %s",
        (1 if ready else 0, room_id, team),
    ).rowcount
    if not changed:
        raise RoomError(f"nobody is in the {team} dugout")
    conn.commit()


def require_mode_change(conn, room_id, mode):
    """Everything that has to hold before a room may play something else.

    Split out from `set_mode` so that a manager asking a screen to turn its
    room is refused by these rules rather than by a copy of them kept
    somewhere else. A phone told "ask away" and a screen then told "no" is a
    worse answer than the phone hearing the reason first, and two copies of a
    rule are two things to keep in step. Returns the room.
    """
    if mode not in MODES:
        raise RoomError(f"mode must be one of {', '.join(MODES)}")
    room = _room(conn, room_id)
    if room["status"] != "lobby":
        raise RoomError("that match has already started")
    # A solo room has no red dugout, so becoming one would have to evict
    # whoever is sitting in this one's. The manager who walked up second is not
    # the screen's to remove; whoever wants score attack can ask them to stand.
    if mode == "solo" and conn.execute(
            "SELECT 1 FROM seat WHERE room_id = %s AND team = 'red'", (room_id,)).fetchone():
        raise RoomError("somebody is in the red dugout, and a solo room has only a blue one")
    return room


def set_mode(conn, room_id, mode):
    """Change which dugouts a room still in its lobby is going to need.

    A screen opens a room before it knows who is going to walk up to it, so the
    mode it chose then is a guess about the next person through the door.
    Changing it here rather than opening a replacement room is what keeps the
    guess cheap: the code stays the one printed on the wall, the QR beside it
    goes on pointing at the same match, and nobody halfway through the join
    form is dropped. Both dugouts have had profiles since `create_room`,
    precisely so a room can gain a red manager it did not open expecting.

    Only in the lobby. After the whistle the mode is what the match was scored
    against, and the boards are keyed on it.
    """
    room = require_mode_change(conn, room_id, mode)
    if room["mode"] == mode:
        return
    conn.execute("UPDATE room SET mode = %s WHERE id = %s", (mode, room_id))
    conn.commit()


def can_kick_off(conn, room_id):
    """True when every dugout this mode needs is filled and ready."""
    room = _room(conn, room_id)
    if room["status"] != "lobby":
        return False
    ready = {row["team"] for row in conn.execute(
        "SELECT team FROM seat WHERE room_id = %s AND ready = 1", (room_id,))}
    return set(required_teams(room["mode"])) <= ready


def require_startable(conn, room_id):
    """Everything about the room itself that has to hold before kick-off.

    Split out from `start_match` so the arena can ask before it goes looking
    for somewhere to play. A lobby with an empty dugout that was told "no pitch
    is free" would be told the wrong thing, and would have taken a pitch out of
    the venue to hear it.
    """
    if not _room(conn, room_id)["host_client_id"]:
        raise RoomError("a match needs a host")
    if not can_kick_off(conn, room_id):
        raise RoomError("not every dugout is ready")


def start_match(conn, room_id):
    """Go live. Physics belongs to whichever grounds the arena assigned."""
    require_startable(conn, room_id)
    conn.execute("UPDATE room SET status = 'live', started_at = %s WHERE id = %s",
                 (time.time(), room_id))
    conn.commit()


def finish_match(conn, room_id, status="finished"):
    if status not in ("finished", "abandoned"):
        raise RoomError("a match ends finished or abandoned")
    if _room(conn, room_id)["status"] != "live":
        raise RoomError("only a live match can end")
    conn.execute("UPDATE room SET status = %s, finished_at = %s WHERE id = %s",
                 (status, time.time(), room_id))
    conn.commit()


def close_lobby(conn, room_id):
    """Shut a room whose screen went away before anybody blew a whistle.

    Not `finish_match`, because there was no match: this room never went live
    and there is nothing to score. The status is the same word all the same,
    because "abandoned" is the honest one and the alternative is inventing a
    fourth ending that every reader of this table would then have to learn.
    """
    if _room(conn, room_id)["status"] != "lobby":
        raise RoomError("only a room still in its lobby can be closed")
    conn.execute("UPDATE room SET status = 'abandoned', finished_at = %s WHERE id = %s",
                 (time.time(), room_id))
    conn.commit()


def append_event(conn, room_id, kind, payload, match_ms=None):
    """Add to the room's log and return the sequence number.

    Scoring is recomputed from this log and never from a submitted total, so
    it is append-only and numbered per room rather than globally.

    The room row is locked first because the number comes from MAX(seq) + 1 and
    the four specialists of one shout write at the same moment. Two of them
    reading the same maximum is not a lost update, it is a UNIQUE violation and
    a 500 back to an agent that did nothing wrong. Today's single connection
    already serialises this; the lock is what makes that a property of the
    statement rather than of how the arena happens to be deployed.
    """
    conn.execute("SELECT id FROM room WHERE id = %s FOR UPDATE", (room_id,)).fetchone()
    row = conn.execute(
        "INSERT INTO event (room_id, seq, kind, payload_json, match_ms, wall_ts) "
        "SELECT %s, COALESCE(MAX(seq), 0) + 1, %s, %s, %s, %s FROM event WHERE room_id = %s "
        "RETURNING seq",
        (room_id, kind, json.dumps(payload), match_ms, time.time(), room_id),
    ).fetchone()
    conn.commit()
    return row["seq"]


def events(conn, room_id):
    """The room's whole log, oldest first, payloads decoded.

    `wall_ts` is the arena's own stamp on the entry. Scoring measures the
    window after a shout with it rather than with the host's match clock,
    because the host is not trusted for scoring and because a profile write has
    no match clock to carry.
    """
    return [
        {"seq": row["seq"], "kind": row["kind"], "match_ms": row["match_ms"],
         "wall_ts": row["wall_ts"], "payload": json.loads(row["payload_json"])}
        for row in conn.execute(
            "SELECT seq, kind, payload_json, match_ms, wall_ts FROM event "
            "WHERE room_id = %s ORDER BY seq",
            (room_id,),
        )
    ]


def snapshot(conn, room_id):
    """What a client is told about a room: over HTTP, and on socket connect."""
    room = _room(conn, room_id)
    seated = conn.execute(
        "SELECT s.team, s.ready, s.philosophy, p.display_name "
        "FROM seat s JOIN player p ON p.id = s.player_id "
        "WHERE s.room_id = %s ORDER BY s.team",
        (room_id,),
    ).fetchall()
    taken = {row["team"] for row in seated}
    return {
        "code": room["code"],
        "mode": room["mode"],
        "status": room["status"],
        # Whether anything was ever played in here, which the status cannot say
        # on its own: a room whose screen went away is "abandoned" whether it
        # was mid-match or had been sitting empty since it opened, and a dugout
        # showing 0-0 and an empty pitch for the second one is describing a
        # match that never happened. Live and finished rooms are counted in
        # regardless of the column, which is NULL on every room that predates
        # it and would otherwise re-describe an evening's history.
        "started": room["started_at"] is not None or room["status"] in ("live", "finished"),
        "ranked": bool(room["ranked"]),
        "seats": {
            row["team"]: {
                "name": row["display_name"],
                "philosophy": row["philosophy"],
                "ready": bool(row["ready"]),
            }
            for row in seated
        },
        "open_seats": [team for team in required_teams(room["mode"]) if team not in taken],
    }


def live(conn):
    """One row per live room, with both manager names, for the wall."""
    return [
        {"code": row["code"], "mode": row["mode"],
         "blue": row["blue_name"], "red": row["red_name"]}
        for row in conn.execute(
            "SELECT r.code, r.mode,"
            "       MAX(CASE WHEN s.team = 'blue' THEN p.display_name END) AS blue_name,"
            "       MAX(CASE WHEN s.team = 'red'  THEN p.display_name END) AS red_name "
            "FROM room r "
            "LEFT JOIN seat s ON s.room_id = r.id "
            "LEFT JOIN player p ON p.id = s.player_id "
            "WHERE r.status = 'live' "
            "GROUP BY r.id ORDER BY r.created_at"
        )
    ]


def open_now(conn):
    """Every room still waiting for a manager, oldest first.

    For a phone that has scanned the sheet on the wall rather than the code
    beside one screen: it has to be told what is in the building, because it
    was not pointed at anything in particular.

    Not the workshop, which sits in its lobby for the life of the deployment
    because it is where the dugout tunes profiles with nobody in a dugout seat.
    Sending somebody there would seat them in a match with no screen.
    """
    waiting = []
    for row in conn.execute(
        "SELECT r.code, r.mode,"
        "       MAX(CASE WHEN s.team = 'blue' THEN p.display_name END) AS blue_name,"
        "       MAX(CASE WHEN s.team = 'red'  THEN p.display_name END) AS red_name "
        "FROM room r "
        "LEFT JOIN seat s ON s.room_id = r.id "
        "LEFT JOIN player p ON p.id = s.player_id "
        "WHERE r.status = 'lobby' AND r.code <> %s "
        "GROUP BY r.id ORDER BY r.created_at, r.id",
        (codes.WORKSHOP,),
    ):
        taken = {team: row[f"{team}_name"] for team in TEAMS if row[f"{team}_name"]}
        # A solo room is full at one, because nobody sits in its red dugout.
        free = [team for team in required_teams(row["mode"]) if team not in taken]
        if free:
            waiting.append({"code": row["code"], "mode": row["mode"],
                            "open_seats": free, "seats": taken})
    return waiting


def current_seat(conn, player_id):
    """The dugout this manager is still sitting in, if there is one.

    Only a room that has not finished: a manager whose match ended is somebody
    with a result, not somebody with somewhere to go back to. Most recent
    first, so an afternoon of matches does not send anybody to the morning.
    """
    return conn.execute(
        "SELECT r.code, r.mode, r.status, s.team FROM seat s "
        "JOIN room r ON r.id = s.room_id "
        "WHERE s.player_id = %s AND r.status IN ('lobby', 'live') "
        "ORDER BY s.joined_at DESC, s.room_id DESC LIMIT 1",
        (player_id,),
    ).fetchone()


def live_count(conn):
    """How many matches are live, for the cap to compare against.

    Separate from `live` because the cap wants a number and `live` builds a row
    per room over two joins and a group-by to get one. The wall needs those
    names; the endpoint a flood hits hardest does not.
    """
    return conn.execute("SELECT count(*) AS live FROM room WHERE status = 'live'"
                        ).fetchone()["live"]


def heard_from(conn, room_id, when=None):
    """Record that this room's host just reported.

    Wall clock rather than a monotonic one: the value is read by whichever
    arena is sweeping, and a monotonic clock means nothing outside the process
    that took it. `when` is for tests running on a fixed clock.
    """
    conn.execute("UPDATE room SET last_heard_at = %s WHERE id = %s",
                 (time.time() if when is None else when, room_id))
    conn.commit()


def heard_from_all(conn, room_codes, when, statuses=("lobby", "live")):
    """Stamp every room named, in one statement.

    For the sweep, which vouches for every room this instance is holding a
    socket open for before it judges any of them. One UPDATE rather than one
    per room: a full venue is a hundred-odd rooms and this runs every few
    seconds for the life of the deployment, down the one shared connection.

    Rooms that have ended are left alone. Their column is deliberately stale --
    it is what says the host stopped reporting when the match did -- and a
    socket somebody left open on a finished room must not rewrite that.

    `statuses` is what the caller is entitled to vouch for. A screen may vouch
    for its lobby and not for a match, because it is not the thing playing the
    match; the grounds, which are, may vouch for either.
    """
    if not room_codes:
        return
    conn.execute(
        "UPDATE room SET last_heard_at = %s "
        "WHERE code = ANY(%s) AND status = ANY(%s)",
        (when, list(room_codes), list(statuses)),
    )
    conn.commit()


def hosted_with_liveness(conn):
    """Every room a screen should be holding, with its last report.

    Waiting rooms as well as running ones. A room is only as real as the tab
    that opened it: that tab holds the physics, and the token proving it may
    lives in the tab's sessionStorage and dies with it, so a lobby whose screen
    has closed cannot be rescued by anybody. It is a code that will never do
    anything. Left in the table it goes on being offered to every phone in the
    building, and the manager who takes the seat kicks off into nothing.

    Not the workshop, which sits in its lobby for the life of the deployment
    with no screen behind it by design.
    """
    return [
        {"id": row["id"], "code": row["code"], "status": row["status"],
         "last_heard_at": row["last_heard_at"]}
        for row in conn.execute(
            "SELECT id, code, status, last_heard_at FROM room "
            "WHERE status IN ('lobby', 'live') AND code <> %s ORDER BY code",
            (codes.WORKSHOP,))
    ]


def unrank(conn, room_id):
    """Take this match off the boards, for good.

    One direction only. A host that reported 1.5x speed for a single frame
    distorted the whole match, and letting a later 1.0x put it back would make
    the rule trivial to walk around.
    """
    conn.execute("UPDATE room SET ranked = 0 WHERE id = %s", (room_id,))
    conn.commit()


def seated(conn, room_id):
    """Who is in this room's dugouts, with the ids scoring needs."""
    return conn.execute(
        "SELECT s.team, s.player_id, s.philosophy, p.display_name "
        "FROM seat s JOIN player p ON p.id = s.player_id "
        "WHERE s.room_id = %s ORDER BY s.team",
        (room_id,),
    ).fetchall()


def seat_owner(conn, room_id, team):
    """Return the player_id in this team's dugout, or None if empty."""
    row = conn.execute(
        "SELECT player_id FROM seat WHERE room_id = %s AND team = %s", (room_id, team)
    ).fetchone()
    return row["player_id"] if row else None


def team_of(conn, room_id, player_id):
    """Which dugout this player holds in this room, or None if they hold none."""
    row = conn.execute(
        "SELECT team FROM seat WHERE room_id = %s AND player_id = %s", (room_id, player_id)
    ).fetchone()
    return row["team"] if row else None


def is_seated(conn, room_id, player_id):
    """True if this player holds a dugout in this room."""
    return team_of(conn, room_id, player_id) is not None


def _room(conn, room_id):
    room = conn.execute("SELECT * FROM room WHERE id = %s", (room_id,)).fetchone()
    if room is None:
        raise RoomError(f"there is no room {room_id}")
    return room
