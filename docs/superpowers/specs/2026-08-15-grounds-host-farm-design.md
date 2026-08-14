# The grounds: matches that run without a screen, and a wall that can watch fifty of them

Date: 2026-08-15
Status: Approved, ready for implementation planning.

## Purpose

Today a match exists because a browser tab is holding it. The screen opens a
room, so the screen holds that room's physics token, and the pitch it frames is
the host. That is the rule, stated at the top of `arena/static/arena.js:4-6`,
and everything in the venue follows from it.

It has two consequences the venue cannot live with:

- **One match needs one screen.** Fifty concurrent matches need fifty screens,
  or fifty tabs on somebody's laptop. `arena/tests/test_load_rehearsal.py` sizes
  the arena for fifty rooms at ten frames a second. Nothing sizes the fifty
  browsers that would have to produce them.
- **A match dies with the tab.** A closed lid, a dimmed screen, a tab switched
  away from, an operator tidying up. `keepAwake()` in
  `game/frontend/src/arena.js:83` is an apology for this rather than a fix, and
  it says so.

This moves the simulation into a service the venue owns, and takes the
consequences: the big screen stops running football and starts only watching
it, which is what finally makes fifty matches browsable from one page.

Three constraints shaped every decision below:

- **The match must not change.** The README's measured tuning result, 0W 1D 7L
  before and 4W 1D 3L after, only means something if the simulation is the same
  simulation. No re-implementation.
- **Matches survive humans, not restarts.** A laptop lid must not end a match.
  A grounds restart may. This was decided explicitly and it keeps the design
  small.
- **Stage 2 is untouched.** The real Chrome window Antigravity opens is the
  centrepiece of the showcase and nothing here may go near it.

## What is being built

```
  the big screen             phones                    grounds        :8004
  one page, one canvas       the dugout                one Chromium, one page
        |                       |                      N Phaser.HEADLESS games
        |  /ws/wall             |  /ws/rooms/{code}            |
        |  /ws/rooms/{code}     |                              | /ws/grounds  (control)
        v                       v                              | /ws/rooms/{code} x N
  +----------------------------------------------------+       |  (data, unchanged)
  | arena                                        :8003 |<------+
  | rooms, seats, profiles, the log, scoring, the bus  |
  +----------------------------------------------------+
```

The fourth thing is `grounds/`, the place the pitches are. It runs matches and
nobody watches it. It is a client of the arena rather than a server: the only
thing it listens on `:8004` for is the health check Cloud Run insists on.

## The rule that changes

> The screen opens a room, so the screen holds that room's physics token.

becomes

> The grounds run every match. A screen only ever watches.

That is the whole design in one line. Everything below is consequence.

### Why the seam is already there

This is a smaller change than it sounds, because the split it needs was built
two commits ago for a different reason.

| What | Where | Why it matters here |
|---|---|---|
| The scene already has two roles | `game/frontend/src/game.js:99` | `new SoccerGameScene({ role: 'host' \| 'viewer' })`. Not a flag threaded through: a role settled before `create()` runs. |
| A viewer simulates nothing | `game.js:479`, `game.js:512` | "A viewer runs none of the thousand-odd lines below." It eases toward frames and that is all it does. |
| The host emits a clean frame | `game.js:1632`, `game.js:1644` | Ten a second, positions as fractions of the canvas, score and clock inside it. |
| The host protocol is already browser-free | `arena/fake_host.py` | It drives a real room over the real socket with no browser anywhere. Nothing in the arena assumes Chrome. |
| Phones never load the pitch | `arena/static/play.html:32`, `play.js:259` | "The arena screen runs the pitch. Your phone stays on for the dugout and the score." Phones draw their own canvas from frames. |

So the question was never whether physics can leave the browser. It was what to
run the existing host role inside.

## The grounds

Two units, one job each, each testable without the other.

### The page

`game/frontend/host.html` and `game/frontend/src/host.js`, a second Vite entry
into the same build. No UI. It exposes exactly two functions:

```js
window.grounds = {
  host(code, token),   // start simulating this room
  drop(code),          // stop, and let the game go
};
```

For each hosted room it makes one `Phaser.Game`:

```js
{
  type: Phaser.HEADLESS,          // no render pass, which is the expensive half
  width: 1408, height: 768,       // the same coordinate space the wire speaks
  physics: { default: 'arcade', arcade: { gravity: { y: 0 }, debug: false } },
  scene: [new SoccerGameScene({ role: 'host' })]
}
```

and wires it to its own `/ws/rooms/{code}?client_id=<token>` exactly as a tab
does today, via the existing `connect()` in `game/frontend/src/arena.js`.

Everything a tab has that a headless host does not need is absent rather than
disabled: no profiles panel, no simulation speed bar, no `autoFocus`, no
`keepAwake()`, no start screen, no debug log. `Phaser.HEADLESS` still builds
every display object the scene creates, because `create()` builds them before
the role is consulted, and that is fine: Chromium has a real DOM and a real
canvas, and HEADLESS simply never draws.

The page is driven by hand from a browser console with no arena in sight, which
is what makes it testable.

### The supervisor

`grounds/`, Python and Playwright, matching the other services. It launches one
Chromium, opens the page, holds the credentials, and decides which rooms this
instance runs. It never touches physics and never parses a frame.

The page is loaded **from the arena**, at `ARENA_URL/pitch/host.html`, rather
than baked into a fourth image beside the arena's copy. One build, and version
skew between the simulation and what the wall renders becomes impossible by
construction.

### The control plane: `/ws/grounds`

One socket per grounds instance, authenticated with the same `X-Arena-Service`
the specialists already carry (`arena/app.py:1075`).

| Direction | Message | Meaning |
|---|---|---|
| up | `grounds.here {capacity}` | on connect, and how many matches this instance will take |
| down | `host {code, token, seed}` | run this match, with this physics token |
| down | `drop {code}` | stop running it |

Nothing else. The frames themselves never touch this socket.

### The data plane: unchanged

Each match reports on its own `/ws/rooms/{code}` with `host.state` and
`host.event`, byte for byte as a tab does today. `_handle_from_host`
(`app.py:1433`) is not modified. Scoring, the log, the bus, `WALL_HZ` and the
rate limits are all untouched, and `fake_host.py` stays valid, which is what
keeps the arena's own tests from needing a browser.

### Assignment happens at kickoff

The wall socket carries only rooms whose status is already `live`
(`rooms.py:380-395`), and a room cannot go live without a host
(`rooms.py:267-268`). The grounds therefore cannot discover work by watching the
wall: they would be waiting for a room that is waiting for them.

So the arena assigns rather than the grounds claiming. Inside `POST /start`:

1. Pick a connected grounds with capacity left.
2. If there is none, refuse in words. The room stays in its lobby.
3. Otherwise send `host {code, token, seed}` and let the match go live.

The refusal is the one genuinely new failure mode in this design, and it is
honest: today it cannot happen, because the screen that opened the room is the
host by definition. `start_match`'s "a match needs a host" stops being vacuous
and starts meaning it.

### A grounds restart does not resume matches

Live matches stop reporting, and the arena's existing sweep (`_give_up_on_the_missing`, `app.py:1540`)
abandons them exactly as it abandons a room whose screen closed. The grounds do
not re-claim them on the way back up.

The alternative is worse. Re-hosting a live room would attach a fresh
simulation to a match already twenty minutes old: players in kickoff positions,
the clock reset, the score at 0-0 while the arena's log says 2-1. A match that
silently starts again is a worse outcome than a match that ends, and recovering
the real state means snapshotting the simulation, which is the durability tier
that was explicitly not chosen.

## The arena's side

### The token splits in two

`host_client_id` currently does two unrelated jobs:

| Job | Where |
|---|---|
| Physics authority | `_handle_from_host`, `app.py:1433` |
| This screen owns this lobby | `_require_host`, guarding the mode switch at `app.py:667` |

Once physics leaves the screen, one token cannot be both. They separate:

- `create_room` keeps minting `host_client_id`. `POST /api/rooms`
  (`app.py:634-647`) stops returning it to the browser and returns a **screen
  token** instead.
- `_require_host` becomes `_require_screen`, covering the mode switch and the
  lobby heartbeat.
- `_handle_from_host` keeps comparing against `host_client_id`, unchanged, now
  only ever matched by the grounds.

The screen token never leaves the arena and the browser, and the physics token
never leaves the arena and the grounds. Neither knows the other exists.

### Liveness splits in two as well

`rooms.py:491-502` explains why a room needs a heartbeat at all: a lobby whose
screen has closed is a code that will never do anything, and the arena goes on
offering it to every phone in the building. That reasoning survives. What
changes is who is doing the reassuring.

| A room in | is held alive by | over |
|---|---|---|
| lobby | its screen | the screen's open socket, bearing the screen token |
| live | the grounds | `host.state` frames, as today |

Same sweep, two sources, both stamping `last_heard_at`.

`a7588f2` landed after this design was drafted and replaced the screen's
`host.here` timer with socket presence: `_HeldRooms` counts the sockets this
instance has open for each room, `holding` is settled once at the handshake,
and the sweep vouches for every held code before judging any of them. That
makes this section *simpler* than it was written, and one detail load-bearing.

`holding` must now ask which kind of client this socket is, because the two
kinds prove different things:

- A **screen** socket proves its lobby is real. It must not prove a *live*
  match is real - otherwise a wall left open on a match whose grounds died
  keeps that match live for the rest of the evening, and the sweep can never
  reach it.
- A **grounds** socket proves the match is being simulated, in lobby or live.

So `_HeldRooms` counts by kind, and the sweep stamps them against different
statuses:

```python
rooms.heard_from_all(connection, held.codes("screen"), now, statuses=("lobby",))
rooms.heard_from_all(connection, held.codes("grounds"), now)
```

The screen's `stillHere()`, `STILL_HERE_MS` and its `host.here` message are
therefore deleted outright rather than re-credentialled. The socket is the
proof; a timer saying the same thing more weakly is not worth keeping, and
`host.here` checked against a token the screen no longer holds would be
silently refused on every tick.

### The substitution toast routes through the arena

`game/frontend/src/main.js:910` gates the injury and substitution poll behind
`if (!isViewer())`, so today only the screen that *hosts* a match shows the
toast. After this change no screen hosts anything, and the toast would vanish
from the venue entirely.

It moves onto the same path as everything else:

- `game/agents/football_mcp_server.py` posts to the arena instead of writing
  `player_state/substitutions/{ROOM}__{team}.json` (`:61-72`), carrying
  `X-Arena-Service` as the specialists already do.
- The arena records it as a room event and publishes it on the room socket.
- The wall, the phones and the dugout all see it, and it survives a cut between
  matches because it is in the log rather than in a file somebody is polling.
- `createSubstitutionPoll`, `SUBSTITUTIONS_URL` (`main.js:847`) and the
  two-second poll go.

This retires the one path the README admits bypasses the arena, and it lets the
`player-state` volume shared between the arena and coach containers
(`deploy/service.yaml:98,118,172`) go with it.

## The wall

### One page, one canvas, booted once

`arena/static/arena.html` loses the `#pitch` iframe and gains a container.
`arena.js` imports the scene as a module and makes one `Phaser.Game` in
`viewer` role when the wall opens. It never makes another.

Loading it needs one build affordance. `/pitch/bundle/*` is content-hashed on
purpose (`app.py:1798`, `:1822`), so the wall cannot name a file. A stable
`/pitch/viewer.js` entry, served by the `Revalidated` mount rather than the
`Immutable` one, gives it something to import. The address comes off
`venue.pitch_url`, which the wall already fetches (`app.py:544`), so
same-origin in production and `:5173` in development both work with no second
mechanism.

Two things the iframe was quietly handling and the direct mount is not:

- The canvas is a fixed 1408x768 and the frame was scaled by CSS, so the mount
  needs an explicit Phaser scale mode to fill `.court`.
- `audio.js` now plays from the wall page rather than from inside a frame. The
  same autoplay situation as today, but worth checking on a real screen.

### The cut

`cutTo()` stops setting `src` and calls a new `scene.point(code)`, which:

- clears `wire`, so no frame from the last match is still pending,
- snaps rather than eases the first frame of the new one,
- resets score, clock and manager nameplates.

Without that reset you get the previous match's players sliding across the
pitch into the new one, which is the single most visible way this can look
wrong.

`game/frontend/src/arena.js:33-44` currently freezes the room from URL params at
module load. It becomes a value the caller supplies: the pitch page reads the
URL and passes it in, the wall passes whatever it is showing. The socket
juggling in `listen()` and `courtRoom` is already correct and stays.

### Fifty matches, browsable

The list-and-click UX already exists. `tileFor` (`arena.js:395-400`) makes every
tile a `<button>` whose click calls `pin(code)`. `strip()` paginates. `paintTile`
draws each match as nine dots and a ball on a 320x180 canvas, deliberately not
Phaser. Number keys pick a tile and Escape returns to auto.

Four things break at fifty, and three of them are what this change is for:

1. **You cannot click a match while your screen is hosting one.** `pin()`
   refuses outright (`arena.js:641`). With the grounds hosting, that refusal has
   nothing left to protect and goes, along with `hostingLive()` and the
   `choose()` branch at `:508` that pins our own match to centre court.
2. **A click costs an iframe reload.** The direct mount makes it a cut on the
   next frame.
3. **Pages rotate on a twelve-second carousel with no way to navigate**
   (`:721-723`). Six per page and fifty matches is nine pages, so the match you
   want is up to a hundred seconds away and you cannot go and get it. This is
   new work: real pagination controls, and the carousel pausing while somebody
   is browsing and resuming when they stop.
4. **Six per page** was sized to be read from the back of a room (`:29-31`). A
   grid you click is a different budget, and a larger one.

Costing this is easy: the wall socket already sends every live room's frames,
thinned to `WALL_HZ` per room (`app.py:1251`). Drawing fifty tiles instead of
six costs the arena nothing. It is canvas work on the screen and no more
messages.

The director survives and keeps its job. When nobody is touching the screen it
picks the best match for the audience, as it does today. A click pins and stops
the carousel; Escape hands it back. `pinned` is already described at
`arena.js:64` as "the operator's choice, which outlives the director's", which
is exactly the behaviour wanted, now reachable at fifty rooms instead of six.

### What gets deleted

From `arena/static/arena.js`: `hostingLive()`, the `choose()` branch at `:508`,
the `src` comparison guard at `:596`, the refusal inside `pin()`, and -
following the liveness section above - `stillHere()`, `STILL_HERE_MS` and the
timer around them. `hostToken()` and its sessionStorage survive under the name
`screenToken()`, holding the screen token rather than the physics one: the wall
still has to prove it owns its own lobby to switch that room's mode, and it
still has to bear that token on the room socket for `_HeldRooms` to count it.

Beyond the wall, `?as=host` and `?as=viewer` lose their last consumers: host
goes to the grounds page, viewer goes to the wall's direct mount, and phones
never loaded the pitch to begin with. So `isHost()`, `isViewer()`,
`room.clientId`, `keepAwake()` and the host and viewer branching through
`main.js` all collapse, and the pitch page reverts to being purely the workshop
lab. This deletion lands in the same change rather than leaving three ways to
boot one scene.

## Determinism, and the seed

`game.js` calls `Math.random()` twenty-two times and seeds nothing. The
simulation is therefore not reproducible, and the test this design most wants,
running the same match in a tab and in the grounds and asserting the frames
agree, cannot be written.

Those twenty-two sites move onto Phaser's seeded RND, and the arena hands each
match a seed in the `host` assignment. This makes the A/B a hard assertion
rather than an impression, and it makes every future change to the simulation
testable at all.

It costs one thing, stated plainly: the random stream changes, so matches play
differently than they do today. That does not invalidate the README's tuning
table, which measures a squad against a squad, but re-running it would not
reproduce the same eight scorelines.

## Deployment

A `grounds` service of its own, beside the existing one.

| Setting | Value | Why |
|---|---|---|
| `minScale` / `maxScale` | `1` / `1` | One process holds the venue's matches. Two would double-run any room assigned twice. |
| `cpu-throttling` | `false` | The arena sets this already (`service.yaml:54`). Here it is load-bearing: the only requests the grounds ever serve are health checks, so between them a throttled instance simply stops playing football. |
| execution environment | gen2 | Chromium. Also `--disable-dev-shm-usage`. |
| cpu / memory | from the rehearsal | Not from a guess. See below. |

The grounds reach the arena over `ARENA_URL` for both the page and the sockets,
and carry `ARENA_SERVICE_TOKEN` as the coach and captain already do.

`MAX_LIVE_ROOMS` (`app.py:64`, checked at `:640`) stops being a number in the arena's config and
starts being the capacity the connected grounds announced.

## Testing

| What | How |
|---|---|
| the host page | vitest against a fake socket, no arena. Given two rooms it runs two games; given `drop` it runs one; a dropped room's socket closes. |
| the supervisor | pytest against a fake page and a fake arena socket, no Chromium. |
| `/ws/grounds`, the token split | beside `test_room_socket.py` and `test_wall_socket.py`, which establish the pattern. A screen token must be refused by `_handle_from_host`; a physics token must be refused by the mode switch. |
| `/start` with no grounds | refuses, and the room stays in its lobby rather than going live into silence. |
| the wall's cut | E2E: open two rooms, drive both with `fake_host.py`, assert the cut happens with no reload and no ghost sprites from the previous match. Needs no grounds at all. |
| the paginated grid | fifty rooms on the wall socket, assert every one is reachable by paging and that a click pins it. |
| fidelity | same seed in a tab and in the grounds, assert the frame streams agree. Only possible once the seeding above lands. |
| capacity | `grounds/tests/test_capacity_rehearsal.py`, a headless twin of `test_load_rehearsal.py`. Ramp matches until frame pacing slips past 10 Hz, print the ceiling. |

The capacity rehearsal is what sets the numbers in the deployment table and in
`MAX_LIVE_ROOMS`. Nothing in this document writes a CPU count before it has run.

## What was considered and rejected

**Node with jsdom, no browser at all.** Lighter than Chromium and cheaper to
run. Rejected because Phaser 4 calls `CanvasPool.create` before its HEADLESS
early return (`phaser.js:17371`, returning at `:17386`), and every `add.text`
creates another (`:90536`), of which the scene makes about twenty per match. So
it needs `node-canvas` native builds, and any WebGL-first internal that leaks
becomes ours to shim. The failure mode is the disqualifying part: a divergence
would not raise, it would just make matches play differently, and nothing in
the test suite would notice.

**Porting the simulation out of Phaser.** The arena owns a plain simulation and
browsers become pure renderers, which `game.js:512` says they nearly already
are. This is the better end state and it retires the whole "the host is trusted
for physics" awkwardness. Rejected for now because it means re-implementing
2267 lines of tuned Arcade behaviour, and re-earning behavioural parity is the
hard part rather than the physics. The host page in this design is exactly the
seam that work would cut at, so nothing here forecloses it.

**One tab running many matches.** The smallest possible change: a page that
makes N `Phaser.HEADLESS` games, opened by the operator. Solves fifty matches
per screen and makes the second problem worse, because one closed tab now takes
fifty matches with it instead of one.

**Grounds claiming rooms rather than the arena assigning them.** Needs a
discovery channel that does not exist, because the wall carries only live rooms
and a room cannot be live without a host. Assignment inside `/start` needs no
new discovery at all and gives the arena something it currently cannot express:
knowing whether anybody can run a match.

## Risks and open numbers

- **Whether fifty matches fit in one Chromium page is unmeasured.** This is the
  load-bearing unknown. Everything above is arranged so the capacity rehearsal
  answers it before the number is written anywhere, and so that a shortfall
  costs a second page in the same instance rather than a redesign.
- **One process holds the venue.** Accepted, and consistent with the arena's own
  single instance. A grounds restart ends every live match.
- **Chromium in Cloud Run.** Well-trodden but not free: image size, `/dev/shm`,
  and a startup that must complete before the first kickoff, which is what
  `minScale: 1` is for.
- **The seeding change alters match outcomes.** Deliberate, stated, and worth it
  for a simulation that can be proven not to have moved.
- **Audio on the wall page.** Moves out of an iframe. Needs looking at on a real
  screen rather than reasoning about.
