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

Needs an arena to play for. It is allowed to be down at boot - this waits, and
reconnects for as long as it is up.

```bash
cd grounds
ARENA_URL=http://localhost:8003 ARENA_SERVICE_TOKEN=dev-token ./run.sh
```

The first run downloads Chromium (~150MB); every run after that is instant.

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
is one Phaser game stepping Arcade physics for four players and a ball, with no
rendering and no audio - cheap, but not free, and fifty of them in one page all
step on the same thread.

Measure it rather than guess: run the capacity check in
`docs/superpowers/plans/2026-08-15-grounds-host-farm.md`, watch the frame
interval as matches are added, and set the value where the interval is still
flat. Set it too high and every match in the venue gets slow together, which is
the failure nobody can see from a tile on a wall; set it too low and kick-off
says the venue is full while there is CPU going spare. Scale out - more
instances - rather than up, and the arena will fill each one to its stated
capacity before it moves to the next.

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
