"""Rooms, seats and the moves between them.

Every function takes a connection. The rules here are the ones a reviewer
should be able to read in one sitting: who may sit down, when a match may kick
off, and which status may follow which. Nothing in this file knows about HTTP.
"""

import json
import time

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
    existing = conn.execute(
        "SELECT id FROM player WHERE email_hash = ?", (email_hash,)
    ).fetchone()
    if existing:
        # A repeat player keeps one row so the board keeps one entry for them,
        # but they may well have typed a different name this time.
        conn.execute("UPDATE player SET display_name = ? WHERE id = ?",
                     (display_name, existing["id"]))
        conn.commit()
        return existing["id"]

    cursor = conn.execute(
        "INSERT INTO player (display_name, email_hash, email_masked, created_at) "
        "VALUES (?, ?, ?, ?)",
        (display_name, email_hash, identity.mask_email(email), time.time()),
    )
    conn.commit()
    return cursor.lastrowid


def get_player(conn, player_id):
    return conn.execute("SELECT * FROM player WHERE id = ?", (player_id,)).fetchone()


def by_code(conn, code):
    return conn.execute("SELECT * FROM room WHERE code = ?", (code,)).fetchone()


def create_room(conn, mode, code=None):
    """Open a room in the lobby. Pass `code` only for the reserved workshop room."""
    if mode not in MODES:
        raise RoomError(f"mode must be one of {', '.join(MODES)}")
    if code is None:
        code = codes.generate(lambda candidate: by_code(conn, candidate) is not None)
    elif by_code(conn, code) is not None:
        raise RoomError(f"room {code} already exists")

    conn.execute(
        "INSERT INTO room (code, mode, status, ranked, created_at) "
        "VALUES (?, ?, 'lobby', ?, ?)",
        (code, mode, 0 if code == codes.WORKSHOP else 1, time.time()),
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
    if conn.execute("SELECT 1 FROM seat WHERE room_id = ? AND team = ?",
                    (room_id, team)).fetchone():
        raise RoomError(f"the {team} dugout is taken")
    if conn.execute("SELECT 1 FROM seat WHERE room_id = ? AND player_id = ?",
                    (room_id, player_id)).fetchone():
        raise RoomError("you already have a dugout in this match")

    conn.execute(
        "INSERT INTO seat (room_id, team, player_id, philosophy, ready, joined_at) "
        "VALUES (?, ?, ?, ?, 0, ?)",
        (room_id, team, player_id, philosophy, time.time()),
    )
    conn.commit()


def set_ready(conn, room_id, team, ready):
    changed = conn.execute(
        "UPDATE seat SET ready = ? WHERE room_id = ? AND team = ?",
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
        "SELECT team FROM seat WHERE room_id = ? AND ready = 1", (room_id,))}
    return set(required_teams(room["mode"])) <= ready


def start_match(conn, room_id, host_client_id):
    """Hand physics to exactly one client and go live."""
    if not host_client_id:
        raise RoomError("a match needs a host")
    if not can_kick_off(conn, room_id):
        raise RoomError("not every dugout is ready")
    conn.execute("UPDATE room SET status = 'live', host_client_id = ? WHERE id = ?",
                 (host_client_id, room_id))
    conn.commit()


def finish_match(conn, room_id, status="finished"):
    if status not in ("finished", "abandoned"):
        raise RoomError("a match ends finished or abandoned")
    if _room(conn, room_id)["status"] != "live":
        raise RoomError("only a live match can end")
    conn.execute("UPDATE room SET status = ?, finished_at = ? WHERE id = ?",
                 (status, time.time(), room_id))
    conn.commit()


def append_event(conn, room_id, kind, payload, match_ms=None):
    """Add to the room's log and return the sequence number.

    Scoring is recomputed from this log and never from a submitted total, so
    it is append-only and numbered per room rather than globally.
    """
    row = conn.execute(
        "INSERT INTO event (room_id, seq, kind, payload_json, match_ms, wall_ts) "
        "SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ?, ?, ? FROM event WHERE room_id = ? "
        "RETURNING seq",
        (room_id, kind, json.dumps(payload), match_ms, time.time(), room_id),
    ).fetchone()
    conn.commit()
    return row["seq"]


def events(conn, room_id):
    """The room's whole log, oldest first, payloads decoded."""
    return [
        {"seq": row["seq"], "kind": row["kind"], "match_ms": row["match_ms"],
         "payload": json.loads(row["payload_json"])}
        for row in conn.execute(
            "SELECT seq, kind, payload_json, match_ms FROM event "
            "WHERE room_id = ? ORDER BY seq",
            (room_id,),
        )
    ]


def snapshot(conn, room_id):
    """What a client is told about a room: over HTTP, and on socket connect."""
    room = _room(conn, room_id)
    seated = conn.execute(
        "SELECT s.team, s.ready, s.philosophy, p.display_name, p.email_masked "
        "FROM seat s JOIN player p ON p.id = s.player_id "
        "WHERE s.room_id = ? ORDER BY s.team",
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


def seat_owner(conn, room_id, team):
    """Return the player_id in this team's dugout, or None if empty."""
    row = conn.execute(
        "SELECT player_id FROM seat WHERE room_id = ? AND team = ?", (room_id, team)
    ).fetchone()
    return row["player_id"] if row else None


def is_seated(conn, room_id, player_id):
    """True if this player holds a dugout in this room."""
    return conn.execute(
        "SELECT 1 FROM seat WHERE room_id = ? AND player_id = ?", (room_id, player_id)
    ).fetchone() is not None


def _room(conn, room_id):
    room = conn.execute("SELECT * FROM room WHERE id = ?", (room_id,)).fetchone()
    if room is None:
        raise RoomError(f"there is no room {room_id}")
    return room
