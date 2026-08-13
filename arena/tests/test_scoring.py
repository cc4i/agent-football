"""The scoring engine, read off canned logs.

No database and no socket here: `scoring` takes a list of log entries and gives
back points, so a match can be written down in a few lines and its total
checked by hand. That is the whole reason the engine is pure.
"""

import pytest

import fake_host
import scoring
from pathlib import Path

FIXTURES = Path(__file__).parent.parent / "fixtures"


def log(*entries):
    """A room's log, numbered and stamped the way the arena would have.

    `at` is match time in seconds, which is what the host reports, and it
    doubles as the wall clock here: in a canned match nothing waits, so the two
    only differ by an offset no rule in the engine can see.
    """
    return [{"seq": seq, "kind": kind, "match_ms": int(at * 1000),
             "wall_ts": 1_700_000_000.0 + at, "payload": payload}
            for seq, (at, kind, payload) in enumerate(entries, start=1)]


def goal(at, team="blue"):
    return (at, "goal", {"team": team})


def own_goal(at, team="blue"):
    return (at, "own_goal", {"team": team})


def shout(at, team="blue"):
    return (at, "shout.sent", {"team": team, "text": "push up"})


def answered(at, shout_seq, role="forward"):
    return (at, "profile.patch", {"team": "blue", "role": role, "shout_seq": shout_seq,
                                  "changed": {"aggression": 0.9}})


def points_for(entries, team="blue"):
    return scoring.score_attack(scoring.read(entries)[team])["points"]


def labels(entries, team="blue"):
    return [row["label"] for row in scoring.score_attack(scoring.read(entries)[team])["breakdown"]]


# ── What a log says happened ──────────────────────────────────────────────

def test_a_goal_counts_for_the_team_that_scored_it():
    facts = scoring.read(log(goal(10, "blue"), goal(20, "red"), goal(30, "blue")))
    assert (facts["blue"]["goals_for"], facts["blue"]["goals_against"]) == (2, 1)
    assert (facts["red"]["goals_for"], facts["red"]["goals_against"]) == (1, 2)


def test_an_own_goal_counts_for_the_other_side():
    facts = scoring.read(log(own_goal(10, "blue")))
    assert facts["blue"]["goals_for"] == 0
    assert facts["red"]["goals_for"] == 1
    assert facts["blue"]["goals_against"] == 1


def test_an_own_goal_can_win_a_match_for_the_side_that_did_not_touch_the_ball():
    facts = scoring.read(log(own_goal(10, "red")))
    assert facts["blue"]["outcome"] == "won"
    assert facts["red"]["outcome"] == "lost"


def test_a_goal_naming_no_known_side_is_not_counted_for_anybody():
    facts = scoring.read(log((10, "goal", {"team": "green"}), (20, "goal", {})))
    assert facts["blue"]["goals_for"] == facts["red"]["goals_for"] == 0


def test_a_match_nobody_scored_in_is_a_draw_for_both():
    facts = scoring.read(log((0, "kickoff", {}), (180, "full_time", {})))
    assert facts["blue"]["outcome"] == facts["red"]["outcome"] == "drew"


def test_never_scoring_is_not_the_same_as_scoring_on_the_whistle():
    assert scoring.read(log(goal(0)))["blue"]["first_goal_ms"] == 0
    assert scoring.read(log(goal(0, "red")))["blue"]["first_goal_ms"] is None


def test_the_first_goal_is_the_first_one_in_the_log():
    facts = scoring.read(log(goal(27.4), goal(112.3), goal(166.9)))
    assert facts["blue"]["first_goal_ms"] == 27_400


# ── Score attack ──────────────────────────────────────────────────────────

def test_the_total_is_the_sum_of_what_the_screen_shows():
    scored = scoring.score_attack(scoring.read(log(goal(27.4), goal(61.8, "red")))["blue"])
    assert scored["points"] == sum(row["points"] for row in scored["breakdown"])


@pytest.mark.parametrize("at, expected", [
    (0, 500), (29.9, 500), (30, 500), (30.1, 350), (60, 350),
    (61, 200), (120, 200), (120.1, 100), (179, 100),
])
def test_scoring_earlier_is_worth_more(at, expected):
    assert scoring._first_goal_points(int(at * 1000)) == expected


def test_a_side_that_never_scored_earns_nothing_for_a_first_goal():
    assert scoring._first_goal_points(None) == 0


def test_a_clean_sheet_win_is_worth_more_than_the_same_win_conceding():
    clean = points_for(log(goal(10)))
    leaky = points_for(log(goal(10), goal(20, "red"), goal(30)))
    assert clean == 1000 + 300 + 500 + 300
    assert leaky == 1000 + 600 + 500 - 100


def test_conceding_stops_costing_after_five():
    facts = scoring.read(log(*[goal(number, "red") for number in range(1, 9)]))["blue"]
    conceded = dict((row["label"], row["points"])
                    for row in scoring.score_attack(facts)["breakdown"])
    assert conceded["8 conceded"] == -500


def test_a_thrashing_is_still_worth_the_losing_hundred():
    assert points_for(log(*[goal(number, "red") for number in range(1, 9)])) == 100 - 500


def test_the_breakdown_says_what_happened_in_a_managers_words():
    assert labels(log(goal(27.4), goal(61.8, "red"), goal(100))) == [
        "Won the match", "2 goals", "First goal at 0:27", "1 conceded",
        "Clean sheet", "No shout led to a goal",
    ]


def test_one_of_something_is_not_written_as_one_of_them():
    assert labels(log(goal(27.4), goal(61.8, "red"), goal(100), goal(120, "red")))[1:4] \
        == ["2 goals", "First goal at 0:27", "2 conceded"]
    assert labels(log(goal(27.4), goal(61.8, "red")))[1:4] \
        == ["1 goal", "First goal at 0:27", "1 conceded"]


def test_the_breakdown_of_a_match_that_went_nowhere_still_names_every_line():
    assert labels(log((0, "kickoff", {}))) == [
        "Drew the match", "No goals", "Never scored", "Nothing conceded",
        "Clean sheet", "No shout led to a goal",
    ]


@pytest.mark.parametrize("match_ms, shown", [
    (0, "0:00"), (999, "0:01"), (27_400, "0:27"), (60_000, "1:00"), (166_900, "2:47"),
])
def test_a_match_time_reads_the_way_it_does_on_the_scoreboard(match_ms, shown):
    assert scoring.clock(match_ms) == shown


# ── Effective shouts ──────────────────────────────────────────────────────

def test_a_shout_the_squad_acted_on_before_a_goal_is_effective():
    entries = log(shout(10), answered(40, 1), goal(70))
    assert scoring.read(entries)["blue"]["effective"] == 1


def test_a_goal_more_than_the_window_after_the_squad_moved_is_nobodys_doing():
    entries = log(shout(10), answered(40, 1), goal(40 + scoring.EFFECTIVE_WINDOW_SECONDS + 1))
    assert scoring.read(entries)["blue"]["effective"] == 0


def test_a_goal_on_the_last_second_of_the_window_still_counts():
    entries = log(shout(10), answered(40, 1), goal(40 + scoring.EFFECTIVE_WINDOW_SECONDS))
    assert scoring.read(entries)["blue"]["effective"] == 1


def test_the_window_opens_when_the_squad_moved_and_not_when_the_shout_went_out():
    # The chain takes tens of seconds. Measured from the shout this goal is
    # outside the window; measured from the patch it caused, it is inside.
    entries = log(shout(0), answered(50, 1), goal(60))
    assert scoring.read(entries)["blue"]["effective"] == 1


def test_a_goal_before_the_squad_moved_was_not_caused_by_the_shout():
    entries = log(shout(10), goal(30), answered(40, 1))
    assert scoring.read(entries)["blue"]["effective"] == 0


def test_a_shout_nothing_came_of_is_not_effective():
    assert scoring.read(log(shout(10), goal(20)))["blue"]["effective"] == 0


def test_the_window_opens_on_the_first_of_the_four_answers():
    # Four specialists answer one shout seconds apart. Opening the window again
    # on the last of them would quietly extend it.
    entries = log(shout(0), answered(10, 1, "forward"), answered(30, 1, "midfielder"),
                  goal(10 + scoring.EFFECTIVE_WINDOW_SECONDS + 1))
    assert scoring.read(entries)["blue"]["effective"] == 0


def test_a_shout_cannot_be_paid_for_the_other_dugouts_goal():
    entries = log(shout(10, "blue"), answered(20, 1), goal(30, "red"))
    assert scoring.read(entries)["blue"]["effective"] == 0


def test_shouting_more_stops_paying_after_three_that_worked():
    entries = log(*[step for number in range(5) for step in (
        shout(number * 100), answered(number * 100 + 10, number * 3 + 1),
        goal(number * 100 + 20))])
    facts = scoring.read(entries)["blue"]
    assert (facts["shouts"], facts["effective"]) == (5, 5)
    paid = dict((row["label"], row["points"])
                for row in scoring.score_attack(facts)["breakdown"])
    assert paid["5 shouts led to goals"] == 300


def test_a_kick_off_stance_is_not_a_shout_anybody_gets_paid_for():
    # Philosophies are applied as profile patches at kick-off and belong to no
    # shout, so they must not open a window of their own.
    entries = log((0, "profile.patch", {"team": "blue", "role": "forward",
                                        "changed": {"aggression": 0.9}}),
                  goal(10))
    facts = scoring.read(entries)["blue"]
    assert (facts["shouts"], facts["effective"]) == (0, 0)


# ── Elo ───────────────────────────────────────────────────────────────────

def test_beating_an_equal_moves_both_by_half_the_k_factor():
    assert scoring.rated(1200, 1200, "won") == 1200 + scoring.K_FACTOR / 2
    assert scoring.rated(1200, 1200, "lost") == 1200 - scoring.K_FACTOR / 2


def test_a_draw_between_equals_moves_nobody():
    assert scoring.rated(1200, 1200, "drew") == 1200


def test_beating_somebody_far_better_is_worth_more_than_beating_an_equal():
    assert scoring.rated(1200, 1600, "won") - 1200 > scoring.rated(1200, 1200, "won") - 1200


def test_losing_to_somebody_far_better_costs_less_than_losing_to_an_equal():
    assert scoring.rated(1200, 1600, "lost") > scoring.rated(1200, 1200, "lost")


def test_a_pair_of_ratings_only_moves_points_between_them():
    blue, red = scoring.rated(1300, 1100, "won"), scoring.rated(1100, 1300, "lost")
    assert blue + red == pytest.approx(1300 + 1100)


# ── The recorded match ────────────────────────────────────────────────────

def test_the_recorded_match_scores_what_its_header_claims():
    """The fixture the fake host replays, scored without replaying it."""
    frames = fake_host.parse_log(FIXTURES / "match-3-1.jsonl")
    entries = log(*[(frame["t"], frame["kind"], frame.get("payload", {}))
                    for frame in frames if frame["type"] == "event"])
    facts = scoring.read(entries)["blue"]
    assert (facts["goals_for"], facts["goals_against"]) == (3, 1)
    assert facts["outcome"] == "won"
    assert facts["first_goal_ms"] == 27_400
    assert scoring.score_attack(facts)["points"] == 1000 + 900 + 500 - 100
