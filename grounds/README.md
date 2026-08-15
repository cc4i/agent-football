# Grounds

Somewhere for matches to be played that is not a tab. One Chromium, one page,
and a socket to the arena. Runs on **:8004** beside the arena (:8003), the
pitch (:5173), the coach (:8000), the captain (:8001) and the dugout (:8002).

A match's physics has always run in a browser. Until this existed, that browser
was the big screen's tab: fifty matches meant fifty tabs on however many
laptops, and a lid closing or a tab going to sleep took its match with it. This
moves the simulation to a server and leaves the tab with nothing to do but
watch. Fifty matches is one page.

```
arena  --host CODE + token + seed-->  grounds  --window.grounds.host()-->  page
       <--------- frames on /ws/rooms/{code}, one socket per match ------------
```

Nothing here parses a frame. The page opens a room socket per match and reports
on it exactly as a tab did, which is what means the arena needs no idea how many
processes are behind the football it is being told about.

## What it does not survive

The process. A match lives in a page in this instance's browser: restart it,
redeploy it, or let Cloud Run replace it, and every match it was running is
gone - the arena's sweep notices within thirty seconds and tells both managers
their match stopped reporting. That is the durability tier this was designed
for, deliberately: it survives humans (a closed lid, a locked phone, a tab
someone tidied away), not infrastructure. Deploy between events.

## Running it

Needs an arena to play for, and that arena has to be serving the pitch: the
page this opens is `{ARENA_URL}/pitch/host.html`, which is the arena's copy and
not Vite's. A stock local arena leaves `ARENA_PITCH_DIR` unset and answers 404
there, so build the bundle once and point the arena at it:

```bash
cd game/frontend && PITCH_BASE=/pitch/ npm run build
```

`PITCH_BASE` is not optional. Without it the built page asks for `/bundle/...`,
the arena serves it under `/pitch/bundle/...`, and what you get is a page that
loads, defines no `window.grounds`, and never says why. `arena/Dockerfile` sets
the same value, so this is the deployed build rather than a special one.

Then run the arena with `ARENA_PITCH_DIR=<repo>/game/frontend/dist` exported,
and this beside it:

```bash
cd grounds
ARENA_URL=http://localhost:8003 ARENA_SERVICE_TOKEN=dev-token ./run.sh
```

The first run downloads Chromium (~150MB); every run after that is instant.

Iterating on the simulation itself does not need any of that - :5173 is the lab
and it reloads as you type. Rebuild when you want the venue's matches to pick
the change up.

```bash
uv run pytest
```

The suite launches no browser and expects no arena. `supervisor.py` is the half
that knows about the arena and nothing about football, and it is tested against
a fake page; the half that knows about football is `game/frontend/src/host.js`
and is tested in that project's vitest suite. The two halves meeting is the
end-to-end run: start an arena, start this, kick off from `/arena`, and watch
the log say `running <CODE> (1 of 12)`.

## Environment

`cp .env.example .env` and edit it, or export the same names in the shell. The
shell wins where both say something, which is what lets one exported
`ARENA_SERVICE_TOKEN` cover every process at once.

| Variable | Default | What it does |
|---|---|---|
| `ARENA_URL` | `http://localhost:8003` | Which arena to play for. Both the control socket and the page come from here, so it is the only address this needs. |
| `ARENA_SERVICE_TOKEN` | unset | The shared secret carried in `X-Arena-Service`. Without it the arena closes the socket with 4403 and this retries against a token that will never work. What comes back down that socket is a room's physics token, which is why it is a server credential and never something a browser holds. |
| `GROUNDS_CAPACITY` | `12` | How many matches this instance offers to run. A promise about CPU: past it, kick-off is refused with a 503 rather than every match in progress slowing down together. |
| `PORT` | `8004` | What the health check listens on. Cloud Run sets this itself. |

## Sizing `GROUNDS_CAPACITY`

The number is per instance and it is a ceiling on CPU, not on memory. One match
is one Phaser game stepping Arcade physics for ten players and a ball, with no
rendering and no audio - cheap, but not free, and every one of them in a page
steps on that page's single thread. More vCPUs do not raise this directly; a
faster one does.

Measure it rather than guess. `tests/test_capacity_rehearsal.py` is the ramp:
each step names a target, opens whatever the page is short of, watches every
room's own socket, and stops when the slowest match drops below 8 frames a
second or its match clock falls under 0.9 of real time.

The shortfall goes out all at once rather than one room after another, and that
is the difference between measuring the page and measuring the ramp. Holding N
matches means replacing one every 180/N seconds, because a match is three
minutes and lets itself go at full time; a serial top-up pays five HTTP round
trips and a pickup wait per match, and against a venue across the internet that
price passes 180/N in the low twenties. A run that opened them one at a time
stopped dead at 23 with the page still at 9.9 Hz and its clocks at 1.01x - no
strain anywhere, just a ramp that could no longer outrun full time.

```bash
ARENA_URL=http://localhost:8003 ARENA_SERVICE_TOKEN=dev-token \
  GROUNDS_CAPACITY_CHECK=1 uv run pytest tests/test_capacity_rehearsal.py -s
```

On a laptop it went past 64 - the whole range the ramp looks at - with the
slowest match at 9.5 Hz and its clock at 0.99x. Fifty matches in one page is
not a stretch there. That is a laptop core, though, and the deployed number has
to describe a Cloud Run one, so measure the deployed instance instead: point
the same command at the deployed arena and add `GROUNDS_ALREADY_RUNNING=1`,
which stops it launching a browser of its own and leaves the football to the
grounds already connected. Give that instance a temporarily generous capacity
first, or the arena runs out of pitches before the page runs out of CPU - the
ramp says which of the two stopped it.

Then set the value under the measured ceiling with a margin. Too high and every
match in the venue gets slow together, which is the failure nobody can see from
a tile on a wall; too low and kick-off says the venue is full while there is
CPU going spare. Scale out - more instances - rather than up, and the arena
fills each one to its stated capacity before it moves to the next.

### What the deployed instance actually did

On the 4 vCPU / 4 GiB instance in `deploy/grounds.yaml`: **23 concurrent
matches, slowest 9.6 Hz, median 9.9 Hz, slowest clock 0.99x.** No ceiling was
found. Nothing in those numbers trends toward one either - the clocks sat
between 0.98x and 1.01x the whole way up, which is real time to within the
measurement.

The ramp stopped three times and not once was it the football:

1. the serial top-up above, at 23;
2. the same wall again after that was fixed, this time as a client-side read
   timeout on a kick-off POST;
3. Cloud Run recycling the arena instance underneath it - `1012 (service
   restart)` on every socket at once, six seconds of `no available instance`,
   then a clean boot. See "When Cloud Run recycles the arena" in
   `deploy/README.md`.

So `GROUNDS_CAPACITY` is 20: under the largest number actually demonstrated,
with margin, on the reasoning about asymmetry above. The real ceiling is
somewhere above 23 and finding it needs a driver closer to the venue than a
laptop on the other side of the internet - the page was never the thing that
ran out.

The spec's fifty in one page is unproven here and is not reached by raising
this number on a guess. It is scale-out, and scale-out needs each grounds
instance individually addressable; `deploy/grounds.yaml` says so where it pins
`maxScale` to one.

## Health

`GET /healthz` is the only thing this serves.

```json
{"ok": true, "running": 3, "capacity": 12}
```

`ok` is whether there is a page to play in, and it is the status code as much
as the body: 200 with a page, 503 without one. A probe reads the code and never
opens the body, so an instance whose browser has gone has to fail its health
check rather than describe its own failure in JSON nobody parses. It also
closes its control socket, so the arena stops offering it matches while the
platform gets on with replacing it.

The same 503 is the answer for the second or two before Chromium is up and the
arena has served the bundle, which is what a startup probe should wait through.
CPU throttling has to be off: between health checks, a throttled instance would
simply stop playing football.
