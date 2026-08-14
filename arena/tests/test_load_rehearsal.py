"""Fifty rooms at ten frames a second, which is what the venue is sized for.

Skipped unless ARENA_LOAD=1. It takes minutes and it is a rehearsal rather
than an assertion about correctness: the numbers it prints are the point.

    cd arena && ARENA_LOAD=1 uv run pytest tests/test_load_rehearsal.py -s

The `-s` is not optional. The report goes to stdout and pytest swallows the
stdout of a test that passed, which this one intends to. `ARENA_LOAD_URL`
points it at a deployed arena instead of at one of its own; see
`deploy/README.md` for what only that run can tell you.

What it measures, and why each is the number it is:

* **Mean cores is the headline**, not the busiest second. An instance is sized
  by what it sustains, and a one-second spike while fifty rooms kick off at
  once is not that. The busiest second is printed beside it as context, from
  the difference of two `ps` TIME readings rather than from `ps %cpu`, which is
  a decaying average and cannot see a spike at all.
* **Loaded against idle** is what makes the lag figure mean anything. The idle
  window is six sweep periods long, so the sweep's own stamping is inside the
  percentile rather than being it. The result to look for is the two p50s and
  the two p99s: if the load has not moved them, the synchronous psycopg driver
  is not what limits this venue.
* **Latency is measured on a room socket**, where every frame goes. Not on the
  wall, where `WALL_HZ` thins them on purpose and a frame that never arrives is
  the design working rather than a queue overflowing. It is timed from this end
  and so it contains this process's own scheduling, which is why the driver
  watches its own loop too and prints that beside it. The stamp goes on before
  the frame is serialised, so the send path is inside the number as well, and
  that only stays negligible while the payload does.
* **Event-loop lag** is the one the plan parked: the arena's async handlers
  call psycopg synchronously, so every query blocks the loop for as long as it
  takes. At one room nobody notices. Fifty at 10 Hz is where it either shows up
  or is proven not to matter.
* **Dropped messages** come from `Subscription.dropped`, which the bus already
  counts, rather than from a gap in sequence numbers.

The arena runs as a subprocess rather than in a thread of this one, because in
process any CPU reading would include the load driver, and at fifty rooms the
driver is the larger of the two. A subprocess is also the only way the single
worker and the sockets under test are the real ones.

The rehearsal sits inside both rate-limit bursts, and not by much: fifty room
opens against a `ROOM_BURST` of 120, a hundred player joins against a
`PLAYER_BURST` of 120, and every one of them from 127.0.0.1, which is one key.
A hundred rooms would not fit. Whoever raises the number finds that out here
rather than from a refusal they read as a bug, and nothing below raises a limit
to make the rehearsal pass: a bucket that refuses something is a real answer
about the venue and it is printed with the rest.
"""

import asyncio
import json
import os
import resource
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import psycopg
import pytest
import websockets
from psycopg import sql

import app
import db
import fake_host
import rooms

pytestmark = pytest.mark.skipif(os.environ.get("ARENA_LOAD") != "1",
                                reason="set ARENA_LOAD=1 to rehearse the venue's load")

# The venue the spec sizes for: fifty matches at once, two managers in each,
# two more phones watching each match, and one big screen on the filmstrip.
ROOMS = 50
HZ = 10
MATCH_SECONDS = 180
VIEWERS_PER_ROOM = 2

COOKIE = "arena_session"
FIXTURE = Path(fake_host.__file__).resolve().parent / "fixtures" / "match-3-1.jsonl"

# The frame `arena/README.md` documents and `game.js` builds: a scoreline, a
# clock and eleven points, at the three decimals the pitch rounds to. Kept here
# to be measured rather than described, because the fixture's state frames
# carry the score and the clock and nothing else, and the difference is the
# main thing this rehearsal understates.
DOCUMENTED_FRAME = {"type": "host.state", "payload": {
    "score": [2, 1], "clock": 102, "ball": [0.551, 0.382],
    "blue": [[0.134, 0.492], [0.271, 0.318], [0.263, 0.664],
             [0.418, 0.205], [0.402, 0.771]],
    "red": [[0.871, 0.489], [0.702, 0.334], [0.719, 0.651],
            [0.583, 0.229], [0.598, 0.744]]}}

# How long the whole run may take before something is wrong rather than slow.
# The match is three minutes; this is that with room for a driver that cannot
# quite keep up, which is a finding rather than a failure.
PATIENCE_SECONDS = 420

# What the lag monitor sleeps for between readings, in the arena's own loop.
TICK = 0.05

# How long to sit still before kicking off. A sleep of TICK overshoots by a
# little on a machine with nothing to do, and without that floor to read it
# against, the lag under load is a number with no meaning.
#
# Six sweep periods rather than a round number of seconds. `_watch_for_the_
# missing` is the only thing that happens in an idle arena, and it stamps every
# live room's `heard_from` synchronously as it goes, so a window one sweep long
# measures the sweep and calls it the floor. Six of them make the idle p99 a
# percentile rather than a single observation, and cost twenty-five seconds on
# a run that already takes three minutes.
QUIET_SECONDS = 6 * app.SWEEP_SECONDS


# Run inside the arena's process, because the two things this rehearsal most
# wants cannot be seen from outside it: how long the event loop was blocked,
# and what the bus threw away. Everything here is stdlib or already a
# dependency - the point of running the arena as a subprocess was to avoid
# adding psutil, and adding one to read the numbers back would undo that.
_ARENA_UNDER_INSTRUMENTS = '''
import asyncio
import json
import signal
import sys
import time
from pathlib import Path

import uvicorn

from bus import Bus
from app import app

TICK = float(sys.argv[3])

# uvicorn restores whatever handler it replaced and then re-raises the signal
# that stopped it, so a default SIGTERM disposition kills this process before
# `serve` has returned and the readings below have been written. A handler that
# does nothing turns that re-raise into a no-op.
signal.signal(signal.SIGTERM, lambda *ignored: None)

lags = []
kept = []

_subscribe = Bus.subscribe


def subscribe(self, topic, maxsize=64):
    """Hold on to every subscription, so its drop count outlives its socket."""
    subscription = _subscribe(self, topic, maxsize)
    kept.append(subscription)
    return subscription


Bus.subscribe = subscribe


async def watch_the_loop():
    """What a sleep of TICK actually cost. The excess is the loop being held.

    Stamped with the clock the driver reads too, so the readings taken while
    the venue was quiet can be told from the ones taken under load. On every
    platform this runs on `time.monotonic` counts from boot, which is what
    makes one process's reading comparable with another's.
    """
    while True:
        before = time.perf_counter()
        await asyncio.sleep(TICK)
        lags.append([time.monotonic(), time.perf_counter() - before - TICK])


async def main(port, readings):
    # The same shape the Dockerfile runs: one worker, one process, no reload.
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                            log_config=None, ws="websockets-sansio")
    watcher = asyncio.create_task(watch_the_loop())
    try:
        await uvicorn.Server(config).serve()
    finally:
        watcher.cancel()
        dropped = {}
        for subscription in kept:
            dropped[subscription.topic] = dropped.get(subscription.topic, 0) + subscription.dropped
        readings.write_text(json.dumps({"lags": lags, "dropped": dropped}))


asyncio.run(main(int(sys.argv[1]), Path(sys.argv[2])))
'''


class _Refused(Exception):
    """A rate limiter said no. Recorded and reported, never worked around."""


class _Venue:
    """Where the rehearsal points, and what it can read back from there.

    `database` and `readings` are None against a deployed arena: its Postgres
    is not this machine's to count rows in, and nothing put a lag monitor in
    somebody else's process. The frame rate, the latency and the delivery
    counts are measured from this end and survive either way.
    """

    def __init__(self, base, database=None, readings=None):
        self.base = base.rstrip("/")
        self.database = database
        self.readings = readings
        self.process = None
        self.born = time.monotonic()

    @property
    def wire(self):
        # http -> ws and https -> wss, in one substitution rather than two.
        return "ws" + self.base[len("http"):]


@pytest.fixture
def venue(dsn, tmp_path):
    """An arena of the rehearsal's own, unless ARENA_LOAD_URL names another."""
    deployed = os.environ.get("ARENA_LOAD_URL", "")
    if deployed:
        yield _Venue(deployed)
        return

    port = _a_free_port()
    readings = tmp_path / "instruments.json"
    here = Path(fake_host.__file__).resolve().parent
    environment = {**os.environ, "ARENA_DB": dsn}
    # The image sets this and refuses to start without three secrets, which is
    # the image working and is not what this measures.
    environment.pop("ARENA_ENV", None)
    arena = subprocess.Popen(
        [sys.executable, "-c", _ARENA_UNDER_INSTRUMENTS,
         str(port), str(readings), str(TICK)],
        cwd=here, env=environment)

    place = _Venue(f"http://127.0.0.1:{port}", database=dsn, readings=readings)
    place.process = arena
    try:
        _wait_for_the_door(place.base, arena)
        yield place
    finally:
        if arena.poll() is None:
            # SIGTERM rather than a kill: uvicorn shuts down gracefully on it,
            # which is what lets the instruments write themselves out.
            arena.terminate()
        arena.wait(timeout=60)


def _a_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        return held.getsockname()[1]


def _wait_for_the_door(base, arena):
    """Poll /health until the arena answers, or say why it never will."""
    for _ in range(300):
        if arena.poll() is not None:
            raise RuntimeError(f"the arena exited with {arena.returncode} before serving")
        try:
            if httpx.get(f"{base}/health", timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("the arena never answered /health")


def _child_cpu():
    """Every reaped child's CPU so far, user and system together, in seconds.

    Reaped is the load-bearing word, and it is what keeps the sampler out of
    this number: `ps` is a child of this process too, but each one is waited
    for the moment it exits, so its cost is already in the reading taken before
    the arena is stopped. Between that reading and the one after, the arena is
    the only child anything waits for.
    """
    spent = resource.getrusage(resource.RUSAGE_CHILDREN)
    return spent.ru_utime + spent.ru_stime


def _cpu_seconds(said):
    """Seconds out of a `ps` TIME field, which is `[[DD-]HH:]MM:SS[.ff]`.

    macOS prints hundredths and leaves the hours off until it needs them;
    procps prints whole seconds and a `DD-` in front once a process has been
    up a day. Read whatever arrives rather than one platform's shape.
    """
    days, _, rest = said.rpartition("-")
    seconds = 0.0
    for part in rest.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds + float(days or 0) * 86400


def _read_cpu(pid):
    """The CPU seconds this process has used since it started, or None if gone."""
    answer = subprocess.run(["ps", "-o", "time=", "-p", str(pid)],
                            capture_output=True, text=True, check=False)
    said = answer.stdout.strip()
    return _cpu_seconds(said) if said else None


def _sample_cpu(pid, stop, windows, seconds=1.0):
    """Utilisation over each window, from the difference of two TIME readings.

    Not `ps -o %cpu`, which is the obvious one call instead of two and is the
    wrong instrument. macOS documents it as "a decaying average over up to a
    minute of previous (real) time", so consecutive one-second readings are the
    same smoothed number and their maximum cannot see a one-second stall, which
    is the only reason a peak is printed at all. Linux computes the same field
    over the whole lifetime, which converges on the mean this report already
    takes from `getrusage` and prints better.

    A window of a second: `ps` gives hundredths on this platform, so the
    reading steps by 0.01 of a core, which is finer than the two decimals it is
    printed to. On a platform whose `ps` prints whole seconds the step is a
    whole core and the figure is worth nothing - check before trusting it
    somewhere new.
    """
    before, since = _read_cpu(pid), time.monotonic()
    while not stop.wait(seconds):
        after, now = _read_cpu(pid), time.monotonic()
        window = now - since
        # A terminated process reports 0:00.00 rather than nothing, which would
        # otherwise land as a large negative window at the end of the run.
        if before is not None and after is not None and after >= before and window > 0:
            windows.append((after - before) / window)
        before, since = after, now


def _at(values, fraction):
    """The value a given fraction of the way up a sample. Empty is None."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def _row_counts(target):
    """How many rows are in each of the arena's tables right now."""
    counts = {}
    with psycopg.connect(target) as reader:
        for table in db.TABLES:
            counted = reader.execute(
                sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))).fetchone()
            counts[table] = counted[0]
    return counts


def _a_match_at(hz, seconds):
    """The fixture's match, re-timed to the rate a real host reports at.

    The only match log in the repo is fifteen frames over three minutes, nine
    of them state: 0.05 Hz, which fifty rooms replaying it would make 2.5 state
    frames a second across the whole venue against the 500 this is meant to
    measure. So the rate is generated and the payloads are not - they cycle out
    of the fixture, real frames from a real match rather than a stub. The six
    recorded events keep their real `t`.

    That is a floor on per-frame cost and not a model of one. The fixture's
    state frames are a score and a clock; the frame the pitch actually sends
    carries a ball and ten players as well, several times the bytes, and
    serialising to a room's subscribers is where the arena's per-frame work
    goes. The report prints both sizes so the difference is not left implicit.
    """
    recorded = fake_host.parse_log(FIXTURE)
    payloads = [frame["payload"] for frame in recorded if frame["type"] == "state"]
    events = [frame for frame in recorded if frame["type"] == "event"]
    states = [{"t": tick / hz, "type": "state", "payload": payloads[tick % len(payloads)]}
              for tick in range(int(hz * seconds))]
    # An event before a state frame sharing its `t`, which is the order the
    # fixture records a goal and the score it produced in.
    return sorted(states + events, key=lambda frame: (frame["t"], frame["type"] == "state"))


async def _asked(caller, refusals, method, path, headers=None, **body):
    """One HTTP call, with a 429 recorded rather than retried or raised past."""
    answer = await caller.request(method, path, headers=headers, **body)
    if answer.status_code == 429:
        refusals.append(f"{method} {path} -> 429 {answer.text}")
        raise _Refused(path)
    answer.raise_for_status()
    return answer


async def _a_phone(caller, refusals, name, email):
    """Join as one manager. Returns the Cookie header their phone would send."""
    answer = await _asked(caller, refusals, "POST", "/api/players",
                          json={"display_name": name, "email": email})
    return {"Cookie": f"{COOKIE}={answer.cookies[COOKIE]}"}


async def _a_full_room(caller, refusals, index):
    """Two managers, a room, both dugouts ready, kick-off. Returns code and token."""
    stance = rooms.PHILOSOPHIES[index % len(rooms.PHILOSOPHIES)]
    seats = {}
    for team in rooms.TEAMS:
        seats[team] = await _a_phone(caller, refusals, f"{team.capitalize()} {index}",
                                     f"{team}{index}@rehearsal.example.com")
    opened = (await _asked(caller, refusals, "POST", "/api/rooms",
                           json={"mode": "versus"})).json()
    code = opened["code"]
    for team, phone in seats.items():
        await _asked(caller, refusals, "POST", f"/api/rooms/{code}/seats/{team}",
                     headers=phone, json={"philosophy": stance})
        await _asked(caller, refusals, "POST", f"/api/rooms/{code}/seats/{team}/ready",
                     headers=phone, json={"ready": True})
    await _asked(caller, refusals, "POST", f"/api/rooms/{code}/start",
                 headers=seats["blue"])
    return code, opened["host_token"]


async def _drain(wire):
    """Read and throw away, so nothing this rehearsal opens applies backpressure.

    A host socket is subscribed to its own room like any other listener, and
    `fake_host.run` never reads after the opening snapshot. Fifty of those
    would fill fifty send buffers over three minutes and manufacture drops a
    browser running physics - which does read - would never produce.
    """
    while True:
        await wire.recv()


async def _a_host(url, frames, tally):
    """One screen running one match, reporting at 10 Hz and reading its own feed."""
    async with websockets.connect(url) as wire:
        await wire.recv()                       # the opening room snapshot
        reader = asyncio.create_task(_drain(wire))

        async def send(message):
            state = message["type"] == "host.state"
            if state:
                # A copy rather than a mutation: `fake_host.to_message` hands
                # back the frame's own payload object, and fifty rooms share
                # one list of frames.
                now = time.monotonic()
                message = {**message, "payload": {**message["payload"], "sent": now}}
            text = json.dumps(message)
            if state:
                tally["sent"] += 1
                # What is actually on the wire, so the report can say what this
                # rehearsal is carrying rather than leave it to be assumed.
                tally["bytes"] = max(tally["bytes"], len(text))
                tally["first"] = min(tally["first"], now)
                tally["last"] = now
            await wire.send(text)

        try:
            await fake_host.replay(frames, send)
        finally:
            reader.cancel()


async def _a_viewer(url, latencies, tally, watching):
    """A phone watching one match. Leaves when the whistle goes."""
    async with websockets.connect(url) as wire:
        await wire.recv()                       # the opening room snapshot
        watching.append(url)
        while True:
            message = json.loads(await wire.recv())
            if message.get("type") == "state" and "sent" in message:
                latencies.append(time.monotonic() - message["sent"])
                tally["room"] += 1
            elif message.get("kind") == "full_time":
                return


async def _the_wall(url, latencies, tally, watching):
    """The big screen's filmstrip. Every live room, thinned to WALL_HZ a room."""
    async with websockets.connect(url) as wire:
        await wire.recv()                       # the opening list of live rooms
        watching.append(url)
        while True:
            message = json.loads(await wire.recv())
            if message.get("type") == "wall.state" and "sent" in message:
                latencies.append(time.monotonic() - message["sent"])
                tally["wall"] += 1


async def _everybody_watching(watching, expected, patience=120):
    """Hold until every screen has its opening snapshot, or say who is missing.

    Kicking off before they are all attached would count a frame nobody could
    have received yet as a frame that went missing.
    """
    deadline = time.monotonic() + patience
    while len(watching) < expected and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    if len(watching) < expected:
        raise RuntimeError(f"only {len(watching)} of {expected} screens ever connected")


async def _watch_our_own_loop(lags):
    """The same monitor as O6's, pointed at this process instead of the arena.

    Latency is timed from this end: a viewer stamps the moment it read the
    frame, and the driver holds a hundred and fifty sockets on one loop. If
    this number is the size of the latency then the latency is the driver
    queueing behind itself, not the arena being slow to answer, and the
    rehearsal would otherwise report the instrument as the venue.
    """
    while True:
        before = time.perf_counter()
        await asyncio.sleep(TICK)
        lags.append(time.perf_counter() - before - TICK)


# The global timeout exists to catch a socket test whose frame never arrives,
# and thirty seconds is right for every other test in the suite. This is the
# one place where minutes are the point rather than the symptom.
@pytest.mark.timeout(0)
async def test_fifty_rooms_at_ten_hertz(venue):
    frames = _a_match_at(HZ, MATCH_SECONDS)
    state_frames = sum(1 for frame in frames if frame["type"] == "state")
    before = _row_counts(venue.database) if venue.database else None

    refusals, opened = [], []
    started = time.monotonic()
    async with httpx.AsyncClient(base_url=venue.base, timeout=30.0) as caller:
        for room in await asyncio.gather(
                *(_a_full_room(caller, refusals, index) for index in range(ROOMS)),
                return_exceptions=True):
            if isinstance(room, tuple):
                opened.append(room)
            elif not isinstance(room, _Refused):
                raise room
    seated = time.monotonic() - started

    latencies, wall_latencies, watching = [], [], []
    tally = {"sent": 0, "room": 0, "wall": 0, "bytes": 0,
             "first": float("inf"), "last": 0.0}
    watchers = [asyncio.create_task(
        _the_wall(f"{venue.wire}/ws/wall", wall_latencies, tally, watching))]
    hosts = []
    for code, token in opened:
        for _ in range(VIEWERS_PER_ROOM):
            watchers.append(asyncio.create_task(
                _a_viewer(f"{venue.wire}/ws/rooms/{code}", latencies, tally, watching)))
        hosts.append(_a_host(f"{venue.wire}/ws/rooms/{code}?client_id={token}", frames, tally))
    await _everybody_watching(watching, len(watchers))

    # The hundred and one listening sockets are open and nothing is being sent
    # down them; the fifty host sockets connect at kick-off, a frame period
    # apart, which is the stagger below. What the loop monitor reads here is
    # the floor: the overshoot of a sleep on an idle machine, which is the only
    # thing the figure under load can be read against. Taken after the
    # handshakes rather than at boot, where the schema check and the workshop
    # room would be measured instead of the arena.
    settled = time.monotonic()
    await asyncio.sleep(QUIET_SECONDS)

    windows, stop = [], threading.Event()
    sampler = None
    if venue.process:
        sampler = threading.Thread(target=_sample_cpu,
                                   args=(venue.process.pid, stop, windows), daemon=True)
        sampler.start()
    ours = []
    watching_ourselves = asyncio.create_task(_watch_our_own_loop(ours))

    # Started a frame period apart across the venue rather than together. Fifty
    # browsers do not share a clock, and fifty frames arriving in the same
    # millisecond would make the loop's lag a property of this driver.
    async def staggered(index, host):
        await asyncio.sleep(index * (1.0 / HZ) / max(len(hosts), 1))
        await host

    kicked_off = time.monotonic()
    await asyncio.wait_for(
        asyncio.gather(*(staggered(index, host) for index, host in enumerate(hosts))),
        timeout=PATIENCE_SECONDS)
    # First frame to last, rather than the whole phase: the phase also contains
    # fifty websocket handshakes, and a rate is what the venue sustained once
    # they were all up.
    played = tally["last"] - tally["first"]
    elapsed = time.monotonic() - kicked_off

    watching_ourselves.cancel()
    await asyncio.gather(watching_ourselves, return_exceptions=True)
    stop.set()
    if sampler:
        sampler.join(timeout=5)
    # The whistle is the last frame sent, so the viewers are a round trip
    # behind it. Give them that, then take the rest down.
    await asyncio.wait(watchers, timeout=10)
    for watcher in watchers:
        watcher.cancel()
    await asyncio.gather(*watchers, return_exceptions=True)

    statuses = {}
    async with httpx.AsyncClient(base_url=venue.base, timeout=30.0) as caller:
        for code, _ in opened:
            status = (await caller.get(f"/api/rooms/{code}")).json()["status"]
            statuses[status] = statuses.get(status, 0) + 1

    after = _row_counts(venue.database) if venue.database else None

    cpu = None
    if venue.process:
        spent_before = _child_cpu()
        venue.process.terminate()
        venue.process.wait(timeout=60)
        # RUSAGE_CHILDREN only counts a child that has been reaped, so the
        # arena's whole life lands between these two readings and nothing else
        # does. Divided by that life, this is mean cores.
        cpu = {"total": _child_cpu() - spent_before,
               "lifetime": time.monotonic() - venue.born}

    instruments = {}
    if venue.readings and venue.readings.exists():
        instruments = json.loads(venue.readings.read_text())

    _report(venue, opened, refusals, seated,
            (settled, kicked_off, played, elapsed), state_frames,
            latencies, wall_latencies, ours, tally, cpu, windows, instruments,
            statuses, before, after)

    # The rehearsal asserts that it rehearsed, and nothing about how fast. A
    # run that opened no rooms or heard no frames would otherwise print zeros
    # and pass, which is worse than not running it at all.
    assert len(opened) == ROOMS, f"only {len(opened)} of {ROOMS} rooms opened"
    assert tally["sent"] == state_frames * ROOMS
    assert latencies, "no viewer ever received a state frame"


def _report(venue, opened, refusals, seated, match, state_frames,
            latencies, wall_latencies, ours, tally, cpu, windows, instruments,
            statuses, before, after):
    """Print the numbers. This is the whole output of the rehearsal."""
    settled, kicked_off, played, elapsed = match
    asked = len(opened) * HZ
    expected = state_frames * len(opened) * VIEWERS_PER_ROOM
    # Split where the load starts, because a sleep of TICK overshoots by a
    # little on an arena doing nothing at all, and that floor is not the arena
    # blocking on a query. The difference between the two is.
    quiet = [lag for when, lag in instruments.get("lags") or []
             if settled <= when < kicked_off]
    lags = [lag for when, lag in instruments.get("lags") or []
            if kicked_off <= when <= kicked_off + elapsed]
    dropped = instruments.get("dropped") or {}
    wall_dropped = dropped.get("wall", 0)
    room_dropped = sum(count for topic, count in dropped.items() if topic != "wall")

    say = print
    say("")
    say("=== fifty rooms, before fifty people ==================================")
    say(f"target              {venue.base}"
        f"{'' if venue.process else '  (deployed; ARENA_LOAD_URL)'}")
    say(f"shape               {len(opened)} versus rooms, {2 * len(opened)} managers, "
        f"{VIEWERS_PER_ROOM} viewers a room, 1 wall screen")
    say(f"opening the venue   {seated:.1f}s for {len(opened)} rooms"
        f"{f', {len(refusals)} refusals' if refusals else ', no refusals'}")
    for refusal in refusals:
        say(f"                    {refusal}")
    say("")
    say("--- the load actually applied ---")
    say(f"frames asked for    {asked:.0f} state frames a second "
        f"({len(opened)} rooms x {HZ} Hz for {MATCH_SECONDS}s)")
    say(f"frames achieved     {tally['sent'] / played:.1f} a second "
        f"({tally['sent']} sent over {played:.1f}s, {elapsed:.1f}s including the handshakes)")
    say(f"                    {100 * (tally['sent'] / played) / asked:.1f}% of the rate asked for")
    say(f"viewer deliveries   {tally['room']} of {expected} "
        f"({100 * tally['room'] / expected:.2f}%)")
    say(f"wall deliveries     {tally['wall']} (thinned to ARENA_WALL_HZ a room, by design)")
    say(f"frame payload       {tally['bytes']} bytes on the wire, against "
        f"{len(json.dumps(DOCUMENTED_FRAME))} for the frame the pitch")
    say("                    sends: the fixture's states are a score and a clock, with no "
        "ball and")
    say("                    no positions, so every number below is a floor on per-frame cost")
    say("")
    say("--- the event loop, which is where the parked risk was ---")
    if lags:
        say(f"under load          p50 {_at(lags, 0.50) * 1000:.1f}ms, "
            f"p99 {_at(lags, 0.99) * 1000:.1f}ms, max {max(lags) * 1000:.1f}ms "
            f"({len(lags)} readings of a {TICK * 1000:.0f}ms sleep)")
        if quiet:
            say(f"idle floor          p50 {_at(quiet, 0.50) * 1000:.1f}ms, "
                f"p99 {_at(quiet, 0.99) * 1000:.1f}ms, max {max(quiet) * 1000:.1f}ms "
                f"({len(quiet)} readings over {QUIET_SECONDS}s)")
            say(f"                    {(VIEWERS_PER_ROOM * len(opened)) + 1} listening sockets "
                f"open and nothing sent; the {len(opened)} host sockets connect at kick-off")
            say(f"                    the window is {QUIET_SECONDS // app.SWEEP_SECONDS} sweep "
                f"periods, so the sweep is in the percentile rather than being it")
    else:
        say("lag                 not measured: nothing instruments a deployed arena")
    say("")
    say("--- latency, measured on a room socket where every frame goes ---")
    if latencies:
        say(f"room p50 / p99      {_at(latencies, 0.50) * 1000:.1f}ms / "
            f"{_at(latencies, 0.99) * 1000:.1f}ms")
        say(f"room max            {max(latencies) * 1000:.1f}ms over {len(latencies)} frames")
    if ours:
        say(f"this driver's loop  p50 {_at(ours, 0.50) * 1000:.1f}ms, "
            f"p99 {_at(ours, 0.99) * 1000:.1f}ms, max {max(ours) * 1000:.1f}ms "
            f"- the latency above is timed here and contains it")
        say(f"                    it covers the receive side; the stamp goes on before "
            f"json.dumps and the")
        say(f"                    socket write, so the send path is in there too - "
            f"microseconds at {tally['bytes']}")
        say("                    bytes a frame, which leans on the payload caveat above "
            "rather than")
        say("                    being independent of it")
    if wall_latencies:
        say(f"wall p99 / max      {_at(wall_latencies, 0.99) * 1000:.1f}ms / "
            f"{max(wall_latencies) * 1000:.1f}ms  (context: the wall drops frames on purpose)")
    say("")
    say("--- cpu, mean first: an instance is sized by what it sustains ---")
    if cpu:
        say(f"mean cores          {cpu['total'] / cpu['lifetime']:.3f} of one core "
            f"({cpu['total']:.1f}s of cpu over the arena's {cpu['lifetime']:.1f}s life)")
        say(f"                    the life is the match plus the setup and the idle window "
            f"in front of it;")
        say(f"                    the match itself is {played:.0f}s of it")
        if windows:
            say(f"under load only     {sum(windows) / len(windows):.3f} of one core, "
                f"the mean of the windows below, which span the match")
            say(f"busiest second      {max(windows):.2f} of one core, "
                f"median {_at(windows, 0.50):.2f}, {len(windows)} one-second windows")
            say("                    each window is the difference of two ps TIME readings, "
                "not ps %cpu,")
            say("                    which is a decaying average and cannot see a "
                "one-second spike")
    else:
        say("cpu                 not measured: the arena is not this test's child")
    say("")
    say("--- what the bus threw away (Subscription.dropped) ---")
    if instruments:
        say(f"room topics         {room_dropped} dropped across "
            f"{len([topic for topic in dropped if topic != 'wall'])} topics")
        say(f"wall topic          {wall_dropped} dropped")
        worst = max(((count, topic) for topic, count in dropped.items()), default=(0, "-"))
        say(f"worst single topic  {worst[1]}: {worst[0]}")
    else:
        say("dropped             not measured: nothing instruments a deployed arena")
    say("")
    say("--- rows, before and after ---")
    if before and after:
        say(f"{'table':<10}{'before':>10}{'after':>10}{'grew by':>10}")
        for table in db.TABLES:
            say(f"{table:<10}{before[table]:>10}{after[table]:>10}"
                f"{after[table] - before[table]:>10}")
    else:
        say("rows                not counted: the deployed database is not ours to read")
    say(f"room statuses       {statuses}")
    say("=======================================================================")
