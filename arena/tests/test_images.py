"""The contract the four images share, read as text so no runtime is needed.

Four Dockerfiles agree on a set of ports and a couple of directory paths, and
the service yamls repeat every one of them. Nothing else would notice them
drifting apart: a port that agrees with nothing is not a build failure, it is a
probe that never passes and a revision that never serves a request.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARENA = (ROOT / "arena" / "Dockerfile").read_text()
COACH = (ROOT / "game" / "Dockerfile.coach").read_text()
CAPTAIN = (ROOT / "game" / "Dockerfile.captain").read_text()
GROUNDS = (ROOT / "grounds" / "Dockerfile").read_text()
DOCKERIGNORE = (ROOT / ".dockerignore").read_text()
# The two callers. An image's port is only right if it is the one being dialled,
# so the numbers below are read out of these rather than written down twice.
COACH_PY = (ROOT / "arena" / "coach.py").read_text()
AGENT_PY = (ROOT / "game" / "agents" / "agent.py").read_text()
# The captain's server, which is the one place a bind and an advertised address
# are two different things rather than one.
CAPTAIN_PY = (ROOT / "game" / "agents" / "captain_server.py").read_text()
# The grounds serve their own port rather than being handed one on a command
# line, and they ship a browser, so both numbers live in the service itself.
GROUNDS_PY = (ROOT / "grounds" / "main.py").read_text()
GROUNDS_PROJECT = (ROOT / "grounds" / "pyproject.toml").read_text()

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


def defaulted(source, name):
    """What a module falls back to for `name` when the environment says nothing."""
    found = re.search(rf'^{name}\s*=\s*(?:int\()?os\.environ\.get\("{name}",\s*"([^"]+)"',
                      source, re.MULTILINE)
    assert found, f"{name} no longer defaults to anything"
    return found.group(1)


def dialled(source, name):
    """The port a module's default URL for `name` points its caller at."""
    found = re.search(rf'^{name}\s*=\s*os\.environ\.get\([^,]+,\s*f?"http://[^:/"]+:(\d+)',
                      source, re.MULTILINE)
    assert found, f"{name} no longer defaults to a URL naming a port"
    return found.group(1)


def test_no_image_expects_a_directory_another_image_writes():
    # There was one, and it was the whole of the mechanism behind a
    # substitution: an in-memory volume mounted into both containers, the
    # coach's MCP server writing a file into it and the arena serving the file
    # back. A path differing by a character was a poll that 404d quietly. A
    # knock is a room event now, so the coupling is gone and the two images
    # share nothing but a network namespace.
    for image in (ARENA, COACH, CAPTAIN, GROUNDS):
        assert "PLAYER_STATE" not in image


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


def test_the_grounds_serve_the_port_they_declare():
    # Unlike the other three, this server is handed no port on a command line -
    # it reads PORT itself - so the image's job is only to agree with it about
    # what that is when nobody sets one.
    assert env(GROUNDS, "PORT") == defaulted(GROUNDS_PY, "PORT")
    assert exposed(GROUNDS) == env(GROUNDS, "PORT")


def test_the_grounds_browser_is_the_build_their_own_playwright_downloads():
    """A Playwright client and a browser build are one version, not two.

    Which is what makes the download a step in this image rather than a base
    image naming a version of its own. Two pins is the bug: a client one
    release ahead of the browsers sitting beside it refuses to launch with
    `Executable doesn't exist`, the base image is fine, the pip pin is fine,
    and no match ever starts.

    So the order is the test. The lockfile is synced, PATH is put in front of
    the venv it made, and only then is `playwright install` the pinned one.
    """
    assert re.search(r'"playwright==\S+?"', GROUNDS_PROJECT), \
        "the grounds no longer pin playwright, so there is no version to agree with"
    steps = [GROUNDS.index(step) for step in ("uv sync --frozen",
                                              'PATH="/app/.venv/bin:$PATH"',
                                              "PLAYWRIGHT_BROWSERS_PATH=",
                                              "playwright install --with-deps chromium")]
    assert steps == sorted(steps), "the grounds download a browser for some other playwright"


def test_the_grounds_bring_the_libraries_their_browser_loads():
    # `--with-deps` is the apt half of the line above, and dropping it builds
    # cleanly: Chromium on a slim base is a binary with nothing to link
    # against, and it fails at launch in Cloud Run rather than here.
    assert "--with-deps" in GROUNDS
    # And the browser goes somewhere that does not depend on who runs the
    # image. The default is under $HOME, so root's cache is not a path a
    # non-root runtime would look in.
    assert env(GROUNDS, "PLAYWRIGHT_BROWSERS_PATH").startswith("/")


def test_the_grounds_bind_wide_because_cloud_run_requires_it_of_the_ingress():
    # Same contract as the arena's, and the same failed revision behind it. The
    # grounds are an ingress container too: nothing dials them but the probe,
    # and the probe does not dial from inside the instance's namespace.
    assert f'host="{WIDE}"' in GROUNDS_PY


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
