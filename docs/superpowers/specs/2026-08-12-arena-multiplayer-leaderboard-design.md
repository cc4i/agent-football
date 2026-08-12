# Arena: multi-tenant matches, phone dugouts and a leaderboard

Date: 2026-08-12
Status: Approved, ready for implementation planning.

## Purpose

Today the game is one browser tab on one machine. One squad, one match, one
set of JSON files on disk, and a shout typed into a DOM input that a Playwright
script drives over CDP. It demonstrates the agent chain well and cannot be
played by two people at once.

This turns it into something a room full of people can play. You scan a QR
code, type your name on your phone, and your phone becomes the dugout: you
talk to the squad in plain English and the existing coach → captain → four
specialists chain reacts. Matches run concurrently, either solo against the
shipped house side or head to head against another manager. A big screen shows
one match at full size with both relay feeds and every other live match on a
wall beneath it. Results land on two leaderboards.

The agent chain does not change. That was a deliberate constraint: the chain is
the thing the workshop exists to show. Everything here is about making it
reachable by more than one person at a time, and about making the 30–60 seconds
it takes to answer into the spectacle rather than dead air.

The companion mockup is `docs/leaderboard_ui_mockup.html`, which renders every
screen described in "Screens" below.

## What already exists

The parts worth keeping, and where they are.

- **The simulation.** `game/frontend/src/game.js` is a 2114-line Phaser 4
  arcade-physics futsal match. `GAME_DURATION_SEC = 180`, score and clock on the
  scene, blue profiles driven from JSON, red profiles hardcoded.
- **The agent chain.** `game/agents/agent.py` defines `ManagerAgent`
  (Gemini Flash-Lite), which transfers to `team_captain_remote`, a
  `RemoteA2aAgent` pointing at the captain server on :8001.
  `game/agents/captain.py:53` fans out to the four specialists with a
  `ParallelAgent`, then `captain.py:70` wraps that in a `SequentialAgent` with a
  synthesis step. The four specialists already run concurrently.
- **MCP.** Each specialist gets its own stdio `McpToolset`
  (`specialist_agents/tools.py:252`) exposing `report_injury` and
  `request_substitution` from `game/agents/football_mcp_server.py`.
- **Validation.** `specialist_agents/profile_guard.py` checks every
  agent-driven write against the shipped baselines and rejects out-of-band
  values, handing reasons back so the agent can correct itself.
- **The dugout.** `dugout/` is a FastAPI app on :8002 wrapping an Antigravity
  SDK agent through five workshop stages.

## What blocks more than one player

Seven pieces of process-global or machine-global state. Each one is a place
where a second concurrent match would silently corrupt the first.

1. **The player profiles are four fixed files.**
   `specialist_agents/tools.py:28` resolves `PLAYER_STATE_DIR` to a single
   directory and every write goes to `{role}.json` in it. Two managers shouting
   at once overwrite each other.
2. **Red has no state at all.** `game.js` deep-copies hardcoded defaults into
   `this.redProfiles`. There is nothing for a second manager to control, so
   head-to-head is not merely unwired — the data model has no seat for it.
3. **Substitutions are one global file.** `football_mcp_server.py` writes
   `substitutions.json` for everybody.
4. **Match status is a temp file.** `dugout/tools/match.py:9` reads and writes
   `/tmp/futsal_status.json`, and `match.py:14` keeps a process-global
   `CALLED` set.
5. **The frontend holds a global request lock.**
   `main.js:377-378` has `isRequestInProgress` and
   `isBackgroundRequestInProgress`; a background status check holds it for
   roughly 13 seconds out of every 55, and shouts arriving in that window are
   dropped.
6. **Shouts arrive by driving the DOM.** `dugout/tools/shout.py:103` connects
   Playwright over CDP to `localhost:9222` and types into `#shout-message-input`
   on the one page it finds at `localhost:5173`. A phone cannot be reached this
   way, and there is only ever one page.
7. **The dugout agent is a singleton with an unrestricted shell.**
   `dugout/session.py:125` holds one `_AGENT` for the process and
   `session.py:152` composes `policy.workspace_only([REPO_ROOT])` with
   `policy.allow_all()`. `dugout/channel.py:17` carries one turn at a time on a
   module-global queue.

Item 7 is the reason for the architecture below: the dugout can run shell
commands as the user, by design, and must never be reachable by someone who
walked up and scanned a QR code.

## Architecture

A new service, `arena/`, on :8003. FastAPI, SQLite in WAL mode, one WebSocket
bus per room.

```
  phone /join /play ─┐                      ┌─ game/ :5173  (pitch, host or viewer)
  phone /play      ──┼── arena :8003 ───────┼─ coach ADK :8000 ── captain A2A :8001
  big screen /arena ─┘   rooms, seats,      └─ dugout :8002 (workshop, unchanged UX)
  /board            ──   profiles, events,
                         scoring, board
```

The arena owns all state that is currently global: rooms, seats, players,
per-room profiles, the event log, and the leaderboard. It serves the four
pages (`/join`, `/play`, `/arena`, `/board`) and a WebSocket at
`/ws/rooms/{room_id}`.

`game/` stops reading fixed paths and becomes room-scoped: it takes
`?room=…&team=…&as=host|viewer` and gets its profiles and state from the arena.

`dugout/` keeps its five stages and its current UX. Its tools stop touching
files and `/tmp` directly and call the arena API against a reserved room named
`workshop`. It stays a separate process on :8002 because of its shell policy,
and the arena never proxies to it.

**Why a separate service rather than extending `game/agents/` or `dugout/`.**
The coach and captain are ADK servers whose job is to run agents; giving them
room state, a database and a socket bus muddles that. The dugout cannot host
it for the security reason above. A third service also means the workshop
keeps working untouched while the arena is built.

## Rooms and identity

```sql
player(id, display_name, email_hash, email_masked, created_at)
room(id, code, mode, status, host_client_id, created_at, finished_at)
seat(room_id, team, player_id, ready, joined_at)
event(id, room_id, seq, kind, payload_json, match_ms, wall_ts)
result(id, room_id, player_id, team, points, breakdown_json, computed_at)
```

`room.code` is four characters from an unambiguous alphabet (no `O`/`0`,
`I`/`1`), for example `K7F2`. `mode` is `solo` or `versus`. `status` is
`lobby`, `live`, `finished` or `abandoned`. `team` is `blue` or `red`.

Identity is deliberately thin. A player is a display name plus an email, and
the email exists only to keep one place on the board across repeat plays. It is
stored as a salted SHA-256 hash plus a masked form (`a***x@example.com`) for
display. The raw address is kept only if a config flag is set, the join form
says so in plain words, and a purge script removes everything after the event.
The board never renders an unmasked address.

Session is a signed token in an HttpOnly cookie carrying `player_id`. There is
no password and no account.

## The physics host

Exactly one client per room advances the simulation. Everyone else renders what
that client sends.

**Who hosts: the room's creator.** Scan the QR on the big screen and you are
seated in the arena screen's own room, so the arena hosts and your match opens
on centre court. Start a match from your phone and your phone hosts. This rule
is chosen because it is explainable by pointing — whoever scanned the screen
owns the screen — and because it lets two people play head to head away from
the big screen when it is busy.

**Viewing is one early return.** `game.js:400` runs roughly 1300 lines of AI
and physics inside a single `update()`, and sprites are Arcade Physics bodies,
so positions are computed locally everywhere. A viewer does not need that code
split out:

```js
update(time, delta) {
  if (this.role === 'viewer') return this.applyFrame(delta);
  /* ...unchanged host path... */
}
```

The viewer calls `this.physics.world.pause()`, then `applyFrame` interpolates
each sprite toward the most recent wire frame and selects the run or idle
animation from the implied velocity. Same sprites, same pitch, same look; the
AI simply never executes there. Wall tiles do not use Phaser at all — nine dots
on a canvas.

**Fairness.** The host is trusted for physics and not for scoring. It emits
`kickoff`, `goal`, `own_goal`, `full_time` and `abandoned` with the match clock
attached; the arena recomputes every point from that log and never accepts a
submitted total. Ranked rooms are forced to `speed: 1.0` and the 0.5–3x slider
is hidden, because it would otherwise distort time-to-first-goal outright.

**Interruptions.** The host holds a Screen Wake Lock. Backgrounding the tab
pauses the match rather than ending it, since the clock is match time. If the
host is gone for more than 30 seconds the room becomes `abandoned`, scores
nothing, and both phones are told why.

## The big screen

Three things on one screen, and they do not swap places.

**Centre court** is one match at full size: pitch, score bug, both coach
nameplates and, in the right rail, the two relay feeds. For a solo run there is
no second dugout, so the red relay reads "No dugout here" rather than sitting
empty — most matches will be solo, so that is a real state and not an edge case.

**The wall** is a filmstrip beneath centre court carrying up to six other live
rooms, each a small pitch with score and manager names, rotating if there are
more. It is always present. Simultaneous play is the point of the feature and
is invisible if nothing renders it.

**The director** chooses centre court automatically: a goal just scored, scores
level, or under 30 seconds left. An operator overrules it by clicking a tile or
pressing its number; `Esc` hands control back. A pin holds until that match
ends — a timer that yanked the screen away mid-shout would be worse than no
override at all. A chip reads `Auto` or `Pinned · Alex v Sam` so it is always
clear which is driving.

When no match is live the screen shows the leaderboard and its own QR code.

## Profiles

Replace the fixed directory with a service.

```
GET   /api/rooms/{room}/teams/{team}/profiles
GET   /api/rooms/{room}/teams/{team}/profiles/{role}
PATCH /api/rooms/{room}/teams/{team}/profiles/{role}   {changes, reason, actor}
```

A `PATCH` validates, persists, appends a `profile.patch` event and broadcasts
the delta on the room bus. This deletes the 2-second filesystem poll at
`main.js:878` and `/tmp/futsal_status.json` along with it.

**Consolidate the two validators.** `specialist_agents/profile_guard.py` and
`dugout/attributes.py` implement the same rules twice; `profile_guard.py:9-10`
says so in a comment. Both routes now go through one module in the arena, and
that module keeps `profile_guard`'s better behaviour of returning reasons so
the agent can correct itself.

**Seed both teams.** Baselines currently populate one set of four files. They
now seed blue *and* red for every room, which is what gives head-to-head a
second controllable squad.

**Room-scope the agent tools.** `update_profile` gains a `ToolContext` and
reads `room_id` and `team` from ADK session state rather than closing over a
module constant. The same change applies to the MCP server's
`substitutions.json`, which becomes per-room-per-team.

## The shout path

`POST /api/rooms/{room}/shout {text}`.

The arena records the shout and broadcasts `shout.sent` immediately, so the
manager sees their own words land with no latency and the existing keyword
sprite reflexes in `triggerShout()` still fire. It then opens an ADK session
carrying `{room_id, team}` in state, calls the coach, and re-emits the stream
as `relay.coach`, `relay.captain`, `relay.specialist` and `relay.huddle`.
Attribute writes land as each specialist finishes rather than at the end.

**Concurrency.** One shout in flight per dugout plus one queued, and the queue
is visible on the phone. Nothing is silently dropped, which is the current
behaviour at `main.js:377`. Across rooms a semaphore bounds how many chains run
at once — default 4, configurable, sized to the Gemini quota on the day — and a
manager waiting on it sees their position rather than a spinner.

**Latency.** The chain is three sequential LLM hops, not six — the specialists
are already parallel. The 30–60 seconds comes mostly from the instruction at
`specialist_agents/forward.py:30`, which requires every specialist to emit
*every* attribute on every call: 28 for the forward, around 42 for the
midfielder and 44 for the goalkeeper. Changing that to only what moved is the
single largest win available and needs no change to the agent topology. A fresh
ADK session per shout is the second cost; sessions become per-seat instead.

This retires the CDP hack at `dugout/tools/shout.py`. The Playwright script
itself survives as workshop stage 2's teaching material.

## Transport

Two sockets, and a client opens at most one of each.

`/ws/rooms/{room_id}` carries a single room. Down: `state` at 10 Hz (score,
clock, ball, eight positions), `event`, `profiles`, `relay.*`, `room`. Up, from
the host only: `host.state` and `host.event`.

`/ws/wall` carries a summary of every live room at 4 Hz — code, managers,
score, clock and positions, no relay traffic. The big screen holds one of each:
a room socket for centre court, which it re-points when the director or the
operator changes match, and the wall socket for the filmstrip. This is why the
wall is one connection rather than six.

The dugout's `/chat` SSE stream is untouched.

## Scoring

Recomputed server-side from the event log. Never submitted by a client.

**Score attack** (solo, against the house side):

| | |
|---|---|
| Win / draw / loss | 1000 / 400 / 100 |
| Each goal scored | +300 |
| Each goal conceded | −100, floored at −500 |
| First goal, by match clock | ≤30s 500 · ≤60s 350 · ≤120s 200 · else 100 |
| Clean sheet | +300 |
| Effective shout | +100 each, capped at 300 |

An *effective* shout is one whose squad changes are followed by a goal for that
team within 45 seconds. The window opens at the **first `profile.patch` caused
by that shout**, not when it was sent — the chain itself takes 30–60 seconds, so
measuring from the send would make the bonus nearly unreachable and would
reward a fast chain rather than a good instruction. This replaces a flat +50
per shout, which would pay people to spam the box. It is stated on the results
screen as "2 shouts led to goals" so the rule is legible without a rules page.

A room is scored only if it is `ranked`. A room is ranked unless it is the
reserved `workshop` room, or its host ever reported a speed other than 1.0, or
it ended `abandoned`. Best run per player counts.

**Head to head** is ranked by wins, then goal difference. Elo is computed and
stored and shown in a column labelled "Rating", but does not sort: most people
at an event play once or twice, and one match is not a rating. Flipping it to
sort is a config change if an event runs long enough to justify it.

The two boards are never merged. The README records the shipped squad as
0W-1D-7L, so beating it and beating a person are not the same achievement.

## Screens

Rendered in `docs/leaderboard_ui_mockup.html`.

- **`/join/{code}`** — name, email with the retention line, tactical
  philosophy. Lands here from the QR with the room prefilled. The four
  philosophies (high press, tiki-taka, counter, low block) are named profile
  patches applied to all four roles at kick-off, stored beside the baselines and
  validated by the same module as any other write.
- **`/play`** — the dugout. Score and clock, mini pitch, condensed relay,
  shout box with four preset chips. At 314px the relay drops the coach rung and
  the attribute deltas and keeps the four quoted branches.
- **`/arena?room=…&as=host`** — centre court, the wall, the director. Lobby
  state shows the QR, the room code and the seat slots.
- **`/board`** — the two leaderboards, podium and table, auto-cycling.

**The relay.** The chain is drawn as a signal travelling down a wiring diagram:
a trunk from coach to captain, then four branches that light independently as
each specialist answers. The shape encodes something true — `captain.py:53` is
a `ParallelAgent` — rather than decorating a chat log. Since the wait is
unavoidable while the chain stays as it is, it has to be worth watching. A
specialist that times out turns grey and reads "no answer"; the huddle
completes on three.

## Testing

- **pytest** for the scoring engine, table-driven from canned event logs; the
  consolidated validator; the room and seat state machine; Elo.
- **A `--fake-host` script** replaying recorded event logs over the WebSocket,
  so the arena, the board and the wall can be developed and tested without a
  browser running physics.
- **vitest** for the profile client and the state reducer.
- **One Playwright smoke test**: two headless phones and one arena, a full
  versus match end to end.

## Build order

Each step leaves the repo working.

1. Arena skeleton: rooms, seats, join, the WebSocket bus, `--fake-host`.
2. Profile service and validator consolidation; delete the filesystem poll.
3. Mobile controller, QR, solo play against the house side, presets only.
4. Shout orchestration, the relay feed, the payload trim, the lock removed.
5. Scoring, leaderboards, the results screen.
6. The viewer path (`applyFrame`, paused physics), red team state, head to
   head, centre court, the wall and the director.
7. Re-point the dugout at the arena API.

## Out of scope

No accounts, passwords or OAuth. No tournament brackets or scheduling. No
spectator chat. No changes to the agent topology, the models used, or the five
workshop stages. No replays or match video. The dugout's shell policy is not
touched — it stays unrestricted and stays unreachable from the arena.
