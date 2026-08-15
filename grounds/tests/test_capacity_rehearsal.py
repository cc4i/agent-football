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

"""How many matches one Chromium actually holds at ten frames a second.

Skipped unless GROUNDS_CAPACITY_CHECK=1, the way the arena's own load
rehearsal is skipped unless ARENA_LOAD=1. It takes minutes, it needs an arena
and a browser, and the numbers it prints are the point rather than the pass.

    cd grounds
    ARENA_URL=http://localhost:8003 ARENA_SERVICE_TOKEN=dev-token \
      GROUNDS_CAPACITY_CHECK=1 uv run pytest tests/test_capacity_rehearsal.py -s

The `-s` is not optional: the report goes to stdout and pytest swallows the
stdout of a test that passed, which this one intends to.

This is the load-bearing unknown of the whole design. A match's physics is one
Phaser game stepping Arcade physics for ten players and a ball, with nothing
drawn and nothing heard - cheap, but not free, and every match in the page
steps on the same thread. GROUNDS_CAPACITY and the CPU request in
deploy/grounds.yaml are whatever this printed, and were not written before it
ran.

What it printed, on a laptop: all 64 matches this ramp looks at, the slowest at
9.5 Hz with its clock at 0.99 of real time. The spec's fifty in one page is not
a stretch on that machine. A Cloud Run vCPU is not a laptop core and the page's
football is one thread, so the deployed number comes from the deployed
instance - see GROUNDS_ALREADY_RUNNING below.

What it does, and why each part is the way it is:

* **It tops up rather than only adding.** A match is three minutes. A ramp that
  added one match every ten seconds would have its first match finish around
  the eighteenth step, and would then be measuring a number of matches it was
  no longer running. So each step names a target and opens rooms until the page
  is actually playing that many, which is also what a busy evening looks like.

* **The clock is the verdict, the frame rate is the symptom.** Both are
  printed. Frames can go missing between the page and here; the match's own
  clock cannot, so a page at 0.6 clock seconds per second is football in slow
  motion and no amount of network explains it away. The ramp stops on either.

* **The slowest match decides, not the median.** Nobody watching the one match
  that has gone to 4 Hz is consoled by the other forty being fine.

* **By default it measures the machine it is run on**, which a laptop with
  eight fast cores and nothing else to do is, and a Cloud Run container is
  not. Every match in a page steps on that page's one thread, so the ceiling
  tracks how fast a single core is more than how many there are, and a laptop
  reading is generous by whatever that difference is.

  For the figure that belongs in deploy/grounds.yaml, deploy a grounds and
  measure that one instead:

      ARENA_URL=https://<the arena> ARENA_SERVICE_TOKEN=<its token> \
        GROUNDS_ALREADY_RUNNING=1 GROUNDS_CAPACITY_CHECK=1 \
        uv run pytest tests/test_capacity_rehearsal.py -s

  Nothing is launched here then; the deployed instance plays the football and
  the frames coming back are the whole of the evidence. Frame rates read a
  little low over the internet, which is exactly why the clock is the verdict.
  Deploy with GROUNDS_CAPACITY set well above the ceiling being looked for, or
  the arena runs out of pitches first and the ramp reports that instead - it
  says so when it happens.
"""

import asyncio
import os

import pytest

from tests.conftest import CEILING_TO_LOOK_FOR, NoPitchFree

pytestmark = [
    pytest.mark.skipif(os.environ.get("GROUNDS_CAPACITY_CHECK") != "1",
                       reason="set GROUNDS_CAPACITY_CHECK=1 to measure the capacity"),
    # The suite's blanket 30-second timeout is there to catch a wedged unit
    # test. This one is minutes of football on purpose.
    pytest.mark.timeout(0),
]

# What the pitch reports at, from `publishFrame` in game/frontend/src/game.js.
TARGET_HZ = 10
# A match producing under 8 Hz is not keeping up.
SLIPPED = 0.8
# And a match whose own clock runs at under 0.9 of real time is being played in
# slow motion, whatever its frame rate says. Tighter than the frame gate, both
# because the measurement is exact -- see Venue, which reads the clock between
# ticks rather than across the window -- and because the failure is worse: a
# missing frame is a stutter on one phone, a slow clock is a match that takes
# longer to play than it is supposed to for everyone at once.
CLOCK_SLIPPED = 0.9

# How long each step is left alone before it is read. Long enough for a new
# match's kick-off flurry to be over and for the clock quantisation above to be
# a tenth rather than a half, short enough that a ramp to sixty-four is minutes
# and not an hour.
WINDOW_SECONDS = 10

# The floor the design needs. Below this the single-page farm does not answer
# the problem it was built for, and the spec's fifty matches would need fifty
# tabs again.
FLOOR = 8

# How long one new match is given to travel from kick-off to the page, and how
# often that is looked at. Generous, because near the ceiling the page is busy
# and building a game takes longer than it did on an idle one - and cheap,
# because it is waited on only until the match arrives.
PICKUP_SECONDS = 20
POLL_SECONDS = 0.1


async def test_how_many_matches_fit(venue, farm):
    """Ramp until the football slows, and print where that was."""
    ceiling = 0
    stopped = (f"as far as this ramp looks - raise CEILING_TO_LOOK_FOR past "
               f"{CEILING_TO_LOOK_FOR} to find the real one")
    for target in range(1, CEILING_TO_LOOK_FOR + 1):
        try:
            await _top_up_to(venue, farm, target)
        except NoPitchFree:
            stopped = (f"the venue had no free pitch at {target}, so this "
                       f"deployment's GROUNDS_CAPACITY is what stopped the "
                       f"ramp rather than the football")
            break
        await venue.settle(WINDOW_SECONDS)

        rates = venue.rates()
        if not rates:
            pytest.fail(f"no match reported a frame at {target}; is the arena up?")
        hz = sorted(rate["hz"] for rate in rates.values())
        clocks = sorted(rate["clock"] for rate in rates.values()
                        if rate["clock"] is not None)
        slowest, median = hz[0], hz[len(hz) // 2]
        slowest_clock = clocks[0] if clocks else None
        print(f"{target:3d} matches ({len(rates)} reporting): "
              f"slowest {slowest:5.1f} Hz, median {median:5.1f} Hz, "
              f"slowest clock {_as_rate(slowest_clock)}", flush=True)

        if slowest < TARGET_HZ * SLIPPED:
            stopped = f"frames slipped at {target}: {slowest:.1f} Hz"
            break
        if slowest_clock is not None and slowest_clock < CLOCK_SLIPPED:
            stopped = (f"the football slowed at {target}: "
                       f"{slowest_clock:.2f} clock seconds a second")
            break
        ceiling = target

    print(f"\nceiling: {ceiling} matches at {TARGET_HZ} Hz\n{stopped}", flush=True)
    assert ceiling >= FLOOR, (
        f"{FLOOR} concurrent matches is the floor this design needs, "
        f"and this run held {ceiling}")


async def _top_up_to(venue, farm, target):
    """Open and kick off rooms until the page is playing `target` matches.

    Against the page rather than against a count kept here, because the page is
    the authority on what is being played: matches reach full time on their own
    and let themselves go, and a step that assumed otherwise would report a
    number of matches that had already finished.

    The shortfall is opened all at once rather than one room after another, and
    that is the difference between measuring the page and measuring this loop.
    Holding N matches on means replacing one every 180/N seconds; a serial
    top-up pays five HTTP round trips and a pickup wait per match, and against
    a venue on the other side of the internet that price passes 180/N somewhere
    in the low twenties. An earlier run stopped dead at 23 for exactly that
    reason, with the page still at 9.9 Hz and its clocks at 1.01x -- no strain
    anywhere, simply a ramp that could no longer outrun full time.
    """
    for _ in range(target * 2 + 4):
        await farm.reconcile()
        missing = target - len(farm.running)
        if missing <= 0:
            return
        await asyncio.gather(*(_open_one(venue, farm) for _ in range(missing)))
    raise AssertionError(f"could not get {target} matches running at once; "
                         f"the page is playing {len(farm.running)}")


async def _open_one(venue, farm):
    """One room, kicked off, waited on until the page has it."""
    await _picked_up(farm, await venue.open_and_kick_off())


async def _picked_up(farm, code):
    """Wait for the page to take the match just kicked off, then return.

    Kick-off answers as soon as the arena has picked a grounds; the page has
    not been told yet, let alone built the game. Opening the next room across
    that gap is how a step meaning to run seventeen matches runs eighteen, and
    the step's own label is what gets written into deploy/grounds.yaml.

    Waits on this room by name rather than on the count, because a match
    reaching full time in the same moment would leave the count where it was
    and buy this a whole pointless timeout at every step near the ceiling.

    Does not call `farm.reconcile()` while it waits, and that is not an
    oversight. Reconciling asks the page a question over CDP, which the page
    answers on the same thread it plays football with. An earlier version of
    this polled it ten times a second and measured a ceiling of 26; without the
    polling the same laptop went past 64 without slowing down. The instrument
    was the load. Anything added to this loop has to be paid for by every match
    in the page.

    Gives up quietly rather than failing: the caller's loop is what decides
    whether the target was reached, and one match the page declined is its
    business rather than this helper's.
    """
    for _ in range(int(PICKUP_SECONDS / POLL_SECONDS)):
        if code in farm.running:
            return
        await asyncio.sleep(POLL_SECONDS)


def _as_rate(value):
    """A clock rate, or a dash when no match has been watched long enough."""
    return f"{value:.2f}x" if value is not None else "   - "
