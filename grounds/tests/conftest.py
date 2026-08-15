# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The service's modules are top-level, so the suite has to see them.

Everything below the first fixture belongs to the capacity rehearsal, which is
skipped unless GROUNDS_CAPACITY_CHECK=1 and which is the only thing here that
launches a browser or expects an arena. Its imports are inside the fixtures so
that the fast suite -- the one that runs on every commit -- still costs under a
second and still needs neither.
"""

import asyncio
import http.cookiejar
import itertools
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# The four stances a manager may pick, cycled so the ramp is not fifty matches
# of one shape. Duplicated from arena/rooms.py rather than imported: the arena
# is a separate project with its own virtualenv, and this rehearsal is allowed
# to know the venue's HTTP API without being able to import its code.
PHILOSOPHIES = ("high press", "tiki-taka", "counter", "low block")

# How long the arena is given to answer one call. It is a local process in the
# usual run and a Cloud Run instance in the other; neither should ever be near
# this, and a rehearsal that hangs tells nobody anything.
PATIENCE_SECONDS = 20

# How far the ramp is willing to look. Not a promise about anything: it is the
# capacity the rehearsal's own supervisor offers, so that the arena never
# refuses a kick-off the ramp meant to make.
CEILING_TO_LOOK_FOR = 64

# How long a room may go without a frame before it stops counting as one being
# played. Frames come at ten a second, so this is twenty missed in a row: past
# anything a network hiccup explains, and far short of the thirty seconds the
# arena's own sweep waits before calling a match abandoned.
SILENT_SECONDS = 2.0


class NoPitchFree(Exception):
    """Every grounds the arena knows about is full.

    A fact about the deployment rather than about the machine: it means
    GROUNDS_CAPACITY, not the football, is what stopped the ramp.
    """


def _opener():
    """A caller with a cookie jar of its own, which is to say one phone."""
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def _post(caller, url, body=None, headers=None):
    """One POST, as JSON, answered as JSON. Raises on anything but 2xx."""
    request = urllib.request.Request(
        url, data=json.dumps(body if body is not None else {}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})})
    with caller.open(request, timeout=PATIENCE_SECONDS) as answer:
        raw = answer.read()
    return json.loads(raw) if raw else {}


def _nothing_seen_yet():
    """One room's window, before anything has arrived in it."""
    return {"frames": 0, "clock": None, "first_at": None, "last_at": None,
            "first_tick": None, "last_tick": None}


def _per_second(amount, start, end):
    """`amount` over the time from `start` to `end`, or None if unmeasurable."""
    if start is None or end is None or end <= start:
        return None
    return amount / (end - start)


class Venue:
    """A running arena, the rooms this rehearsal opens in it, and their frames.

    Frames are counted off one listener socket per room rather than off the
    wall. The wall is the cheaper place to listen -- one socket carrying every
    room -- but it thins each room's tile to `ARENA_WALL_HZ`, which is 2 by
    design, so a wall reading cannot tell a match at 10 Hz from a match at 3.
    A room socket carries every frame the pitch sends, which is the thing being
    measured, and it needs no flag set on the arena to say so.

    Two numbers come back per room, and the second is the one to trust:

    * **frames a second**, which is what a phone watching that match sees.
    * **clock seconds a second**, the match's own time against real time. A
      frame can go missing between the page and here; the simulation's clock
      cannot, so this is what separates a slow network from slow football, and
      slow football is the failure GROUNDS_CAPACITY exists to prevent.

    Both are measured between observations rather than across the window that
    asked for them. The clock is a whole number of seconds counting down, so a
    ten-second window catches nine ticks or ten depending only on where it
    happened to start, and a healthy match reads 0.9 as often as 1.0 -- a gate
    on that number would condemn the page for the window's rounding. Measured
    from the first tick seen to the last, there is no part-tick at either end
    and the reading is the real rate. The frame rate is taken the same way, and
    for the same reason on a smaller scale.
    """

    def __init__(self, base, token):
        self.base = base.rstrip("/")
        self.token = token
        self.seen = {}
        self.watchers = {}
        # Room -> when a frame last arrived from it. Kept apart from `seen`,
        # which is emptied every window: this is what is being played, not what
        # has been measured lately.
        self.heard_from = {}
        self._numbered = itertools.count()

    @property
    def wire(self):
        # http -> ws and https -> wss, in one substitution rather than two.
        return "ws" + self.base[len("http"):]

    async def open_and_kick_off(self):
        """A manager, a solo room, a dugout ready, kick-off. Returns the code.

        Solo rather than versus because it halves the phones: the pitch plays
        the same ten players either way -- nobody sits in a solo room's red
        dugout, and its five still run -- so this is the same football for one
        POST /api/players instead of two, against a bucket of 120.
        """
        index = next(self._numbered)
        code = await asyncio.to_thread(self._kick_off, index)
        self.seen[code] = _nothing_seen_yet()
        self.watchers[code] = asyncio.create_task(self._watch(code))
        return code

    def _kick_off(self, index):
        caller = _opener()
        _post(caller, f"{self.base}/api/players",
              {"display_name": f"Rehearsal {index}",
               "email": f"rehearsal{index}@grounds.example.com"})
        code = _post(caller, f"{self.base}/api/rooms", {"mode": "solo"})["code"]
        _post(caller, f"{self.base}/api/rooms/{code}/seats/blue",
              {"philosophy": PHILOSOPHIES[index % len(PHILOSOPHIES)]})
        _post(caller, f"{self.base}/api/rooms/{code}/seats/blue/ready", {"ready": True})
        try:
            _post(caller, f"{self.base}/api/rooms/{code}/start")
        except urllib.error.HTTPError as refusal:
            if refusal.code == 503:
                raise NoPitchFree(refusal.read().decode(errors="replace")) from None
            raise
        return code

    async def _watch(self, code):
        """Count one room's frames, for as long as that room is being played."""
        import websockets

        try:
            async with websockets.connect(f"{self.wire}/ws/rooms/{code}") as socket:
                await socket.recv()             # the opening room snapshot
                while True:
                    message = json.loads(await socket.recv())
                    if message.get("type") != "state":
                        continue
                    self.heard_from[code] = time.monotonic()
                    self._note(self.seen[code], message.get("clock"))
        except asyncio.CancelledError:
            raise
        except Exception:
            # A socket the arena closed because the match finished, or one that
            # dropped. Either way this room stops reporting and the ramp's own
            # top-up is what notices, which is the same thing the venue does.
            return
        finally:
            self.heard_from.pop(code, None)

    @staticmethod
    def _note(watched, clock):
        """One frame, and the tick it carried if the clock has moved on."""
        at = time.monotonic()
        watched["frames"] += 1
        if watched["first_at"] is None:
            watched["first_at"] = at
        watched["last_at"] = at
        if clock is not None and clock != watched["clock"]:
            if watched["first_tick"] is None:
                watched["first_tick"] = (clock, at)
            watched["last_tick"] = (clock, at)
        watched["clock"] = clock

    def mark(self):
        """Forget what has been seen, so the next window stands on its own.

        Everything but the clock's last value, which is what the first frame of
        the next window is compared against to know whether it carried a tick.
        """
        for watched in self.seen.values():
            watched.update(_nothing_seen_yet(), clock=watched["clock"])

    def playing_now(self):
        """The rooms a frame has arrived from in the last `SILENT_SECONDS`.

        Recently, rather than ever, and that distinction is the whole of this
        method. A room's socket stays open after full time -- the phone reads
        its result off the same socket -- so a finished match goes on looking
        connected while sending nothing at all. Counting those, a ramp that
        believed it had 33 matches on was measuring a page playing 17: it kept
        being told the target was met and stopped opening rooms, and every
        number it printed past the eighteenth carried the wrong label.
        """
        cutoff = time.monotonic() - SILENT_SECONDS
        return {code for code, at in self.heard_from.items() if at >= cutoff}

    async def settle(self, seconds):
        """Sit still for a window, having forgotten the one before it."""
        self.mark()
        await asyncio.sleep(seconds)

    def rates(self):
        """Per room: frames a second, and match seconds per real second.

        A room that sent fewer than two frames in the window is left out
        entirely rather than reported as slow: one frame gives nothing to
        measure between, and a match that kicked off as the window closed is
        not evidence about anything.
        """
        report = {}
        for code, watched in self.seen.items():
            hz = _per_second(watched["frames"] - 1,
                             watched["first_at"], watched["last_at"])
            if hz is None:
                continue
            first, last = watched["first_tick"], watched["last_tick"]
            report[code] = {
                "hz": hz,
                # The clock counts down, so the older reading is the larger.
                "clock": _per_second(first[0] - last[0], first[1], last[1])
                         if first and last else None,
            }
        return report

    async def close(self):
        for watcher in self.watchers.values():
            watcher.cancel()
        await asyncio.gather(*self.watchers.values(), return_exceptions=True)


class Elsewhere:
    """A grounds this rehearsal did not launch: a deployed one, measured.

    The local farm knows what it is playing because it asks the page. This one
    is inferred from the only evidence that crosses the network: a room sending
    frames right now is a room somebody is simulating right now. That is a
    stricter reading than the arena's own books, which record what was assigned
    rather than what is actually being played.
    """

    def __init__(self, venue):
        self.venue = venue

    @property
    def running(self):
        return self.venue.playing_now()

    async def reconcile(self):
        """Nothing to do: `running` is read off the frames every time."""


def _grounds_is_elsewhere():
    """Whether the football is played by a deployed grounds rather than here."""
    return os.environ.get("GROUNDS_ALREADY_RUNNING") == "1"


@pytest.fixture
def venue():
    """Where this rehearsal plays. An arena somebody already has running.

    Not one of its own, the way arena/tests/test_load_rehearsal.py starts one:
    that arena is a Python project with a Postgres behind it, and standing it
    up from here would put psycopg and a database into a service whose whole
    dependency list is a browser and two sockets. Point this at a local arena
    for a laptop number and at a deployed one for a Cloud Run number.
    """
    token = os.environ.get("ARENA_SERVICE_TOKEN", "")
    if not token:
        pytest.skip("set ARENA_SERVICE_TOKEN to the arena's, so a grounds may connect")
    return Venue(os.environ.get("ARENA_URL", "http://localhost:8003"), token)


@pytest.fixture
async def grounds_page(venue):
    """A real Chromium on the built host page, launched as the service does.

    The same flags as grounds/main.py, because a measurement taken with the GPU
    on or the audio unmuted is a measurement of a browser this service never
    runs. `--disable-dev-shm-usage` matters least here and is kept anyway: the
    number is meant to describe the container.

    None when the grounds is a deployed one: there is no page here to drive,
    and launching one anyway would put a second farm on the arena's books,
    competing for the very kick-offs the ramp is trying to place on the first.
    """
    if _grounds_is_elsewhere():
        yield None
        return

    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=[
            "--disable-dev-shm-usage", "--no-sandbox",
            "--disable-gpu", "--mute-audio",
        ])
        page = await browser.new_page()
        page.on("pageerror", lambda problem: print(f"page: {problem}"))
        await page.goto(f"{venue.base}/pitch/host.html", wait_until="load")
        await page.wait_for_function("() => !!window.grounds")
        try:
            yield page
        finally:
            await browser.close()


@pytest.fixture
async def farm(venue, grounds_page):
    """Whatever is playing the football, and a way to ask what it holds.

    Two shapes, because the number this rehearsal produces has to describe a
    Cloud Run container and a laptop is not one:

    * By default the rehearsal is itself the farm. It launches the browser,
      holds the control socket and runs the service's own supervisor, which
      measures the machine the ramp runs on. The arena refuses kick-off when no
      grounds is free, so a rehearsal that only drove a page over CDP could not
      start a match at all -- hence a real socket rather than a pretend one.
      The capacity is turned up past whatever the deployment currently
      promises: finding where that promise belongs is the point.
    * With GROUNDS_ALREADY_RUNNING=1 it measures somebody else's, a deployed
      grounds already connected to the arena this is pointed at. Nothing is
      launched, the frames are the only evidence, and the reading describes
      production rather than a laptop.
    """
    if grounds_page is None:
        try:
            yield Elsewhere(venue)
        finally:
            await venue.close()
        return

    import websockets

    from supervisor import Supervisor

    supervisor = Supervisor(grounds_page, CEILING_TO_LOOK_FOR)

    async def talking():
        async with websockets.connect(
                f"{venue.wire}/ws/grounds",
                additional_headers={"X-Arena-Service": venue.token}) as socket:
            await socket.send(json.dumps(supervisor.hello()))
            async for raw in socket:
                await supervisor.apply(json.loads(raw))

    held = asyncio.create_task(talking())
    # The arena must have this grounds on its books before the first kick-off,
    # or the ramp's first match is refused for a reason that is this fixture's
    # fault rather than the venue's.
    await asyncio.sleep(1.0)
    try:
        yield supervisor
    finally:
        held.cancel()
        await asyncio.gather(held, return_exceptions=True)
        await venue.close()
