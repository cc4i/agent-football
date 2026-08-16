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

import logging
import os
import re

import psycopg
from psycopg import conninfo, sql
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

# Local development over the Unix socket: no password, no port, no host. On
# Cloud SQL this is a full conninfo string naming /cloudsql/<connection-name>
# as the host, which is a socket too.
DEFAULT_DSN = "postgresql:///arena"

SCHEMA = """
-- The address is optional, so both of its columns are. Unique among the
-- players who gave one and no constraint at all on those who did not, because
-- Postgres counts NULLs as distinct from one another: withholding an address
-- is not a claim on the one empty slot. What identifies a manager who gave
-- none is their name, which `one_player_per_name` below keeps theirs alone.
CREATE TABLE IF NOT EXISTS player (
    id           INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    display_name TEXT    NOT NULL,
    email_hash   TEXT    UNIQUE,
    email_masked TEXT,
    created_at   DOUBLE PRECISION NOT NULL
);

-- `ranked` covers the reserved workshop room and a host that reported a speed
-- other than 1.0. Abandonment is read from `status`.
-- `last_heard_at` is a column rather than a dict in memory because a Cloud Run
-- rollout runs the old instance and the new one at the same time for a few
-- seconds, and an in-memory map on the new one would abandon every match whose
-- host is still talking to the old one.
-- `started_at` is what separates a room that was played from one that was only
-- ever open. Both end up "abandoned" when their screen goes, and the status
-- alone cannot tell a dugout whether to show a scoreline or say the room shut
-- before anything happened in it.
CREATE TABLE IF NOT EXISTS room (
    id             INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    code           TEXT    NOT NULL UNIQUE,
    mode           TEXT    NOT NULL,
    status         TEXT    NOT NULL,
    host_client_id   TEXT,
    screen_client_id TEXT,
    ranked         INTEGER NOT NULL,
    created_at     DOUBLE PRECISION NOT NULL,
    started_at     DOUBLE PRECISION,
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

# Columns whose rules changed after somebody's database was already made.
# `CREATE TABLE IF NOT EXISTS` does nothing at all to a table that is already
# there, so the email columns above would stay NOT NULL forever on every
# database that predates the address becoming optional, and the first manager
# to withhold one would get a 500. Each statement here is a no-op against a
# table that already has the shape it asks for, so this runs on every boot.
MIGRATIONS = """
ALTER TABLE player ALTER COLUMN email_hash DROP NOT NULL;
ALTER TABLE player ALTER COLUMN email_masked DROP NOT NULL;
ALTER TABLE room ADD COLUMN IF NOT EXISTS started_at DOUBLE PRECISION;
ALTER TABLE room ADD COLUMN IF NOT EXISTS screen_client_id TEXT;
"""

# What makes a name a manager's own. Compared case-insensitively, over names
# `identity.normalise_name` has already collapsed the whitespace in, so `Alex
# Rivera` and `alex  rivera` are one name and the second person to type it is
# asked for another. An index rather than a table constraint for the reason
# above: `CREATE TABLE IF NOT EXISTS` will not add one to a table that exists.
ONE_NAME_EACH = ("CREATE UNIQUE INDEX IF NOT EXISTS one_player_per_name "
                 "ON player (lower(display_name))")

# How many renaming rounds `_pull_apart_shared_names` may take. Each round
# leaves strictly fewer clashes than it found, and one that changes nothing
# ends it early, so this only bounds the case where a rename keeps landing on a
# name that is itself taken. Running out means the index below fails to create
# and the arena does not boot, which is the right way to be wrong about this.
RENAME_ROUNDS = 5

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

# The one failure `_ensure_database` is allowed to answer by creating something.
# The quoted name and the word `database` are both load-bearing: libpq words a
# missing role identically - `role "arena" does not exist` - and a bare `does
# not exist` therefore matches the one error nothing here can fix.
MISSING_DATABASE = re.compile(r'database "[^"]*" does not exist')


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
    sqlstate on it, so the message is the only thing there is to match on, and
    it has to be matched narrowly: `MISSING_DATABASE` is the sentence naming a
    database and nothing else. Anything else - server down, bad password, wrong
    socket, a role that is not there - comes back out of here as it came, and
    the caller sees the server's own words rather than a second failure against
    the maintenance database one layer further from the cause.
    """
    try:
        psycopg.connect(dsn).close()
        return
    except psycopg.OperationalError as absent:
        if not MISSING_DATABASE.search(str(absent)):
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
    connection.execute(MIGRATIONS)
    _pull_apart_shared_names(connection)
    connection.execute(ONE_NAME_EACH)
    connection.commit()


def _pull_apart_shared_names(connection):
    """Rename any manager who shares a name with an older one.

    A name became a manager's identity the day the address stopped being
    required, and a database made before that day may well hold two Alexes.
    The unique index cannot be created over a table that already holds a pair,
    so this is what lets it be created at all -- and it has to run on every
    boot, because there is nowhere to record that it has run before.

    The oldest row keeps the name, because it is the one the board has been
    showing. The rest get their own id appended, which is unique by
    construction, so no two rows renamed in the same round can collide with
    each other; only with a name that was already sitting there, which is what
    the second round is for.
    """
    for _ in range(RENAME_ROUNDS):
        renamed = connection.execute(
            "UPDATE player SET display_name = player.display_name || ' #' || player.id "
            "FROM (SELECT id, row_number() OVER (PARTITION BY lower(display_name) "
            "                                    ORDER BY created_at, id) AS seniority "
            "        FROM player) ranked "
            "WHERE ranked.id = player.id AND ranked.seniority > 1"
        ).rowcount
        if not renamed:
            return


def finish(connection):
    """Return the connection to a clean idle state at the end of a unit of work.

    psycopg opens a transaction on the first statement of any kind, reads
    included, and one connection is shared by everything. Left alone a read
    holds a transaction open for the life of the instance, which pins the
    vacuum horizon on a leaderboard meant to last weeks; and a write that hit a
    constraint leaves the transaction aborted, so every later statement in the
    process fails until somebody restarts it. Rolling back costs nothing after
    a commit and is the difference between one failed request and a dead arena.

    It never raises, because every caller is in a `finally`. A connection the
    server has hung up on -- an instance restarted, a route changed, an idle
    reaper -- reports a transaction status that is not IDLE, so this reached
    for a rollback and psycopg answered `the connection is lost`. Coming out of
    a `finally` that replaced whatever had gone wrong first, and in the
    watchdog it ended the loop the surrounding `except` exists to keep alive:
    one lost connection and no room was ever given up on again, silently, with
    the arena still answering every probe put to it. Measured against a real
    Postgres in `tests/test_db.py` by taking the backend away mid-transaction.

    There is nothing to put back on a connection that is already gone, and no
    caller in a `finally` could do anything about it if there were. What
    notices instead is the sweep failing to complete, which is what `/health`
    answers for.
    """
    try:
        if connection.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            connection.rollback()
    except psycopg.Error as gone:
        logger.warning("could not put the connection back: %s", gone)
