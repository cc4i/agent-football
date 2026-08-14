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
