import pytest

import codes


def test_a_generated_code_is_four_characters_from_the_alphabet():
    code = codes.generate(lambda candidate: False)
    assert len(code) == codes.LENGTH == 4
    assert set(code) <= set(codes.ALPHABET)


def test_the_alphabet_drops_the_characters_people_misread():
    # The code gets read off a big screen and typed on a phone, sometimes
    # shouted across a room. O/0 and I/1 are where that goes wrong.
    for character in "O0I1":
        assert character not in codes.ALPHABET


def test_generate_never_hands_out_a_code_that_is_taken():
    handed_out = set()
    for _ in range(50):
        code = codes.generate(handed_out.__contains__)
        assert code not in handed_out
        handed_out.add(code)


def test_generate_gives_up_rather_than_spinning_forever():
    with pytest.raises(codes.CodesExhausted):
        codes.generate(lambda candidate: True)


def test_the_workshop_code_is_not_one_generate_could_produce():
    # The dugout reserves it, so a generated code must never collide with it.
    assert not codes.is_valid(codes.WORKSHOP)


def test_is_valid_rejects_the_wrong_length_and_the_banned_letters():
    assert codes.is_valid("K7F2")
    assert not codes.is_valid("K7F")
    assert not codes.is_valid("K7F22")
    assert not codes.is_valid("K0F2")
    assert not codes.is_valid("k7f2")
