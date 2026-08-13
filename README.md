# ⚽ Futsal WorldCup Workshop

A showcase for the **Antigravity** CLI agent. The football is the vehicle: a
running game gives the agent something real to act on, where you can watch it
work and judge whether it was any good.

You are the manager. You type what you want in plain English. Antigravity does
the work, in front of you.

## What Antigravity actually does here

Five things, in any order and as often as you like:

| | Stage | What it shows |
|---|---|---|
| 1 | **Rebrand the team** | Generates new player sprites and puts your crest on the shirt. |
| 2 | **Take the field** | Writes its own Playwright script and runs it. A real Chrome window opens and plays a match. |
| 3 | **Read the game** | Reads the live score and every attribute, with the range each one must stay inside. |
| 4 | **Tune the squad** | Four subagents in parallel, one player each. Each can only touch its own player. |
| 5 | **Shout to the bench** | Types into the game's own coach bar and lets *the game's* agents decide what to change. |

Stage 2 is the one people remember: nobody handed the agent a browser tool, so
it writes the automation itself and fixes it when it breaks. Stage 5 is the
other one, Antigravity driving a second multi-agent system through its user
interface, with the whole chain answering back on screen.

Stages 4 and 5 are deliberately opposite. Tuning sets numbers the agent
chooses. A shout hands the decision to the game's own coach, captain and four
player agents, and they pick the numbers.

## The three processes

```
game/    the simulator, near enough as it was: a hook so the score is
         readable from outside the canvas, a kit portrait in each top corner,
         and a guard on the writes its own agents make
         Vite :5173   ADK coach :8000   team captain (A2A) :8001

dugout/  the showcase: a chat UI over an in-process Antigravity agent
         FastAPI :8002

arena/   rooms, seats, player profiles and the live match bus, so more than
         one person can play at once
         FastAPI :8003
```

The dugout embeds the Antigravity SDK directly, so thoughts, tool calls and
subagent work stream into the match log as they happen. Every line names who
did it. Amber is Antigravity and nothing else; cyan belongs to the game's own
agents, so the two systems never blur together.

## Running it

You need [uv](https://docs.astral.sh/uv/), Node, and the Antigravity CLI with
`agy login` done.

```bash
cp game/.env.example   game/.env      # then set GOOGLE_CLOUD_PROJECT
cp dugout/.env.example dugout/.env    # same

export ARENA_SERVICE_TOKEN=dev-token  # the same value in all three shells

cd arena  && ./run.sh                 # rooms, seats and player profiles
cd game   && ./run.sh                 # frontend, coach and captain
cd dugout && ./run.sh                 # then open http://localhost:8002
```

Both `.env` files are needed. Without `game/.env` the pitch still renders and
most of the quest works, but every call into the game's own agents fails and
the squad never resets.

The arena owns the player profiles, so it has to be up first, and nothing the
dugout does with the squad works until it is. The token is why all three
shells need the same value: the game's agents and the dugout's tools carry it
instead of a phone session, and an unset token is refused rather than waved
through. See `arena/README.md`.

The agent runs shell commands unrestricted, by design, so that it can launch
the script it writes in stage 2. Run this on your own machine.

## Does the tuning actually do anything?

Yes, and it is measured rather than asserted. Eight full matches each:

| squad | record | scored | conceded |
|---|---|---|---|
| as shipped | 0W 1D 7L | 4 | 12 |
| after tuning | 4W 1D 3L | 9 | 8 |

The shipped squad is deliberately weak, so the before and after is worth
watching. The reasoning behind the winning changes lives in a skill the agent
loads, `dugout/skills/winning-the-match/SKILL.md`. You can read it from the
team sheet too, by clicking the skill on stage 4.

## Docs

- `docs/superpowers/SMOKE.md` - the manual checklist, and what each step should
  look like when it is working.
- `docs/superpowers/specs/` - the design, including why the dugout is built the
  way it is.
