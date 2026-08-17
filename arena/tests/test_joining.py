"""Who a manager is, now that the address is theirs to withhold.

Two rules meet here. A name is unique across the venue, because it is what the
board shows and what the wall calls somebody. An email is optional, because the
only thing it buys is finding the same player again from another phone, and a
venue has no business holding an address it does not need for anything else.

Between them sits the question these tests are mostly about: when somebody taps
the button, which player row is that?
"""

import rooms

# Worded the way every other refusal the arena raises is: lower-case and
# unpunctuated, for `api.js` to make a sentence of on the phone.
TAKEN = "somebody at this event is already managing as Alex Rivera - pick another name"


def player(client, name, email=None, code=""):
    """One join, as a phone makes it. Under E1, claims need the recovery code."""
    body = {"display_name": name, "recovery_code": code}
    if email is not None:
        body["email"] = email
    return client.post("/api/players", json=body)


# ── The address is optional ──────────────────────────────────────────


def test_joining_without_an_email_at_all_is_allowed(client):
    response = player(client, "Alex Rivera")
    assert response.status_code == 200
    assert response.json()["display_name"] == "Alex Rivera"
    assert response.json()["email"] is None
    assert "arena_session" in response.cookies


def test_an_empty_email_is_the_same_as_none_at_all(client):
    # The form sends what the box holds, and an untouched optional box holds "".
    assert player(client, "Alex Rivera", "").json()["email"] is None


def test_a_player_who_gave_no_address_stores_neither_hash_nor_mask(client):
    identifier = player(client, "Alex Rivera").json()["id"]
    row = rooms.get_player(client.app.state.conn, identifier)
    assert row["email_hash"] is None
    assert row["email_masked"] is None


def test_two_players_may_both_withhold_their_address(client, phones):
    # The unique index on the hash is what this could have fallen foul of.
    # Postgres counts NULLs as distinct from one another, which is the whole
    # reason a second anonymous manager can exist at all.
    assert player(client, "Alex Rivera").status_code == 200
    phones.fresh()
    assert player(client, "Sam Okafor").status_code == 200


def test_an_address_that_is_not_one_is_still_refused(client):
    for bad in ("alex", "alex@", "@example.com", "alex@example"):
        assert player(client, "Alex Rivera", bad).status_code == 422, bad


# ── The name is not ──────────────────────────────────────────────────


def test_a_name_somebody_else_holds_is_refused_and_says_so(client, phones):
    player(client, "Alex Rivera", "alex@example.com")
    phones.fresh()
    clash = player(client, "Alex Rivera", "sam@example.com")
    assert clash.status_code == 409
    assert clash.json()["detail"] == TAKEN


def test_a_name_is_taken_whatever_its_case_and_spacing(client, phones):
    player(client, "Alex Rivera", "alex@example.com")
    phones.fresh()
    # The board would otherwise show two rows a person cannot tell apart.
    assert player(client, "  alex   RIVERA ", "sam@example.com").status_code == 409


def test_a_name_keeps_its_shape_but_not_its_stray_spaces(client):
    assert player(client, "  Alex   Rivera ").json()["display_name"] == "Alex Rivera"


def test_a_name_of_nothing_but_spaces_is_refused(client):
    assert player(client, "   ").status_code == 422


def test_no_name_at_all_is_refused_in_the_arena_s_own_words(client):
    trouble = player(client, "").json()["detail"][0]
    # Not "String should have at least 1 character", which is pydantic talking
    # to whoever wrote the request. What reads this is somebody holding a phone.
    assert trouble["msg"].endswith("that needs a name in it")
    # Located, so the form can say it under the box it is about rather than in
    # the banner over the whole page.
    assert trouble["loc"] == ["body", "display_name"]


def test_a_name_too_long_for_the_board_is_refused_in_the_same_voice(client):
    trouble = player(client, "A" * 41).json()["detail"][0]
    assert trouble["msg"].endswith("that is longer than the 40 characters the board shows")
    assert trouble["loc"] == ["body", "display_name"]


def test_a_free_name_is_still_free_for_the_taking(client, phones):
    player(client, "Alex Rivera", "alex@example.com")
    phones.fresh()
    assert player(client, "Sam Okafor").status_code == 200


# ── Which player row a join lands on ─────────────────────────────────


def test_your_own_name_is_never_taken_from_you(client):
    first = player(client, "Alex Rivera", "alex@example.com").json()
    # The same phone, the same name, a second match. The cookie is still there.
    second = player(client, "Alex Rivera", "alex@example.com")
    assert second.status_code == 200
    assert second.json()["id"] == first["id"]


def test_a_phone_that_has_joined_before_may_change_its_name(client):
    first = player(client, "Alex Rivera").json()
    second = player(client, "Alex R").json()
    assert second["id"] == first["id"]
    assert second["display_name"] == "Alex R"


def test_a_name_given_up_by_its_holder_is_free_for_somebody_else(client, phones):
    player(client, "Alex Rivera")
    player(client, "Alex R")
    phones.fresh()
    assert player(client, "Alex Rivera").status_code == 200


def test_an_address_brings_a_manager_back_on_a_phone_with_no_cookie(client, phones, conn):
    """An address still brings a manager back across phones, now with the recovery
    code. Before E1, the address alone was sufficient; after E1, a claim without
    the cookie needs the code."""
    alex_resp = player(client, "Alex Rivera", "alex@example.com").json()
    alex = alex_resp["id"]
    code = alex_resp["recovery_code"]
    phones.fresh()
    again = player(client, "Alex Rivera", "ALEX@example.com", code)
    assert again.status_code == 200
    assert again.json()["id"] == alex


def test_an_address_outranks_a_cookie_somebody_else_left_on_the_phone(client, phones):
    """An address still outranks a cookie, now with the recovery code. Before E1,
    the address alone overrode the cookie; after E1, the recovery code is required
    to prove the address is yours."""
    # Alex plays on their own phone, then borrows Sam's, which still holds
    # Sam's session. Typing their own address has to hand Alex their own place
    # on the board rather than quietly adding the match to Sam's.
    alex_resp = player(client, "Alex Rivera", "alex@example.com").json()
    alex = alex_resp["id"]
    alex_code = alex_resp["recovery_code"]
    phones.fresh()
    sam = player(client, "Sam Okafor", "sam@example.com").json()["id"]
    assert sam != alex

    borrowed = player(client, "Alex Rivera", "alex@example.com", alex_code)
    assert borrowed.status_code == 200
    assert borrowed.json()["id"] == alex


def test_a_returning_manager_who_leaves_the_address_blank_keeps_it(client):
    # Withholding it this time must not throw away the thing that has been
    # keeping their one place on the board.
    player(client, "Alex Rivera", "alex@example.com")
    assert player(client, "Alex Rivera").json()["email"] == "a***x@example.com"


def test_a_returning_manager_may_add_an_address_later(client):
    player(client, "Alex Rivera")
    assert player(client, "Alex Rivera", "alex@example.com").json()["email"] \
        == "a***x@example.com"


# ── Asking before you tap ────────────────────────────────────────────


def test_a_name_nobody_holds_reads_as_available(client):
    answer = client.get("/api/players/available", params={"name": "Alex Rivera"})
    assert answer.status_code == 200
    assert answer.json() == {"name": "Alex Rivera", "available": True}


def test_a_name_somebody_holds_reads_as_taken(client, phones):
    player(client, "Alex Rivera", "alex@example.com")
    phones.fresh()
    assert client.get("/api/players/available",
                      params={"name": "Alex Rivera"}).json()["available"] is False


def test_a_free_name_is_answered_about_as_it_will_be_stored(client):
    # Tidied, so the form can show back the name it is talking about.
    answer = client.get("/api/players/available", params={"name": " Alex   Rivera "})
    assert answer.json() == {"name": "Alex Rivera", "available": True}


def test_a_taken_name_comes_back_spelled_as_its_holder_spells_it(client, phones):
    player(client, "Alex Rivera", "alex@example.com")
    phones.fresh()
    answer = client.get("/api/players/available", params={"name": " alex   rivera "})
    # Not as it was typed. The board shows "Alex Rivera", the join refuses in
    # those words too, and a hint that says "alex rivera is taken" underneath a
    # box the manager typed lower case in reads as a different quarrel entirely.
    assert answer.json() == {"name": "Alex Rivera", "available": False}


def test_your_own_name_reads_as_available_to_you(client):
    # Otherwise a manager coming back for a second match is told the name on
    # their own board entry is taken, by themselves.
    player(client, "Alex Rivera", "alex@example.com")
    assert client.get("/api/players/available",
                      params={"name": "Alex Rivera"}).json()["available"] is True


def test_asking_about_nothing_is_refused_rather_than_answered(client):
    assert client.get("/api/players/available", params={"name": "  "}).status_code == 422


def test_asking_about_a_name_too_long_to_hold_is_refused(client):
    assert client.get("/api/players/available",
                      params={"name": "A" * 41}).status_code == 422


def test_asking_about_a_nul_is_refused_rather_than_bound(client):
    # psycopg will not bind one, so it cannot be allowed to reach the lookup.
    assert client.get("/api/players/available",
                      params={"name": "Al\x00ex"}).status_code == 422
