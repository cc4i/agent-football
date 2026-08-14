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

from app import MAX_LIVE_ROOMS, MAX_WALL_SOCKETS
from rooms import TEAMS
from tests.test_images import ARENA, CAPTAIN, COACH, LOOPBACK, ROOT, env, exposed

SERVICE = (ROOT / "deploy" / "service.yaml").read_text()
DEPLOY = (ROOT / "deploy" / "deploy.sh").read_text()
# The yaml with its prose taken out. `sed` rewrites a comment along with
# everything else, but a mangled comment is cosmetic and a mangled value is a
# broken deploy, so the bare-token check below reads this and the header comment
# stays free to name the trap it is warning about.
SETTINGS = "\n".join(line for line in SERVICE.splitlines()
                     if not line.lstrip().startswith("#"))

# The three variables that dial another container in this instance. Scoped, not
# every URL in the file: `ARENA_PUBLIC_URL` is added to this same `env:` block by
# the last step of a first deploy and is a public https name by definition.
SIBLINGS = ("ARENA_COACH_URL", "CAPTAIN_A2A_URL", "ARENA_URL")

# What must never be written into this file, and the Secret Manager secret each
# one is read from. Four values that would each be a different kind of bad day.
SECRETS = {
    "PGPASSWORD": "arena-db-password",
    "ARENA_SECRET": "arena-secret",
    "ARENA_EMAIL_SALT": "arena-email-salt",
    "ARENA_SERVICE_TOKEN": "arena-service-token",
}

# A token deploy.sh renders. Two underscores on both sides so that it cannot
# occur inside an identifier the yaml also uses; see the O2 test at the foot of
# this file for what that costs when it does. Single underscores are allowed
# between words -- __DB_HOST__ is as well delimited as __TAG__ is -- but never
# doubled inside, which would give one token two places sed could end it.
PLACEHOLDER = re.compile(r"^__[A-Z]+(_[A-Z]+)*__$")


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


def secreted(name):
    """The Secret Manager secret each of an environment variable's entries reads.

    Anchored to the `- name:` above it rather than looked for anywhere in the
    file, so that two variables cannot swap the secrets they read.
    """
    found = re.findall(
        rf"^\s+- name: {name}\n\s+valueFrom:\n\s+secretKeyRef: "
        rf"\{{name: (\S+), key: latest\}}$", SERVICE, re.MULTILINE)
    assert found, f"the service does not read a secret for {name}"
    return found


def block(container):
    """One container's own lines, from its `- name:` to the next container's.

    A container is named in lower case and an environment variable in upper, so
    the two `- name:` keys are told apart by that and by sharing an indent.
    """
    found = re.search(rf"^(\s+)- name: {container}$(.*?)(?=^\1- name: [a-z]|\Z)",
                      SERVICE, re.MULTILINE | re.DOTALL)
    assert found, f"the service has no {container} container"
    return found.group(2)


def probed(container):
    """Every port a container's probes dial, whichever kind of probe they are."""
    found = re.findall(r"^\s+(?:httpGet: \{path: \S+, port|tcpSocket: \{port): (\d+)\}$",
                       block(container), re.MULTILINE)
    assert found, f"nothing probes {container} at all"
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
    # for running it by hand.
    assert scalar("containerPort") == env(ARENA, "PORT")


def test_every_probe_dials_the_port_its_container_listens_on():
    # A probe on the wrong port is not a quiet mistake. The arena's startup
    # probe failing is a revision that never goes ready, and its liveness probe
    # failing is Cloud Run restarting a healthy instance in the middle of a
    # match, over and over, with the log showing nothing but clean starts.
    assert probed("arena") == [scalar("containerPort")] * 2
    assert probed("coach") == [exposed(COACH)]
    assert probed("captain") == [exposed(CAPTAIN)]


def test_the_arena_proxies_to_the_port_the_coach_serves():
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
    #
    # These three and not every URL in the file. `ARENA_PUBLIC_URL` joins this
    # same `env:` block at the last step of a first deploy and is a public
    # https name by definition, and a test that reddens when the README's own
    # instructions are followed gets deleted rather than read.
    for name in SIBLINGS:
        for url in valued(name):
            assert url.startswith(f"http://{LOOPBACK}"), f"{name} is {url}"


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


def test_the_deploy_stands_down_the_revisions_it_replaces():
    """One instance is per revision, and a deploy leaves the last one running.

    Which makes the test above true of a revision and not of the venue. The
    arena a deploy supersedes keeps its pinned container, takes no traffic, and
    goes on deciding abandonments against the same database from whatever code
    it was built from -- three of them were up at once in production. A revision
    is immutable, so deleting is the only lever, and `deploy.sh` pulls it.

    Read as text like everything else here, and what it reads for is the guard
    rather than the delete. Deleting revisions is only ever safe because the
    list is filtered first, and every way of getting that filter slightly wrong
    ends with the arena the venue is playing on being the one deleted.
    """
    assert "gcloud run revisions delete" in DEPLOY, "a deploy leaves its old arenas up"
    guard = DEPLOY.split("gcloud run revisions delete")[0]
    # Filtered against what Cloud Run says is taking traffic, rather than
    # against the tag deployed a moment ago or the newest name in the list.
    assert "value(status.traffic[].revisionName)" in guard
    assert 'if [ -z "$serving" ]' in guard, "an empty answer would delete every revision"
    # Whole line and fixed string: arena-00007-cvr contains arena-00007-cv, and
    # a prefix or a regex is a footgun pointed at the live one.
    assert "grep -vxF" in guard
    # And one of the replaced ones is spared, because a revision that still
    # exists is a rollback of seconds where rebuilding its tag is minutes. The
    # list comes back newest first, so that one is the head of it and the
    # deletes run over the tail.
    assert "head -n 1" in guard
    assert "tail -n +2" in guard


def test_the_instance_takes_a_whole_venue_of_sockets_at_once():
    """`containerConcurrency` is a hard cap and every socket here is long-lived.

    Counted from the arena's own two limits rather than written down again,
    because those are what a bigger workshop raises. Per live room there are
    four sockets on `/ws/rooms/{code}`: the screen's, the pitch iframe's, and a
    phone for each seat. The wall is one more per watcher, room or no room.

    Over the cap Cloud Run queues and then answers 429, and `maxScale: "1"`
    means there is no second instance for the overflow to go to. The phone
    reads it as a network fault and the arena's log says nothing at all,
    because the request never reached the arena.
    """
    venue = MAX_LIVE_ROOMS * (2 + len(TEAMS)) + MAX_WALL_SOCKETS
    concurrency = int(scalar("containerConcurrency"))
    assert concurrency >= venue, f"{venue - concurrency} phones get 429 at a full venue"
    # Raising the arena's limits past this is the deploy that cannot work:
    # Cloud Run will not admit a revision asking for more than 1000, so the
    # room and wall caps have to come down instead, or the whole single-instance
    # argument has to be given up.
    assert concurrency <= 1000, "Cloud Run's per-instance ceiling is 1000"


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
    #
    # Each ref is read from under the variable it belongs to and not looked for
    # loose in the file, so that two of these swapping secrets is caught. That
    # swap is the nastiest of the four failures available here: everything
    # starts, and the arena signs sessions with the salt while hashing emails
    # with the session secret, so every returning player is a stranger and the
    # cookies from before the deploy are all invalid.
    for name, secret in SECRETS.items():
        assert set(follows(name)) == {"valueFrom:"}, f"{name} is written out in the yaml"
        assert set(secreted(name)) == {secret}


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


def test_no_setting_spells_a_placeholder_word_bare():
    # The other direction, and the one somebody actually types: a new line
    # written as `value: PROJECT` because the underscores looked decorative.
    # sed renders it, so the deploy succeeds and the yaml looks right in the
    # diff; what breaks is whatever else on the line shares the word.
    #
    # Comments are not read. sed rewrites those too, but a mangled sentence is
    # cosmetic where a mangled value is a broken deploy, and this file's header
    # comment has to stay free to name the trap it is warning about.
    bare = "|".join(sorted(token.strip("_") for token in placeholders()))
    loose = re.search(rf"(?<![A-Z_])({bare})(?![A-Z_])", SETTINGS)
    assert not loose, f"{loose and loose.group()} is rendered wherever it appears"
