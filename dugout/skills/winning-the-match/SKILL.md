---
name: winning-the-match
description: Use when tuning any player attributes in Futsal WorldCup, or when the manager asks the team to defend, attack, press, score or stop conceding. Explains how the simulation turns attributes into goals, what the opponent is fixed at, and which levers actually move the scoreline.
---

# Winning the match

The blue team loses by default. Measured over eight full matches at 1x with
the shipped attributes: **blue 2 goals, red 16**. Tuning is not decoration
here, it is the difference between a demoralising 0-3 and a win.

## The one thing that catches everyone

A number's meaning depends on its size.

    value <= 2.0  ->  a multiplier or a fraction of a maximum
    value >  2.0  ->  an absolute quantity, used as-is

So `"speed": 0.5` on the forward is **not** half of some sensible default. It
is `0.5 x 260 = 130 pixels per second`, against an opponent forward who runs
at a flat `200`. Setting `speed: 0.95` gives 247 and beats them.

Base speeds, multiplied by the `speed` weight:

| role | base |
|---|---|
| defender | 210 |
| midfielder | 235 |
| forward | 260 |
| goalkeeper | 180 |

Distances resolve as `value <= 2 ? max * value : value`:

| attribute | max |
|---|---|
| shotRange | 700 |
| passRange | 600 |
| tackleRadius | 90 |
| interceptionRadius | 100 |

`decisionDelay` and `tackleCooldown` are milliseconds and always absolute.
Lower is faster. Everything else is a 0.0-1.0 weight.

## What you are playing against

The opponent never changes. These are its fixed values, so treat them as the
bar to clear:

| | defender | midfielder | forward |
|---|---|---|---|
| speed (px/s) | 150 | 180 | 200 |
| shotRange (px) | 300 | 500 | 700 |
| shotPower | 0.60 | 0.75 | 0.95 |
| chance to shoot | 25% | 15% | 70% |
| decisionDelay (ms) | 150 | 100 | 50 |

Its keeper sits on its line and never sweeps.

## How a goal actually happens

Work backwards from the net. A shot only exists if every one of these holds:

1. **The player is in the opposition half.** Blue only shoots from `x > 700`.
2. **They are inside their own shotRange of the goal.** A forward on
   `shotRange: 0.5` gets 350px, which is barely outside the box: they carry
   the ball into traffic instead of shooting.
3. **They choose to shoot rather than pass.** The chance is `1 - passProbability`.
   A forward on `passProbability: 0.8` shoots one time in five.
4. **No teammate is in the way.** This is the trap. The kick is redirected to
   any teammate within 47 degrees of the shot and closer than 480px, and it
   becomes a pass. Pushing every role forward at once means the forward's
   shots keep turning into square balls.
5. **The shot beats the keeper.** Speed is `420 + 360 x shotPower`, so 0.5
   gives 600 and 0.95 gives 762. The aim is a corner of a goal mouth 236px
   tall.

The lesson from step 4: do not send the whole team forward. Raise the
forward's `attackPositioning` and leave the defender's low.

## The plan that actually wins

The midfielder is the shipped squad's weakest link at `speed: 0.5`, or 118
px/s against an opponent midfielder doing 180. The middle is lost before
anything else goes wrong, which is why the game feels like constant pressure.

Each tuner changes at most three attributes per call. These twelve were
measured over eight matches each and take the team from **0 wins, 1 draw, 7
losses** to **4 wins, 1 draw, 3 losses**, with goals conceded falling from 12
to 8. Start here, then adjust.

| role | change to | and to | and to |
|---|---|---|---|
| forward | `speed` 0.95 | `shotRange` 1.0 | `shotPower` 0.95 |
| midfielder | `speed` 0.85 | `decisionDelay` 60 | `defensiveWorkRate` 0.9 |
| defender | `defensePositioning` 0.95 | `interceptionRadius` 0.95 | `tackleCooldown` 200 |
| goalkeeper | `trackingSpeed` 0.9 | `attackPositioning` 0.0 | |

Why each one:

- The forward is the only player who reliably shoots, so it gets the reach
  and the power. `shotRange` 1.0 is 700px, matching the opponent's forward.
- The midfielder's `defensiveWorkRate` is what wins the middle back. Raising
  `passProbability` here instead was tried and is worse: it scores more, 13
  goals against 9, but concedes 18 against 8 and loses the match. An open
  game is not a won game.
- `tackleCooldown` is milliseconds between tackles, so 200 means the defender
  can challenge again almost immediately.
- The keeper's `trackingSpeed` is a per-frame lerp toward the ball, so higher
  genuinely is better. Keep its `attackPositioning` at 0: every 0.1 pulls it
  18px off its line and opens the net behind it.

Leave the defender's `attackPositioning` low whatever else you do. Pushing
everyone forward gives the forward's shots more teammates to be intercepted
by, per step 4 above.

## Read the game before you touch it

`read_player_stats(role)` reports the current value and the legal range for
every attribute. The range is enforced on write, so a change outside it comes
back as a rejection rather than taking effect. Read first: the squad may
already have been tuned, and stacking another change on top of a value you
did not check is how a team ends up worse.

`get_match_status()` gives score and time remaining. A match is 180 seconds
and changes reload into the running game within about two seconds, so after
tuning, check the score again before claiming it worked. If the score has not
moved and the match is still on, the honest answer is that it has not paid
off yet.
