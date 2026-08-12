import sqlite3

import pytest

import db


def test_the_schema_creates_every_table(conn):
    names = {row["name"] for row in
             conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"player", "room", "seat", "event"} <= names


def test_a_file_backed_database_runs_in_wal_mode(db_path):
    # The wall socket reads while a match is writing events. WAL is what lets
    # those happen at the same time without a locked database.
    connection = db.connect(db_path)
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    connection.close()


def test_init_db_over_an_existing_database_keeps_the_rows(db_path):
    first = db.connect(db_path)
    db.init_db(first)
    first.execute(
        "INSERT INTO player (display_name, email_hash, email_masked, created_at) "
        "VALUES ('Alex Rivera', 'hash', 'a***x@example.com', 0)"
    )
    first.commit()
    first.close()

    second = db.connect(db_path)
    db.init_db(second)
    assert second.execute("SELECT COUNT(*) AS n FROM player").fetchone()["n"] == 1
    second.close()


def test_two_rooms_cannot_share_a_code(conn):
    conn.execute("INSERT INTO room (code, mode, status, ranked, created_at) "
                 "VALUES ('K7F2', 'solo', 'lobby', 1, 0)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO room (code, mode, status, ranked, created_at) "
                     "VALUES ('K7F2', 'versus', 'lobby', 1, 0)")


def test_one_dugout_holds_one_player(conn):
    conn.execute("INSERT INTO room (code, mode, status, ranked, created_at) "
                 "VALUES ('K7F2', 'versus', 'lobby', 1, 0)")
    for name in ("Alex", "Sam"):
        conn.execute(
            "INSERT INTO player (display_name, email_hash, email_masked, created_at) "
            "VALUES (?, ?, 'x***x@e.com', 0)",
            (name, name),
        )
    conn.execute("INSERT INTO seat (room_id, team, player_id, philosophy, ready, joined_at) "
                 "VALUES (1, 'blue', 1, 'high press', 0, 0)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO seat (room_id, team, player_id, philosophy, ready, joined_at) "
                     "VALUES (1, 'blue', 2, 'counter', 0, 0)")


def test_a_room_cannot_reuse_a_sequence_number(conn):
    conn.execute("INSERT INTO room (code, mode, status, ranked, created_at) "
                 "VALUES ('K7F2', 'solo', 'live', 1, 0)")
    conn.execute("INSERT INTO event (room_id, seq, kind, payload_json, match_ms, wall_ts) "
                 "VALUES (1, 1, 'kickoff', '{}', 0, 0)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO event (room_id, seq, kind, payload_json, match_ms, wall_ts) "
                     "VALUES (1, 1, 'goal', '{}', 100, 0)")
