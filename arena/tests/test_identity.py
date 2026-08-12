import identity

SALT = "test-salt"


def test_the_same_address_always_hashes_the_same_way():
    assert identity.hash_email("alex@example.com", SALT) == \
           identity.hash_email("alex@example.com", SALT)


def test_case_and_stray_spaces_do_not_make_a_second_player():
    assert identity.hash_email("  Alex@Example.COM ", SALT) == \
           identity.hash_email("alex@example.com", SALT)


def test_a_different_salt_gives_a_different_hash():
    assert identity.hash_email("alex@example.com", "one") != \
           identity.hash_email("alex@example.com", "two")


def test_the_hash_carries_none_of_the_address():
    digest = identity.hash_email("alex@example.com", SALT)
    assert "alex" not in digest
    assert "example" not in digest


def test_masking_keeps_the_first_letter_the_last_letter_and_the_domain():
    assert identity.mask_email("alex@example.com") == "a***x@example.com"


def test_masking_a_one_letter_local_part_does_not_show_it_twice():
    assert identity.mask_email("a@example.com") == "a***@example.com"


def test_masking_normalises_first_so_the_board_never_shows_shouty_addresses():
    assert identity.mask_email(" Alex@Example.COM ") == "a***x@example.com"


def test_a_signed_token_round_trips():
    assert identity.verify_token(identity.sign_token(42, "secret"), "secret") == 42


def test_a_token_signed_with_another_secret_is_refused():
    assert identity.verify_token(identity.sign_token(42, "secret"), "other") is None


def test_editing_the_player_id_out_of_a_token_is_refused():
    _, _, mac = identity.sign_token(42, "secret").partition(".")
    assert identity.verify_token(f"99.{mac}", "secret") is None


def test_rubbish_is_refused_rather_than_raising():
    assert identity.verify_token(None, "secret") is None
    assert identity.verify_token("", "secret") is None
    assert identity.verify_token("not-a-token", "secret") is None
    assert identity.verify_token("42", "secret") is None
