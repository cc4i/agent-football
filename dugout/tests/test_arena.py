import httpx
import pytest

import arena


def test_every_call_goes_to_the_workshop_and_the_blue_dugout(fake_arena):
    arena.read_profiles()
    arena.read_profile("forward")
    assert fake_arena.asked("GET") == [
        "/api/rooms/WRKS/teams/blue/profiles",
        "/api/rooms/WRKS/teams/blue/profiles/forward",
    ]


def test_the_service_token_is_presented_on_every_call(fake_arena):
    arena.read_profiles()
    arena.patch_profile("forward", {"finishing": 0.9}, "a tuner", "score more")
    assert fake_arena.tokens == ["test-service-token", "test-service-token"]


def test_the_rules_come_back_role_by_role(fake_arena):
    assert set(arena.rules()) == {"defender", "midfielder", "forward",
                                  "goalkeeper"}
    assert arena.rules()["forward"]["finishing"]["max"] == 1.0


def test_a_patch_reports_only_what_moved(fake_arena):
    moved = arena.patch_profile(
        "forward", {"finishing": 0.9, "shotPower": 0.6}, "a tuner", "score more")
    assert moved["changed"] == {"finishing": 0.9}
    assert arena.read_profile("forward")["finishing"] == 0.9


def test_a_refused_patch_carries_every_reason_the_arena_gave(fake_arena):
    with pytest.raises(arena.Refused) as refusal:
        arena.patch_profile("forward", {"finishing": 4, "nope": 1},
                            "a tuner", "score more")
    assert "between 0.0 and 1.0" in str(refusal.value)
    assert "nope" in str(refusal.value)


def test_a_refusal_with_one_sentence_reads_as_that_sentence(fake_arena):
    fake_arena.refusal = (409, "that match is over")
    with pytest.raises(arena.Refused, match="that match is over"):
        arena.read_profiles()


def test_a_refusal_with_no_words_at_least_names_its_status(monkeypatch):
    # A proxy or a crash answers in HTML, not in the arena's JSON.
    def wordless(request):
        return httpx.Response(503, text="<html>bad gateway</html>")

    monkeypatch.setattr(
        arena, "_session", httpx.Client(transport=httpx.MockTransport(wordless)))
    with pytest.raises(arena.Refused, match=r"the arena refused \(503\)"):
        arena.read_profiles()


def test_an_arena_that_is_not_running_says_so_in_plain_words(fake_arena):
    # The manager's next move is to run arena/run.sh, so the address they need
    # to see it answering on is in the message.
    fake_arena.silent = True
    with pytest.raises(arena.Down, match="http://127.0.0.1:8003"):
        arena.read_profiles()


def test_reading_needs_no_token_because_the_arena_asks_for_none(fake_arena,
                                                                monkeypatch):
    # The pitch reads profiles with no session at all, so a dugout without a
    # token can still show the manager the squad.
    monkeypatch.delenv("ARENA_SERVICE_TOKEN")
    assert arena.read_profiles()["forward"]["finishing"] == 0.5
    assert fake_arena.tokens == [None]


def test_writing_without_a_token_is_refused_before_it_is_sent(fake_arena,
                                                              monkeypatch):
    # The arena would answer 401 in words written for a phone that has not
    # joined a match, which tells whoever forgot to export the token nothing.
    monkeypatch.delenv("ARENA_SERVICE_TOKEN")
    with pytest.raises(arena.Refused, match="ARENA_SERVICE_TOKEN is unset"):
        arena.shout("press them")
    assert fake_arena.seen == []


def test_a_role_that_is_not_one_cannot_walk_out_of_the_url(fake_arena):
    with pytest.raises(arena.Refused):
        arena.read_profile("../../rooms")
    assert fake_arena.asked("GET") == [
        "/api/rooms/WRKS/teams/blue/profiles/..%2F..%2Frooms"]


def test_the_socket_is_the_same_arena_as_the_calls(monkeypatch):
    monkeypatch.delenv("ARENA_URL", raising=False)
    assert arena.socket_url() == "ws://127.0.0.1:8003/ws/rooms/WRKS"
    monkeypatch.setenv("ARENA_URL", "https://arena.example.com/")
    assert arena.socket_url() == "wss://arena.example.com/ws/rooms/WRKS"


def test_the_arena_can_be_somewhere_else(monkeypatch):
    monkeypatch.setenv("ARENA_URL", "http://box.local:9000/")
    assert arena.base_url() == "http://box.local:9000"
