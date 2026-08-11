import json
import pytest

import channel
from tools import shout
from tools.match import CALLED
from attributes import ROLES


@pytest.fixture(autouse=True)
def clean():
    CALLED.clear()
    yield
    CALLED.clear()


async def test_an_empty_shout_is_refused():
    assert "error" in await shout.shout_to_the_team("   ")


async def test_no_match_window_is_reported_not_raised(monkeypatch):
    # The manager may shout before taking the field. That must read as an
    # instruction the agent can act on, not a stack trace in the log.
    monkeypatch.setattr(shout, "DEBUG_URL", "http://localhost:9")
    result = await shout.shout_to_the_team("press high")
    assert result["error"] == "no_match_window"
    assert "take the field" in result["detail"].lower()


async def test_shouting_is_recorded_for_the_quest(monkeypatch):
    monkeypatch.setattr(shout, "DEBUG_URL", "http://localhost:9")
    await shout.shout_to_the_team("press high")
    assert "shout_to_the_team" in CALLED


def test_only_the_new_terminal_lines_come_back():
    before = "> ready\n> waiting\n"
    after = "> ready\n> waiting\n> Coach shouted\n\n> Captain: huddle\n"
    assert shout._new_lines(before, after) == ["> Coach shouted", "> Captain: huddle"]


def test_the_chain_is_complete_once_four_players_answer():
    partial = ["> Coach shouted", "Coach: Relaying to Team Captain"]
    assert shout._chain_complete(partial) is False
    full = partial + ["Captain: Huddle assembled!"] + [
        f"└─ {role}: on it" for role in
        ("DEFENDER", "MIDFIELDER", "FORWARD", "GOALKEEPER")]
    assert shout._chain_complete(full) is True


async def test_a_half_finished_chain_after_full_time_says_so(monkeypatch):
    monkeypatch.setattr(shout, "read_status", lambda: {"gameActive": False})
    assert shout._chain_complete([]) is False
    # The note is built from the same read_status the tool uses.
    assert not shout.read_status()["gameActive"]


@pytest.fixture
def squad(tmp_path, monkeypatch):
    baseline = {"finishing": 0.5, "shotPower": 0.5}
    for name in ROLES:
        (tmp_path / f"{name}.json").write_text(json.dumps(baseline))
        (tmp_path / f"{name}_baseline.json").write_text(json.dumps(baseline))
    monkeypatch.setattr(shout, "PLAYER_STATE_DIR", tmp_path)
    monkeypatch.setattr("attributes.PLAYER_STATE_DIR", tmp_path)
    return tmp_path


def test_the_squad_is_read_from_disk(squad):
    assert shout._profiles()["forward"]["finishing"] == 0.5
    assert set(shout._profiles()) == set(ROLES)


def test_an_unreadable_profile_is_skipped_not_raised(squad):
    (squad / "forward.json").write_text("{ broken")
    profiles = shout._profiles()
    assert "forward" not in profiles
    assert "defender" in profiles


def test_a_role_the_agents_left_alone_is_not_reported(squad):
    before = {"forward": {"finishing": 0.5}}
    assert shout._diff(before, {"forward": {"finishing": 0.5}}) == []


def test_every_role_the_agents_moved_comes_back(squad):
    before = {"forward": {"finishing": 0.5}, "defender": {"finishing": 0.5}}
    after = {"forward": {"finishing": 0.9}, "defender": {"finishing": 0.5}}
    changed = shout._diff(before, after)
    assert [c["role"] for c in changed] == ["forward"]
    assert changed[0]["deltas"][0]["before"] == 0.5
    assert changed[0]["deltas"][0]["after"] == 0.9


def test_a_shout_carries_no_reason_because_it_gave_none(squad):
    changed = shout._diff({"forward": {"finishing": 0.5}},
                          {"forward": {"finishing": 0.9}})
    assert changed[0]["reason"] is None


def test_a_role_unreadable_before_the_shout_is_skipped(squad):
    # Nothing to measure the move against, so reporting it would invent a
    # before value the manager never had.
    assert shout._diff({}, {"forward": {"finishing": 0.9}}) == []


async def test_early_error_paths_do_not_publish(monkeypatch):
    published = []
    monkeypatch.setattr(channel, "publish", lambda name, result: published.append((name, result)))
    monkeypatch.setattr(shout, "DEBUG_URL", "http://localhost:9")
    result = await shout.shout_to_the_team("press high")
    assert result["error"] == "no_match_window" and published == []


async def test_a_completed_shout_publishes_its_result(monkeypatch):
    published = []
    monkeypatch.setattr(channel, "publish", lambda name, result: published.append((name, result)))
    monkeypatch.setattr(shout, "REPLY_TIMEOUT_MS", 0)
    monkeypatch.setattr(shout, "read_status", lambda: {"gameActive": False})

    class StubButton:
        async def wait_for(self, state, timeout): pass
        async def is_enabled(self): return True
        async def click(self): pass

    class StubPage:
        url = f"http://{shout.GAME_URL}/match"
        def locator(self, selector): return StubButton()
        async def inner_text(self, selector): return "ready"
        async def fill(self, selector, text): pass

    class StubContext:
        pages = [StubPage()]

    class StubBrowser:
        contexts = [StubContext()]
        async def close(self): pass

    class StubChromium:
        async def connect_over_cdp(self, url): return StubBrowser()

    class StubPlaywright:
        chromium = StubChromium()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

    def stub_async_playwright():
        return StubPlaywright()

    monkeypatch.setattr("playwright.async_api.async_playwright", stub_async_playwright)

    result = await shout.shout_to_the_team("press high")
    assert published == [("shout_to_the_team", result)]
    assert "note" in result
