"""The service yaml against the images it deploys, read as text.

`test_images.py` exists because three Dockerfiles agree on a set of ports and
two directory paths and nothing would notice them drifting apart. The service
yaml is now the third copy of every one of those values, so it is read here
against the same sources rather than against a second set of literals.

Text and not `pyyaml`: the arena does not depend on a yaml parser and this is
not worth adding one for. The file's shape is stable enough to match on, and
every helper below asserts before it returns, so a match that stops matching
fails as itself rather than as an AttributeError three lines later.
"""

import re
import subprocess

from tests.test_images import ARENA, CAPTAIN, COACH, LOOPBACK, ROOT, env, exposed

SERVICE = (ROOT / "deploy" / "service.yaml").read_text()
DEPLOY = (ROOT / "deploy" / "deploy.sh").read_text()

# What must never be written into this file, and the Secret Manager secret each
# one is read from. Four values that would each be a different kind of bad day.
SECRETS = {
    "PGPASSWORD": "arena-db-password",
    "ARENA_SECRET": "arena-secret",
    "ARENA_EMAIL_SALT": "arena-email-salt",
    "ARENA_SERVICE_TOKEN": "arena-service-token",
}

# A token deploy.sh renders. Underscores on both sides so that it cannot occur
# inside an identifier the yaml also uses; see the O2 test at the foot of this
# file for what that costs when it does.
PLACEHOLDER = re.compile(r"^__[A-Z]+__$")


def valued(name):
    """What the service sets an environment variable to, everywhere it sets it."""
    found = re.findall(rf"^\s+- name: {name}\n\s+value: (\S+)$", SERVICE, re.MULTILINE)
    assert found, f"the service sets no literal {name}"
    return found


def follows(name):
    """The key under each of an environment variable's entries.

    `value:` for a literal and `valueFrom:` for a reference, which is the whole
    of what the secret test needs to know.
    """
    found = re.findall(rf"^\s+- name: {name}\n\s+(\S+)", SERVICE, re.MULTILINE)
    assert found, f"the service does not set {name} at all"
    return found


def scalar(key):
    """A plain `key: value` from anywhere in the file, quotes included."""
    found = re.search(rf"^\s+{re.escape(key)}: (\S+)$", SERVICE, re.MULTILINE)
    assert found, f"the service sets no {key}"
    return found.group(1)


def mounted():
    """Every path the shared volume is mounted at."""
    found = re.findall(r"^\s+mountPath: (\S+)$", SERVICE, re.MULTILINE)
    assert found, "the service mounts the shared volume nowhere"
    return found


def port_of(url):
    """The port a URL in the yaml dials."""
    found = re.match(r"https?://[^:/]+:(\d+)", url)
    assert found, f"{url} names no port"
    return found.group(1)


def path_of(url):
    """The path a URL in the yaml asks for."""
    found = re.match(r"https?://[^/]+(/.*)$", url)
    assert found, f"{url} names no path"
    return found.group(1)


def rendered():
    """The tokens deploy.sh replaces, each with the shell variable it uses."""
    found = re.findall(r'-e "s\|([^|]+)\|\$\{(\w+)\}\|g"', DEPLOY)
    assert found, "deploy.sh no longer renders the service yaml with sed"
    return dict(found)


def placeholders():
    """Every token in the yaml that is waiting to be rendered."""
    return set(re.findall(r"__\w+?__", SERVICE))


def well_known_path():
    """The agent-card path, from the ADK rather than from a literal here.

    Read through the game's own interpreter. `google-adk` is the game's
    dependency and not the arena's, and O5's point is that a constant is what
    the application dials, not that the arena should learn to import it.
    """
    interpreter = ROOT / "game" / ".venv" / "bin" / "python"
    assert interpreter.exists(), "the game's environment is missing: cd game && uv sync"
    read = subprocess.run(
        [interpreter, "-c", "from google.adk.agents.remote_a2a_agent import "
                            "AGENT_CARD_WELL_KNOWN_PATH; print(AGENT_CARD_WELL_KNOWN_PATH)"],
        capture_output=True, text=True)
    assert read.returncode == 0, read.stderr
    return read.stdout.strip()


def test_both_containers_mount_the_volume_where_their_images_look_for_it():
    # The coach's MCP server writes a substitution and the arena serves it, and
    # that is the whole of the mechanism. A mountPath differing from either
    # image by a character is a poll that 404s and a toast that never appears.
    shared = env(COACH, "PLAYER_STATE_DIR")
    assert shared == env(ARENA, "ARENA_PLAYER_STATE_DIR")
    assert mounted() == [shared, shared]


def test_the_service_publishes_the_port_the_arena_listens_on():
    # Cloud Run sets PORT from this, and the image's ENV is only the default
    # for running it by hand. They still have to agree: the arena's own probes
    # dial the number written here.
    assert scalar("containerPort") == env(ARENA, "PORT")


def test_the_arena_proxies_to_the_port_the_coach_serves():
    assert valued("ARENA_COACH_URL") == ["http://127.0.0.1:8000"]
    assert port_of(valued("ARENA_COACH_URL")[0]) == exposed(COACH)


def test_the_coach_dials_the_port_the_captain_serves():
    assert port_of(valued("CAPTAIN_A2A_URL")[0]) == exposed(CAPTAIN)


def test_the_coach_asks_for_the_agent_card_at_the_path_the_adk_publishes():
    # Imported rather than spelled out. It resolves to
    # /.well-known/agent-card.json today, and the day the ADK moves it this
    # fails here instead of at the first shout after a deploy.
    assert path_of(valued("CAPTAIN_A2A_URL")[0]) == well_known_path()


def test_the_specialists_reach_the_arena_on_its_own_port():
    # Both sidecars carry the service token to this URL. Wrong, every patch a
    # specialist makes fails and the shout chain reports a huddle over a squad
    # that never moved.
    for url in valued("ARENA_URL"):
        assert port_of(url) == scalar("containerPort")


def test_nothing_dials_a_sibling_container_by_name():
    # Neither sidecar binds ::1, and getaddrinfo("localhost") answers ::1 first
    # wherever IPv6 is up, so a name dial works only by falling back after a
    # refused connect. Every one of these is a literal today, and this is what
    # keeps it that way.
    urls = re.findall(r"^\s+value: (https?://\S+)$", SERVICE, re.MULTILINE)
    assert urls, "the service dials nothing at all, which cannot be right"
    for url in urls:
        assert url.startswith(f"http://{LOOPBACK}"), url


def test_the_service_runs_exactly_one_instance():
    """One instance is a correctness constraint, not thrift.

    The match bus, host liveness and the chain's Gemini semaphore are all in
    process. A second instance means phones that never see a frame, a watchdog
    that abandons matches somebody is playing, and a shout whose specialist
    patches land on the other instance so `caused_by` returns None and the
    leaderboard is quietly wrong with nothing anywhere saying so.

    The quotes are part of it: a Knative annotation value has to be a string.
    """
    assert scalar("autoscaling.knative.dev/minScale") == '"1"'
    assert scalar("autoscaling.knative.dev/maxScale") == '"1"'


def test_the_request_timeout_outlasts_a_match():
    # A WebSocket is a request as far as Cloud Run is concerned and the default
    # is 300 seconds, which cuts the room socket mid-match and reads on the
    # phone as a network fault. 3600 is the maximum Cloud Run allows.
    assert scalar("timeoutSeconds") == "3600"


def test_every_secret_is_a_reference_and_never_a_literal():
    # The test that fails the day somebody debugs a deploy by pasting a token
    # in "just to see". A value here is committed, and the salt in particular
    # cannot be rotated afterwards without making every returning player a
    # stranger.
    for name, secret in SECRETS.items():
        assert set(follows(name)) == {"valueFrom:"}, f"{name} is written out in the yaml"
        assert f"secretKeyRef: {{name: {secret}, key: latest}}" in SERVICE


def test_the_placeholders_cannot_match_inside_an_identifier():
    # A bare PROJECT is a substring of GOOGLE_CLOUD_PROJECT, and `sed s|...|g`
    # does not care: it rewrites the variable's name along with its value, the
    # sidecars are handed GOOGLE_CLOUD_<project id>, which nothing reads, and
    # Vertex has no project. That surfaces as a model call failing at the first
    # shout rather than as anything at deploy time.
    assert "GOOGLE_CLOUD_PROJECT" in SERVICE, "the trap this test guards is gone"
    substitutions = rendered()
    for token, variable in substitutions.items():
        assert PLACEHOLDER.match(token), f"sed would rewrite {token} inside an identifier"
        assert token == f"__{variable}__"
    assert set(substitutions) == placeholders()
