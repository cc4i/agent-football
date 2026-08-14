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

"""Which matches this instance is running, and how the page is told.

The division is strict, and it is what makes both halves testable: the page
knows about football and nothing about the arena, this knows about the arena
and nothing about football. Nothing here ever parses a frame - frames go from
the page straight down each match's own room socket, exactly as they went from
a tab.
"""

import logging

logger = logging.getLogger(__name__)

# Driven with an argument rather than an interpolated string, because a physics
# token spliced into JavaScript ends up in a stack trace, in a CDP log, and in
# anything else that records what was evaluated.
HOST = "(a) => window.grounds.host(a.code, a.token, a.seed)"
DROP = "(a) => window.grounds.drop(a.code)"
RUNNING = "() => window.grounds.running()"


class Supervisor:
    def __init__(self, page, capacity):
        self.page = page
        self.capacity = capacity
        self.running = set()

    def hello(self):
        """What this instance tells the arena it can take."""
        return {"type": "grounds.here", "capacity": self.capacity}

    def page_reloaded(self):
        """The page lost every game it had, so this instance runs nothing.

        Not re-hosted. A match already twenty minutes old cannot be resumed by
        a fresh simulation with the clock at zero, and the arena's sweep is
        already the right answer to a match nobody is playing.
        """
        if self.running:
            logger.warning("the page reloaded; %s matches went with it",
                           len(self.running))
        self.running.clear()

    async def apply(self, message):
        """Act on one message from the arena. Anything else is ignored."""
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        code = message.get("code")
        if not code:
            return
        if kind == "host":
            await self._host(code, message)
        elif kind == "drop":
            await self._drop(code)

    async def reconcile(self):
        """Match the books to what the page is actually playing.

        A match that reached full time is let go by the page itself: the arena
        closes the room on that whistle and answers nothing, because it knows
        the page knows. Nothing else would ever take it off this list, and an
        instance whose list only grows stops taking matches after `capacity` of
        them - a farm that quietly fills up over an evening and refuses every
        kick-off after that.

        Only ever removes. The page is the authority on what is playing, not on
        what was assigned, and a page that answered oddly must not be able to
        talk this instance into holding slots the arena never gave it.
        """
        playing = await self.page.evaluate(RUNNING)
        if not isinstance(playing, list):
            # A reload lands here before `page_reloaded` does, and one odd
            # answer must not read as "the venue stopped playing football".
            return
        finished = self.running - set(playing)
        if not finished:
            return
        self.running -= finished
        logger.info("%s finished: %s (%s of %s)", len(finished),
                    ", ".join(sorted(finished)), len(self.running), self.capacity)

    async def _host(self, code, message):
        if code in self.running:
            return
        if len(self.running) >= self.capacity:
            logger.warning("refused %s: at capacity %s", code, self.capacity)
            return
        try:
            await self.page.evaluate(HOST, {"code": code,
                                            "token": message.get("token"),
                                            "seed": message.get("seed")})
        except Exception:
            # Counted only once the page has it. A slot booked against a match
            # that never started is a slot this instance holds all evening, and
            # the arena has already assigned the room either way: the sweep is
            # what notices, thirty seconds from now.
            logger.exception("the page would not take %s", code)
            return
        self.running.add(code)
        logger.info("running %s (%s of %s)", code, len(self.running), self.capacity)

    async def _drop(self, code):
        if code not in self.running:
            return
        try:
            await self.page.evaluate(DROP, {"code": code})
        except Exception:
            # Forgotten regardless. The arena has given up on this room, so
            # holding a slot for it because the page did not answer would be
            # keeping a seat warm for nobody.
            logger.exception("the page would not drop %s", code)
        self.running.discard(code)
        logger.info("dropped %s (%s of %s)", code, len(self.running), self.capacity)
