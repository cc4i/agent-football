"""Shouting to the bench, through the game's own coach.

Tuning edits the squad's files directly. This does the opposite: it types into
the game's shout bar and lets the game's own agent chain decide what to change.
That chain is ADK coach -> team captain over A2A -> four player agents, and
watching Antigravity drive it through the interface is the point of the stage.

The match window is the one the agent opened in stage 2, reached over its
debug port, so the shout lands in the match already on screen rather than in
some second browser nobody is watching.
"""

import asyncio

from tools.match import CALLED, read_status

DEBUG_URL = "http://localhost:9222"
GAME_URL = "localhost:5173"
SHOUT_INPUT = "#shout-message-input"
SHOUT_BUTTON = "#shout-send-btn"
TERMINAL = "#terminal-body"

# The coach, the captain and four player agents all answer in turn.
REPLY_TIMEOUT_MS = 120000


def _chain_complete(replies: list[str]) -> bool:
    """True once all four players have answered the captain's huddle."""
    return sum(1 for line in replies if line.startswith("\u2514")) >= 4


def _new_lines(before: str, after: str) -> list[str]:
    old = before.splitlines()
    return [line.strip() for line in after.splitlines()[len(old):] if line.strip()]


async def shout_to_the_team(message: str) -> dict:
    """Shout an instruction to the players through the game's coach.

    Use this instead of editing attributes when the manager wants the team
    told something: press, push up, sit deep, shoot on sight. The game's own
    agents decide what that means and change the squad themselves.

    Waits for the whole chain and returns every reply it heard, so call it
    once. Calling it again does not fetch the previous answers, it shouts
    again.

    Args:
      message: what to shout, in the manager's words.
    """
    from playwright.async_api import async_playwright

    CALLED.add("shout_to_the_team")
    stripped = message.strip()
    if not stripped:
        return {"error": "nothing to shout"}

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(DEBUG_URL)
        except Exception:
            return {"error": "no_match_window",
                    "detail": "No match is on screen. Take the field first, "
                              "and launch it with --remote-debugging-port=9222."}
        try:
            page = next((p for c in browser.contexts for p in c.pages
                         if GAME_URL in p.url), None)
            if page is None:
                return {"error": "no_match_window",
                        "detail": f"Nothing at {GAME_URL} in the open browser."}

            button = page.locator(SHOUT_BUTTON)
            # The bar disables itself while the coach is busy.
            await button.wait_for(state="attached", timeout=10000)
            for _ in range(60):
                if await button.is_enabled():
                    break
                await asyncio.sleep(0.5)

            before = await page.inner_text(TERMINAL)
            await page.fill(SHOUT_INPUT, stripped)
            await button.click()

            deadline = REPLY_TIMEOUT_MS / 1000
            waited, quiet, seen = 0.0, 0.0, before
            while waited < deadline:
                await asyncio.sleep(1)
                waited += 1
                now = await page.inner_text(TERMINAL)
                if now != seen:
                    quiet, seen = 0.0, now
                    if _chain_complete(_new_lines(before, now)):
                        break
                    continue
                quiet += 1
                # The coach answers within a second, then the terminal goes
                # quiet for half a minute or more while the captain briefs four
                # player agents over A2A. Giving up on ten seconds of silence
                # returns just the relay line and misses the huddle entirely.
                if quiet >= 30 and now != before:
                    break

            replies = _new_lines(before, seen)
            result = {"shouted": stripped, "replies": replies}
            if not _chain_complete(replies):
                # The players only answer during a live match, so a half
                # finished chain after full time is expected, not a fault.
                over = "error" in read_status() or not read_status().get("gameActive")
                result["note"] = (
                    "The coach took it, but the players only answer during a "
                    "live match and this one has finished. Kick off again to "
                    "see the full huddle."
                    if over else
                    f"The players had not all answered within {int(deadline)}s. "
                    "Report what came back; shouting again will not fetch more.")
            return result
        finally:
            await browser.close()
