"""Delete the rehearsal managers and their matches from the arena's database.

Runs as a Cloud Run job on the arena image, inside the VPC, because the Cloud
SQL instance has a private address and the laptop has no route to it.

Matches on the masked email's domain rather than on the display name. Both
rehearsals stamp a domain nobody can register - `@grounds.example.com` for the
grounds capacity ramp, `@rehearsal.example.com` for the arena's load rehearsal
- and a manager who types "Rehearsal 12" into the lobby is a human whose row
this has no business touching.

A room goes only if every seat in it is a rehearsal player's. The grounds ramp
opens solo rooms and nothing else, so in practice that is all of them; the
check is there so that the day someone rehearses a versus match against a real
manager, the real manager's match survives.

DELETE order is the foreign keys backwards: result and event and profile and
seat all point at room, result and seat point at player.
"""

import os

import psycopg

DOMAINS = ("%@grounds.example.com", "%@rehearsal.example.com")

with psycopg.connect(os.environ["ARENA_DB"]) as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, display_name FROM player WHERE email_masked LIKE ANY(%s)",
            (list(DOMAINS),))
        players = cur.fetchall()
        ids = [row[0] for row in players]
        print(f"rehearsal managers: {len(ids)}")
        for _, name in sorted(players, key=lambda row: row[1])[:5]:
            print(f"  e.g. {name}")

        if not ids:
            print("nothing to do")
            raise SystemExit(0)

        # Rooms every one of whose seats is a rehearsal player's.
        cur.execute("""
            SELECT room_id FROM seat GROUP BY room_id
            HAVING bool_and(player_id = ANY(%s))
        """, (ids,))
        rooms = [row[0] for row in cur.fetchall()]
        print(f"their rooms: {len(rooms)}")

        # A rehearsal player sitting in somebody else's room would keep that
        # room and lose only their own result row. Say so rather than imply it.
        cur.execute("""
            SELECT count(DISTINCT room_id) FROM seat
            WHERE player_id = ANY(%s) AND room_id <> ALL(%s)
        """, (ids, rooms))
        shared = cur.fetchone()[0]
        if shared:
            print(f"  ({shared} shared with a real manager, kept)")

        counts = {}
        for table, column, values in (("result", "room_id", rooms),
                                      ("event", "room_id", rooms),
                                      ("profile", "room_id", rooms),
                                      ("result", "player_id", ids),
                                      ("seat", "room_id", rooms),
                                      ("seat", "player_id", ids),
                                      ("room", "id", rooms),
                                      ("player", "id", ids)):
            cur.execute(f"DELETE FROM {table} WHERE {column} = ANY(%s)", (values,))
            counts[f"{table}.{column}"] = cur.rowcount

        for what, n in counts.items():
            print(f"deleted {n:6d} from {what}")

    if os.environ.get("TIDY_APPLY") == "1":
        conn.commit()
        print("committed")
    else:
        conn.rollback()
        print("rolled back - set TIDY_APPLY=1 to apply")
