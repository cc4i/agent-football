# Blue vs red: why nobody was scoring, and how to make goals feel earned

15 August 2026. Everything below is measured on the grounds, at 1x, in full
three-minute matches, with goals read out of the arena's own event log - the
same log scoring is computed from. 60 matches in total.

## The headline

**Blue could not score. Not rarely - could not.**

| batch | blue squad | n | BLUE | RED | W-D-L | goalless |
|---|---|---|---|---|---|---|
| A | as shipped | 12 | **0** | 16 | 0-4-8 | 33% |
| B | shooting cone cleared | 12 | **0** | 13 | 0-4-8 | 33% |
| C | the tuning `winning-the-match` says wins | 12 | **0** | 12 | 0-4-8 | 42% |
| | **before the fix** | **36** | **0** | **41** | **0-12-24** | **36%** |
| E | as shipped, goal unboarded | 12 | **3** | 11 | 1-3-8 | 25% |
| F | tuned, goal unboarded | 12 | 0 | 8 | 0-6-6 | 42% |

Three different squads, 36 matches, 108 minutes of football, zero blue goals.
That is not balance. That is a wall.

### The wall was literal

`game.js` builds each goal with `buildGoalColliders(backX, frontX)`: a collider
the full height of the goal mouth at `backX`, and the two posts at `frontX`.

```js
buildGoalColliders(this.leftGoalBack /*70*/,   this.leftGoalLine  /*150*/);  // correct
buildGoalColliders(this.rightGoalLine /*1258*/, this.rightGoalBack /*1338*/); // swapped
```

The right-hand goal - the one blue attacks - got its full-height back net built
**on the goal line**. Red's goal was boarded up.

It is invisible on screen, because the nets are painted into the pitch
background image. The goal looks like a goal. Blue's shots stop 5px short of
being one.

Recording a live match's frames off the room socket is what named it:

```
ball in RED's half (blue can shoot):              70.2%
ball within shooting distance of red's goal:       1.3%
mean ball x: 0.569        max ball x: 0.890
```

Blue **owned** the game - 70% territory - and got the ball to within shooting
distance 1.3% of the time. The ball's x never once passed 0.890 of the pitch.
The pitch is 1408 wide, the goal line is at 1258, the wall is 10px thick, so
its near face is at 1253 - which is 0.8899. The ball was hitting the wall on
every attack, all evening.

Fixed in `49bffec`: same argument order at both ends. The same shipped squad
then scores 3 in 12 and wins one, with first goals at 69s and 151s.

## What the two squads actually are

Blue plays the arena's baselines, which are **weights**. Red plays
`hardcodedDefaults` in `game.js`, which are **absolute**. Both resolve through
`value <= 2.0 ? max * value : value`.

| | blue | red | |
|---|---|---|---|
| forward speed | 247 px/s | 200 | blue |
| midfielder speed | 200 px/s | 180 | blue |
| defender speed | 168 px/s | 150 | blue |
| forward shotRange | 630 px | 700 | red |
| forward decisionDelay | 100 ms | 50 | red |
| keeper trackingSpeed | 0.80 | 0.05 | blue |
| keeper diveChance | 0.90 | 0.08 | blue |
| **keeper sweeps off its line** | **162 px** | **0** | **red** |

Two things follow.

**`winning-the-match/SKILL.md` is stale.** It says the shipped squad is weak -
midfielder at `speed: 0.5`, forward at `shotRange: 0.5` - and that its twelve
changes take blue from 0W-1D-7L to 4W-1D-3L. The shipped baselines have since
moved: the midfielder is already 0.85 and the forward already 0.95/0.9/1.0. The
agent is being told to make changes that are mostly already made. Measured
today, that plan produces 0 goals in 12 (batch C, and 0 in 12 again post-fix in
batch F). It needs rewriting against what ships now.

**Blue's keeper is its own worst enemy.** `attackPositioning: 0.9` pulls it up
to 162px off its line whenever the ball is upfield, and SKILL.md explicitly
says keep this at 0. Red's keeper never leaves its line. This is most of red's
41 goals.

## Why shouting helps and chips cannot

This is the most useful structural finding, and it is about **who can set what**.

Scoring in this simulation needs the four roles set to *different* values. The
reason is `releaseKick`, which is shared by shots and passes:

```js
const passTarget = this.findTeammateInDirection(player, teammates, kdx, kdy);
if (passTarget) { /* it becomes a pass */ } else { /* it is a shot */ }
```

Any teammate inside a **94-degree cone within 480px** of the shot steals it and
turns it into a square ball. So a team that scores needs its forward high and
everybody else low - a *shape*, not a setting.

Now look at what each control can express:

| control | who writes it | can it set roles differently? |
|---|---|---|
| Opening philosophy | `playbook.apply` over all four roles | **no** - one value, four roles |
| The four chips | `presets.apply` over all four roles | **no** - one value, four roles |
| A typed shout | four specialist agents, one per role | **yes** |
| The dugout's tuners | four subagents, one per role | **yes** |

`playbook.py` says so in its own docstring: a patch "names only attributes all
four roles share... applied to all four roles". So `high press` sets
`attackPositioning: 0.85` on the goalkeeper too, and `shoot-early` sets 0.95 on
the defender.

**That is why a shout can improve a team a lot and a chip cannot.** A chip is a
blunt instrument by construction. A shout is four agents each writing their own
player, which is the only mechanism in the product that can produce an
attacking shape. It is also exactly the thing the workshop is built to show
off, so the demo and the mechanics agree - which is worth keeping.

For head to head, your read is right and the reason is the same: both dugouts
are writing shapes, so the outcome is whatever the two sets of instructions
were, not a fixed house bar.

## Making goals happen in 3-5 minutes

A match is **180 seconds**. Post-fix, blue scores 0.25 a match, red 0.9, and
25-42% of matches finish goalless. For an event, that is the number to move.

The scoring system already assumes goals arrive early - `+500` for a first goal
inside 30 seconds, sliding to `+100` after two minutes. Today almost nobody
collects it. A manager's typical solo result is *lost, no goals, conceded one*,
which pays about 100 points out of a possible 2,600+. The board says as much:
the three names on it scored one goal each.

Ranked by how much they buy for how little risk:

1. **Ship the goal fix.** Done. It is the difference between "hard" and
   "impossible", and everything else is unmeasurable until it is in.
2. **Put blue's keeper back on its line.** `attackPositioning: 0.9 -> 0.0` in
   `arena/baselines/goalkeeper.json`. SKILL.md already says to; the baseline
   disagrees with it. This should take a large bite out of red's 0.9 a match.
3. **Rewrite `winning-the-match/SKILL.md` against the shipped baselines.** The
   agent is currently reasoning from numbers that are two revisions old, and
   its headline claim - 4W-1D-3L - does not reproduce.
4. **Give the forward the same reach as red's.** `shotRange: 0.9 -> 1.0` is
   630px against red's 700. One attribute, and it is the one that decides
   whether a shot exists at all.
5. **Consider shortening the pitch or lengthening the match.** 180 seconds with
   a 1408px pitch is a lot of ground. If goals still do not come after 2-4,
   this is the lever with the most headroom - and 3-5 minutes is your own
   target, so a 240s match is a one-line change worth trying.
6. **Do not fix it by making chips stronger.** Their blanket-to-four-roles
   shape is what stops them working; making the numbers bigger just crowds the
   shooting cone harder. `shoot-early` already sets `attackPositioning: 0.95`
   on all four roles, which is close to the worst thing you can do to a shot.

## Making a goal *feel* like something

The mechanics above decide whether goals happen. These decide whether anybody
cares when they do. Ordered by effort against payoff.

**The moment needs to be bigger than the score changing.** Right now a goal is
a number incrementing on a canvas and a line in a log. On a wall across a room,
that is nothing. A goal should own the big screen for two or three seconds:
freeze, name the scorer's manager, show the clock it went in at. The arena
already broadcasts `goal` on the room socket with the team and the score, so
every screen and phone can react to the same event - the plumbing is there.

**Pay the manager for the thing they just did.** The scoring engine already
knows whether a goal came within 45 seconds of a shout landing - that is
`EFFECTIVE_WINDOW_SECONDS` and it is worth +100. Nobody is told at the time.
Saying *"that shout made that goal, +100"* on the phone, the instant it
happens, connects the one thing the manager did to the one thing they wanted.
It is the whole product promise in a single toast, and it is computable from
the log the arena already keeps.

**Name the wall's near-misses.** Blue held 70% territory and scored nothing.
Even with a working goal, most attacks end without a goal, and a wall showing
only 0-0 reads as broken. The host already reports positions at 10Hz - a
"shot!" or "close!" event on the relay would fill the 170 seconds between
goals with the feeling that something is being attempted.

**Make the first goal loud, not the fifth.** The `+500` bracket at 30 seconds
is good design that nobody experiences. If the board's podium showed *"first
goal 0:27"* as prominently as the points total, the bracket becomes a thing
people chase rather than a line in a breakdown.

**Do not solve this by inflating the scoreline.** A 6-5 every time is as
unengaging as 0-0, faster. The target worth aiming at is roughly *one goal per
manager per match, with the first inside 90 seconds and a real chance of a
second* - enough that a shout can visibly change the game, rare enough that the
goal is still worth shouting about.

## What I would do next, in order

1. Deploy `49bffec` and watch a real event. Every number here changes underneath it.
2. Keeper to its line, forward's reach to 1.0. Re-measure with the harness.
3. Rewrite the skill against what actually ships.
4. Then, and only then, tune the match length.

## How to reproduce any of this

The measurement harness is throwaway and lives in `/tmp` - `measure_policy.py`
runs N solo matches per philosophy against a local arena and grounds and prints
goals, wins and time-to-first-goal; `watch_territory.py` records one match's
frames off the room socket and prints where the ball actually went. Both need a
local arena with `ARENA_PITCH_DIR` set and a grounds running against it. Twelve
matches run in parallel in a little under four minutes.

## What this report does not know

Cell sizes are three matches per philosophy. That is enough to establish "blue
scored 0 across 36 matches" beyond doubt, and **not** enough to rank the four
philosophies against each other - the post-fix per-philosophy numbers in
batches E and F should be read as a signal to investigate, not a ranking. The
tuned batches (C, F) scoring no higher than the shipped one is interesting and
under-powered; I would not act on it without more matches.

Nothing here measures a *typed* shout end to end, because that needs the Gemini
chain and costs quota per match. The claim that shouts can express a shape and
chips cannot is read off the code, not measured.
