"""Postgres storage for the arena. One connection, no ORM.

Every other module takes an open connection rather than reaching for a global,
so a test can point at a throwaway database without touching module state.

One connection on purpose, not for want of a pool. It serialises every write,
and that serialisation is what makes the four specialists of a single shout -
which patch their profiles in parallel - land four distinct sequence numbers
in a room's log. `rooms.append_event` locks the room as well, so the guarantee
survives whoever eventually adds a pool, but the day that happens is the day
`tests/test_append_event_race.py` earns its keep.
"""

import os

import psycopg
from psycopg import conninfo, sql
from psycopg.rows import dict_row

# Local development over the Unix socket: no password, no port, no host. On
# Cloud SQL this is a full conninfo string naming /cloudsql/<connection-name>
# as the host, which is a socket too.
DEFAULT_DSN = "postgresql:///arena"

SCHEMA = """
CREATE TABLE IF NOT EXISTS player (
    id           INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    display_name TEXT    NOT NULL,
    email_hash   TEXT    NOT NULL UNIQUE,
    email_masked TEXT    NOT NULL,
    created_at   DOUBLE PRECISION NOT NULL
);

-- `ranked` covers the reserved workshop room and a host that reported a speed
-- other than 1.0. Abandonment is read from `status`.
-- `last_heard_at` is a column rather than a dict in memory because a Cloud Run
-- rollout runs the old instance and the new one at the same time for a few
-- seconds, and an in-memory map on the new one would abandon every match whose
-- host is still talking to the old one.
CREATE TABLE IF NOT EXISTS room (
    id             INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    code           TEXT    NOT NULL UNIQUE,
    mode           TEXT    NOT NULL,
    status         TEXT    NOT NULL,
    host_client_id TEXT,
    ranked         INTEGER NOT NULL,
    created_at     DOUBLE PRECISION NOT NULL,
    finished_at    DOUBLE PRECISION,
    last_heard_at  DOUBLE PRECISION
);

-- The primary key is what stops two people taking the same dugout.
CREATE TABLE IF NOT EXISTS seat (
    room_id    INTEGER NOT NULL REFERENCES room(id),
    team       TEXT    NOT NULL,
    player_id  INTEGER NOT NULL REFERENCES player(id),
    philosophy TEXT,
    ready      INTEGER NOT NULL DEFAULT 0,
    joined_at  DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (room_id, team)
);

-- Append-only. Scoring is recomputed from this and never from a submitted
-- total, so the sequence is per room and gapless.
CREATE TABLE IF NOT EXISTS event (
    id           INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    room_id      INTEGER NOT NULL REFERENCES room(id),
    seq          INTEGER NOT NULL,
    kind         TEXT    NOT NULL,
    payload_json TEXT    NOT NULL,
    match_ms     BIGINT,
    wall_ts      DOUBLE PRECISION NOT NULL,
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
    updated_at      DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (room_id, team, role)
);

-- One row per dugout of a finished match, written once at the whistle from
-- that room's own log. The columns the two boards rank and render on are their
-- own columns rather than keys inside the JSON, so a board is a query. The
-- JSON holds the breakdown exactly as the results screen shows it, so the
-- screen never has to re-derive the point table and disagree with the total.
CREATE TABLE IF NOT EXISTS result (
    id             INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    room_id        INTEGER NOT NULL REFERENCES room(id),
    player_id      INTEGER NOT NULL REFERENCES player(id),
    team           TEXT    NOT NULL,
    points         INTEGER NOT NULL,
    outcome        TEXT    NOT NULL,
    goals_for      INTEGER NOT NULL,
    goals_against  INTEGER NOT NULL,
    first_goal_ms  BIGINT,
    shouts         INTEGER NOT NULL,
    effective      INTEGER NOT NULL,
    -- The player's Elo after this match, head to head only. Kept here rather
    -- than on the player so that a rating is always a match's own record and
    -- the whole ladder can be replayed from the results that made it.
    rating         DOUBLE PRECISION,
    breakdown_json TEXT    NOT NULL,
    computed_at    DOUBLE PRECISION NOT NULL,
    UNIQUE (room_id, player_id)
);

CREATE INDEX IF NOT EXISTS room_by_status ON room (status);
CREATE INDEX IF NOT EXISTS result_by_player ON result (player_id);

-- The seat primary key stops two people taking the same dugout; this stops one
-- person taking both. `take_seat` says as much and checks for it, but a check
-- is a read and sitting down is a write, and a double-tapped phone is enough to
-- get between them. Head to head scoring would then rate a player against
-- themselves. An index rather than a table constraint because `CREATE TABLE IF
-- NOT EXISTS` will not add one to a table that is already there, which would
-- leave every database made before today quietly unprotected.
CREATE UNIQUE INDEX IF NOT EXISTS one_dugout_per_player ON seat (room_id, player_id);
"""

# Every table, newest dependency last. The test suite truncates these between
# tests; nothing in the arena itself reads it.
TABLES = ("result", "event", "profile", "seat", "room", "player")

# What `init_db` holds while it runs SCHEMA. `CREATE TABLE IF NOT EXISTS` is
# not atomic in Postgres: two of them at once collide on pg_type and one side
# crashes. A Cloud Run rollout starts the new instance while the old one is
# still serving, so booting twice at once is the ordinary case here rather than
# the rare one. The number is arbitrary and only has to be the same in every
# instance; advisory locks share one space per database, so it spells ARENASCH
# to keep it clear of whatever else might one day take a lock here.
SCHEMA_LOCK = 0x4152454E41534348


def connect(dsn=None):
    """Open the arena database, creating it if the server has no such database.

    `dsn` is anything libpq accepts. The default reaches a local Postgres over
    its Unix socket, which needs no password and no port.
    """
    dsn = dsn or os.environ.get("ARENA_DB", DEFAULT_DSN)
    _ensure_database(dsn)
    return psycopg.connect(dsn, row_factory=dict_row)


def _ensure_database(dsn):
    """Create the database if the server is up but the database is missing.

    Homebrew's postgresql@18 is keg-only and its binaries carry a -18 suffix
    that is not on PATH, so `createdb` is not something a contributor can be
    told to run. Doing it here means `brew services start postgresql@18` is the
    whole of the setup, and it costs nothing on Cloud SQL, where the database
    is made once by the deploy and this never fires.

    libpq reports a missing database as a bare OperationalError with no
    sqlstate on it, so the message is the only thing there is to match on.
    Anything else - server down, bad password, wrong socket - is re-raised as
    it came, because those are not ours to paper over.
    """
    try:
        psycopg.connect(dsn).close()
        return
    except psycopg.OperationalError as absent:
        if "does not exist" not in str(absent):
            raise

    name = conninfo.conninfo_to_dict(dsn).get("dbname")
    if not name:
        raise psycopg.OperationalError(f"no database named in the DSN: {dsn}")
    with psycopg.connect(conninfo.make_conninfo(dsn, dbname="postgres"),
                         autocommit=True) as maintenance:
        try:
            maintenance.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        except psycopg.errors.DuplicateDatabase:
            # Two arenas started at once. Whoever lost the race is still fine.
            pass


def init_db(connection):
    """Create every table. Safe to call against a database that already has them.

    Safe to call from two instances at once as well, which is what the lock is
    for. It is transaction-scoped, so the commit below is what releases it.
    """
    connection.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK,))
    connection.execute(SCHEMA)
    connection.commit()


def finish(connection):
    """Return the connection to a clean idle state at the end of a unit of work.

    psycopg opens a transaction on the first statement of any kind, reads
    included, and one connection is shared by everything. Left alone a read
    holds a transaction open for the life of the instance, which pins the
    vacuum horizon on a leaderboard meant to last weeks; and a write that hit a
    constraint leaves the transaction aborted, so every later statement in the
    process fails until somebody restarts it. Rolling back costs nothing after
    a commit and is the difference between one failed request and a dead arena.
    """
    if connection.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        connection.rollback()
