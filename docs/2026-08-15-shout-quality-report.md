# Teaching the specialists how a goal happens

15 August 2026. A follow-up to `2026-08-15-scoring-and-engagement-report.md`.

Three arms, six matches each, all on the deployed venue, all identical: solo,
three per philosophy across `high press` and `counter`, one typed shout 15
seconds after kick-off, the same words every time -

> "We need a goal. Get forward and shoot on sight."

Every shout in all 18 matches reached the squad and moved a profile. What
changed between arms is only what the four specialist agents were told.

## Result

| arm | blue | red | GD | W-D-L | goalless |
|---|---|---|---|---|---|
| **before** - no model | 3 | 4 | -1 | 1-3-2 | 2/6 |
| **after 1** - first model | 0 | 8 | **-8** | 0-1-5 | 1/6 |
| **after 2** - refined model | 3 | 3 | **0** | **3-1-2** | 1/6 |

Arm three is the first time blue has been level with the house side in anything
measured, and **all three of its goals arrived after the shout landed**. Three
1-0 wins.

## What the agents were writing, and what they write now

Counted over six shouts per arm. Four roles answer each shout, so a role
appearing 6/6 means it did that thing in every single match.

| written | before | after 1 | after 2 | |
|---|---|---|---|---|
| `goalkeeper.attackPositioning` | **6** | 0 | 0 | pulled the keeper off its line |
| `goalkeeper.shotRange` | 6 | 0 | 0 | meaningless on a keeper |
| `goalkeeper.joinAttack` | 6 | 0 | 0 | meaningless on a keeper |
| `defender.attackPositioning` | **6** | 0 | 0 | pushed the defender into the shooting lane |
| `defender.shotRange` | 5 | 0 | 0 | meaningless on a defender |
| `forward.attackPositioning` | 3 | 6 | **6** | correct |
| `forward.passProbability` | 0 | **6** | 0 | already 0.3; nothing to win |
| `midfielder.passProbability` | 0 | **6** | 0 | stopped the midfielder passing |
| `midfielder.forwardPassProbability` | 0 | 0 | **6** | delivery to the forward |

Before, **24 of the writes went to the goalkeeper and defender** - the two
roles that cannot score - and every single shout pushed the keeper off its line
and the defender into the forward's shooting lane. Those are the two worst
things available under this simulation. Not one shout touched the shoot-or-pass
decision.

They were not being stupid. The prompt was a list of attribute names and their
ranges and nothing else, so "attack" genuinely does read as *every role raises
`attackPositioning`*. Nothing told them that a kick is redirected into a pass by
any teammate within 47 degrees and 480px, so a team pushed forward as a block
cannot shoot at all. The model that explains this already existed - in
`dugout/skills/winning-the-match/SKILL.md` - and was given only to the dugout's
four tuners, never to the agents a phone's shout actually drives, which is the
route every manager at a venue uses.

## The middle arm is the interesting one

The first model fixed the reasoning completely - keeper and defender writes went
from 24 to 0 - **and the football got worse**: blue 3 goals became 0, red 4
became 8, goal difference -1 became -8.

It said `passProbability` is the shoot-or-pass lever and stopped there, so all
four specialists lowered it, the midfielder included. A midfielder that shoots
rather than passes gives the ball away 490px from goal, which is a counter, and
the forward it was supposed to be feeding never gets it. Red's rate doubled,
which is exactly what more turnovers look like.

So a correct fact, stated without saying whose lever it is, made the team worse
than telling them nothing. The refinement says three things:

- the lever belongs to the forward alone, and its shipped value is already 0.3,
  so there is almost nothing to win by lowering it
- never lower the midfielder's or the defender's - their job is delivery
- if you are asked for goals and you are not the forward, the useful changes are
  the ones that get the ball there faster

The agents took it: `midfielder.forwardPassProbability` went 0 → 6/6, and the
whole-team shooting stopped.

## What this means for the product

**Shouting now does something, and does the right thing.** That is the claim the
workshop is built on and until today it was false in the venue: the mechanism
worked perfectly - relay, attribution, the four agents answering in about forty
seconds - while the content of what they wrote made the team worse.

**Chips still cannot do this.** A preset writes one value to all four roles, so
it cannot express "forward up, defender back", which is the shape that scores.
That is a property of `playbook.apply`, not of the numbers in the JSON, and it
is why a shout is worth waiting forty seconds for and a chip is not.

**Red does not need weakening.** On the evidence here, blue draws level with the
house side once its own agents stop sabotaging it. The earlier plan to nerf red
would have hidden this rather than fixed it, and it would have made every score
already on the board incomparable with every score after it.

## What I would not conclude

Six matches an arm. Blue's underlying rate is around half a goal a match, so
each arm's total is a small count and the confidence intervals overlap heavily.
What is *not* marginal is the behavioural change - 24 wasted writes to 0, and
`forwardPassProbability` 0/6 to 6/6 - because that is deterministic enough to
read off nine of nine shouts.

Read the table as: the agents now choose well, and the football is no longer
getting worse when a manager shouts. Whether arm three's +8 goal-difference
swing over arm two holds up needs 20-30 matches an arm, which is a quota
question rather than a hard one.

Also untested: head to head, where both dugouts are shouting, and the dugout's
own four tuner subagents, which read the stale `SKILL.md` and were not touched
here.

## Next

1. **Rewrite `dugout/skills/winning-the-match/SKILL.md`.** It still claims the
   shipped midfielder is `speed: 0.5` when it is 0.85, and its headline record
   does not reproduce. The same model now in `tools.py` should replace it.
2. **Re-measure at 20+ matches an arm** before treating the arm-three numbers as
   settled.
3. **Then** revisit match length. A shout takes ~40 seconds to land and its
   goals arrive after that; 180 seconds gives a manager one shout, and arm three
   shows a shout is now worth having.
