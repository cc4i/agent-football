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
| 5 | **Shout to the bench** | Shouts into the match and lets *the game's* own coach, captain and four player agents work out what it means. |

Stage 2 is the one people remember: nobody handed the agent a browser tool, so
it writes the automation itself and fixes it when it breaks. Stage 5 is the
other one, Antigravity setting a second multi-agent system going, with the
whole chain answering back on screen as it works.

Stages 4 and 5 are deliberately opposite. Tuning sets numbers the agent
chooses. A shout hands the decision to the game's own coach, captain and four
player agents, and they pick the numbers.

## Architecture

Four processes on a laptop, or two Cloud Run services: the arena with three
containers in it, and the grounds on its own. Either way the arena owns the
state and everything else asks it.

```
  you, in a chat window        a phone, one per manager      the big screen
            |                            |                        |
            v                            v                        v
  +-------------------+       +---------------------------------------------+
  | dugout      :8002 |       | arena                                 :8003 |
  |                   |  HTTP |                                             |
  | the Antigravity   |------>| rooms, seats, profiles, the event log,      |
  | agent, in process |<------| scoring, the live match bus                 |
  | four tuner        |  ws   |                                             |
  | subagents         |       | FastAPI + Postgres                          |
  +-------------------+       +---------------------------------------------+
                                 |   ^                            ^
                    a shout,     |   |  PATCH the profiles        |  frames up,
                    as far as    |   |  they moved, carrying      |  matches to
                    /run_sse     v   |  X-Arena-Service           v  play down
                              +------------------------+  +------------------+
                              | game agents            |  | grounds    :8004 |
                              | coach (ADK)      :8000 |  | one Chromium,    |
                              | captain (A2A)    :8001 |  | every match in   |
                              | four specialists       |  | one page         |
                              | MCP tools over stdio   |  +------------------+
                              +------------------------+
```

The pitch on :5173 is not in that picture any more, and that is the point: it
is the lab where the simulator is worked on, not where the venue's football is
played.

| | what it is | on |
|---|---|---|
| `arena/` | Rooms, seats, player profiles, the event log, scoring and the live match bus. The only writer of the squad. | :8003 |
| `game/` | The simulator, near enough as it was: a hook so the score is readable from outside the canvas, and a kit portrait in each top corner. The game's own agents live here too. | :5173 lab, :8000 coach, :8001 captain |
| `grounds/` | One headless Chromium, told by the arena which matches to play, holding all of them in a single page. | :8004 |
| `dugout/` | The showcase: a chat UI over an in-process Antigravity agent, with one tuner subagent per player. | :8002 |

**The arena owns the state.** Profiles used to be four JSON files beside the
pitch, so every match in the venue shared one defender. They belong to a room
and a dugout now, and the arena is the only writer: it holds the ranges,
refuses anything outside them naming every problem at once, records who moved
what, and tells the pitch. A tuner's change and a shout's change land the same
way, and neither has to know the other exists.

**The football belongs to a server, not to a tab.** The grounds is one headless
Chromium the arena assigns matches to, and every match in the venue runs in the
same page there. Nothing a person can close is holding a simulation: shut the
big screen mid-match, come back ninety seconds later, and the clock has moved
on without you. The big screen only watches now.

**The grounds is trusted for physics and nothing else.** It advances the
simulation and reports positions and events; the arena keeps the log and works
the result out from it afterwards. A host cannot say who won. The Chrome window
Antigravity opens in stage 2 plays its own match in its own tab, which is what
a lab is for, and it is nothing more special than that.

**The two agent systems never blur.** The dugout embeds the Antigravity SDK
directly, so thoughts, tool calls and subagent work stream into the match log
as they happen, and every line names who did it. Amber is Antigravity and
nothing else; cyan belongs to the game's own agents.

### One shout, end to end

The dugout's shout tool and a phone's shout chip both take this route. The
pitch's own coach bar predates it and still calls the coach directly, which is
what keeps the workshop working with no room and no phone in sight.

1. `POST /api/rooms/{code}/shout`. The arena logs the words and answers at
   once: the chain takes tens of seconds and the request cannot wait for it.
2. The arena opens an ADK session holding the room, the dugout, who shouted and
   what they said, then posts to the coach's `/run_sse`.
3. The coach hands the shout to the captain over A2A. The captain puts it to
   all four specialists at the same time.
4. Each specialist decides what the words mean for its own player and PATCHes
   the arena, carrying `X-Arena-Service` where a phone would carry a cookie.
   The arena works out for itself which shout caused the change, so nobody can
   claim one by saying so in a request body.
5. Every rung reports on the room's socket as it gets there, ending in the
   huddle. The phone, the dugout and the pitch all watch the same relay.

A specialist can also call the MCP server its own process spawns over stdio, to
report an injury or ask to come off. Those used to be written as JSON files
beside the pitch and polled by the browser, which is why they only ever reached
one screen. They go to the arena now, carrying the same `X-Arena-Service` as
everything else, and come back out on the room's socket - so the toast reaches
the wall and every phone at once, and is still in the log when you cut away and
come back.

### One tune, end to end

Four subagents, one tool each: a tuner is handed exactly one role's function,
so it cannot move another player even if it decides it wants to. The tool
PATCHes the arena, which accepts or refuses, records the delta against
`Antigravity <role>-tuner`, and pushes it to the room. The pitch redraws from
that push rather than from a poll, which is why a change shows up mid-match.

## Running it

You need [uv](https://docs.astral.sh/uv/), Node, the Antigravity CLI with
`agy login` done, and a Postgres for the arena to keep its history in. Native
is the default path:

```bash
brew services start postgresql@18     # or your platform's equivalent
```

The arena makes its own database on the way up, so that is the whole of the
setup. If you would rather not install one, `compose.yaml` at the repository
root brings up the same thing in a container, on 5433 so it cannot shadow a
local 5432:

```bash
podman compose up -d                  # or docker compose up -d
export ARENA_DB=postgresql://arena:arena@localhost:5433/arena
```

Either way `arena/run.sh` says which of the two to run if it cannot reach one,
rather than letting uvicorn die on a traceback.

```bash
cp game/.env.example   game/.env      # then set GOOGLE_CLOUD_PROJECT
cp dugout/.env.example dugout/.env    # same
cp arena/.env.example  arena/.env     # ships ready for one machine

export ARENA_SERVICE_TOKEN=dev-token  # the same value in all four shells
export ARENA_PITCH_DIR=$PWD/game/frontend/dist         # from the repository root
(cd game/frontend && PITCH_BASE=/pitch/ npm run build) # what the grounds plays in

cd arena   && ./run.sh                # rooms, seats and player profiles
cd game    && ./run.sh                # the lab, the coach and the captain
cd grounds && ./run.sh                # the Chromium that plays the matches
cd dugout  && ./run.sh                # then open http://localhost:8002
```

The build and `ARENA_PITCH_DIR` are what the fourth shell needs: the grounds
opens the arena's copy of the pitch, not Vite's, and an arena with no pitch
directory answers 404 for it. Without a grounds up, kick-off is refused in
words - `no pitch is free to run this match` - and the room stays in its lobby.
See `grounds/README.md`.

The game's and the dugout's `.env` are both needed. Without `game/.env` the
pitch still renders and most of the quest works, but every call into the game's
own agents fails and the squad never resets. The arena's is optional: it starts
on defaults that are right for one machine and says on the way up what it would
want at a real event.

The arena owns the player profiles, so it has to be up first, and nothing the
dugout does with the squad works until it is. The token is why all three
shells need the same value: the game's agents and the dugout's tools carry it
instead of a phone session, and an unset token is refused rather than waved
through. Exporting it beats writing it into three `.env` files, because the
shell wins over all of them. See `arena/README.md`.

The agent runs shell commands unrestricted, by design, so that it can launch
the script it writes in stage 2. Run this on your own machine.

## Deployed

Two Cloud Run services and a Cloud SQL database behind them. The arena is three
containers - the arena on the port, the coach and the captain on the loopback
interface they share. The grounds is its own service, because a browser holding
a venue's football is the thing most likely to need replacing on its own and a
container in the arena's instance cannot be replaced alone. `deploy/service.yaml`
and `deploy/grounds.yaml` are the whole topology and `deploy/README.md` is how
to put it there.

The dugout does not go with it. It embeds the Antigravity CLI and runs shell
commands unrestricted, which is exactly what stage 2 needs and exactly what has
no business facing the internet, so it stays on the presenter's machine and
talks to the deployed arena over `ARENA_URL`.

One instance, on purpose: the match bus, host liveness and the chain's
semaphore all live in one process. That means every deploy drops every live
match, which `deploy/README.md` says rather more loudly.

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
