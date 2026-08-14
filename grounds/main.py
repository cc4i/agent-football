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

"""One Chromium, one page, and a socket to the arena.

This process serves nothing anybody uses. The port exists because Cloud Run
insists on something to health-check, and CPU throttling has to be off, because
between health checks a throttled instance would simply stop playing football.

It is a client of the arena rather than a server to it: the arena assigns, this
opens the match in its page, and the page reports on it down its own room socket
exactly as a tab did. Nothing here parses a frame.
"""

import asyncio
import contextlib
import json
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from playwright.async_api import async_playwright

from supervisor import Supervisor

load_dotenv()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("grounds")

ARENA_URL = os.environ.get("ARENA_URL", "http://localhost:8003").rstrip("/")
SERVICE_TOKEN = os.environ.get("ARENA_SERVICE_TOKEN", "")
CAPACITY = int(os.environ.get("GROUNDS_CAPACITY", "12"))
PORT = int(os.environ.get("PORT", "8004"))

# Everything that waits, waits like this: the arena redeploys, the network
# blinks, and an instance that gave up on the first refusal would be a pitch the
# venue lost for the evening.
FIRST_WAIT = 0.5
LONGEST_WAIT = 8.0
# How often the books are checked against the page. Full time is the one ending
# that arrives on no message, so this is the only thing that notices it. Often
# enough that a finished match frees its slot while the evening is still going,
# rare enough to be nothing beside sixty frames a second of football.
TIDY_SECONDS = 5.0


def _loads(raw):
    """Whatever came down the socket, as a message or as nothing."""
    try:
        message = json.loads(raw)
    except ValueError:
        return {}
    return message if isinstance(message, dict) else {}


async def opening(browser, where):
    """Get the page up, however long the arena takes to be there.

    Retried rather than fatal, because in development this process is usually
    started before the arena it points at, and in production a deploy is a
    minute of exactly the same thing.
    """
    wait = FIRST_WAIT
    while True:
        page = await browser.new_page()
        page.on("console", lambda note: logger.info("page: %s", note.text))
        page.on("pageerror", lambda problem: logger.error("page: %s", problem))
        try:
            await page.goto(where, wait_until="load")
            # The bundle is what defines football here, so being served HTML is
            # not the same as being ready to play.
            await page.wait_for_function("() => !!window.grounds")
            return page
        except Exception as problem:
            logger.warning("no pitch at %s yet (%s); back in %ss", where, problem, wait)
            with contextlib.suppress(Exception):
                await page.close()
        await asyncio.sleep(wait)
        wait = min(wait * 2, LONGEST_WAIT)


async def tidying(supervisor, state):
    """Ask the page what it is still playing, for as long as we are up."""
    while True:
        await asyncio.sleep(TIDY_SECONDS)
        try:
            await supervisor.reconcile()
            state["running"] = len(supervisor.running)
        except Exception:
            # A page mid-reload, or a browser on its way out. The next pass is
            # five seconds off and this loop has to outlive both.
            logger.exception("could not reconcile with the page")


async def talking(supervisor, state):
    """Hold the control socket open, and reconnect for as long as we are up.

    Matches do not survive the arena going away - its sweep abandons whatever
    was running here - which is the durability this was designed for and not a
    bug to fix from this end. What must survive is the connection itself.
    """
    address = ARENA_URL.replace("https://", "wss://").replace("http://", "ws://")
    wait = FIRST_WAIT
    while True:
        try:
            async with websockets.connect(
                    f"{address}/ws/grounds",
                    additional_headers={"X-Arena-Service": SERVICE_TOKEN}) as socket:
                wait = FIRST_WAIT
                await socket.send(json.dumps(supervisor.hello()))
                logger.info("connected to the arena at %s, offering %s pitches",
                            ARENA_URL, supervisor.capacity)
                async for raw in socket:
                    await supervisor.apply(_loads(raw))
                    state["running"] = len(supervisor.running)
        except asyncio.CancelledError:
            raise
        except Exception as problem:
            logger.warning("the arena socket dropped (%s); back in %ss", problem, wait)
        await asyncio.sleep(wait)
        wait = min(wait * 2, LONGEST_WAIT)


async def pitches(state):
    """Open the browser, open the page, and keep the two of them company."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=[
            # /dev/shm in a container is 64MB, and a page holding a venue's
            # worth of games wants more. Without this Chromium dies partway
            # through an evening with a renderer crash that says nothing about
            # memory.
            "--disable-dev-shm-usage",
            "--no-sandbox",
            # Nothing is drawn and nothing is heard. Both are real CPU per
            # match, and CPU per match is the whole capacity question.
            "--disable-gpu",
            "--mute-audio",
        ])
        page = await opening(browser, f"{ARENA_URL}/pitch/host.html")
        logger.info("the pitch is open at %s", ARENA_URL)

        supervisor = Supervisor(page, CAPACITY)
        # Wired after the first load, so this instance's own arrival does not
        # read as everything it was running having been lost.
        page.on("load", lambda _: supervisor.page_reloaded())

        # A browser that has gone takes every match with it, and an instance
        # with no browser must stop being offered more. Both halves of that are
        # below: the socket closes, so the arena forgets this grounds, and the
        # health check starts saying no, so the platform replaces it.
        gone = asyncio.Event()
        browser.on("disconnected", lambda _: gone.set())
        page.on("close", lambda _: gone.set())
        page.on("crash", lambda _: gone.set())

        state["open"] = True
        work = [asyncio.create_task(talking(supervisor, state)),
                asyncio.create_task(tidying(supervisor, state))]
        try:
            await gone.wait()
            logger.error("the browser is gone; this instance has no pitches left")
        finally:
            state["open"] = False
            state["running"] = 0
            for task in work:
                task.cancel()
            await asyncio.gather(*work, return_exceptions=True)
            with contextlib.suppress(Exception):
                await browser.close()


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    # What the health check is allowed to know, and all it is allowed to know.
    # A route able to reach the supervisor is a route able to stop a match.
    fastapi_app.state.grounds = {"open": False, "running": 0, "capacity": CAPACITY}
    playing = asyncio.create_task(pitches(fastapi_app.state.grounds))
    yield
    playing.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await playing


app = FastAPI(title="Grounds", lifespan=lifespan)


@app.get("/healthz")
def healthz(request: Request):
    """Up, and how much football is happening. The only request this serves."""
    state = request.app.state.grounds
    return {"ok": state["open"], "running": state["running"],
            "capacity": state["capacity"]}


def run():
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    run()
