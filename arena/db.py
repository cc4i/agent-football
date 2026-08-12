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

-- `ranked` covers the reserved workshop room and, from step 5, a host that
-- reported a speed other than 1.0. Abandonment is read from `status`.
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

CREATE INDEX IF NOT EXISTS room_by_status ON room (status);
"""


def connect(path=DB_PATH):
    """Open the arena database, creating the file if it is not there yet."""
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    # WAL lets the wall socket read while a match is writing events.
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_db(connection):
    """Create every table. Safe to call against a database that already has them."""
    connection.executescript(SCHEMA)
    connection.commit()
