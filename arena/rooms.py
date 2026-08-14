"""Rooms, seats and the moves between them.

Every function takes a connection. The rules here are the ones a reviewer
should be able to read in one sitting: who may sit down, when a match may kick
off, and which status may follow which. Nothing in this file knows about HTTP.
"""

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


def required_teams(mode):
    """The dugouts that must be filled before this mode can kick off."""
    return ("blue",) if mode == "solo" else TEAMS


def create_player(conn, display_name, email, salt):
    """Insert or find a player, keyed on the hashed email. Returns the id."""
    email_hash = identity.hash_email(email, salt)
    # A repeat player keeps one row so the board keeps one entry for them, but
    # they may well have typed a different name this time. One statement rather
    # than a look and then a write: a rollout runs two instances at once, and a
    # join arriving at both of them is an ordinary Saturday, not an edge case.
    row = conn.execute(
        "INSERT INTO player (display_name, email_hash, email_masked, created_at) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (email_hash) DO UPDATE SET display_name = EXCLUDED.display_name "
        "RETURNING id",
        (display_name, email_hash, identity.mask_email(email), time.time()),
    ).fetchone()
    conn.commit()
    return row["id"]


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

    # Physics belongs to whoever opened the room: the big screen at a venue, the
    # phone when somebody plays alone. The token is minted here rather than at
    # kick-off because that is what makes the rule true -- the creator is the
    # only client it is ever handed to, and no player can take it from them.
    conn.execute(
        "INSERT INTO room (code, mode, status, ranked, host_client_id, created_at) "
        "VALUES (%s, %s, 'lobby', %s, %s, %s)",
        (code, mode, 0 if code == codes.WORKSHOP else 1,
         secrets.token_urlsafe(16), time.time()),
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
        # The check above is the common path and words the refusal from the
        # room's own state, but it is a read and this is a write, and in
        # between the two another instance can seat somebody. One of two people
        # reaching for the same dugout has to lose, so the loser gets the answer
        # the rules already have rather than a 500. The rollback is what lets
        # everybody else carry on: a statement the server refused leaves the
        # transaction in error, and every later one fails until it is ended.
        conn.rollback()
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


def can_kick_off(conn, room_id):
    """True when every dugout this mode needs is filled and ready."""
    room = _room(conn, room_id)
    if room["status"] != "lobby":
        return False
    ready = {row["team"] for row in conn.execute(
        "SELECT team FROM seat WHERE room_id = %s AND ready = 1", (room_id,))}
    return set(required_teams(room["mode"])) <= ready


def start_match(conn, room_id):
    """Go live. Physics already belongs to whoever opened the room."""
    if not _room(conn, room_id)["host_client_id"]:
        raise RoomError("a match needs a host")
    if not can_kick_off(conn, room_id):
        raise RoomError("not every dugout is ready")
    conn.execute("UPDATE room SET status = 'live' WHERE id = %s", (room_id,))
    conn.commit()


def finish_match(conn, room_id, status="finished"):
    if status not in ("finished", "abandoned"):
        raise RoomError("a match ends finished or abandoned")
    if _room(conn, room_id)["status"] != "live":
        raise RoomError("only a live match can end")
    conn.execute("UPDATE room SET status = %s, finished_at = %s WHERE id = %s",
                 (status, time.time(), room_id))
    conn.commit()


def append_event(conn, room_id, kind, payload, match_ms=None):
    """Add to the room's log and return the sequence number.

    Scoring is recomputed from this log and never from a submitted total, so
    it is append-only and numbered per room rather than globally.
    """
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
        "SELECT s.team, s.ready, s.philosophy, p.display_name, p.email_masked "
        "FROM seat s JOIN player p ON p.id = s.player_id "
        "WHERE s.room_id = %s ORDER BY s.team",
        (room_id,),
    ).fetchall()
    taken = {row["team"] for row in seated}
    return {
        "code": room["code"],
        "mode": room["mode"],
        "status": room["status"],
        "ranked": bool(room["ranked"]),
        "seats": {
            row["team"]: {
                "name": row["display_name"],
                "email": row["email_masked"],
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
        "SELECT s.team, s.player_id, s.philosophy, p.display_name, p.email_masked "
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
