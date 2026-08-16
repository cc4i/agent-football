# Is `SIMULATION_MODEL` the problem?

16 August 2026. A direct answer to a fair question: after a lot of measurement,
blue's win rate has not moved. Is the guidance we hand the four specialists
making things worse, and is optimising it worth doing?

Short answer, in three parts:

1. **Yes, it does measurable harm** - one instruction in it moves the forward
   backwards in half of all shouts - but the harm is small.
2. **No, it is not why there is no progress.** We measured the ceiling on
   everything a prompt can achieve, and the ceiling is the baseline.
3. **So optimise it cheaply and stop.** Fixing the two wrong lines is worth
   half an hour. A prompt-optimisation campaign has an expected return of zero,
   and that is a measured bound rather than an opinion.

## 1. The audit: every claim in the model, checked against the engine

`SIMULATION_MODEL` lives in `game/agents/specialist_agents/tools.py` and is
prepended to all four specialist prompts on the hot path of every shout. Here
is every factual claim it makes, checked against `game/frontend/src/game.js`.

| claim | true? | does it matter? |
|---|---|---|
| `shotRange` is a fraction of 700px | **yes** - `resolveDistance(profile.shotRange, 700, 500)` | yes |
| a kick becomes a pass if a teammate is within 47 degrees and 480px | **yes** - `dot > 0.68`, `dist > 480` skip | **barely.** Measured: steals 6.4% of blue's chances, 7.9% of red's |
| shot speed is `420 + 360 x shotPower` | **yes**, line 1317 | yes |
| the keeper strays 18px per 0.1 of `attackPositioning` | **yes** - `0.1 x 180` | **no.** 32 matches with it patched to 0.0: no effect |
| base pace is 210/235/260/180, opponent 150/180/200 | **yes**, `getPlayerSpeed` | yes |
| the forward's `passProbability` ships at 0.3 | **yes** | yes |
| `forwardPassProbability` is a delivery lever | **no. The engine has never read it** | fixed in `8eb4c7e` |
| *"Raise the FORWARD's `attackPositioning`"* | the attribute is real | **actively harmful - see below** |

Six of eight claims are mechanically correct. The model is not sloppy about
what attributes *do*. It is wrong about which ones *matter*, and that is the
whole difficulty: it was written by reading the source, and reading the source
tells you an attribute's mechanism but not its magnitude. Two of its most
emphasised points - the keeper's line and the shooting cone, the one it calls
"THE TRAP THAT CATCHES EVERYONE" - turn out on measurement to be worth nothing
and almost nothing respectively.

**It also has one significant omission.** The model gives four conditions for a
shot to exist and none of them is the one that actually decides. `game.js:935`:

```js
if (distToGoal < shotRange && inOppositionHalf) {
  if (distToGoal < 220 || !preferDribble) {      // preferDribble = chance() < dribbleTendency
```

Outside 220px of goal a forward only shoots if it rolls against
`dribbleTendency`, which ships at 0.8. So blue's forward shoots on 20% x 70% =
**14% of decisions** at range, and dribbles the rest. The model never mentions
`dribbleTendency` or the 220px gate, so no specialist has ever touched either.

## 2. The measured harm: an instruction with nowhere to go

This is the concrete negative, and it is not subtle.

`forward.attackPositioning` **ships at 1.0, which is the top of its range.**
There is no headroom. The model's headline instruction for attacking is to
raise it.

Across 28 shouts where the written values were recorded:

| write | n | started at | written | direction |
|---|---|---|---|---|
| **`forward.attackPositioning`** | **28/28** | 0.85 or 1.0 | 0.9, 0.95 | **14 up, 14 down** |
| `midfielder.attackPositioning` | 16 | 0.85 or 0.9 | 0.8, 0.9 | 13 up, 3 down |
| `midfielder.forwardPassProbability` | 16 | 0.8 | 0.9, 0.95 | 16 up, all inert |
| `midfielder.shotRange` | 11 | 0.7 | 0.8, 0.9 | 11 up |
| `forward.shotRange` | 10 | 0.9 | 0.95 | 10 up |
| `midfielder.counterAttackUrgency` | 5 | 0.98 | 0.9 | 5 **down** |
| `midfielder.passProbability` | 2 | 0.9 | 0.1 | 2 **down** |

Every shout writes `forward.attackPositioning`, and **half of those writes
lower it.** The agents pick 0.9 or 0.95 because those read as "high". Under
`high press` the philosophy has just set 0.85, so 0.9 is a small raise. Under
`counter` the forward is already at 1.0, so 0.9 is a small drop. The single
most reliable behaviour in the entire shout system is a coin flip on whether it
helps or hurts, and the model caused it by pointing at a maxed-out lever.

Two other pathologies show in the same table. `midfielder.counterAttackUrgency`
was lowered in 5 of 5 writes (0.98 to 0.9), because the agent does not know the
`counter` philosophy already raised it. And since `forwardPassProbability` was
removed, the freed budget has moved to `midfielder.shotRange`, raised in 11 of
11 post-fix shouts - which the model explicitly tells them not to do
(*"not shooting from further out yourself"*).

**The model's one clear success** should be recorded too. Before it existed,
24 of every 6 shouts' writes went to the goalkeeper and defender, the two roles
that cannot score. That is now zero, in 50 consecutive measured shouts. The
model genuinely fixed the reasoning. It just aimed the fixed reasoning at
levers that do not move the game.

## 3. Why optimising it further has a ceiling we already measured

This is the part that answers "is it worth it".

A prompt can only cause attribute writes. So **the best possible prompt cannot
beat hand-picking the best possible attributes.** We measured that directly.
The `max` arm patched 14 attributes across three roles into the strongest
attacking shape the surface can express - forward at maximum reach, power,
speed and urgency, midfielder set to feed it, defenders pulled back out of the
shooting lane - with no language model involved at all:

| arm | n | win% | blue/match | red/match |
|---|---|---|---|---|
| untouched baseline | 96 | 18% | 0.39 | 0.67 |
| **maximal attacking squad** | 16 | 12% | **0.38** | 1.12 |
| shouted, as shipped | 56 | 14% | 0.34 | 0.82 |

The perfect answer scores what doing nothing scores, and concedes more. Six
other interventions - the keeper on its line, shooting from range, the forward
deciding instantly, and the shout itself - all came back null against a
96-match baseline whose 95% interval is 11-27%.

So the expected return on prompt optimisation, measured in win rate, is zero.
Not "small": bounded at zero by an experiment that removed the model from the
loop entirely and still could not beat the control.

What optimising it *can* buy is stopping the shout being slightly net-negative:
removing the coin-flip write, the `counterAttackUrgency` regression, and the
new `midfielder.shotRange` drift. That is worth doing because it is nearly
free, not because it will move the scoreline.

## 4. What the time actually bought

Fair accounting, because the headline metric has not moved and that is the
thing that was asked for.

**Not achieved:** blue's win rate is 18% untouched and 14% shouted, the same as
it was yesterday, and no intervention has beaten the control.

**Achieved:**

- **A month-old bug found and fixed.** 100% of shouts were writing an attribute
  the engine has never read. The 15 August report scored that write going
  `0/6 -> 6/6` as proof the tuning worked. It was inert. That is now impossible
  by construction, verified on prod: 12/12 shouts land, 0 dead writes, and the
  agents needed no refusals to get there.
- **Five hypotheses eliminated with evidence**, three of which were the
  standing recommendations of the two previous reports: put the keeper back on
  its line, give the forward more reach, rewrite the tuning plan. All measured,
  all null. The plan of record was a dead end and now we know.
- **The binding constraint located**, with frame-level evidence rather than
  code reading: blue creates 3.7x more point-blank chances than red and puts
  the ball in its goal mouth 12x more often, then converts four times worse,
  because every blue shot aims at the same 25px band while red's keeper stands
  a median 2px from the ball.
- **The ceiling established.** Attributes cannot fix conversion. That is the
  single most useful thing here, because it stops the next month going the same
  way as the last one.

The honest summary is that the evening produced one bug fix and a decisive
negative result. The negative result is expensive but it is real: every
remaining idea in the attribute space is now known to be worth nothing, which
is why the next move is an engine change rather than a fourteenth arm.

## 5. What I would do

| | cost | expected win-rate gain | worth it |
|---|---|---|---|
| **A. Correct the two wrong lines** in `SIMULATION_MODEL` | ~30 min | ~0, but stops a net-negative | **yes** |
| B. Optimise the prompt properly | days | **0, measured** | no |
| C. **The engine change (#2)** | hours + a measured A/B | the only option with headroom | **yes** |

**A, concretely.** Three edits, all one-liners:

- Stop telling the forward to raise `attackPositioning`. It ships at 1.0. Say
  so, and say that under `high press` its job is to restore what the
  philosophy lowered, not to invent a higher number.
- Tell every role its philosophy may already have moved an attribute, so it
  should not write a number lower than what is there.
- Name `dribbleTendency` and the 220px gate, which decide whether a shot
  happens at all and which the model has never mentioned. Then stop the
  midfielder reaching for `shotRange`.

**C** stays as written in `2026-08-16-why-a-shout-cannot-win-the-match.md`:
vary the shot's aim off the keeper's real position instead of a constant, and
give the spread to `forward.finishing`, which exists in the baseline at 1.0 and
is read by nothing. That is what makes conversion attribute-controlled, which
is what makes a shout able to matter, which is the thing A cannot buy at any
price.

I would do A and C together and measure once, rather than measuring A on its
own: A's predicted effect is under the noise floor of a 24-match arm, so it
would tell us nothing on its own.

## What this report does not know

- The `max` arm is *a* strong squad, not a provably optimal one. It is my
  hand-picked ceiling, and a different fourteen attributes might do better. It
  is one arm of 16 matches; what makes it convincing is that six other arms
  agree with it, not its own n.
- Nothing here is an A/B of the model against no model on the current build.
  The only such comparison is the 15 August report's six-match arms, which do
  not reproduce. The harm identified in section 2 is read off the write log,
  which is deterministic enough to trust at 28/28, rather than off outcomes.
- The direction analysis assumes the philosophy is the only thing that moved an
  attribute before the shout landed. That holds for these runs, where nothing
  else wrote to the squad.
