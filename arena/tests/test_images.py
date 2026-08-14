"""The contract the three images share, read as text so no runtime is needed.

Three Dockerfiles agree on a set of ports and two directory paths, and Task 14's
service yaml repeats every one of them. Nothing else would notice them drifting
apart, and the drift that matters is the shared player_state path: get it wrong
in one image and the coach writes a substitution the arena answers with a 404
that the browser's poll swallows.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARENA = (ROOT / "arena" / "Dockerfile").read_text()
COACH = (ROOT / "game" / "Dockerfile.coach").read_text()
CAPTAIN = (ROOT / "game" / "Dockerfile.captain").read_text()
DOCKERIGNORE = (ROOT / ".dockerignore").read_text()
ENV_EXAMPLE = (ROOT / "arena" / ".env.example").read_text()
# The two callers. An image's port is only right if it is the one being dialled,
# so the numbers below are read out of these rather than written down twice.
COACH_PY = (ROOT / "arena" / "coach.py").read_text()
AGENT_PY = (ROOT / "game" / "agents" / "agent.py").read_text()
# The captain's server, which is the one place a bind and an advertised address
# are two different things rather than one.
CAPTAIN_PY = (ROOT / "game" / "agents" / "captain_server.py").read_text()

# What a container reaches its own instance on, written out rather than left to
# `localhost`, which is two addresses wherever IPv6 is up.
LOOPBACK = "127.0.0.1"
# Every interface. What a container has to bind to be probed, which is not the
# same address as the one anything dials it on.
WIDE = "0.0.0.0"


def env(dockerfile, name):
    """What an image's ENV sets a variable to, backslash continuations and all."""
    one_line_per_instruction = dockerfile.replace("\\\n", " ")
    found = re.search(rf"^ENV\b.*?\b{name}=(\S+)", one_line_per_instruction, re.MULTILINE)
    assert found, f"no ENV sets {name}"
    return found.group(1)


def exposed(dockerfile):
    """The port an image declares, as written."""
    found = re.search(r"^EXPOSE (\d+)", dockerfile, re.MULTILINE)
    assert found, "the image declares no port"
    return found.group(1)


def commanded(dockerfile):
    """The port the image's CMD hands its server on the command line."""
    found = re.search(r'"--port",\s*"(\d+)"', dockerfile)
    assert found, "the image's CMD names no port"
    return found.group(1)


def bound(dockerfile):
    """The address the image's CMD binds, whether written exec form or shell."""
    found = re.search(r'"--host",\s*"([^"]+)"|--host (\S+)', dockerfile)
    assert found, "the image's CMD names no bind address"
    return found.group(1) or found.group(2)


def dialled(source, name):
    """The port a module's default URL for `name` points its caller at."""
    found = re.search(rf'^{name}\s*=\s*os\.environ\.get\([^,]+,\s*f?"http://[^:/"]+:(\d+)',
                      source, re.MULTILINE)
    assert found, f"{name} no longer defaults to a URL naming a port"
    return found.group(1)


def test_the_two_images_share_one_player_state_directory():
    # One in-memory volume is mounted into both containers and this is the whole
    # of the mechanism behind a substitution: the coach's MCP server writes a
    # file and the arena serves it. A path differing by a character is a poll
    # that 404s quietly.
    assert env(COACH, "PLAYER_STATE_DIR") == env(ARENA, "ARENA_PLAYER_STATE_DIR")


def test_the_documented_player_state_path_is_the_one_the_images_use():
    documented = re.search(r"^# ARENA_PLAYER_STATE_DIR=(\S+)", ENV_EXAMPLE, re.MULTILINE)
    assert documented, ".env.example no longer documents ARENA_PLAYER_STATE_DIR"
    assert documented.group(1) == env(ARENA, "ARENA_PLAYER_STATE_DIR")


def test_the_shared_directory_is_not_underneath_a_symlink():
    # /var/run is a symlink to /run in python:3.14-slim. Mounting the volume
    # under it in two containers makes both depend on the runtime resolving that
    # symlink the same way, and the failure when they diverge is the silent 404
    # above rather than anything that says so.
    assert not env(ARENA, "ARENA_PLAYER_STATE_DIR").startswith("/var/run")


def test_the_arena_serves_the_pitch_its_build_stage_wrote():
    written_to = re.search(r"^COPY --from=pitch \S+ (\S+)", ARENA, re.MULTILINE)
    assert written_to, "the arena image no longer copies the built pitch in"
    assert env(ARENA, "ARENA_PITCH_DIR") == written_to.group(1)


def test_the_arena_listens_on_the_port_cloud_run_names():
    # Cloud Run sets PORT; the ENV is only the default for running the image by
    # hand, so the CMD has to read the variable rather than repeat the number.
    assert env(ARENA, "PORT") == "8080"
    assert exposed(ARENA) == env(ARENA, "PORT")
    assert "--port ${PORT}" in ARENA


def test_the_coach_serves_the_port_the_arena_proxies_to():
    # Read from arena/coach.py's default rather than repeated here. A literal in
    # both places is a pair that can be changed together and stay green, which
    # is the drift this file exists to catch rather than an instance of it.
    assert exposed(COACH) == dialled(COACH_PY, "COACH_URL")
    assert commanded(COACH) == exposed(COACH)


def test_the_captain_serves_the_port_the_coach_calls():
    assert exposed(CAPTAIN) == dialled(AGENT_PY, "CAPTAIN_A2A_URL")
    assert env(CAPTAIN, "CAPTAIN_PORT") == exposed(CAPTAIN)


def test_the_sidecars_bind_wide_because_the_probe_is_not_on_their_loopback():
    # Settled by a deploy rather than by argument. Both sidecars bound loopback
    # on the reasoning that the containers of one instance share a network
    # namespace, which is true and is not the question: the startup probe is
    # run by Cloud Run and does not dial from inside that namespace. Revision
    # arena-00001 has the captain logging `Uvicorn running on
    # http://127.0.0.1:8001` and, against the same port, forty consecutive
    # `STARTUP TCP probe failed ... DEADLINE_EXCEEDED`. The instance never
    # started, so the coach behind it in `container-dependencies` never ran to
    # fail the same way.
    #
    # Widening costs nothing that was holding: neither server authenticates
    # anything, and what isolates them is that neither publishes a port. There
    # is no path in - Cloud Run routes external requests to the ingress
    # container alone, and Direct VPC egress is egress, so nothing in the VPC
    # can dial an instance either.
    assert bound(COACH) == WIDE
    assert env(CAPTAIN, "CAPTAIN_BIND") == WIDE


def test_the_captain_advertises_the_loopback_rather_than_the_bind():
    # Two variables, and collapsing them to one is the regression this guards.
    # `to_a2a` writes host into the agent card's rpc_url, and the coach dials
    # the card's url rather than the address it fetched the card from - so the
    # card has to name an address that can be dialled, and 0.0.0.0 is not one.
    # The bind is the wide address; the card keeps the loopback.
    assert 'os.environ.get("CAPTAIN_HOST"' in CAPTAIN_PY
    assert 'os.environ.get("CAPTAIN_BIND"' in CAPTAIN_PY
    assert env(CAPTAIN, "CAPTAIN_HOST") == LOOPBACK


def test_the_arena_binds_wide_because_cloud_run_requires_it_of_the_ingress():
    # The ingress container's case is documented rather than discovered: the
    # container contract has it listening on 0.0.0.0:$PORT, and a revision that
    # binds loopback instead never serves a request.
    assert bound(ARENA) == WIDE


def test_the_build_context_excludes_what_must_not_reach_an_image():
    excluded = [line.strip() for line in DOCKERIGNORE.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    # `dugout` is the security one: it embeds the Antigravity CLI and runs shell
    # commands unrestricted, so it has no business in an image that faces the
    # internet. The other three are correctness - this Mac's .env, its aarch64
    # virtual environment and its host-built native modules would each land on
    # top of what the image just built for itself.
    for entry in ("dugout", "**/.env", "**/.venv", "**/node_modules"):
        assert entry in excluded
