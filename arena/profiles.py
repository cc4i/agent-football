"""Per-room, per-team player profiles.

The pitch used to read four JSON files from disk, which meant every match in
the venue shared one defender. A profile now belongs to a room and a dugout,
starts at the shipped baseline, and only moves by a validated patch.

This module knows a room only by its id. `rooms` imports it, not the other way
round, which is why `seed` is told which dugouts to fill.
"""

import json
import time

import attributes


class Rejected(Exception):
    """A patch the rules refuse. `problems` is fit to show a manager as-is."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


def seed(conn, room_id, teams):
    """Give every named dugout a fresh copy of each role's baseline.

    Safe to call twice: a profile that is already there keeps whatever it has
    moved to since.
    """
    now = time.time()
    # psycopg puts executemany on the cursor rather than the connection, which
    # is the one place the driver swap is visible in this file.
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO profile "
            "(room_id, team, role, attributes_json, updated_at) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            [(room_id, team, role, json.dumps(attributes.baseline_for(role)), now)
             for team in teams for role in attributes.ROLES],
        )
    conn.commit()


def read_all(conn, room_id, team):
    """Every role's current attributes for one dugout, keyed by role."""
    return {
        row["role"]: json.loads(row["attributes_json"])
        for row in conn.execute(
            "SELECT role, attributes_json FROM profile "
            "WHERE room_id = %s AND team = %s ORDER BY role",
            (room_id, team),
        )
    }


def read_one(conn, room_id, team, role):
    """One role's current attributes, or None if this dugout has no such role."""
    row = conn.execute(
        "SELECT attributes_json FROM profile WHERE room_id = %s AND team = %s AND role = %s",
        (room_id, team, role),
    ).fetchone()
    return json.loads(row["attributes_json"]) if row else None


def reset(conn, room_id, team):
    """Put a whole dugout back to the shipped baseline.

    Returns one `patch`-shaped result per role that actually moved, so a reset
    reaches the log as the changes it made rather than as an opaque event a
    late arrival cannot replay. A patch can only ever set attributes the
    baseline names, so writing the baseline back is the whole of it.
    """
    now = time.time()
    results = []
    for role in attributes.ROLES:
        current = read_one(conn, room_id, team, role)
        if current is None:
            continue
        baseline = attributes.baseline_for(role)
        changed = {key: value for key, value in baseline.items()
                   if current.get(key) != value}
        if not changed:
            continue
        conn.execute(
            "UPDATE profile SET attributes_json = %s, updated_at = %s "
            "WHERE room_id = %s AND team = %s AND role = %s",
            (json.dumps(baseline), now, room_id, team, role),
        )
        results.append({"role": role, "attributes": baseline, "changed": changed})
    conn.commit()
    return results


def patch(conn, room_id, team, role, changes):
    """Apply validated changes to one profile.

    Returns the role, its whole attribute set afterwards, and just the values
    that moved. Raises Rejected carrying every reason at once, rather than the
    first: the caller is often a language model, and it can only correct what
    it is told. A patch with any bad value lands none of its values.
    """
    problems = attributes.validate(role, changes)
    if problems:
        raise Rejected(problems)

    current = read_one(conn, room_id, team, role)
    if current is None:
        raise Rejected([f"this room has no {team} {role}"])

    changed = {key: value for key, value in changes.items() if current.get(key) != value}
    current.update(changes)
    conn.execute(
        "UPDATE profile SET attributes_json = %s, updated_at = %s "
        "WHERE room_id = %s AND team = %s AND role = %s",
        (json.dumps(current), time.time(), room_id, team, role),
    )
    conn.commit()
    return {"role": role, "attributes": current, "changed": changed}
