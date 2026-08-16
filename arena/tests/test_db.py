import psycopg
import pytest
from psycopg import conninfo, sql

import db


def test_connect_hands_back_dict_rows(dsn):
    connection = db.connect(dsn)
    row = connection.execute("SELECT 1 AS one").fetchone()
    assert row["one"] == 1
    connection.close()


def test_init_db_is_safe_to_run_twice(dsn):
    connection = db.connect(dsn)
    db.init_db(connection)
    db.init_db(connection)
    connection.close()


def test_connect_creates_a_database_that_is_not_there_yet():
    # The one instruction a contributor should need is `brew services start
    # postgresql@18`. Homebrew's psql is keg-only and suffixed, so `createdb`
    # is not on anybody's PATH and the arena makes its own.
    target = "postgresql:///arena_bootstrap_probe"
    admin = conninfo.make_conninfo(target, dbname="postgres")
    name = conninfo.conninfo_to_dict(target)["dbname"]
    with psycopg.connect(admin, autocommit=True) as maintenance:
        maintenance.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    try:
        connection = db.connect(target)
        db.init_db(connection)
        assert connection.execute("SELECT count(*) AS n FROM room").fetchone()["n"] == 0
        connection.close()
    finally:
        with psycopg.connect(admin, autocommit=True) as maintenance:
            maintenance.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def test_a_missing_role_is_not_mistaken_for_a_missing_database(dsn, monkeypatch):
    """A role that is not there comes back out, and nothing is created for it.

    libpq words the two the same way, `role "x" does not exist` reading just
    like `database "x" does not exist`, and only one of them is answered by
    creating something.

    The wording is not what tells the two paths apart, which is the trap here:
    swallow the role error and the connect goes on to the maintenance database
    and fails there with the same sentence, so the message a caller sees is
    almost identical. What tells them apart is that there is no second attempt,
    and no attempt at all against a database nobody asked for.
    """
    attempts = []
    dialled = psycopg.connect

    def watched(target, **kwargs):
        attempts.append(conninfo.conninfo_to_dict(target).get("dbname"))
        return dialled(target, **kwargs)

    monkeypatch.setattr(psycopg, "connect", watched)

    # Built off the suite's own DSN, so this reaches whichever server the suite
    # is pointed at, socket or TCP, with only the role changed.
    nobody = conninfo.make_conninfo(dsn, user="arena_no_such_role")
    with pytest.raises(psycopg.OperationalError) as refused:
        db.connect(nobody)
    assert 'role "arena_no_such_role" does not exist' in str(refused.value)
    assert attempts == [conninfo.conninfo_to_dict(dsn)["dbname"]]


def test_the_schema_creates_every_table(conn):
    names = {row["name"] for row in conn.execute(
        "SELECT tablename AS name FROM pg_catalog.pg_tables WHERE schemaname = 'public'")}
    assert {"player", "room", "seat", "event"} <= names


def test_init_db_over_an_existing_database_keeps_the_rows(dsn):
    first = db.connect(dsn)
    db.init_db(first)
    first.execute(
        "INSERT INTO player (display_name, email_hash, email_masked, created_at) "
        "VALUES ('Alex Rivera', 'hash', 'a***x@example.com', 0)"
    )
    first.commit()
    first.close()

    second = db.connect(dsn)
    db.init_db(second)
    assert second.execute("SELECT COUNT(*) AS n FROM player").fetchone()["n"] == 1
    second.close()


def test_a_player_may_hold_no_address_at_all(conn):
    conn.execute("INSERT INTO player (display_name, created_at) VALUES ('Alex Rivera', 0)")
    row = conn.execute("SELECT email_hash, email_masked FROM player").fetchone()
    assert row == {"email_hash": None, "email_masked": None}


def test_any_number_of_players_may_hold_no_address(conn):
    # The hash is still unique, and the unique index on it is what this could
    # have fallen foul of: Postgres counts NULLs as distinct from one another,
    # so withholding an address is not a claim on the one empty slot.
    for name in ("Alex Rivera", "Sam Okafor"):
        conn.execute("INSERT INTO player (display_name, created_at) VALUES (%s, 0)", (name,))
    assert conn.execute("SELECT count(*) AS n FROM player").fetchone()["n"] == 2


def test_two_players_cannot_share_a_name_whatever_the_case(conn):
    conn.execute("INSERT INTO player (display_name, created_at) VALUES ('Alex Rivera', 0)")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO player (display_name, created_at) "
                     "VALUES ('alex rivera', 0)")
    db.finish(conn)


def test_two_alexes_from_before_the_rule_are_pulled_apart_on_the_next_boot(conn):
    """A database made when names were free of each other still has to open.

    Names became a manager's identity the day the address stopped being
    required, and the index below cannot be created over a table that already
    holds two of them. The older row keeps the name, because it is the one the
    board has been showing all along.
    """
    conn.execute("DROP INDEX one_player_per_name")
    for made in (1.0, 2.0):
        conn.execute("INSERT INTO player (display_name, created_at) VALUES ('Alex', %s)",
                     (made,))
    conn.commit()

    db.init_db(conn)

    names = [row["display_name"] for row in conn.execute(
        "SELECT display_name FROM player ORDER BY created_at")]
    assert names[0] == "Alex"
    assert names[1].startswith("Alex #")
    # And the rule is in force from here on, which is the point of the renaming.
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO player (display_name, created_at) VALUES ('Alex', 3)")
    db.finish(conn)


def test_two_rooms_cannot_share_a_code(conn):
    conn.execute("INSERT INTO room (code, mode, status, ranked, created_at) "
                 "VALUES ('K7F2', 'solo', 'lobby', 1, 0)")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO room (code, mode, status, ranked, created_at) "
                     "VALUES ('K7F2', 'versus', 'lobby', 1, 0)")
    # A refused statement aborts the whole transaction, so anything this test
    # went on to do would fail for the wrong reason. This is what the arena
    # does after a constraint refuses it, and it is what a test has to do too.
    db.finish(conn)


def test_one_dugout_holds_one_player(conn):
    conn.execute("INSERT INTO room (code, mode, status, ranked, created_at) "
                 "VALUES ('K7F2', 'versus', 'lobby', 1, 0)")
    for name in ("Alex", "Sam"):
        conn.execute(
            "INSERT INTO player (display_name, email_hash, email_masked, created_at) "
            "VALUES (%s, %s, 'x***x@e.com', 0)",
            (name, name),
        )
    conn.execute("INSERT INTO seat (room_id, team, player_id, philosophy, ready, joined_at) "
                 "VALUES (1, 'blue', 1, 'high press', 0, 0)")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO seat (room_id, team, player_id, philosophy, ready, joined_at) "
                     "VALUES (1, 'blue', 2, 'counter', 0, 0)")
    db.finish(conn)


def test_a_room_cannot_reuse_a_sequence_number(conn):
    conn.execute("INSERT INTO room (code, mode, status, ranked, created_at) "
                 "VALUES ('K7F2', 'solo', 'live', 1, 0)")
    conn.execute("INSERT INTO event (room_id, seq, kind, payload_json, match_ms, wall_ts) "
                 "VALUES (1, 1, 'kickoff', '{}', 0, 0)")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO event (room_id, seq, kind, payload_json, match_ms, wall_ts) "
                     "VALUES (1, 1, 'goal', '{}', 100, 0)")
    db.finish(conn)


def hang_up_on(dsn, connection):
    """Kill this connection's backend from the server side, the way a real one goes.

    Not `connection.close()`: that leaves psycopg knowing the connection is
    shut. What a Cloud SQL instance restarting, a VPC route changing or an idle
    reaper does is take the socket away underneath a client that still believes
    it has one, and the difference is exactly what `finish` has to survive.
    """
    with psycopg.connect(dsn, autocommit=True) as server_side:
        server_side.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()")


def test_putting_back_a_connection_the_server_hung_up_on_does_not_raise(dsn):
    """Every caller of `finish` is in a `finally`, so it must not raise.

    A broken connection reports a transaction status that is not IDLE, so
    `finish` reached for a rollback and psycopg answered `the connection is
    lost`. Raising from a `finally` replaces whatever went wrong first, and in
    the watchdog's case it ended the loop that the surrounding `except` exists
    to keep alive: one lost connection and no room is ever given up on again.

    There is nothing to put back on a connection that is already gone, and
    nothing a caller in a `finally` could do about it if there were.
    """
    connection = db.connect(dsn)
    connection.execute("SELECT 1")
    hang_up_on(dsn, connection)
    assert connection.info.transaction_status != psycopg.pq.TransactionStatus.IDLE

    db.finish(connection)
    connection.close()


def test_putting_back_a_connection_that_was_closed_properly_does_not_raise(dsn):
    connection = db.connect(dsn)
    connection.execute("SELECT 1")
    connection.close()
    db.finish(connection)


def test_a_working_connection_is_still_rolled_back(dsn):
    # The swallowing above must not have turned `finish` into a no-op.
    connection = db.connect(dsn)
    connection.execute("CREATE TEMP TABLE scribble (n INTEGER)")
    connection.execute("INSERT INTO scribble VALUES (1)")
    db.finish(connection)
    assert connection.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    # The temp table went with the rolled-back transaction that made it.
    with pytest.raises(psycopg.errors.UndefinedTable):
        connection.execute("SELECT * FROM scribble")
    connection.close()
