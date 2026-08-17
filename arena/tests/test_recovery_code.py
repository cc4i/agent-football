"""Recovery codes: proof an address is yours, for the claim that needs one.

E1 in the access-control design. An address may still bring a manager back to
their own row. It may no longer do so on its own.
"""

import identity
import rooms


def player(client, name, email=None, code=""):
    """One join, as a phone makes it."""
    body = {"display_name": name, "recovery_code": code}
    if email is not None:
        body["email"] = email
    return client.post("/api/players", json=body)


def test_a_fresh_player_gets_a_code(client):
    identifier = player(client, "Alex Rivera", "alex@example.com").json()["id"]
    row = rooms.get_player(client.app.state.conn, identifier)
    assert row["recovery_code"] is not None
    assert len(row["recovery_code"]) == identity.RECOVERY_LENGTH


def test_a_player_without_an_address_gets_no_code(client):
    identifier = player(client, "Alex Rivera").json()["id"]
    row = rooms.get_player(client.app.state.conn, identifier)
    assert row["recovery_code"] is None


def test_the_backfill_gives_an_existing_address_bearing_row_a_code(client, conn):
    # Somebody who registered before the column existed.
    import db
    conn.execute(
        "INSERT INTO player (display_name, email_hash, email_masked, created_at) "
        "VALUES (%s, %s, %s, %s)",
        ("Alex Rivera", identity.hash_email("alex@example.com", "salt"),
         identity.mask_email("alex@example.com"), 1234567890.0))
    conn.commit()
    # Boot again, which runs the backfill.
    db.init_db(conn)
    row = conn.execute("SELECT * FROM player WHERE display_name = 'Alex Rivera'").fetchone()
    assert row["recovery_code"] is not None
    assert len(row["recovery_code"]) == identity.RECOVERY_LENGTH


def test_the_backfill_is_idempotent(client, conn):
    import db
    conn.execute(
        "INSERT INTO player (display_name, email_hash, email_masked, created_at) "
        "VALUES (%s, %s, %s, %s)",
        ("Alex Rivera", identity.hash_email("alex@example.com", "salt"),
         identity.mask_email("alex@example.com"), 1234567890.0))
    conn.commit()
    db.init_db(conn)
    first = conn.execute("SELECT recovery_code FROM player WHERE display_name = 'Alex Rivera'").fetchone()["recovery_code"]
    # Run it again.
    db.init_db(conn)
    second = conn.execute("SELECT recovery_code FROM player WHERE display_name = 'Alex Rivera'").fetchone()["recovery_code"]
    assert first == second


def test_a_claim_from_a_cookie_less_phone_without_the_code_is_refused(client, phones):
    player(client, "Alex Rivera", "alex@example.com")
    phones.fresh()
    claim = player(client, "Alex Rivera", "alex@example.com")
    assert claim.status_code == 409
    detail = claim.json()["detail"]
    assert "recovery code" in detail["problems"][0]
    assert detail["field"] == "recovery_code"


def test_a_claim_with_the_code_returns_the_original_row(client, phones, conn):
    first = player(client, "Alex Rivera", "alex@example.com").json()
    code = rooms.get_player(conn, first["id"])["recovery_code"]
    phones.fresh()
    second = player(client, "Alex Rivera", "alex@example.com", code)
    assert second.status_code == 200
    assert second.json()["id"] == first["id"]


def test_a_lower_case_code_is_accepted(client, phones, conn):
    first = player(client, "Alex Rivera", "alex@example.com").json()
    code = rooms.get_player(conn, first["id"])["recovery_code"]
    phones.fresh()
    second = player(client, "Alex Rivera", "alex@example.com", code.lower())
    assert second.status_code == 200
    assert second.json()["id"] == first["id"]


def test_the_same_phone_playing_again_still_needs_no_code(client):
    first = player(client, "Alex Rivera", "alex@example.com").json()
    # The cookie is still there, no code needed.
    second = player(client, "Alex Rivera", "alex@example.com")
    assert second.status_code == 200
    assert second.json()["id"] == first["id"]


def test_an_address_still_outranks_somebody_else_s_cookie_given_the_code(client, phones, conn):
    # Alex plays on their own phone, then borrows Sam's. With the code, Alex
    # still gets their own place on the board.
    alex = player(client, "Alex Rivera", "alex@example.com").json()["id"]
    alex_code = rooms.get_player(conn, alex)["recovery_code"]
    phones.fresh()
    sam = player(client, "Sam Okafor", "sam@example.com").json()["id"]
    assert sam != alex

    borrowed = player(client, "Alex Rivera", "alex@example.com", alex_code)
    assert borrowed.status_code == 200
    assert borrowed.json()["id"] == alex


def test_a_wrong_code_is_refused(client, phones, conn):
    """Renamed from constant_time because that test only proves refusal.
    The implementation uses hmac.compare_digest, but swapping for == keeps this green."""
    player(client, "Alex Rivera", "alex@example.com")
    phones.fresh()
    # Wrong code, all from the alphabet, so it passes validation and reaches the comparison.
    claim = player(client, "Alex Rivera", "alex@example.com", "ABCDEF")
    assert claim.status_code == 409


def test_forgetting_a_code_models_the_attacker(client, phones):
    """The forget() method exists so a test can model an attacker."""
    phones.join("Alex Rivera", "alex@example.com")
    phones.forget("alex@example.com")
    phones.fresh()
    # Without the code, the claim is refused.
    claim = player(client, "Alex Rivera", "alex@example.com")
    assert claim.status_code == 409


def test_the_board_carries_no_address(client, finished):
    """Board rows have no email field."""
    boards = client.get("/api/board").json()
    assert len(boards["solo"]) == 1
    assert "email" not in boards["solo"][0]


def test_room_snapshots_carry_no_address(client, phones):
    """Seat snapshots have no email field."""
    phones.join("Alex Rivera", "alex@example.com")
    code = client.post("/api/rooms", json={"mode": "solo"}).json()["code"]
    client.post(f"/api/rooms/{code}/seats/blue", json={"philosophy": "high press"})
    snap = client.get(f"/api/rooms/{code}").json()
    assert "email" not in snap["seats"]["blue"]


def test_results_carry_no_address(client, finished):
    """Result rows have no email field."""
    result = client.get(f"/api/rooms/{finished}/result").json()
    assert "email" not in result["results"]["blue"]


def test_the_backfill_leaves_address_less_rows_with_null_code(conn):
    """A row with no address can never be claimed and needs no code."""
    import db
    conn.execute(
        "INSERT INTO player (display_name, email_hash, email_masked, created_at) "
        "VALUES (%s, %s, %s, %s)",
        ("Taylor Quinn", None, None, 1234567890.0))
    conn.commit()
    db.init_db(conn)
    row = conn.execute("SELECT * FROM player WHERE display_name = 'Taylor Quinn'").fetchone()
    assert row["recovery_code"] is None
