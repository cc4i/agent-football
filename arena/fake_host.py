"""Replay a recorded match into an arena room over the room socket.

The arena, the board and the wall can then be built, tested and demoed without
a browser running physics:

    uv run python fake_host.py --room K7F2 --client-id <token> --log fixtures/match-3-1.jsonl

The room must already be live, and `--client-id` must be its physics token -
the `host_client_id` column. No HTTP response carries it: the arena hands it to
the grounds over the control socket and nowhere else, so a hand replay reads it
out of the database. To make a room live, join, take a seat, mark ready, and
call /start with that session.

The log is JSON Lines, one frame per line, `#` for a comment:

    {"t": 0.0,  "type": "event", "kind": "kickoff", "payload": {}}
    {"t": 0.5,  "type": "state", "payload": {"score": [0, 0], "clock": 179}}

`t` is match time in seconds and is what drives the pacing.
"""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import quote

import websockets

FRAME_TYPES = ("state", "event")


def parse_log(path):
    """Read a JSONL match log into frames, oldest first."""
    frames = []
    for number, line in enumerate(Path(path).read_text().splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        frame = json.loads(line)
        if not isinstance(frame, dict):
            raise ValueError(f"{path}:{number} must be a JSON object")
        if not isinstance(frame.get("t"), (int, float)):
            raise ValueError(f"{path}:{number} needs a numeric 't'")
        if frame.get("type") not in FRAME_TYPES:
            raise ValueError(f"{path}:{number} type must be one of {', '.join(FRAME_TYPES)}")
        if frame["type"] == "event" and not frame.get("kind"):
            raise ValueError(f"{path}:{number} is an event with no kind")
        frames.append(frame)
    frames.sort(key=lambda frame: frame["t"])
    return frames


def to_message(frame):
    """Turn a log frame into what the room socket expects from its host."""
    if frame["type"] == "state":
        return {"type": "host.state", "payload": frame.get("payload", {})}
    return {
        "type": "host.event",
        "kind": frame["kind"],
        "match_ms": int(frame["t"] * 1000),
        "payload": frame.get("payload", {}),
    }


async def replay(frames, send, speed=1.0, sleep=asyncio.sleep):
    """Send every frame, waiting out the gaps between them.

    `send` and `sleep` are arguments so this can be tested without a socket and
    without sitting through three minutes of football.
    """
    clock = 0.0
    for frame in frames:
        gap = (frame["t"] - clock) / speed
        if gap > 0:
            await sleep(gap)
        clock = frame["t"]
        await send(to_message(frame))


async def run(url, frames, speed):
    async with websockets.connect(url) as socket:
        await socket.recv()          # the opening room snapshot
        await replay(frames, lambda message: socket.send(json.dumps(message)), speed)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Replay a recorded match into an arena room.")
    parser.add_argument("--room", required=True, help="room code, for example K7F2")
    parser.add_argument("--log", required=True, help="path to a JSONL match log")
    parser.add_argument("--client-id", required=True,
                        help="the room's physics token, its host_client_id")
    parser.add_argument("--arena", default="ws://127.0.0.1:8003")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="1.0 plays in real time; 10 is useful for a smoke test")
    options = parser.parse_args(argv)

    if options.speed <= 0:
        raise ValueError("speed must be positive")
    frames = parse_log(options.log)
    url = f"{options.arena}/ws/rooms/{options.room}?client_id={quote(options.client_id)}"
    print(f"--> replaying {len(frames)} frames into {options.room} at {options.speed}x")
    asyncio.run(run(url, frames, options.speed))


if __name__ == "__main__":
    main()
