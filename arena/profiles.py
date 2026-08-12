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


def seed(conn, room_id, teams):
    """Give every named dugout a fresh copy of each role's baseline.

    Safe to call twice: a profile that is already there keeps whatever it has
    moved to since.
    """
    now = time.time()
    conn.executemany(
        "INSERT OR IGNORE INTO profile "
        "(room_id, team, role, attributes_json, updated_at) VALUES (?, ?, ?, ?, ?)",
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
            "WHERE room_id = ? AND team = ? ORDER BY role",
            (room_id, team),
        )
    }


def read_one(conn, room_id, team, role):
    """One role's current attributes, or None if this dugout has no such role."""
    row = conn.execute(
        "SELECT attributes_json FROM profile WHERE room_id = ? AND team = ? AND role = ?",
        (room_id, team, role),
    ).fetchone()
    return json.loads(row["attributes_json"]) if row else None
