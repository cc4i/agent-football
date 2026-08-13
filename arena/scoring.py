"""What a manager earned, read out of a room's event log.

Nothing here opens a database or a socket. It takes a log and gives back
points, which is what lets the table below be checked against a canned match in
a test rather than by playing one.

The host is trusted for physics and not for scoring. Every number the arena
pays out is recomputed from the log the arena wrote itself, and a client that
submits a total is not asked.
"""

TEAMS = ("blue", "red")
OUTCOMES = ("won", "drew", "lost")

# Score attack, against the house side.
WIN, DRAW, LOSS = 1000, 400, 100
PER_GOAL = 300
PER_CONCEDED = -100
WORST_CONCEDED = -500
CLEAN_SHEET = 300
PER_EFFECTIVE_SHOUT = 100
MOST_EFFECTIVE_SHOUTS = 3

# The first goal, by the match clock. Read in order, first bracket that fits.
FIRST_GOAL_BRACKETS = ((30_000, 500), (60_000, 350), (120_000, 200))
FIRST_GOAL_LATE = 100

# How long after a shout reaches the squad a goal still counts as its doing.
# Measured from the first profile write the shout caused rather than from the
# shout itself: the chain takes 30-60 seconds to answer, so measuring from the
# send would pay for a fast chain rather than for a good instruction.
EFFECTIVE_WINDOW_SECONDS = 45.0

# Elo, for head to head only. Everybody starts level and K is the standard 32:
# most people at an event play once or twice, so a rating that moves slowly
# would say nothing at all by the time they stop.
START_RATING = 1200.0
K_FACTOR = 32.0


def read(log):
    """What the tables need, per dugout, out of one room's whole log.

    One pass, because the log is the only source and walking it twice invites
    the two walks to disagree about what a goal is.
    """
    goals = {team: [] for team in TEAMS}
    shouts = {team: [] for team in TEAMS}
    # Shout seq -> when the first profile write it caused landed.
    opened = {}
    for entry in log:
        kind = entry["kind"]
        payload = entry.get("payload") or {}
        if kind in ("goal", "own_goal"):
            scored_for = _credited(kind, payload.get("team"))
            if scored_for:
                goals[scored_for].append(entry)
        elif kind == "shout.sent":
            if payload.get("team") in shouts:
                shouts[payload["team"]].append(entry["seq"])
        elif kind == "profile.patch":
            caused_by = payload.get("shout_seq")
            # The window opens once. A shout moves four profiles and the last
            # of them can land seconds after the first.
            if caused_by is not None and caused_by not in opened:
                opened[caused_by] = entry["wall_ts"]

    return {team: _facts(team, goals, shouts[team], opened) for team in TEAMS}


def _credited(kind, named):
    """Which dugout a goal counts for.

    An own goal names the side that put it into its own net, because that is
    what the pitch knows at the moment it happens. It counts for the other one.
    """
    if named not in TEAMS:
        return None
    if kind == "own_goal":
        return TEAMS[1] if named == TEAMS[0] else TEAMS[0]
    return named


def _facts(team, goals, shouts, opened):
    mine, theirs = goals[team], goals[_other(team)]
    return {
        "goals_for": len(mine),
        "goals_against": len(theirs),
        "outcome": _outcome(len(mine), len(theirs)),
        # None rather than 0: never scoring and scoring on the whistle are not
        # the same thing, and a results screen has to say which happened.
        "first_goal_ms": mine[0]["match_ms"] if mine else None,
        "shouts": len(shouts),
        "effective": _effective(mine, shouts, opened),
    }


def _other(team):
    return TEAMS[1] if team == TEAMS[0] else TEAMS[0]


def _outcome(mine, theirs):
    if mine > theirs:
        return "won"
    return "drew" if mine == theirs else "lost"


def _effective(goals, shouts, opened):
    """How many of this dugout's shouts were followed by a goal for it."""
    scored_at = sorted(entry["wall_ts"] for entry in goals)
    landed = 0
    for seq in shouts:
        # A shout the squad never acted on has no window. Nothing moved, so
        # nothing it did can have led to anything.
        start = opened.get(seq)
        if start is None:
            continue
        if any(start <= when <= start + EFFECTIVE_WINDOW_SECONDS for when in scored_at):
            landed += 1
    return landed


def score_attack(facts):
    """Points and the rows a results screen shows, for one dugout's facts.

    The rows are built here rather than on the phone so that the screen and the
    total can never disagree: the total is the sum of what is shown.
    """
    rows = [
        _row({"won": "Won the match", "drew": "Drew the match", "lost": "Lost the match"}
             [facts["outcome"]],
             {"won": WIN, "drew": DRAW, "lost": LOSS}[facts["outcome"]]),
        _row(_plural(facts["goals_for"], "goal", "No goals"),
             facts["goals_for"] * PER_GOAL),
        _row(_first_goal_label(facts["first_goal_ms"]),
             _first_goal_points(facts["first_goal_ms"])),
        _row(_conceded_label(facts["goals_against"]),
             max(WORST_CONCEDED, facts["goals_against"] * PER_CONCEDED)),
        _row("Clean sheet", CLEAN_SHEET if not facts["goals_against"] else 0),
        _row(_shouts_label(facts["effective"]),
             min(facts["effective"], MOST_EFFECTIVE_SHOUTS) * PER_EFFECTIVE_SHOUT),
    ]
    return {"points": sum(row["points"] for row in rows), "breakdown": rows}


def _row(label, points):
    return {"label": label, "points": points}


def _plural(count, noun, none):
    if not count:
        return none
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _first_goal_label(match_ms):
    return f"First goal at {clock(match_ms)}" if match_ms is not None else "Never scored"


def _first_goal_points(match_ms):
    if match_ms is None:
        return 0
    for limit, points in FIRST_GOAL_BRACKETS:
        if match_ms <= limit:
            return points
    return FIRST_GOAL_LATE


def _conceded_label(against):
    return f"{against} conceded" if against else "Nothing conceded"


def _shouts_label(effective):
    if not effective:
        return "No shout led to a goal"
    return f"{effective} shout{'' if effective == 1 else 's'} led to goals"


def clock(match_ms):
    """A match time as a manager reads it on the scoreboard."""
    whole = max(0, round((match_ms or 0) / 1000))
    return f"{whole // 60}:{whole % 60:02d}"


def rated(mine, theirs, outcome):
    """My rating after a match against somebody on `theirs`.

    Elo is stored and shown and does not sort the head to head board: one match
    is not a rating, and most people at an event play once.
    """
    expected = 1 / (1 + 10 ** ((theirs - mine) / 400))
    got = {"won": 1.0, "drew": 0.5, "lost": 0.0}[outcome]
    return mine + K_FACTOR * (got - expected)
