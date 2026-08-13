"""SQLite storage for the arena. One file, WAL mode, no ORM.

Every other module takes an open connection rather than reaching for a global,
so a test can point at a throwaway database without touching module state.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "arena.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS player (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT    NOT NULL,
    email_hash   TEXT    NOT NULL UNIQUE,
    email_masked TEXT    NOT NULL,
    created_at   REAL    NOT NULL
);

-- `ranked` covers the reserved workshop room and a host that reported a speed
-- other than 1.0. Abandonment is read from `status`.
CREATE TABLE IF NOT EXISTS room (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT    NOT NULL UNIQUE,
    mode           TEXT    NOT NULL,
    status         TEXT    NOT NULL,
    host_client_id TEXT,
    ranked         INTEGER NOT NULL,
    created_at     REAL    NOT NULL,
    finished_at    REAL
);

-- The primary key is what stops two people taking the same dugout.
CREATE TABLE IF NOT EXISTS seat (
    room_id    INTEGER NOT NULL REFERENCES room(id),
    team       TEXT    NOT NULL,
    player_id  INTEGER NOT NULL REFERENCES player(id),
    philosophy TEXT,
    ready      INTEGER NOT NULL DEFAULT 0,
    joined_at  REAL    NOT NULL,
    PRIMARY KEY (room_id, team)
);

-- Append-only. Scoring is recomputed from this and never from a submitted
-- total, so the sequence is per room and gapless.
CREATE TABLE IF NOT EXISTS event (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id      INTEGER NOT NULL REFERENCES room(id),
    seq          INTEGER NOT NULL,
    kind         TEXT    NOT NULL,
    payload_json TEXT    NOT NULL,
    match_ms     INTEGER,
    wall_ts      REAL    NOT NULL,
    UNIQUE (room_id, seq)
);

-- One row per room, team and role, seeded from the shipped baselines when the
-- room opens. Seeding up front means a patch is always an update, so nothing
-- has to decide at write time whether a dugout exists yet.
CREATE TABLE IF NOT EXISTS profile (
    room_id         INTEGER NOT NULL REFERENCES room(id),
    team            TEXT    NOT NULL,
    role            TEXT    NOT NULL,
    attributes_json TEXT    NOT NULL,
    updated_at      REAL    NOT NULL,
    PRIMARY KEY (room_id, team, role)
);

-- One row per dugout of a finished match, written once at the whistle from
-- that room's own log. The columns the two boards rank and render on are their
-- own columns rather than keys inside the JSON, so a board is a query. The
-- JSON holds the breakdown exactly as the results screen shows it, so the
-- screen never has to re-derive the point table and disagree with the total.
CREATE TABLE IF NOT EXISTS result (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id        INTEGER NOT NULL REFERENCES room(id),
    player_id      INTEGER NOT NULL REFERENCES player(id),
    team           TEXT    NOT NULL,
    points         INTEGER NOT NULL,
    outcome        TEXT    NOT NULL,
    goals_for      INTEGER NOT NULL,
    goals_against  INTEGER NOT NULL,
    first_goal_ms  INTEGER,
    shouts         INTEGER NOT NULL,
    effective      INTEGER NOT NULL,
    -- The player's Elo after this match, head to head only. Kept here rather
    -- than on the player so that a rating is always a match's own record and
    -- the whole ladder can be replayed from the results that made it.
    rating         REAL,
    breakdown_json TEXT    NOT NULL,
    computed_at    REAL    NOT NULL,
    UNIQUE (room_id, player_id)
);

CREATE INDEX IF NOT EXISTS room_by_status ON room (status);
CREATE INDEX IF NOT EXISTS result_by_player ON result (player_id);
"""


def connect(path=DB_PATH):
    """Open the arena database, creating the file if it is not there yet."""
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    # WAL mode for when a second connection is added; single-threaded for now.
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_db(connection):
    """Create every table. Safe to call against a database that already has them."""
    connection.executescript(SCHEMA)
    connection.commit()
