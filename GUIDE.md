# Guide

How the game actually runs, in the order it happens. Two tracks share one
arena: **the venue**, which is what people at an event play, and **the
workshop**, which is the Antigravity showcase in a chat window. The venue comes
first here because it is the one with strangers in it.

`README.md` is what the project is and why. This is how to operate it.

## The pieces

| | what it is | on | needed for |
|---|---|---|---|
| `arena/` | Rooms, seats, profiles, the event log, scoring, the match bus. The only writer of the squad. | :8003 | both tracks |
| `grounds/` | One headless Chromium. Every match in the venue is played in its single page. | :8004 | the venue |
| `game/` | The simulator, plus the game's own coach and captain. :5173 is the lab, not the venue's football. | :5173 :8000 :8001 | both tracks |
| `dugout/` | The chat UI over an in-process Antigravity agent, with four tuner subagents. | :8002 | the workshop |

The arena owns the state. Everything else asks it. Start it first and nothing
else will work until it answers.

## Bring it up

You need [uv](https://docs.astral.sh/uv/), Node, a Postgres, and - for the
workshop track only - the Antigravity CLI with `agy login` done.

```bash
brew services start postgresql@18       # the arena makes its own database
```

```bash
cp game/.env.example   game/.env        # then set GOOGLE_CLOUD_PROJECT
cp dugout/.env.example dugout/.env      # same
cp arena/.env.example  arena/.env       # ships ready for one machine
```

Two things every shell needs, and getting either wrong fails quietly:

```bash
export ARENA_SERVICE_TOKEN=dev-token                   # the same value in all four
export ARENA_PITCH_DIR=$PWD/game/frontend/dist         # from the repository root
(cd game/frontend && PITCH_BASE=/pitch/ npm run build) # what the grounds plays in
```

The token is what the dugout's tuners and the game's four player agents carry
instead of a phone session. The build is what the grounds opens: it plays the
arena's copy of the pitch at `/pitch/host.html`, not Vite's, and an arena with
`ARENA_PITCH_DIR` unset answers 404 for it. `PITCH_BASE` is not optional -
without it the page loads, defines no `window.grounds`, and never says why.

Then four shells, in this order:

```bash
cd arena   && ./run.sh    # :8003 - first, it owns the squad
cd game    && ./run.sh    # :5173 lab, :8000 coach, :8001 captain
cd grounds && ./run.sh    # :8004 - the Chromium that plays the matches
cd dugout  && ./run.sh    # :8002 - the workshop only; skip it for a venue
```

Confirm with `curl -s localhost:8003/health` before you put anything on a
projector.

---

# Track A - the venue

What a person at an event does, from the sheet on the wall to their name on the
board.

```
  printed sheet        big screen            phone                match
   /poster              /arena               /join/CODE          the grounds
      |                   |                     |                     |
      |  one QR, once     |  opens a room       |  takes a dugout     |
      +------ /scan ----->+------ CODE -------->+------ Ready ------->+
                          |                     |                     |
                          |<---- watches -------|<--- shouts ---------|
                          |                     |                     |
                          +------------- full time -------------------+
                                        |
                                     /board
```

## 1. Print the sheet, once

Open `/poster` on any screen and print it. It carries one QR for the whole
venue, pointing at `/scan`, and it is deliberately not about any one room - it
is printed in the morning and every room in the building is opened after that.

**Set `ARENA_PUBLIC_URL` before you print.** Unset, the arena works the address
out from the request's forwarded headers, which is right for a screen that can
be reloaded and wrong for a sheet pinned to a wall.

## 2. Open the big screen

Open `/arena` on the laptop driving the projector. It opens a room for itself
and shows:

- the four-character room code, and a QR pointing at `/join/{code}`
- the two dugout seats as they fill
- the standings, until the whistle
- **Also on now**: a live tile per match everywhere else in the venue

Two controls in the bar:

- **Solo / Head to head.** The screen guessed when it opened the room, before
  anybody walked up to it. Change it any time before kick-off. Going back to
  solo is refused if somebody is already sitting in the red dugout - that
  manager is not the screen's to remove.
- **New room.** Opens a fresh one.

Codes have no `O`, `0`, `I` or `1` in them, because they are read across a
noisy room and typed on a phone.

**If somebody wants the other mode**, they tap "Ask for head to head" under the
room on their `/home` page. The screen's pill lights up gold - *Alex Rivera
wants head to head* - and whoever is standing at the screen presses it. The ask
never moves the room on its own, stands for a minute, and is one per manager
per room, so a bored phone cannot strobe a wall screen. A screen with nobody
next to it simply goes quiet again.

## 3. A manager arrives

Two ways in, and they converge:

- **Scans the room's QR** on the big screen, landing on `/join/{code}`.
- **Scans the printed sheet**, landing on `/scan`. A phone the venue already
  knows goes to `/home`; a stranger goes to `/register` first. `/home` lists
  the rooms open now, the match they walked away from, and the board.

The join form asks for three things:

| field | notes |
|---|---|
| Name on the board | One manager per name at the event. A name somebody else holds is refused, worded with their spelling so the difference is visible. |
| Email | Optional. Only so they keep one place on the board if they play again on another phone. Never published; deleted after the event. |
| Where do you start | One of four opening stances. |

The four stances, applied to all four players **at kick-off**, not on joining:

| | stance | what it does |
|---|---|---|
| ⚡ | high press | Win it back in their half. Costly if it breaks. |
| 🎯 | tiki-taka | Short, safe, endless. Keep it and they cannot score. |
| 🔄 | counter | Let them come, then go long and go fast. |
| 🛡️ | low block | Two banks, no space, nothing given away. |

## 4. Ready, then kick off

Each seated manager taps **Ready** on their phone. A solo room needs blue only;
head to head needs both. Once every dugout the mode requires is ready, anyone
seated in the match can kick off.

What the arena does, in this order and for a reason:

1. Checks the room itself. A lobby with an empty dugout hears about the dugout.
2. Asks the grounds for a free pitch. **No pitch free is a refusal in words** -
   `no pitch is free to run this match` - and the room stays in its lobby. It
   is checked before anything is committed, because a room that went live with
   nobody simulating it would sit at 0-0 with a clock that never starts.
3. Applies both stances, before announcing the room live.
4. Hands the grounds the room's physics token and a seed, over one socket, to
   exactly one server.

## 5. The match

Three minutes at 1x. The grounds plays it; the big screen only watches.

**On the phone:** the score, the clock, a mini pitch, the relay, and the
composer. Two ways to talk to the squad, and they are not the same thing:

- **The four chips** - Press high, Sit deep, Break wide, Shoot early - are
  presets. They apply immediately, with no model in the loop, which is what
  keeps a match playable while the coach is thinking or unreachable.
- **Typed words** go through the chain. The arena logs and broadcasts them
  first, then answers; the chain runs on after the request returns.

```
words -> arena logs it -> coach /run_sse -> captain over A2A
                                              |
                              four specialists, at once
                                              |
                      PATCH profiles, carrying X-Arena-Service
                                              |
                    every rung on the room socket, ending in the huddle
```

Thirty to sixty seconds, end to end. Two shouts per dugout may be in flight at
once; a third is refused with *give the squad a moment*. Out-of-range values
are refused by the arena naming every problem at once, and the specialist
corrects itself.

A specialist can also report an injury or ask to come off. That lands in the
same log as everything else, so it reaches the phone, the big screen and any
screen that cuts to the match a minute later.

**On the big screen:** the match at the size of the room, both dugout relays
beside it, and the wall underneath. Click a tile to put that match on the big
screen; the chip in the bar hands it back. A screen showing somebody else's
match keeps a small QR for its own room, because a code offering to "manage"
what you are watching would be offering the wrong room.

**The match outlives the tab.** Close the arena entirely, come back ninety
seconds later, and the clock has moved on without you. If that does not hold,
the grounds is not doing its job and nothing else matters.

## 6. Full time, and the board

The whistle closes the room. Every number is recomputed by the arena from the
log it wrote itself, so what the phone shows and what the board shows cannot
come apart. A client that submits a total is not asked.

**Solo** pays points:

| row | points |
|---|---|
| Won / drew / lost | +1000 / +400 / +100 |
| Each goal | +300 |
| First goal: ≤30s, ≤60s, ≤120s, later | +500, +350, +200, +100 |
| Each conceded | -100, floor of -500 |
| Clean sheet | +300 |
| Each effective shout, up to three | +100 |

A shout is *effective* if a goal followed within 45 seconds of the first
profile write it caused. Measured from the write, not the send, so it pays for
a good instruction rather than a fast chain. A shout the squad never acted on
has no window at all.

**Head to head** is Elo instead: everybody starts at 1200, K is 32.

`/board` shows both tables, on a wall-mounted screen or on a phone.

---

# Track B - the workshop

The Antigravity showcase. One presenter, a chat window on :8002, and the
reserved room `WRKS` - unranked, never kicks off, never ends, and the room the
pitch renders when opened with no `?room=`.

This track does not need a grounds. It does need `game/.env`: without it the
pitch still renders and most of it works, but every call into the game's own
agents fails and the squad never resets.

Open http://localhost:8002. The header should show Antigravity lit amber and
four green dots, arena among them. Five stages, in any order and as often as
you like:

| | stage | say something like | what it shows |
|---|---|---|---|
| 1 | Rebrand the team | *Kit us out in black and gold with a wolf crest.* | Generates the sprites and puts your crest on the shirt. |
| 2 | Take the field | *Now get us on the pitch.* | Nobody handed it a browser tool. It writes the Playwright script itself and fixes it when it breaks. |
| 3 | Read the game | *How are we doing?* | The live score, and every attribute with the band it must stay inside. |
| 4 | Tune the squad | *They keep breaking through the middle. Tighten it up.* | Four subagents at once, one player each, each able to touch only its own. |
| 5 | Shout to the bench | *Tell the lads to push up and press high.* | Hands the decision to the game's own coach, captain and four players. |

Stages 4 and 5 are deliberately opposite: tuning sets numbers Antigravity
chooses, a shout lets the game's agents choose them. Both write through the
same arena, which is why stage 5 does not tick stage 4.

Stage 2 is the one people remember. A real Chrome window opens in front of you
and plays the match, maximised and muted. If it comes up headless, the script
is wrong.

**Colour is the contract.** Amber is Antigravity and nothing else; cyan belongs
to the game's own agents. Every line in the log names who did it.

**Press Start over** when you open the page onto a quest somebody else was
halfway through. Stages are judged per session, so a dugout left running keeps
counting yesterday's work. It blanks the quest and gives the agent a fresh
conversation, leaving the kit on disk and the squad in the arena alone.

`docs/superpowers/SMOKE.md` is the step-by-step version of this, with what each
step looks like when it is working.

---

# When it goes wrong

Symptoms in the words you will actually see.

| what you see | what it is | what to do |
|---|---|---|
| `no pitch is free to run this match` | No grounds is up, or every pitch is taken. The room is fine and still in its lobby. | Start the grounds. Kick off the same room again; it recovers without being reopened. |
| Room goes live, clock never starts | Should not happen any more - it is what the check above exists to prevent. | Check the grounds' log. This is a bug worth reporting. |
| `somebody at this event is already managing as Alex` | One manager per name. | Pick another name. The refusal shows the holder's spelling, so a near-miss is visible. |
| `not every dugout is ready` | A seat is empty or unready. Head to head needs both. | Fill it, or switch the room to solo from the big screen. |
| `you have already asked - give the screen a moment` | One mode request per manager per room, per minute. | Wait, or ask about a different room. |
| `that room is already head to head` | Asking for the mode it already plays. | Nothing to do - just join it. |
| `give the squad a moment` | Two of that dugout's shouts are still going out. | Wait, or use a chip - presets need no chain. |
| *The screen running this match stopped reporting* | The grounds went away. The sweep notices within 30 seconds. | The match is gone. A grounds restart or redeploy takes every live match with it, by design. Deploy between events. |
| A dot in the dugout header goes red | That service is down. The header polls every four seconds. | Arena red first: nothing else works until it answers. |
| `No API key was provided` | `game/.env` is missing. | Copy it from the example and set `GOOGLE_CLOUD_PROJECT`. |
| A squad that reads fine and never moves | `ARENA_SERVICE_TOKEN` differs between shells, so the arena refuses the writes. | Export the same value in every shell. The shell wins over every `.env`. |
| 404 at `/pitch/host.html` | `ARENA_PITCH_DIR` unset, or the bundle built without `PITCH_BASE=/pitch/`. | Rebuild and restart the arena. |
| Red banner in the dugout | It prints the reason. Usually a missing Antigravity login. | `agy login`, then reload. The composer staying disabled is expected, not a second fault. |
| Stage 4 done but stage 3 never run | Somebody else's session. | Press **Start over**. |

The venue is full at 120 live rooms and 60 wall screens; both are configurable.
See `arena/README.md`.

# Read more

- `arena/README.md` - every endpoint, every environment variable, and the
  arithmetic for sizing `ARENA_CHAIN_LIMIT` against your Vertex quota
- `grounds/README.md` - what the grounds survives and what it does not
- `deploy/README.md` - two Cloud Run services, and why every deploy drops every
  live match
- `docs/superpowers/SMOKE.md` - the manual checklist for both tracks
- `docs/superpowers/specs/` - the designs, including why the dugout is built
  the way it is
