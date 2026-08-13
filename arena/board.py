"""Results, and the two boards they add up to.

A result is written once, at the whistle, from the room's own log. Nothing a
client sends is stored as a score, and nothing here recomputes a match that has
already been scored -- a second whistle for the same room is a no-op, so a host
that reconnects and replays cannot pay somebody twice.

The two boards never merge. The README records the shipped squad as 0W-1D-7L,
so beating it and beating a person are not the same achievement.
"""

import json
import time

import rooms
import scoring

# What both boards are made of: a finished, ranked room. The workshop is not
# ranked, a match somebody ran fast is not ranked, and an abandoned match never
# reaches `finished`.
RANKED = "room.ranked = 1 AND room.status = 'finished'"


def record(conn, room):
    """Score a finished match and store one row per dugout. Returns the rows.

    Idempotent by the unique key on (room, player): if this room has already
    been scored, the stored rows come back untouched.
    """
    already = read(conn, room["id"])
    if already:
        return already

    facts = scoring.read(rooms.events(conn, room["id"]))
    seats = rooms.seated(conn, room["id"])
    ratings = _new_ratings(conn, room, seats, facts)

    now = time.time()
    for seat in seats:
        mine = facts[seat["team"]]
        scored = scoring.score_attack(mine)
        conn.execute(
            "INSERT INTO result (room_id, player_id, team, points, outcome,"
            "                    goals_for, goals_against, first_goal_ms, shouts,"
            "                    effective, rating, breakdown_json, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (room["id"], seat["player_id"], seat["team"], scored["points"],
             mine["outcome"], mine["goals_for"], mine["goals_against"],
             mine["first_goal_ms"], mine["shouts"], mine["effective"],
             ratings.get(seat["team"]), json.dumps(scored["breakdown"]), now),
        )
    conn.commit()
    return read(conn, room["id"])


def _new_ratings(conn, room, seats, facts):
    """Both managers' Elo after this match, or nothing if it was not a duel.

    A rating is a claim about one person against another, so the house side
    earns nobody one and a versus room that kicked off with an empty dugout
    earns nobody one either.
    """
    if room["mode"] != "versus" or len(seats) != len(scoring.TEAMS):
        return {}
    before = {seat["team"]: rating(conn, seat["player_id"]) for seat in seats}
    blue, red = scoring.TEAMS
    return {
        blue: scoring.rated(before[blue], before[red], facts[blue]["outcome"]),
        red: scoring.rated(before[red], before[blue], facts[red]["outcome"]),
    }


def rating(conn, player_id):
    """This player's Elo going into their next duel."""
    row = conn.execute(
        "SELECT rating FROM result WHERE player_id = ? AND rating IS NOT NULL "
        "ORDER BY computed_at DESC, id DESC LIMIT 1",
        (player_id,),
    ).fetchone()
    return row["rating"] if row else scoring.START_RATING


def read(conn, room_id):
    """One room's results, keyed by dugout. Empty until the whistle.

    This is the only read that carries the breakdown, because the results
    screen is the only place that shows one.
    """
    return {row["team"]: {**_run(row), "breakdown": json.loads(row["breakdown_json"])}
            for row in conn.execute(
                "SELECT r.*, p.display_name, p.email_masked, s.philosophy, room.code "
                "FROM result r "
                "JOIN room ON room.id = r.room_id "
                "JOIN player p ON p.id = r.player_id "
                "LEFT JOIN seat s ON s.room_id = r.room_id AND s.team = r.team "
                "WHERE r.room_id = ? ORDER BY r.team",
                (room_id,),
            )}


def solo(conn):
    """Score attack, best run per manager, highest first.

    Best run and not latest run: a manager who plays twice is trying to beat
    themselves, and a board that forgot their good one would punish them for
    having another go.
    """
    best = {}
    for row in conn.execute(
        "SELECT r.*, p.display_name, p.email_masked, s.philosophy, room.code "
        "FROM result r "
        "JOIN room ON room.id = r.room_id "
        "JOIN player p ON p.id = r.player_id "
        "LEFT JOIN seat s ON s.room_id = r.room_id AND s.team = r.team "
        f"WHERE {RANKED} AND room.mode = 'solo' "
        "ORDER BY r.points DESC, r.computed_at ASC, r.id ASC",
    ):
        # Ties go to whoever got there first, which is why the query orders on
        # the clock as well as the points.
        best.setdefault(row["player_id"], _run(row))
    return list(best.values())


def versus(conn):
    """Head to head, by wins and then goal difference.

    Not by rating. Elo is computed, stored and shown, but most people at an
    event play once or twice and one match is not a rating.
    """
    played = {}
    for row in conn.execute(
        "SELECT r.*, p.display_name, p.email_masked, room.code,"
        "       (SELECT p2.display_name FROM result r2"
        "          JOIN player p2 ON p2.id = r2.player_id"
        "         WHERE r2.room_id = r.room_id AND r2.player_id != r.player_id) AS against "
        "FROM result r "
        "JOIN room ON room.id = r.room_id "
        "JOIN player p ON p.id = r.player_id "
        f"WHERE {RANKED} AND room.mode = 'versus' "
        "ORDER BY r.computed_at ASC, r.id ASC",
    ):
        standing = played.setdefault(row["player_id"], _standing(row))
        standing["played"] += 1
        standing[row["outcome"]] += 1
        standing["goals_for"] += row["goals_for"]
        standing["goals_against"] += row["goals_against"]
        # The rows arrive oldest first, so the last one to land is the latest,
        # and the rating it carries is the one this manager holds now.
        standing["rating"] = row["rating"]
        standing["last"] = {"outcome": row["outcome"], "room": row["code"],
                            "goals_for": row["goals_for"],
                            "goals_against": row["goals_against"],
                            "against": row["against"]}

    table = list(played.values())
    for standing in table:
        standing["difference"] = standing["goals_for"] - standing["goals_against"]
    table.sort(key=lambda one: (-one["won"], -one["difference"], -one["goals_for"]))
    return table


def table_for(conn, mode):
    """Whichever board a match of this mode belongs on."""
    return solo(conn) if mode == "solo" else versus(conn)


def placing(conn, room, results):
    """Where this match leaves each dugout on the board it belongs to.

    Empty for a room that earns no place: the workshop, a match run fast, one
    still being played. The results screen still shows the breakdown for those,
    because what a practice run earned is worth reading; it just does not say
    where it ranks, because it ranks nowhere.
    """
    if not room["ranked"] or room["status"] != "finished":
        return {}
    table = table_for(conn, room["mode"])
    where = {row["player_id"]: index for index, row in enumerate(table)}
    standing = {}
    for team, result in results.items():
        index = where.get(result["player_id"])
        if index is None:
            continue
        standing[team] = {"rank": index + 1, "of": len(table)}
        if room["mode"] == "solo":
            # The board holds only a manager's best run, so this run is their
            # best exactly when the board is the one showing it.
            standing[team]["best"] = table[index]["room"] == room["code"]
    return standing


def top(conn, mode, most=3):
    """The head of the board this match belongs to, for the results screen."""
    return table_for(conn, mode)[:most]


def managers(conn):
    """How many people hold a ranked result at all, for the board's header."""
    return conn.execute(
        "SELECT COUNT(DISTINCT r.player_id) AS them FROM result r "
        f"JOIN room ON room.id = r.room_id WHERE {RANKED}"
    ).fetchone()["them"]


def _run(row):
    """One stored result, as every reader but the results screen wants it."""
    return {
        "player_id": row["player_id"],
        "name": row["display_name"],
        "email": row["email_masked"],
        "team": row["team"],
        "philosophy": row["philosophy"],
        "room": row["code"],
        "points": row["points"],
        "outcome": row["outcome"],
        "goals_for": row["goals_for"],
        "goals_against": row["goals_against"],
        "first_goal_ms": row["first_goal_ms"],
        "shouts": row["shouts"],
        "effective": row["effective"],
        "rating": row["rating"],
    }


def _standing(row):
    return {"player_id": row["player_id"], "name": row["display_name"],
            "email": row["email_masked"], "played": 0,
            "won": 0, "drew": 0, "lost": 0,
            "goals_for": 0, "goals_against": 0, "difference": 0,
            "rating": row["rating"], "last": None}
