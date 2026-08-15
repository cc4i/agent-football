# Why a shout cannot win the match, and what to change

16 August 2026. 240 matches on the deployed venue, plus about 31,000 recorded
frames off the room socket and a read of `game.js`. A follow-up to
`2026-08-15-scoring-and-engagement-report.md` and
`2026-08-15-shout-quality-report.md`, both of which this corrects in places.

## The headline

**Shouting does not raise blue's chance of winning, and neither does any
attribute change I could construct.** Every arm below was measured on prod, in
paired waves, against an untouched control running in the same minute on the
same grounds.

| arm | n | W-D-L | win% | 95% CI | blue/match | red/match | p vs control |
|---|---|---|---|---|---|---|---|
| **untouched (control)** | 96 | 17-51-28 | **18%** | 11-27% | 0.39 | 0.67 | - |
| shout, as shipped | 40 | 6-16-18 | 15% | 7-29% | 0.35 | 0.82 | 0.805 |
| keeper put back on its line | 16 | 2-7-7 | 12% | 3-36% | 0.38 | 1.00 | 1.000 |
| keeper on its line + shout | 16 | 2-7-7 | 12% | 3-36% | 0.31 | 0.62 | 1.000 |
| forward shoots from range | 16 | 3-5-8 | 19% | 7-43% | 0.50 | 0.94 | 1.000 |
| forward decides at once | 40 | 9-11-20 | 22% | 12-38% | 0.38 | 0.72 | 0.633 |
| maximal attacking squad | 16 | 2-7-7 | 12% | 3-36% | 0.38 | 1.12 | 1.000 |

Pooled: 56 shouted matches win 14%, 96 untouched matches win 18%, Fisher
p = 0.655. The last arm is the one that settles it - fourteen attributes across
three roles, the best attacking squad the attribute surface can express, forward
at maximum everything and defenders pulled back out of the lane. It scored 0.38
a match, exactly what doing nothing scores.

Two of these deserve a note because they contradict earlier reports.

**The keeper was never the problem.** `2026-08-15-scoring-and-engagement-report.md`
said blue's keeper was "its own worst enemy" and "most of red's 41 goals",
reading `attackPositioning: 0.9` off `baselines/goalkeeper.json`. It missed
`trackingSpeed: 0.8` two lines below it in `updateGkAI`:

```js
gk.x += (targetX - gk.x) * trackingSpeed;   // 0.8 closes 80% of the gap per frame
```

Three frames is 99% of the way home, about 50ms, and `targetX` slides back on
its own as `ballX` falls during a counter. The keeper is never caught upfield.
Patched to 0.0 and verified on the live profile in 32 of 32 rooms, it changed
nothing. That line should be struck from the earlier report.

**Arm three of the shout report does not reproduce.** Its 3-1-2 over six
matches is 6-16-18 over forty.

## The reason: half of what a shout writes is not read

The engine's whole vocabulary is the ~21 keys in `hardcodedDefaults` in
`game.js`. The baseline JSONs carry 147 attributes across the four roles.
Everything outside that vocabulary is decoration - the arena validates it, the
event log records it, the dugout renders it moving, and `game.js` never looks
at it.

Here is what the four specialists actually wrote, counted over **50 landed
shouts** on prod:

| written | share of shouts | read by `game.js`? |
|---|---|---|
| `forward.attackPositioning` | 100% | yes, but already 1.0 at baseline |
| `midfielder.forwardPassProbability` | **100%** | **no. Zero references** |
| `midfielder.attackPositioning` | 50% | yes - pushes the midfielder into the lane |
| `forward.shotRange` | 42% | yes |
| `midfielder.shootingUrgency` | 6% | **no. Zero references** |
| `midfielder.speed` | 6% | yes |
| `midfielder.passProbability` | 2% | yes |

```
$ grep -c forwardPassProbability game/frontend/src/game.js          # 0
$ grep -c forwardPassProbability /tmp/deployed_game.js              # 0  (deployed bundle)
```

The single most reliable thing the squad does in answer to a shout - fifty
times out of fifty - is write an attribute the simulation does not read.

This is the direct result of the last round of tuning. `198aaf8` and `df92e8b`
taught the specialists that `forwardPassProbability` is how you get the ball to
the forward, and the shout-quality report scored that as the win: *"the agents
took it: `midfielder.forwardPassProbability` went 0 → 6/6"*. They did take it.
It does nothing. The behavioural change was real and was measured correctly;
what was never checked is whether the attribute exists in the engine.

`forward.attackPositioning` is barely better. Its baseline is already 1.0, so
under `counter` the write is a no-op, and under `high press` it only undoes the
0.85 the philosophy just applied.

So of everything a shout currently writes, roughly one write in four can move
the simulation at all, and the ones that can are marginal or actively harmful
(`midfielder.attackPositioning` crowds the shooting lane).

## Where the football actually goes

Eighteen matches recorded frame by frame off `/ws/rooms/{code}` at 10Hz, both
teams measured inside the same match.

| | blue | red |
|---|---|---|
| territory (ball in the opponent's half) | **65.2%** | 34.8% |
| forward inside its shooting zone | **68.9%** | 37.2% |
| forward on the ball inside 220px of goal | **4.11%** | 1.10% |
| forward on the ball beyond 400px | 4.00% | **12.78%** |
| ball arriving at the opponent's goal mouth | **5.93%** | 0.49% |
| shot stolen by a teammate in the cone | 6.4% | 7.9% |
| goals per match | 0.39 | 0.67 |

Blue is not short of chances. It has the territory, its forward is camped in
the box, and it puts the ball in the strip in front of red's goal **twelve
times as often** as red manages at the other end. It converts about four times
worse.

The shooting-cone theory from the earlier report is dead: `findTeammateInDirection`
steals 6.4% of blue's chances and 7.9% of red's. It is real, it is symmetric,
and it is not what is happening.

Three findings name what is:

**1. Blue walks the ball in; red shoots from distance.** Blue's forward is on
the ball inside 220px three and a half times more than red's; red's is on the
ball beyond 400px three times more than blue's. `decisionDelay` is not a
re-decision interval, it is how long a player dithers after winning the ball
(`game.js:910`, `time - possessionStart >= decisionDelay`), and blue's forward
spends its 100ms dribbling forward into a set defence.

**2. Red's keeper is already where the ball is.** Measured over every frame the
ball spent in red's box: the keeper's y is a **median 2px** from the ball's, and
within 20px in **100%** of them. Not because it tracks well - `trackingSpeed` is
0.05, the slowest value in the game - but because blue arrives slowly and
centrally, and the keeper has seconds to settle.

**3. Every blue shot is aimed at the same place.** `game.js:938`:

```js
const shotY = oppGk.y < 380 ? 460 + this.chance() * 25 : 300 - this.chance() * 25;
```

Red's keeper parks at exactly y=380 (`gk.y += (380 - gk.y) * 0.05` converges
there, and its `attackPositioning` is 0.0 so it never leaves x=1228). `380 < 380`
is false, so blue aims at y=275-300 **every single time** - a 25px band whose
lower edge is 7px from the top post collider, with a ball of radius 7.1px.
Red's shots do not have this problem: blue's keeper has `trackingSpeed: 0.8`
and moves around, so `oppGk.y < 380` genuinely varies and red alternates corners.

Where the ball dies, as a histogram of x inside the goal-mouth band:

```
red's goal (line 1258)                 blue's goal (line 150)
  1180-1189   33 #########               150-159    3 ###   <- line
  1190-1199   28 ########                140-149    5 #####
  1200-1209   22 ######                  130-139    4 ####
  1220-1229    2 #    <- keeper 1228     120-129    4 ####
  1240-1249   62 ###############         110-119    7 #####
  1250-1259    3 #    <- goal line        60-69     5 #####
  1260-1269    3 #                        50-59     4 ####
```

Blue's attacks pile up in front of the keeper and stop. Red's run through the
line and out the back.

That is the whole story: blue funnels every attack into one slow, central,
identically-aimed shot at a keeper that is already standing on it, and no
attribute in the manager's reach changes where a shot goes or how it is saved.
Which is why all seven arms above came back null.

## What I recommend

Three changes, in order of how much they cost and how sure I am.

### 1. Stop the shout spending itself on attributes that do not exist

No engine change, and it is the difference between a shout doing a quarter of
something and doing all of it.

- **Correct the specialists' model.** `SIMULATION_MODEL` in
  `game/agents/specialist_agents/tools.py` currently names
  `forwardPassProbability` as one of the levers for delivery. It is not a lever.
  It should name only attributes `game.js` reads, and the guidance for "we need
  a goal" should point at the ones that move the simulation.
- **Make a dead write impossible rather than merely discouraged.** The engine's
  vocabulary is enumerable - it is the keys of `hardcodedDefaults`. Either strip
  the other 65 attributes from `arena/baselines/*.json`, or have
  `arena/attributes.py` refuse them with a message naming the real one. A
  validator that accepts `finishing: 1.0` and a simulation that ignores it is a
  trap the agents will keep walking into, because the names are plausible.

I would do this regardless of what happens to the rest, because the current
state also means the dugout's tuning panel shows managers changes that cannot
matter.

### 2. Make conversion something an attribute can control

This is the one that unlocks the win rate, and it is an engine change, so it
needs your approval before I touch it.

Right now nothing a manager can write affects whether a shot goes in. The
proposal is one line and one wiring:

- **Widen the aim.** Replace the binary corner choice keyed on `oppGk.y < 380`
  with an aim point that varies - offset from the keeper's actual y rather than
  from a constant, so blue stops firing into the same 25px band all match.
- **Give the spread an owner.** `forward.finishing` already exists in the
  baseline at 1.0 and has **zero** references in `game.js`. Wire the shot's
  accuracy to it. That turns a dead attribute into the conversion lever, gives
  the specialists something real to reach for when a manager shouts "we need a
  goal", and makes the shout causally connected to the scoreline for the first
  time.

Both halves are small, and the second is the reason to prefer this over just
nerfing red's keeper: it makes the *manager's* input matter, rather than making
the house side worse.

**How to verify it:** the harness for this already exists and needs no deploy to
run the control - `/tmp/measure_range.py` style, three arms, paired waves of
twelve, patching through the same `PATCH /api/rooms/{code}/teams/{team}/profiles/{role}`
route a specialist uses. With the change deployed, `finishing` at 0.2 against
`finishing` at 1.0 should separate cleanly; if it does not, the wiring is wrong
and nothing has been lost but an afternoon. Target n = 24 an arm, which is about
20 minutes of wall clock.

### 3. Make the change felt, whatever the scoreline does

Worth doing on its own merits, and it is the part of "the players feel they
changed the game" that does not depend on the engine at all.

The shout mechanism is in excellent shape and nobody can see it. Fifty out of
fifty shouts landed and moved a profile, in about forty seconds, with correct
attribution. What is missing is the telling:

- **Say the shout worked, at the moment it works.** `scoring.py` already
  computes whether a goal arrived within `EFFECTIVE_WINDOW_SECONDS` of a shout
  and pays +100 for it. Nobody is told at the time. *"That shout made that goal,
  +100"* on the phone, the instant it happens, is the whole product promise in
  one toast and it is computable from the log the arena already keeps.
- **Narrate the near-misses.** Blue puts the ball in red's goal mouth in 5.93%
  of frames. That is a genuinely frantic match that the wall renders as 0-0. A
  `shot!` or `close!` event on the relay would fill the space between goals with
  the football that is already happening.
- **Show what the squad actually did.** The four specialists answer a shout with
  real, role-specific choices. Once they are writing attributes that exist,
  showing "your midfielder pushed up, your forward moved higher" is a manager
  watching their instruction become a team.

## What this does not know

- Every match here is solo against the house side. Head to head, where both
  dugouts write shapes, is untested and the conclusion may not carry: two teams
  with the same funnel problem is a different question from one.
- The frame work infers possession from proximity (nearest player within 45px)
  rather than reading `this.possessor`. The engine captures inside 46px, so the
  two agree closely, but the numbers are an estimate rather than the engine's
  own count.
- 96 control matches put blue's win rate at 18% with a 11-27% interval. That
  excludes the large effects claimed in the two earlier reports. It does not
  exclude an effect of a few percentage points from any single arm, and I would
  not read the ordering of the null arms as meaning anything.
- The proposed shot-targeting change is reasoned from the code and the
  histogram, not measured. It is a hypothesis with a verification plan, not a
  result.

## Operational note

Twelve concurrent matches with six concurrent shouts wedged the arena for 21
minutes during this work. Cloud Run shed every external request with
`Rate exceeded` while the container sat healthy answering its own liveness
probe; `minScale` and `maxScale` are both 1, so there was nowhere to route and
it did not self-heal. A full venue is exactly this load.

Worse, and separately: after the arena was replaced, **the grounds never
rejoined it**. Every kick-off returned 503 "no pitch is free" and neither log
said why - revision `arena-00028-t5q` never received a `/ws/grounds` handshake,
and the grounds' retry loop (`grounds/main.py:121-138`) logged nothing because
its socket never dropped. `timeoutSeconds: 3600` let it stay bonded to the old,
drained instance, which still answered pings. Restarting the grounds fixed it.
A fix worth considering: have the arena include a boot id in the hello ack, and
have the grounds re-handshake when the id it sees over HTTP stops matching the
one on its socket.

Both restarts were done with a throwaway env var (`ARENA_RESTART`,
`GROUNDS_RESTART`) on the same images, which should be removed at the next
deploy. The board is also carrying roughly 240 rehearsal managers, all stamped
`@rehearsal.example.com` so the existing `tidy-rehearsals` Cloud Run job clears
them.

## How to reproduce

Throwaway harnesses in `/tmp`, all pointed at the deployed arena:

- `measure_win_chance.py` - paired shout/quiet waves, W-D-L and what the
  specialists wrote
- `measure_keeper_line.py`, `measure_range.py`, `measure_ceiling.py`,
  `measure_fast.py` - the attribute arms, each patching through the arena's own
  profile route and reading the value back before counting the match
- `watch_shots.py`, `watch_shots2.py`, `watch_line.py` - the frame recorders:
  cone test, distance bands, and the goal-line histogram

Twelve matches run in parallel in about four and a half minutes. Only the shout
arms cost Gemini quota.
