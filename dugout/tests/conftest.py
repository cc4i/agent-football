"""One stand-in arena, shared by every test whose subject talks to it.

The double sits at the transport, not at `arena.py`. Everything the dugout's
client actually does -- building the URL, attaching the service token, reading
a refusal out of the body, turning a dead socket into something a manager can
read -- runs for real, and only the wire is imaginary. A fake client would test
the fake.

It is deliberately strict about the shapes it answers with: they are copied
from the arena's own routes, and `arena/tests/` is what proves those are right.
"""

import json

import httpx
import pytest

import arena
import attributes

RULES = {
    "defender": {
        "aggression": {"baseline": 0.6, "min": 0.0, "max": 1.0},
        "tackleCooldown": {"baseline": 800.0, "min": 100.0, "max": 2000.0},
    },
    "midfielder": {
        "passRange": {"baseline": 0.7, "min": 0.0, "max": 1.0},
        "stamina": {"baseline": 0.5, "min": 0.0, "max": 1.0},
    },
    "forward": {
        "finishing": {"baseline": 0.5, "min": 0.0, "max": 1.0},
        "shotPower": {"baseline": 0.6, "min": 0.0, "max": 1.0},
    },
    "goalkeeper": {
        "reflexes": {"baseline": 0.8, "min": 0.0, "max": 1.0},
        "decisionDelay": {"baseline": 80.0, "min": 40.0, "max": 400.0},
    },
}

SQUAD = {role: {name: limits["baseline"] for name, limits in bands.items()}
         for role, bands in RULES.items()}

TOKEN = "test-service-token"


class FakeArena:
    """The arena as far as the dugout can tell.

    Holds one workshop squad and answers the five calls the dugout makes of it.
    Set `refusal` or `silent` to make it behave like an arena having a bad day.
    """

    def __init__(self):
        self.rules = {role: dict(bands) for role, bands in RULES.items()}
        self.squad = {role: dict(values) for role, values in SQUAD.items()}
        self.seen = []          # (method, path, body) for everything asked
        self.tokens = []        # the service token on each, or None
        self.refusal = None     # (status, detail) to answer everything with
        self.silent = False     # nothing listening on the port at all
        self.next_seq = 41

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._answer))

    def asked(self, method: str) -> list[str]:
        return [path for verb, path, _ in self.seen if verb == method]

    def _answer(self, request: httpx.Request) -> httpx.Response:
        # The raw path, not the decoded one: an escaped role must stay escaped
        # here or the escaping is exactly what the test cannot see.
        path = request.url.raw_path.decode().partition("?")[0]
        body = json.loads(request.content) if request.content else None
        self.seen.append((request.method, path, body))
        self.tokens.append(request.headers.get("X-Arena-Service"))
        if self.silent:
            raise httpx.ConnectError("All connection attempts failed",
                                     request=request)
        if self.refusal is not None:
            status, detail = self.refusal
            return httpx.Response(status, json={"detail": detail})
        return self._route(request, path, body)

    def _route(self, request, path, body):
        if path == "/api/attributes":
            return httpx.Response(200, json={"roles": self.rules})
        if path == f"/api/rooms/{arena.ROOM}/shout":
            return self._shout(body)

        squad_path = f"/api/rooms/{arena.ROOM}/teams/{arena.TEAM}/profiles"
        if path == squad_path:
            return httpx.Response(200, json={"team": arena.TEAM,
                                             "profiles": self.squad})
        if path.startswith(squad_path + "/"):
            role = path[len(squad_path) + 1:]
            if role not in self.squad:
                return httpx.Response(
                    404, json={"detail": f"this room has no blue {role}"})
            if request.method == "PATCH":
                return self._patch(role, body)
            return httpx.Response(200, json={"team": arena.TEAM, "role": role,
                                             "attributes": self.squad[role]})
        return httpx.Response(404, json={"detail": f"no route for {path}"})

    def _shout(self, body):
        words = " ".join((body or {}).get("text", "").split())
        if not words:
            return httpx.Response(422, json={"detail": "a shout needs some "
                                                       "words in it"})
        self.next_seq += 1
        return httpx.Response(200, json={
            "seq": self.next_seq, "ahead": 0, "team": arena.TEAM,
            "text": words, "preset": None, "actor": arena.ACTOR})

    def _patch(self, role, body):
        problems = self._problems(role, body["changes"])
        if problems:
            return httpx.Response(422, json={"detail": {"problems": problems}})
        held = self.squad[role]
        changed = {name: value for name, value in body["changes"].items()
                   if held.get(name) != value}
        held.update(changed)
        self.next_seq += 1
        return httpx.Response(200, json={"role": role, "attributes": held,
                                         "changed": changed,
                                         "seq": self.next_seq})

    def _problems(self, role, changes):
        """The same refusals the arena's validator makes, in the same words."""
        problems = []
        for name, value in changes.items():
            limits = self.rules[role].get(name)
            if limits is None:
                problems.append(f"{role} has no attribute {name!r}")
            elif not isinstance(value, (int, float)) or isinstance(value, bool):
                problems.append(f"{name} must be a number, got {value!r}")
            elif not limits["min"] <= value <= limits["max"]:
                problems.append(f"{name} must be between {limits['min']} and "
                                f"{limits['max']}, got {value}")
        return problems


@pytest.fixture
def fake_arena(monkeypatch):
    """An arena on the other end of the dugout's client, holding a squad."""
    pretend = FakeArena()
    monkeypatch.setattr(arena, "_session", pretend.client())
    monkeypatch.setenv("ARENA_SERVICE_TOKEN", TOKEN)
    monkeypatch.delenv("ARENA_URL", raising=False)
    # The rules are cached for the life of the process, and this arena is not
    # the one the last test used.
    monkeypatch.setattr(attributes, "_rules", None)
    return pretend
