# Dugout Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `dugout/`'s form-based avatar generator with a chat-driven app that embeds the Antigravity Python SDK in-process and renders the agent's live trajectory, covering quest stages 1 through 4a.

**Architecture:** One FastAPI process on :8002 serves a static chat UI and a single SSE endpoint. `session.py` owns an in-process `Agent` and fans `ChatResponse.thoughts`, `.tool_calls` and `.chunks` into one `asyncio.Queue` drained to SSE. Curated Python tools do the privileged work (image generation, match reading, stat tuning) so the agent never touches `player_state/` through `edit_file`. Four subagents each hold exactly one role-specific tuning tool, which is both the guardrail and the attribution mechanism.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, `google-antigravity==0.1.9`, google-genai, Pillow, pytest + pytest-asyncio, Playwright (for agent-authored scripts), vanilla JS/CSS for the UI, vitest for the one `game/` regression test.

## Global Constraints

- `google-antigravity==0.1.9` pinned **exactly**. It is a 0.x SDK and the surface used here (`.thoughts`, `.tool_calls`, `SubagentConfig`) is young.
- `SubagentConfig` and `SubagentCapabilities` import from `google.antigravity.types`, **not** the package root. Verified against the installed wheel.
- Dugout runs on port **8002**. Game stack is Vite :5173, ADK coach :8000, captain A2A :8001.
- **Never use the em dash `—` anywhere**, in code, comments, copy, commit messages or docs. Use a plain dash `-`.
- Player attribute values are **floats in 0.0-1.0**, except `tackleCooldown` (ms, ~800), `decisionDelay` (ms, ~80) and `recoverySpeedMultiplier` (~1.2). Attribute counts differ per role (defender 30, midfielder 42, forward 32, goalkeeper 53), so never hardcode a count. Any "0-100" values in the mockup are illustrative and wrong.
- The attribute allowlist is derived at runtime from `game/frontend/public/player_state/<role>_baseline.json`. Do **not** reuse the hardcoded default dicts in `game/agents/specialist_agents/tools.py:33-190` or `dugout/app.py:187-335`; they disagree with each other and with disk.
- The four valid roles are `defender`, `midfielder`, `forward`, `goalkeeper`.
- In the UI, amber is reserved for Antigravity and nothing else; cyan is reserved for the game's own agent chain. See the Interface section of the spec.
- Scope is stages 1, 2, 3 and 4a. **Stage 4b is out of scope for this plan**, and so is the `update_profile` hardening in `game/agents/specialist_agents/tools.py`, which is a 4b prerequisite.

**Reference documents:**
- Spec: `docs/superpowers/specs/2026-07-30-antigravity-dugout-design.md`
- Approved UI mockup: `docs/superpowers/specs/assets/dugout-mockup.html`

---

## File Structure

**Create:**

| File | Responsibility |
| --- | --- |
| `dugout/attributes.py` | Derive the per-role attribute allowlist and valid ranges from the baseline JSON |
| `dugout/tools/__init__.py` | Re-export the curated tools |
| `dugout/tools/match.py` | `get_match_status()`, `read_player_stats()` |
| `dugout/tools/tuning.py` | `tune_defender/midfielder/forward/goalkeeper()` and the shared validator |
| `dugout/tools/avatars.py` | `generate_team_avatars()` wrapping `prompts.py` + `utils.py` |
| `dugout/stages.py` | The four in-scope stages as data, with pure done-predicates |
| `dugout/subagents.py` | Four `SubagentConfig` definitions, one tool each |
| `dugout/instructions.md` | Agent system instructions |
| `dugout/session.py` | Agent lifecycle, the event multiplexer, actor attribution |
| `dugout/static/chat.css` | Styles lifted from the approved mockup |
| `dugout/static/chat.js` | SSE client and trajectory renderer |
| `dugout/tests/*` | pytest suite |
| `game/frontend/src/status.js` | `createStatusHook(getGame)` - testable, no DOM |
| `game/frontend/test/futsal-status.test.js` | vitest regression test |
| `docs/superpowers/SMOKE.md` | Manual end-to-end checklist |

**Modify:**

| File | Change |
| --- | --- |
| `dugout/pyproject.toml` | Add pinned SDK, playwright, pytest dev group |
| `dugout/app.py` | Full rewrite to four routes |
| `dugout/utils.py:59-67` | Delete `get_index_html` |
| `dugout/static/index.html` | Replace with the mockup's markup |
| `dugout/run.sh` | Add preflight |
| `game/frontend/src/main.js:20,699` | Install `window.__futsal` |

**Unchanged:** `dugout/prompts.py`, all of `game/agents/`, `game/frontend/src/game.js`.

---

### Task 1: Dependencies, test tooling, and the attribute schema

Everything downstream validates against this, so it lands first.

**Files:**
- Modify: `dugout/pyproject.toml`
- Create: `dugout/attributes.py`
- Create: `dugout/tests/__init__.py` (empty), `dugout/tests/test_attributes.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ROLES: tuple[str, ...]` = `("defender", "midfielder", "forward", "goalkeeper")`
  - `PLAYER_STATE_DIR: Path`
  - `range_for(attribute: str) -> tuple[float, float]`
  - `allowed_attributes(role: str) -> frozenset[str]` - raises `ValueError` on unknown role
  - `validate_changes(role: str, changes: dict[str, float]) -> list[str]` - returns human-readable violation strings, empty list means valid

- [ ] **Step 1: Add dependencies**

In `dugout/pyproject.toml`, add to `dependencies`:

```toml
    "google-antigravity==0.1.9",
    "playwright",
```

and append a dev group plus pytest config:

```toml
[dependency-groups]
dev = [
    "pytest==8.4.2",
    "pytest-asyncio==1.2.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

Run: `cd dugout && uv sync --all-groups`
Expected: resolves, installs `google-antigravity 0.1.9`.

- [ ] **Step 2: Write the failing test**

Create `dugout/tests/test_attributes.py`:

```python
import pytest

from attributes import ROLES, allowed_attributes, range_for, validate_changes


def test_roles_are_the_four_players():
    assert ROLES == ("defender", "midfielder", "forward", "goalkeeper")


def test_allowlist_comes_from_the_baseline_file():
    keys = allowed_attributes("forward")
    assert "finishing" in keys
    assert "shotPower" in keys
    assert "notARealAttribute" not in keys


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError, match="unknown role"):
        allowed_attributes("striker")


def test_unit_attributes_range_zero_to_one():
    assert range_for("finishing") == (0.0, 1.0)


def test_millisecond_attributes_have_their_own_range():
    assert range_for("tackleCooldown") == (100.0, 2000.0)
    assert range_for("decisionDelay") == (0.0, 500.0)
    assert range_for("recoverySpeedMultiplier") == (0.5, 2.0)


def test_valid_changes_produce_no_violations():
    assert validate_changes("forward", {"finishing": 0.8}) == []


def test_unknown_attribute_is_a_violation():
    violations = validate_changes("forward", {"nope": 0.5})
    assert len(violations) == 1
    assert "nope" in violations[0]


def test_out_of_range_value_is_a_violation():
    violations = validate_changes("forward", {"finishing": 1.4})
    assert len(violations) == 1
    assert "1.4" in violations[0]
    assert "0.0" in violations[0] and "1.0" in violations[0]


def test_non_numeric_value_is_a_violation():
    violations = validate_changes("forward", {"finishing": "fast"})
    assert len(violations) == 1
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `cd dugout && uv run pytest tests/test_attributes.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'attributes'`

- [ ] **Step 4: Implement**

Create `dugout/attributes.py`:

```python
"""Per-role attribute allowlist and ranges, derived from the game's baselines."""

import json
from pathlib import Path

ROLES = ("defender", "midfielder", "forward", "goalkeeper")

PLAYER_STATE_DIR = (
    Path(__file__).resolve().parent.parent
    / "game" / "frontend" / "public" / "player_state"
)

# Everything is a 0.0-1.0 weight except these three, which carry real units.
_EXPLICIT_RANGES = {
    "tackleCooldown": (100.0, 2000.0),
    "decisionDelay": (0.0, 500.0),
    "recoverySpeedMultiplier": (0.5, 2.0),
}
_UNIT_RANGE = (0.0, 1.0)


def range_for(attribute: str) -> tuple[float, float]:
    return _EXPLICIT_RANGES.get(attribute, _UNIT_RANGE)


def allowed_attributes(role: str) -> frozenset[str]:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}, expected one of {ROLES}")
    baseline = PLAYER_STATE_DIR / f"{role}_baseline.json"
    return frozenset(json.loads(baseline.read_text()))


def validate_changes(role: str, changes: dict) -> list[str]:
    allowed = allowed_attributes(role)
    violations = []
    for key, value in changes.items():
        if key not in allowed:
            violations.append(f"{key!r} is not an attribute of the {role}")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            violations.append(f"{key} must be a number, got {value!r}")
            continue
        low, high = range_for(key)
        if not low <= value <= high:
            violations.append(f"{key}={value} is outside {low} to {high}")
    return violations
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `cd dugout && uv run pytest tests/test_attributes.py -v`
Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add dugout/pyproject.toml dugout/uv.lock dugout/attributes.py dugout/tests/
git commit -m "Add dugout attribute schema derived from game baselines"
```

---

### Task 2: Match reading tools

**Files:**
- Create: `dugout/tools/__init__.py`, `dugout/tools/match.py`
- Create: `dugout/tests/test_match_tools.py`

**Interfaces:**
- Consumes: `attributes.ROLES`, `attributes.PLAYER_STATE_DIR`, `attributes.range_for`
- Produces:
  - `STATUS_FILE: Path` = `Path("/tmp/futsal_status.json")`
  - `CALLED: set[str]` - names of tools invoked this session, so `stages.py` can tell that the agent actually read the game
  - `STATUS_MAX_AGE_SEC: float` = `15.0`
  - `status_is_fresh() -> bool` - True when `STATUS_FILE` was written within `STATUS_MAX_AGE_SEC`
  - `get_match_status() -> dict` - either `{"error": "game_not_running"}` or `{"score1": int, "score2": int, "matchTime": float, "gameActive": bool}`
  - `read_player_stats(role: str | None = None) -> dict` - `{role: {attribute: {"value": float, "min": float, "max": float}}}`

Both return typed dicts rather than raising, so the agent can read the failure and tell the user to run `game/run.sh`.

- [ ] **Step 1: Write the failing test**

Create `dugout/tests/test_match_tools.py`:

```python
import json

import pytest

from tools import match


def test_status_reports_game_not_running_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(match, "STATUS_FILE", tmp_path / "missing.json")
    assert match.get_match_status() == {"error": "game_not_running"}


def test_status_reports_game_not_running_on_null_payload(tmp_path, monkeypatch):
    f = tmp_path / "status.json"
    f.write_text("null")
    monkeypatch.setattr(match, "STATUS_FILE", f)
    assert match.get_match_status() == {"error": "game_not_running"}


def test_status_reports_game_not_running_on_corrupt_payload(tmp_path, monkeypatch):
    f = tmp_path / "status.json"
    f.write_text("{not json")
    monkeypatch.setattr(match, "STATUS_FILE", f)
    assert match.get_match_status() == {"error": "game_not_running"}


def test_status_passes_through_a_live_match(tmp_path, monkeypatch):
    f = tmp_path / "status.json"
    f.write_text(json.dumps(
        {"score1": 2, "score2": 1, "matchTime": 41.5, "gameActive": True}))
    monkeypatch.setattr(match, "STATUS_FILE", f)
    assert match.get_match_status() == {
        "score1": 2, "score2": 1, "matchTime": 41.5, "gameActive": True}


def test_read_player_stats_returns_all_four_roles():
    stats = match.read_player_stats()
    assert set(stats) == {"defender", "midfielder", "forward", "goalkeeper"}


def test_read_player_stats_includes_the_valid_range():
    entry = match.read_player_stats("forward")["forward"]["finishing"]
    assert entry["min"] == 0.0
    assert entry["max"] == 1.0
    assert isinstance(entry["value"], (int, float))


def test_read_player_stats_rejects_an_unknown_role():
    with pytest.raises(ValueError, match="unknown role"):
        match.read_player_stats("striker")
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `cd dugout && uv run pytest tests/test_match_tools.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'tools'`

- [ ] **Step 3: Implement**

Create `dugout/tools/__init__.py`:

```python
from tools.match import get_match_status, read_player_stats

__all__ = ["get_match_status", "read_player_stats"]
```

Create `dugout/tools/match.py`:

```python
"""Read-only views of the running match, for the agent."""

import json
from pathlib import Path

from attributes import PLAYER_STATE_DIR, ROLES, range_for

STATUS_FILE = Path("/tmp/futsal_status.json")

# Reading the game is not observable on disk, so the stage predicate needs the
# tools to say they ran. Reset by the app on a fresh session.
CALLED: set[str] = set()


def get_match_status() -> dict:
    """Return the live score and clock, or an error the agent can act on.

    The status file is written by the agent's own Playwright script, which polls
    window.__futsal.status(). No file means no match is being played.
    """
    CALLED.add("get_match_status")
    try:
        payload = json.loads(STATUS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"error": "game_not_running"}
    if not isinstance(payload, dict):
        return {"error": "game_not_running"}
    return payload


def read_player_stats(role: str | None = None) -> dict:
    """Return current attributes with the range each one must stay inside."""
    if role is not None and role not in ROLES:
        raise ValueError(f"unknown role {role!r}, expected one of {ROLES}")
    CALLED.add("read_player_stats")
    wanted = (role,) if role else ROLES
    stats = {}
    for name in wanted:
        profile = json.loads((PLAYER_STATE_DIR / f"{name}.json").read_text())
        low_high = {k: range_for(k) for k in profile}
        stats[name] = {
            k: {"value": v, "min": low_high[k][0], "max": low_high[k][1]}
            for k, v in profile.items()
        }
    return stats
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `cd dugout && uv run pytest tests/test_match_tools.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add dugout/tools/ dugout/tests/test_match_tools.py
git commit -m "Add curated match status and player stat tools"
```

---

### Task 3: Per-role tuning tools

This is where the subagent guardrail lives. Each subagent gets exactly one of these four functions, so `forward-tuner` is structurally incapable of writing `defender.json`. The tool name is also how `session.py` attributes the action, since the SDK exposes no subagent identity.

**Files:**
- Create: `dugout/tools/tuning.py`
- Modify: `dugout/tools/__init__.py`
- Create: `dugout/tests/test_tuning_tools.py`

**Interfaces:**
- Consumes: `attributes.validate_changes`, `attributes.PLAYER_STATE_DIR`
- Produces:
  - `MAX_ATTRIBUTES_PER_CALL: int` = `3`
  - `tune_defender(changes: dict, reason: str) -> dict`
  - `tune_midfielder(changes: dict, reason: str) -> dict`
  - `tune_forward(changes: dict, reason: str) -> dict`
  - `tune_goalkeeper(changes: dict, reason: str) -> dict`
  - `TUNING_TOOL_BY_ROLE: dict[str, callable]`
  - `ROLE_BY_TUNING_TOOL: dict[str, str]` - maps `"tune_forward"` to `"forward"`, used by `session.py` for attribution

Each returns `{"ok": True, "role": ..., "applied": {...}, "reason": ...}` or `{"ok": False, "role": ..., "violations": [...]}`. Never raises for bad input: the violation list goes into the trajectory and the subagent can retry.

- [ ] **Step 1: Write the failing test**

Create `dugout/tests/test_tuning_tools.py`:

```python
import json

import pytest

from tools import tuning


@pytest.fixture
def state(tmp_path, monkeypatch):
    baseline = {"finishing": 0.5, "shotPower": 0.5, "pace": 0.5,
                "aggression": 0.5, "decisionDelay": 80}
    for name in ("forward", "defender", "midfielder", "goalkeeper"):
        (tmp_path / f"{name}.json").write_text(json.dumps(baseline))
        (tmp_path / f"{name}_baseline.json").write_text(json.dumps(baseline))
    monkeypatch.setattr(tuning, "PLAYER_STATE_DIR", tmp_path)
    monkeypatch.setattr("attributes.PLAYER_STATE_DIR", tmp_path)
    return tmp_path


def test_a_valid_change_is_written_to_disk(state):
    result = tuning.tune_forward({"finishing": 0.9}, "needs a goal")
    assert result["ok"] is True
    assert result["applied"] == {"finishing": 0.9}
    assert json.loads((state / "forward.json").read_text())["finishing"] == 0.9


def test_untouched_attributes_survive(state):
    tuning.tune_forward({"finishing": 0.9}, "needs a goal")
    profile = json.loads((state / "forward.json").read_text())
    assert profile["shotPower"] == 0.5


def test_more_than_three_attributes_is_refused(state):
    result = tuning.tune_forward(
        {"finishing": 0.6, "shotPower": 0.6, "pace": 0.6, "aggression": 0.6},
        "everything")
    assert result["ok"] is False
    assert "at most 3" in result["violations"][0]
    assert json.loads((state / "forward.json").read_text())["finishing"] == 0.5


def test_out_of_range_is_refused_and_nothing_is_written(state):
    result = tuning.tune_forward({"finishing": 2.0}, "score more")
    assert result["ok"] is False
    assert json.loads((state / "forward.json").read_text())["finishing"] == 0.5


def test_a_missing_reason_is_refused(state):
    result = tuning.tune_forward({"finishing": 0.9}, "   ")
    assert result["ok"] is False
    assert "reason" in result["violations"][0]


def test_each_tool_only_writes_its_own_file(state):
    tuning.tune_defender({"aggression": 0.9}, "hold the line")
    assert json.loads((state / "defender.json").read_text())["aggression"] == 0.9
    assert json.loads((state / "forward.json").read_text())["aggression"] == 0.5


def test_tool_name_maps_back_to_role():
    assert tuning.ROLE_BY_TUNING_TOOL["tune_forward"] == "forward"
    assert set(tuning.ROLE_BY_TUNING_TOOL.values()) == {
        "defender", "midfielder", "forward", "goalkeeper"}
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `cd dugout && uv run pytest tests/test_tuning_tools.py -v`
Expected: FAIL, `ImportError: cannot import name 'tuning'`

- [ ] **Step 3: Implement**

Create `dugout/tools/tuning.py`:

```python
"""One tuning tool per role.

Each subagent is given exactly one of these, so a subagent cannot write another
player's file even if it wants to. The tool name is also the actor identity the
trajectory renders, because the SDK exposes no subagent id.
"""

import json

from attributes import PLAYER_STATE_DIR, validate_changes

MAX_ATTRIBUTES_PER_CALL = 3


def _tune(role: str, changes: dict, reason: str) -> dict:
    violations = []
    if not isinstance(changes, dict) or not changes:
        violations.append("changes must be a non-empty object")
    elif len(changes) > MAX_ATTRIBUTES_PER_CALL:
        violations.append(
            f"change at most {MAX_ATTRIBUTES_PER_CALL} attributes per call, "
            f"got {len(changes)}")
    if not isinstance(reason, str) or not reason.strip():
        violations.append("a reason is required, so the change is legible")
    if not violations:
        violations = validate_changes(role, changes)
    if violations:
        return {"ok": False, "role": role, "violations": violations}

    path = PLAYER_STATE_DIR / f"{role}.json"
    profile = json.loads(path.read_text())
    profile.update(changes)
    path.write_text(json.dumps(profile, indent=2))
    return {"ok": True, "role": role, "applied": changes, "reason": reason.strip()}


def tune_defender(changes: dict, reason: str) -> dict:
    """Change up to 3 of the defender's attributes. Say why."""
    return _tune("defender", changes, reason)


def tune_midfielder(changes: dict, reason: str) -> dict:
    """Change up to 3 of the midfielder's attributes. Say why."""
    return _tune("midfielder", changes, reason)


def tune_forward(changes: dict, reason: str) -> dict:
    """Change up to 3 of the forward's attributes. Say why."""
    return _tune("forward", changes, reason)


def tune_goalkeeper(changes: dict, reason: str) -> dict:
    """Change up to 3 of the goalkeeper's attributes. Say why."""
    return _tune("goalkeeper", changes, reason)


TUNING_TOOL_BY_ROLE = {
    "defender": tune_defender,
    "midfielder": tune_midfielder,
    "forward": tune_forward,
    "goalkeeper": tune_goalkeeper,
}

ROLE_BY_TUNING_TOOL = {fn.__name__: role for role, fn in TUNING_TOOL_BY_ROLE.items()}
```

Update `dugout/tools/__init__.py`:

```python
from tools.match import get_match_status, read_player_stats
from tools.tuning import (
    ROLE_BY_TUNING_TOOL,
    TUNING_TOOL_BY_ROLE,
    tune_defender,
    tune_forward,
    tune_goalkeeper,
    tune_midfielder,
)

__all__ = [
    "get_match_status", "read_player_stats",
    "tune_defender", "tune_midfielder", "tune_forward", "tune_goalkeeper",
    "TUNING_TOOL_BY_ROLE", "ROLE_BY_TUNING_TOOL",
]
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `cd dugout && uv run pytest tests/test_tuning_tools.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add dugout/tools/ dugout/tests/test_tuning_tools.py
git commit -m "Add per-role tuning tools with server-side guardrails"
```

---

### Task 4: Avatar generation tool

**Files:**
- Create: `dugout/tools/avatars.py`
- Modify: `dugout/tools/__init__.py`
- Create: `dugout/tests/test_avatars.py`
- Modify: `dugout/utils.py` (delete `get_index_html`, lines 59-67)

**Interfaces:**
- Consumes: `prompts.get_player_prompt`, `prompts.get_goalkeeper_prompt`, `utils.extract_image_bytes`, `utils.process_avatar_image`, `utils.save_and_encode_image`
- Produces:
  - `class AvatarGenerationError(RuntimeError)`
  - `generate_team_avatars(team: str, color: str, logo: str, style: str) -> dict` - `{"team": ..., "sprite_sheet": "<path>", "goalkeeper": "<path>"}`
  - `SPRITE_DIR: Path`
  - `_client()` - lazy genai client accessor, so importing the module never needs credentials

`team` is `"blue"` or `"red"`. Raises `AvatarGenerationError` when the model returns no image, which surfaces as a tool error in the trajectory and leaves the stage incomplete and retryable.

- [ ] **Step 1: Write the failing test**

Create `dugout/tests/test_avatars.py`:

```python
import pytest

from tools import avatars


class FakeResponse:
    pass


def test_unknown_team_is_rejected():
    with pytest.raises(ValueError, match="unknown team"):
        avatars.generate_team_avatars("green", "blue", "star", "spiky hair")


def test_no_image_from_the_model_raises_a_typed_error(monkeypatch):
    monkeypatch.setattr(avatars, "_client", lambda: object())
    monkeypatch.setattr(avatars, "_generate_one", lambda *a, **k: None)
    with pytest.raises(avatars.AvatarGenerationError, match="no image"):
        avatars.generate_team_avatars("blue", "black", "wolf", "blond hair")


def test_a_successful_run_reports_both_written_paths(monkeypatch):
    calls = []

    def fake_generate_one(client, prompt, filename, make_default_gk):
        calls.append((filename, make_default_gk))
        return f"/sprites/{filename}"

    monkeypatch.setattr(avatars, "_client", lambda: object())
    monkeypatch.setattr(avatars, "_generate_one", fake_generate_one)

    result = avatars.generate_team_avatars("blue", "black", "wolf", "blond hair")

    assert result["team"] == "blue"
    assert result["sprite_sheet"] == "/sprites/player_blue_team.png"
    assert result["goalkeeper"] == "/sprites/goalkeeper_blue_team.png"
    assert calls == [("player_blue_team.png", False),
                     ("goalkeeper_blue_team.png", True)]


def test_the_opponent_keeper_does_not_become_the_default(monkeypatch):
    calls = []
    monkeypatch.setattr(avatars, "_client", lambda: object())
    monkeypatch.setattr(avatars, "_generate_one",
                        lambda c, p, filename, make_default_gk: (
                            calls.append((filename, make_default_gk)),
                            f"/sprites/{filename}")[1])
    avatars.generate_team_avatars("red", "white", "tiger", "dark hair")
    assert calls == [("player_red_team.png", False),
                     ("goalkeeper_red_team.png", False)]
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `cd dugout && uv run pytest tests/test_avatars.py -v`
Expected: FAIL, `ImportError: cannot import name 'avatars'`

- [ ] **Step 3: Implement**

Create `dugout/tools/avatars.py`:

```python
"""Team rebranding. Owns image generation so the chroma-key pipeline always runs."""

from pathlib import Path

from google import genai

import prompts
import utils

SPRITE_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "game" / "frontend" / "public" / "assets" / "sprites"
)
SPRITE_SIZE = (1408, 768)
TEAMS = ("blue", "red")

_CLIENT = None


class AvatarGenerationError(RuntimeError):
    """The model returned a response with no usable image."""


def _client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = genai.Client()
    return _CLIENT


def _generate_one(client, prompt: str, filename: str, make_default_gk: bool):
    response = client.models.generate_content(
        model="gemini-2.5-flash-image", contents=prompt)
    image_bytes = utils.extract_image_bytes(response)
    if not image_bytes:
        return None
    image = utils.process_avatar_image(image_bytes, SPRITE_SIZE)
    utils.save_and_encode_image(image, filename, str(SPRITE_DIR),
                                make_default_gk=make_default_gk)
    return str(SPRITE_DIR / filename)


def generate_team_avatars(team: str, color: str, logo: str, style: str) -> dict:
    """Regenerate one team's outfield sprite sheet and goalkeeper.

    Args:
      team: "blue" for our side or "red" for the opponent.
      color: jersey colour, for example "black".
      logo: crest description, for example "gold wolf head".
      style: visual detail, for example "short blond hair".
    """
    if team not in TEAMS:
        raise ValueError(f"unknown team {team!r}, expected one of {TEAMS}")

    client = _client()
    SPRITE_DIR.mkdir(parents=True, exist_ok=True)

    outfield = _generate_one(
        client, prompts.get_player_prompt(color, logo, style),
        f"player_{team}_team.png", False)
    if outfield is None:
        raise AvatarGenerationError(
            f"the model returned no image for the {team} outfield players")

    # Only our own keeper becomes the fallback goalkeeper.png; the opponent's
    # must not overwrite it.
    keeper = _generate_one(
        client, prompts.get_goalkeeper_prompt(color, logo, style),
        f"goalkeeper_{team}_team.png", team == "blue")
    if keeper is None:
        raise AvatarGenerationError(
            f"the model returned no image for the {team} goalkeeper")

    return {"team": team, "sprite_sheet": outfield, "goalkeeper": keeper}
```

Replace `dugout/tools/__init__.py` entirely, so it is final for the rest of the plan:

```python
from tools.avatars import AvatarGenerationError, generate_team_avatars
from tools.match import get_match_status, read_player_stats
from tools.tuning import (
    ROLE_BY_TUNING_TOOL,
    TUNING_TOOL_BY_ROLE,
    tune_defender,
    tune_forward,
    tune_goalkeeper,
    tune_midfielder,
)

__all__ = [
    "AvatarGenerationError", "generate_team_avatars",
    "get_match_status", "read_player_stats",
    "tune_defender", "tune_midfielder", "tune_forward", "tune_goalkeeper",
    "TUNING_TOOL_BY_ROLE", "ROLE_BY_TUNING_TOOL",
]
```

Delete `get_index_html` from `dugout/utils.py` (lines 59-67).

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `cd dugout && uv run pytest tests/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dugout/tools/ dugout/utils.py dugout/tests/test_avatars.py
git commit -m "Add curated avatar generation tool and drop get_index_html"
```

---

### Task 5: Stage definitions and done-predicates

**Files:**
- Create: `dugout/stages.py`
- Create: `dugout/tests/test_stages.py`
- Modify: `dugout/tools/match.py` (add `STATUS_MAX_AGE_SEC` and `status_is_fresh`, and make `get_match_status` refuse a stale file)
- Modify: `dugout/tests/test_match_tools.py` (one test for the stale case)

**Amendment to Task 2.** `get_match_status` currently trusts any parseable status file. The file is written continuously by the agent's Playwright script, so a frozen one means that script died: the score is history, not the live match. Reading it would have the agent tune against a match that already ended. Add to `dugout/tools/match.py`:

```python
STATUS_MAX_AGE_SEC = 15.0


def status_is_fresh() -> bool:
    """A live match rewrites the status file constantly; a frozen one is dead."""
    try:
        return (time.time() - STATUS_FILE.stat().st_mtime) <= STATUS_MAX_AGE_SEC
    except OSError:
        return False
```

with `import time` at the top, and make `get_match_status` return the same typed error when the file is stale, immediately after the `CALLED.add(...)` line:

```python
    if not status_is_fresh():
        return {"error": "game_not_running"}
```

Add this test to `dugout/tests/test_match_tools.py`:

```python
def test_status_reports_game_not_running_when_the_file_is_stale(tmp_path, monkeypatch):
    import os
    f = tmp_path / "status.json"
    f.write_text(json.dumps({"score1": 1, "score2": 0, "gameActive": True}))
    old = time.time() - (match.STATUS_MAX_AGE_SEC + 30)
    os.utime(f, (old, old))
    monkeypatch.setattr(match, "STATUS_FILE", f)
    assert match.get_match_status() == {"error": "game_not_running"}
```

with `import time` added to that test file. The four existing status tests write their file immediately before reading it, so they stay fresh and keep passing.

**Interfaces:**
- Consumes: `tools.match.STATUS_FILE`, `tools.avatars.SPRITE_DIR`, `attributes.PLAYER_STATE_DIR`, `attributes.ROLES`
- Produces:
  - `@dataclass(frozen=True) Stage` with fields `id: str`, `title: str`, `blurb: str`, `suggested: str`, `is_done: Callable[[], bool]`
  - `STAGES: tuple[Stage, ...]` - ids `rebrand`, `take_the_field`, `read_the_game`, `tune_the_squad`
  - `stage_status() -> list[dict]` - `[{"id", "title", "blurb", "suggested", "done"}]`

Predicates are pure functions over filesystem state, so they test without an agent. `tune_the_squad` is done when any role file differs from its baseline.

- [ ] **Step 1: Write the failing test**

Create `dugout/tests/test_stages.py`:

```python
import json
import os
import time

import pytest

import stages


@pytest.fixture
def fake_fs(tmp_path, monkeypatch):
    sprites = tmp_path / "sprites"
    sprites.mkdir()
    state = tmp_path / "player_state"
    state.mkdir()
    for name in ("defender", "midfielder", "forward", "goalkeeper"):
        payload = json.dumps({"pace": 0.5})
        (state / f"{name}.json").write_text(payload)
        (state / f"{name}_baseline.json").write_text(payload)
    monkeypatch.setattr(stages, "SPRITE_DIR", sprites)
    monkeypatch.setattr(stages, "PLAYER_STATE_DIR", state)
    monkeypatch.setattr(stages, "STATUS_FILE", tmp_path / "status.json")
    return tmp_path


def test_four_stages_in_scope():
    assert [s.id for s in stages.STAGES] == [
        "rebrand", "take_the_field", "read_the_game", "tune_the_squad"]


def test_every_stage_has_a_suggested_prompt():
    assert all(s.suggested.strip() for s in stages.STAGES)


def test_no_em_dash_in_any_stage_copy():
    for s in stages.STAGES:
        assert "—" not in (s.title + s.blurb + s.suggested)


def test_rebrand_needs_both_sprite_sheets(fake_fs, monkeypatch):
    monkeypatch.setattr(stages, "STARTED_AT", 0)
    by_id = {s.id: s for s in stages.STAGES}
    assert by_id["rebrand"].is_done() is False
    (fake_fs / "sprites" / "player_blue_team.png").write_bytes(b"x")
    assert by_id["rebrand"].is_done() is False
    (fake_fs / "sprites" / "player_red_team.png").write_bytes(b"x")
    assert by_id["rebrand"].is_done() is True


def test_sprites_that_predate_this_session_do_not_count(fake_fs, monkeypatch):
    for team in ("blue", "red"):
        (fake_fs / "sprites" / f"player_{team}_team.png").write_bytes(b"x")
    # The repo ships sprites; only a rewrite during this session counts.
    monkeypatch.setattr(stages, "STARTED_AT", time.time() + 60)
    by_id = {s.id: s for s in stages.STAGES}
    assert by_id["rebrand"].is_done() is False


def test_read_the_game_needs_the_stats_tool_to_have_run(fake_fs, monkeypatch):
    monkeypatch.setattr(stages.match, "CALLED", set())
    by_id = {s.id: s for s in stages.STAGES}
    assert by_id["read_the_game"].is_done() is False
    stages.match.CALLED.add("read_player_stats")
    assert by_id["read_the_game"].is_done() is True


def test_take_the_field_needs_a_live_status_file(fake_fs, monkeypatch):
    monkeypatch.setattr(stages.match, "STATUS_FILE", fake_fs / "status.json")
    by_id = {s.id: s for s in stages.STAGES}
    assert by_id["take_the_field"].is_done() is False
    (fake_fs / "status.json").write_text(json.dumps({"score1": 0, "score2": 0}))
    assert by_id["take_the_field"].is_done() is True


def test_a_stale_status_file_does_not_count_as_being_on_the_field(fake_fs, monkeypatch):
    f = fake_fs / "status.json"
    f.write_text(json.dumps({"score1": 1, "score2": 0}))
    old = time.time() - (stages.match.STATUS_MAX_AGE_SEC + 30)
    os.utime(f, (old, old))
    monkeypatch.setattr(stages.match, "STATUS_FILE", f)
    by_id = {s.id: s for s in stages.STAGES}
    assert by_id["take_the_field"].is_done() is False


def test_tune_is_done_once_a_role_file_is_rewritten_this_session(fake_fs, monkeypatch):
    monkeypatch.setattr(stages, "STARTED_AT", time.time())
    by_id = {s.id: s for s in stages.STAGES}
    assert by_id["tune_the_squad"].is_done() is False
    (fake_fs / "player_state" / "forward.json").write_text(json.dumps({"pace": 0.9}))
    assert by_id["tune_the_squad"].is_done() is True


def test_role_files_shipped_by_the_repo_do_not_count_as_tuned(fake_fs, monkeypatch):
    # Three of the four shipped role files already differ from their baselines,
    # so a content comparison would read as done on a clean checkout.
    monkeypatch.setattr(stages, "STARTED_AT", time.time() + 60)
    by_id = {s.id: s for s in stages.STAGES}
    assert by_id["tune_the_squad"].is_done() is False


def test_stage_status_is_json_serialisable(fake_fs):
    payload = stages.stage_status()
    json.dumps(payload)
    assert {"id", "title", "blurb", "suggested", "done"} == set(payload[0])
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `cd dugout && uv run pytest tests/test_stages.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'stages'`

- [ ] **Step 3: Implement**

Create `dugout/stages.py`:

```python
"""The quest, as data. Predicates are pure functions over filesystem state."""

import json
import time
from dataclasses import dataclass
from typing import Callable

from attributes import PLAYER_STATE_DIR, ROLES
from tools import match
from tools.avatars import SPRITE_DIR
from tools.match import STATUS_FILE


# Stages describe this session's progress, not the repository's contents.
STARTED_AT = time.time()


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    blurb: str
    suggested: str
    is_done: Callable[[], bool]


def _rebranded() -> bool:
    """True once both sprite sheets have been rewritten during this session.

    The repository ships working sprites, so existence proves nothing. Only a
    write newer than process start means the manager actually rebranded.
    """
    for team in ("blue", "red"):
        sheet = SPRITE_DIR / f"player_{team}_team.png"
        if not sheet.exists() or sheet.stat().st_mtime < STARTED_AT:
            return False
    return True


def _on_the_field() -> bool:
    """True only while a match is actually being played right now.

    A status file left over from an earlier run would otherwise make this
    stage look complete before the agent had done anything.
    """
    return match.status_is_fresh() and "error" not in match.get_match_status()


def _scouted() -> bool:
    # Reading the game leaves no trace on disk, so the tool records its own use.
    return "read_player_stats" in match.CALLED


def _tuned() -> bool:
    """True once a role file has been rewritten during this session.

    Comparing against the baseline does not work: the repository ships live
    files that already differ from their baselines for three of the four
    roles, so a content diff reads as done before anything has happened.
    """
    for role in ROLES:
        live = PLAYER_STATE_DIR / f"{role}.json"
        if live.exists() and live.stat().st_mtime >= STARTED_AT:
            return True
    return False


STAGES = (
    Stage(
        id="rebrand",
        title="Rebrand the team",
        blurb="Generate new player sprites and put your own crest on the shirt.",
        suggested="Kit us out in black and gold with a wolf crest.",
        is_done=_rebranded,
    ),
    Stage(
        id="take_the_field",
        title="Take the field",
        blurb="Antigravity writes its own Playwright script and starts a match.",
        suggested="Now get us on the pitch and keep the score where you can see it.",
        is_done=_on_the_field,
    ),
    Stage(
        id="read_the_game",
        title="Read the game",
        blurb="Read the live score and the squad's current attributes.",
        suggested="How are we doing, and where are we losing it?",
        is_done=_scouted,
    ),
    Stage(
        id="tune_the_squad",
        title="Tune the squad",
        blurb="Four subagents, one player file each. Changes land within two seconds.",
        suggested="They keep breaking through the middle. Tighten it up.",
        is_done=_tuned,
    ),
)


def stage_status() -> list[dict]:
    return [
        {"id": s.id, "title": s.title, "blurb": s.blurb,
         "suggested": s.suggested, "done": s.is_done()}
        for s in STAGES
    ]
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `cd dugout && uv run pytest tests/test_stages.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add dugout/stages.py dugout/tests/test_stages.py
git commit -m "Add stage definitions with pure done-predicates"
```

---

### Task 6: The event multiplexer

The riskiest component and the easiest to isolate. It gets its own task and its own stub. No SDK calls here: the multiplexer takes any object exposing the three async iterators.

**Files:**
- Create: `dugout/session.py` (multiplexer only; the agent lifecycle lands in Task 7)
- Create: `dugout/tests/test_multiplexer.py`

**Interfaces:**
- Consumes: `tools.tuning.ROLE_BY_TUNING_TOOL`
- Produces:
  - `ACTOR_USER = "user"`, `ACTOR_AGENT = "antigravity"`
  - `actor_for_tool_call(name: str) -> str` - `"subagent:forward-tuner"` for `tune_forward`, else `ACTOR_AGENT`
  - `async multiplex(response) -> AsyncIterator[dict]` - yields `{"kind", "actor", "data"}` dicts, then one `usage` event, and never raises out of a pump

Event kinds: `thought`, `tool_call`, `text`, `usage`, `error`.

- [ ] **Step 1: Write the failing test**

Create `dugout/tests/test_multiplexer.py`:

```python
import asyncio

import pytest

import session


class FakeToolCall:
    def __init__(self, name, args=None):
        self.name = name
        self.args = args or {}
        self.id = "call-1"
        self.canonical_path = None
        self.server_name = None


class FakeResponse:
    """Stands in for ChatResponse: three independent async iterators."""

    def __init__(self, thoughts=(), tool_calls=(), chunks=(), usage="u"):
        self._thoughts, self._tool_calls = list(thoughts), list(tool_calls)
        self._chunks, self._usage = list(chunks), usage

    async def _drain(self, items, delay):
        for item in items:
            await asyncio.sleep(delay)
            yield item

    @property
    def thoughts(self):
        return self._drain(self._thoughts, 0.001)

    @property
    def tool_calls(self):
        return self._drain(self._tool_calls, 0.002)

    @property
    def chunks(self):
        return self._drain(self._chunks, 0.003)

    @property
    def usage_metadata(self):
        return self._usage


async def collect(response):
    return [e async for e in session.multiplex(response)]


def test_tuning_tool_names_attribute_to_their_subagent():
    assert session.actor_for_tool_call("tune_forward") == "subagent:forward-tuner"
    assert session.actor_for_tool_call("tune_goalkeeper") == "subagent:goalkeeper-tuner"


def test_other_tools_attribute_to_antigravity():
    assert session.actor_for_tool_call("get_match_status") == session.ACTOR_AGENT
    assert session.actor_for_tool_call("run_command") == session.ACTOR_AGENT


async def test_every_event_from_all_three_sources_arrives():
    events = await collect(FakeResponse(
        thoughts=["t1", "t2"],
        tool_calls=[FakeToolCall("get_match_status")],
        chunks=["hello ", "world"]))
    kinds = [e["kind"] for e in events]
    assert kinds.count("thought") == 2
    assert kinds.count("tool_call") == 1
    assert kinds.count("text") == 2
    assert kinds.count("usage") == 1


async def test_usage_is_the_final_event():
    events = await collect(FakeResponse(thoughts=["t"], chunks=["c"]))
    assert events[-1]["kind"] == "usage"


async def test_every_event_carries_an_actor():
    events = await collect(FakeResponse(
        thoughts=["t"], tool_calls=[FakeToolCall("tune_forward")], chunks=["c"]))
    assert all(e["actor"] for e in events)
    by_kind = {e["kind"]: e for e in events}
    assert by_kind["tool_call"]["actor"] == "subagent:forward-tuner"
    assert by_kind["thought"]["actor"] == session.ACTOR_AGENT


async def test_ordering_within_a_single_source_is_preserved():
    events = await collect(FakeResponse(thoughts=["first", "second", "third"]))
    texts = [e["data"] for e in events if e["kind"] == "thought"]
    assert texts == ["first", "second", "third"]


async def test_a_failing_source_becomes_an_error_event_not_a_crash():
    class Exploding(FakeResponse):
        @property
        def thoughts(self):
            async def boom():
                yield "one"
                raise RuntimeError("stream died")
            return boom()

    events = await collect(Exploding(chunks=["still here"]))
    kinds = [e["kind"] for e in events]
    assert "error" in kinds
    assert "text" in kinds
    assert kinds[-1] == "usage"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `cd dugout && uv run pytest tests/test_multiplexer.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'session'`

- [ ] **Step 3: Implement**

Create `dugout/session.py`:

```python
"""Agent lifecycle and the event multiplexer."""

import asyncio

from tools.tuning import ROLE_BY_TUNING_TOOL

ACTOR_USER = "user"
ACTOR_AGENT = "antigravity"

_DONE = object()


def actor_for_tool_call(name: str) -> str:
    """Attribute a tool call to whoever made it.

    The SDK exposes no subagent identity on ToolCall, Thought or ToolResult, so
    the tool name is the handle: each subagent holds exactly one tuning tool.
    """
    role = ROLE_BY_TUNING_TOOL.get(name)
    return f"subagent:{role}-tuner" if role else ACTOR_AGENT


async def _pump(source, kind, queue):
    try:
        async for item in source:
            actor = (actor_for_tool_call(getattr(item, "name", ""))
                     if kind == "tool_call" else ACTOR_AGENT)
            await queue.put({"kind": kind, "actor": actor, "data": item})
    except Exception as exc:  # a dead stream must not kill the other two
        await queue.put({"kind": "error", "actor": ACTOR_AGENT,
                         "data": f"{kind} stream failed: {exc}"})
    finally:
        await queue.put(_DONE)


async def multiplex(response):
    """Fan thoughts, tool calls and text chunks into one ordered timeline."""
    queue: asyncio.Queue = asyncio.Queue()
    sources = (
        (response.thoughts, "thought"),
        (response.tool_calls, "tool_call"),
        (response.chunks, "text"),
    )
    tasks = [asyncio.create_task(_pump(src, kind, queue)) for src, kind in sources]

    remaining = len(tasks)
    try:
        while remaining:
            event = await queue.get()
            if event is _DONE:
                remaining -= 1
                continue
            yield event
    finally:
        for task in tasks:
            task.cancel()

    yield {"kind": "usage", "actor": ACTOR_AGENT,
           "data": getattr(response, "usage_metadata", None)}
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `cd dugout && uv run pytest tests/test_multiplexer.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add dugout/session.py dugout/tests/test_multiplexer.py
git commit -m "Add event multiplexer with tool-name actor attribution"
```

---

### Task 7: Agent lifecycle, subagents, and instructions

**Files:**
- Modify: `dugout/session.py` (append)
- Create: `dugout/subagents.py`
- Create: `dugout/instructions.md`
- Create: `dugout/tests/test_subagents.py`

**Interfaces:**
- Consumes: everything from Tasks 2-4, `session.multiplex`
- Produces:
  - `subagents.SUBAGENTS: tuple[SubagentConfig, ...]` - four entries named `defender-tuner` etc.
  - `session.agent_health() -> dict` - `{"ok": bool, "detail": str}`
  - `session.get_agent()` - starts the agent on first call, caches it, raises `AgentUnavailable`
  - `session.AgentUnavailable(RuntimeError)`

- [ ] **Step 1: Write the failing test**

Create `dugout/tests/test_subagents.py`:

```python
from subagents import SUBAGENTS
from tools.tuning import ROLE_BY_TUNING_TOOL


def test_there_is_one_subagent_per_role():
    assert [s.name for s in SUBAGENTS] == [
        "defender-tuner", "midfielder-tuner", "forward-tuner", "goalkeeper-tuner"]


def test_each_subagent_holds_exactly_one_tuning_tool():
    for sub in SUBAGENTS:
        tuning = [t for t in sub.tools if getattr(t, "__name__", "") in ROLE_BY_TUNING_TOOL]
        assert len(tuning) == 1


def test_a_subagent_cannot_reach_another_role_tool():
    forward = next(s for s in SUBAGENTS if s.name == "forward-tuner")
    names = {getattr(t, "__name__", "") for t in forward.tools}
    assert "tune_forward" in names
    assert "tune_defender" not in names


def test_subagents_can_read_the_match():
    for sub in SUBAGENTS:
        names = {getattr(t, "__name__", "") for t in sub.tools}
        assert "get_match_status" in names
        assert "read_player_stats" in names


def test_no_em_dash_in_subagent_instructions():
    for sub in SUBAGENTS:
        assert "—" not in sub.system_instructions
        assert "—" not in sub.description
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `cd dugout && uv run pytest tests/test_subagents.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'subagents'`

- [ ] **Step 3: Implement subagents**

Create `dugout/subagents.py`:

```python
"""One tuner per role. The tool set is the guardrail: a tuner physically cannot
write another player's file, because it is not given that function."""

from google.antigravity.types import SubagentConfig

from tools.match import get_match_status, read_player_stats
from tools.tuning import TUNING_TOOL_BY_ROLE

_INSTRUCTIONS = (
    "You tune the {role} of the blue team during a live futsal match.\n"
    "Call get_match_status() and read_player_stats('{role}') first, so your "
    "change answers what is actually happening.\n"
    "Then call {tool}() exactly once. Change at most 3 attributes and give a "
    "one-line reason naming what you expect to improve.\n"
    "Every value must stay inside the min and max that read_player_stats "
    "reports for it. Most attributes are 0.0 to 1.0 weights.\n"
    "You cannot edit any other player. Do not try."
)

SUBAGENTS = tuple(
    SubagentConfig(
        name=f"{role}-tuner",
        description=f"Tune the {role} in response to the live match state",
        system_instructions=_INSTRUCTIONS.format(role=role, tool=tool.__name__),
        tools=[get_match_status, read_player_stats, tool],
    )
    for role, tool in TUNING_TOOL_BY_ROLE.items()
)
```

- [ ] **Step 4: Run the subagent tests**

Run: `cd dugout && uv run pytest tests/test_subagents.py -v`
Expected: 5 passed.

- [ ] **Step 5: Write the system instructions**

Create `dugout/instructions.md`:

```markdown
You are the coaching staff in the dugout of Futsal WorldCup. You work for the
person in the chat, who is the manager. You do the work; they decide what they
want.

The repository root is your workspace. The game is a Vite app on
http://localhost:5173 with an ADK coach on :8000 and a team captain on :8001.

What you can do:

1. Rebrand the team. Call generate_team_avatars(). It owns the chroma-key and
   resize pipeline, so never generate images another way.
2. Take the field. There is no browser tool. Write a Playwright script with
   create_file and run it with run_command. Two things will bite you: the
   kick-off button #kick-off-btn carries a CSS pulse animation, so a plain
   click() times out and you must pass force=True; and the score is drawn on a
   canvas, so read it with page.evaluate("window.__futsal.status()"). Have your
   script poll that and write it to /tmp/futsal_status.json so the dugout can
   read it too. status() returns null before kick-off.
3. Read the game. get_match_status() and read_player_stats() tell you the score,
   the clock and every attribute with the range it must stay inside.
4. Tune the squad. Start all four subagents at once: defender-tuner,
   midfielder-tuner, forward-tuner, goalkeeper-tuner. Each owns one player. The
   running game reloads their files within about two seconds, so you can watch
   the effect and go again.

How to work:

- Say what you are about to do in one short sentence before you do it, then do
  it. The manager is watching you work; that is the point.
- Prefer your curated tools over shell commands.
- If a command fails, read the error and fix it yourself. Stop after three
  attempts at the same thing and explain what is blocking you.
- Never use the em dash character. Use a plain dash.
- Keep replies short. You are on a touchline, not writing a report.
```

- [ ] **Step 6: Implement the agent lifecycle**

In `dugout/session.py`, add these to the imports **at the top of the file**, beside the existing `import asyncio`:

```python
import os
from pathlib import Path

from google.antigravity import (
    Agent,
    BuiltinTools,
    CapabilitiesConfig,
    LocalAgentConfig,
)

from subagents import SUBAGENTS
from tools.avatars import generate_team_avatars
from tools.match import get_match_status, read_player_stats
```

Then append the rest to the bottom of the file:

```python
REPO_ROOT = Path(__file__).resolve().parent.parent
_AGENT = None


class AgentUnavailable(RuntimeError):
    """The SDK could not start an agent, almost always because agy is not logged in."""


def _build_config() -> LocalAgentConfig:
    return LocalAgentConfig(
        system_instructions=(Path(__file__).parent / "instructions.md").read_text(),
        capabilities=CapabilitiesConfig(
            enable_subagents=True,
            enabled_tools=[
                BuiltinTools.RUN_COMMAND,
                BuiltinTools.CREATE_FILE,
                BuiltinTools.EDIT_FILE,
                BuiltinTools.VIEW_FILE,
                BuiltinTools.LIST_DIR,
                BuiltinTools.START_SUBAGENT,
                BuiltinTools.FINISH,
            ],
        ),
        tools=[generate_team_avatars, get_match_status, read_player_stats],
        subagents=list(SUBAGENTS),
        workspaces=[str(REPO_ROOT)],
        vertex=True,
        project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION"),
    )


def get_agent():
    global _AGENT
    if _AGENT is None:
        try:
            _AGENT = Agent(_build_config())
        except Exception as exc:
            raise AgentUnavailable(str(exc)) from exc
    return _AGENT


def agent_health() -> dict:
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return {"ok": False,
                "detail": "GOOGLE_CLOUD_PROJECT is not set. Check dugout/.env."}
    try:
        get_agent()
    except AgentUnavailable as exc:
        return {"ok": False,
                "detail": f"Antigravity could not start. Run `agy login` in a "
                          f"terminal, then reload. ({exc})"}
    return {"ok": True, "detail": "ready"}
```

- [ ] **Step 7: Run the full suite**

Run: `cd dugout && uv run pytest tests/ -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add dugout/session.py dugout/subagents.py dugout/instructions.md dugout/tests/test_subagents.py
git commit -m "Add agent lifecycle, four role subagents, and system instructions"
```

---

### Task 8: FastAPI routes

**Files:**
- Modify: `dugout/app.py` (full rewrite, 352 lines down to roughly 70)
- Create: `dugout/tests/test_app.py`

**Interfaces:**
- Consumes: `stages.stage_status`, `session.agent_health`, `session.get_agent`, `session.multiplex`, `session.ACTOR_USER`
- Produces:
  - `app: FastAPI` with `GET /`, `GET /health`, `GET /stages`, `POST /chat`
  - `GAME_SERVICES: dict[str, int]` = `{"pitch": 5173, "coach": 8000, "captain": 8001}`
  - `game_services() -> dict[str, bool]` - the three status dots the header renders

`/health` returns `{"agent": {...}, "game": {"pitch": bool, "coach": bool, "captain": bool}}`. The game check is a bare TCP connect with a short timeout, not an HTTP request, because ADK and the captain server answer different paths and a connect is all the dot needs.

`POST /chat` takes `{"message": str}` and returns `text/event-stream`. Each SSE frame is `event: <kind>` plus `data: <json>` where the JSON is `{"actor": str, "payload": ...}`.

`Agent.chat()` is a plain synchronous call that returns a `ChatResponse` holding three async iterators, so it should return promptly rather than blocking for the whole turn. If it turns out to block, wrap it in `await asyncio.to_thread(agent.chat, message)` and make the endpoint `async def`. Check this the first time a real turn runs.

- [ ] **Step 1: Write the failing test**

Create `dugout/tests/test_app.py`:

```python
from fastapi.testclient import TestClient

import app as app_module


def client(monkeypatch, **overrides):
    for name, value in overrides.items():
        monkeypatch.setattr(app_module, name, value)
    return TestClient(app_module.app)


def test_health_reports_the_agent_state(monkeypatch):
    c = client(monkeypatch,
               agent_health=lambda: {"ok": False, "detail": "no login"},
               game_services=lambda: {"pitch": True, "coach": True, "captain": True})
    body = c.get("/health").json()
    assert body["agent"] == {"ok": False, "detail": "no login"}


def test_health_reports_the_three_game_services(monkeypatch):
    c = client(monkeypatch,
               agent_health=lambda: {"ok": True, "detail": "ready"},
               game_services=lambda: {"pitch": True, "coach": False, "captain": False})
    assert c.get("/health").json()["game"] == {
        "pitch": True, "coach": False, "captain": False}


def test_game_services_reports_false_for_a_closed_port(monkeypatch):
    # 9 is discard, reliably closed on a dev machine.
    monkeypatch.setattr(app_module, "GAME_SERVICES", {"nothing": 9})
    assert app_module.game_services() == {"nothing": False}


def test_stages_returns_the_quest(monkeypatch):
    c = client(monkeypatch, stage_status=lambda: [{"id": "rebrand", "done": True}])
    assert c.get("/stages").json() == [{"id": "rebrand", "done": True}]


def test_chat_rejects_an_empty_message(monkeypatch):
    c = client(monkeypatch)
    assert c.post("/chat", json={"message": "   "}).status_code == 422


def test_chat_streams_events_when_the_agent_is_down(monkeypatch):
    def boom():
        raise app_module.AgentUnavailable("agy is not logged in")

    c = client(monkeypatch, get_agent=boom)
    with c.stream("POST", "/chat", json={"message": "hello"}) as r:
        body = "".join(r.iter_text())
    assert "event: error" in body
    assert "agy is not logged in" in body
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `cd dugout && uv run pytest tests/test_app.py -v`
Expected: FAIL (old `app.py` has no `/health`).

- [ ] **Step 3: Implement**

Replace the entire contents of `dugout/app.py`:

```python
"""Dugout: a chat front end for an in-process Antigravity agent."""

import json
import socket
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from session import ACTOR_USER, AgentUnavailable, agent_health, get_agent, multiplex
from stages import stage_status

load_dotenv()

STATIC_DIR = Path(__file__).parent / "static"
GAME_SERVICES = {"pitch": 5173, "coach": 8000, "captain": 8001}

app = FastAPI(title="Dugout")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def game_services() -> dict:
    """One TCP connect per service. Enough to light the header dots."""
    up = {}
    for name, port in GAME_SERVICES.items():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                up[name] = True
        except OSError:
            up[name] = False
    return up


class ChatRequest(BaseModel):
    message: str

    @field_validator("message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message cannot be blank")
        return value.strip()


def _frame(kind: str, actor: str, payload) -> str:
    body = json.dumps({"actor": actor, "payload": payload}, default=str)
    return f"event: {kind}\ndata: {body}\n\n"


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"agent": agent_health(), "game": game_services()}


@app.get("/stages")
def stages():
    return stage_status()


@app.post("/chat")
def chat(request: ChatRequest):
    async def stream():
        yield _frame("user", ACTOR_USER, request.message)
        try:
            agent = get_agent()
            response = agent.chat(request.message)
        except AgentUnavailable as exc:
            yield _frame("error", "antigravity", str(exc))
            return
        except Exception as exc:
            yield _frame("error", "antigravity", f"the agent failed to start: {exc}")
            return

        try:
            async for event in multiplex(response):
                payload = event["data"]
                if event["kind"] == "tool_call":
                    payload = {"name": payload.name, "args": payload.args}
                yield _frame(event["kind"], event["actor"], payload)
        except Exception as exc:
            yield _frame("error", "antigravity", str(exc))

        yield _frame("stage_done", "antigravity", stage_status())

    return StreamingResponse(stream(), media_type="text/event-stream")
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `cd dugout && uv run pytest tests/ -v`
Expected: all pass. `GET /` is not asserted here because it serves Task 10's markup; that test lands with the UI.

- [ ] **Step 5: Commit**

```bash
git add dugout/app.py dugout/tests/test_app.py
git commit -m "Rewrite dugout app as four routes over an SSE chat endpoint"
```

---

### Task 9: The game's status hook

The score is drawn on the Phaser canvas and unreadable from outside, so the agent needs this. `main.js` builds its DOM by string injection and starts three intervals at import, so it is not importable under jsdom. The logic therefore lives in a tiny standalone module that takes a getter, which makes it testable without Phaser.

**Files:**
- Create: `game/frontend/src/status.js`
- Modify: `game/frontend/src/main.js` (after line 699)
- Create: `game/frontend/test/futsal-status.test.js`

**Interfaces:**
- Produces: `createStatusHook(getGame) -> () => {score1, score2, matchTime, gameActive} | null`

Returns `null` when there is no scene, which is the pre-kick-off state, because `gameInstance` is null until `startPhaserGame()` runs. It reports `gameActive` in the payload rather than gating on it, so the final score is still readable after full time (`game.js:1512` sets `gameActive = false` at game over).

- [ ] **Step 1: Write the failing test**

Create `game/frontend/test/futsal-status.test.js`:

```javascript
import { describe, expect, it } from 'vitest';
import { createStatusHook } from '../src/status.js';

const sceneStub = (fields) => ({
  scene: { getScene: (key) => (key === 'SoccerGameScene' ? fields : null) },
});

describe('createStatusHook', () => {
  it('returns null before kick-off, when no game exists yet', () => {
    expect(createStatusHook(() => null)()).toBeNull();
  });

  it('returns null when the scene is not running', () => {
    const game = { scene: { getScene: () => null } };
    expect(createStatusHook(() => game)()).toBeNull();
  });

  it('reports the live score, clock and active flag', () => {
    const game = sceneStub({ score1: 2, score2: 1, matchTime: 41.5, gameActive: true });
    expect(createStatusHook(() => game)()).toEqual({
      score1: 2, score2: 1, matchTime: 41.5, gameActive: true,
    });
  });

  it('still reports the final score after full time', () => {
    const game = sceneStub({ score1: 3, score2: 1, matchTime: 0, gameActive: false });
    const status = createStatusHook(() => game)();
    expect(status.gameActive).toBe(false);
    expect(status.score1).toBe(3);
  });

  it('coerces missing numeric fields to null rather than undefined', () => {
    const game = sceneStub({ gameActive: true });
    const status = createStatusHook(() => game)();
    expect(status.score1).toBeNull();
    expect(status.matchTime).toBeNull();
  });
});
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `cd game/frontend && npm test`
Expected: FAIL, cannot resolve `../src/status.js`.

- [ ] **Step 3: Implement**

Create `game/frontend/src/status.js`:

```javascript
// Reads the live score out of the Phaser scene. The score is drawn on the
// canvas, so nothing else outside the game can see it.
export const SCENE_KEY = 'SoccerGameScene';

const numberOrNull = (value) => (typeof value === 'number' ? value : null);

export function createStatusHook(getGame) {
  return function status() {
    const scene = getGame()?.scene?.getScene(SCENE_KEY);
    if (!scene) return null;
    return {
      score1: numberOrNull(scene.score1),
      score2: numberOrNull(scene.score2),
      matchTime: numberOrNull(scene.matchTime),
      gameActive: Boolean(scene.gameActive),
    };
  };
}
```

In `game/frontend/src/main.js`, add the import beside the existing imports at the top:

```javascript
import { createStatusHook } from './status.js';
```

and immediately after `gameInstance = new Phaser.Game(config);` (line 699):

```javascript
  window.__futsal = { status: createStatusHook(() => gameInstance) };
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `cd game/frontend && npm test`
Expected: all pass, including the pre-existing `kick-direction.test.js`.

- [ ] **Step 5: Commit**

```bash
git add game/frontend/src/status.js game/frontend/src/main.js game/frontend/test/futsal-status.test.js
git commit -m "Expose live match status to callers outside the Phaser canvas"
```

---

### Task 10: The chat UI

Derived from the approved mockup, which is the design of record. The mockup is one file; split it into three and replace its hardcoded log with SSE-driven rendering.

**Files:**
- Modify: `dugout/static/index.html` (replace entirely)
- Create: `dugout/static/chat.css`, `dugout/static/chat.js`
- Reference: `docs/superpowers/specs/assets/dugout-mockup.html`

**Interfaces:**
- Consumes: `GET /stages`, `GET /health`, `POST /chat` SSE frames

- [ ] **Step 1: Split the mockup**

```bash
cp docs/superpowers/specs/assets/dugout-mockup.html dugout/static/index.html
```

Move everything between `<style>` and `</style>` into `dugout/static/chat.css` and replace it with `<link rel="stylesheet" href="/static/chat.css">`. Move the inline `<script>` into `dugout/static/chat.js` and replace it with `<script type="module" src="/static/chat.js"></script>`.

- [ ] **Step 2: Strip the mocked content**

In `index.html`:

- Empty `<div class="log">` and `<ol class="stages">`. Both render from data now.
- Delete every hardcoded `.ev` block and the `.handoff` block, which belongs to out-of-scope stage 4b.
- Delete the inline `?state=blocked` / `?at=end` demo script; `checkHealth()` owns `is-blocked` now.
- Tag the three service dots so `checkHealth()` can find them, replacing the three `<div class="svc">` lines with:

```html
      <div class="svc" data-service="pitch"><i></i><em>PITCH</em>5173</div>
      <div class="svc" data-service="coach"><i></i><em>COACH</em>8000</div>
      <div class="svc" data-service="captain"><i></i><em>CAPTAIN</em>8001</div>
```

- Replace the hardcoded scoreline with `<div class="scoreline" hidden></div>`. Live score rendering is not in this plan; the agent reports the score in chat.

- [ ] **Step 3: Write the client**

Replace `dugout/static/chat.js`:

```javascript
const log = document.querySelector('.log');
const stagesEl = document.querySelector('.stages');
const input = document.querySelector('#say');
const sendBtn = document.querySelector('.send');
const acting = document.querySelector('.acting');

const ACTOR_CLASS = { user: 'a-you', antigravity: 'a-agy' };
const ACTOR_LABEL = { user: 'You', antigravity: 'Antigravity' };
const VERB = {
  generate_team_avatars: 'Called', get_match_status: 'Called',
  read_player_stats: 'Called', create_file: 'Wrote', edit_file: 'Edited',
  run_command: 'Ran', start_subagent: 'Started',
};

let lastActor = null;

const label = (actor) =>
  actor.startsWith('subagent:') ? actor.slice(9).replace('-tuner', '') : (ACTOR_LABEL[actor] || actor);

const actorClass = (actor) =>
  actor.startsWith('subagent:') ? 'a-agy' : (ACTOR_CLASS[actor] || 'a-agy');

function addEvent(actor, minute, node) {
  const ev = document.createElement('div');
  ev.className = `ev ${actorClass(actor)}`;
  const same = actor === lastActor;
  lastActor = actor;
  ev.innerHTML =
    `<div class="min"><b>${minute}</b>` +
    (same ? '<span class="who cont">·</span>'
          : `<span class="who">${label(actor)}</span>`) +
    '</div><div class="body"></div>';
  ev.querySelector('.body').append(node);
  log.append(ev);
  log.scrollTop = log.scrollHeight;
}

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

function toolCallNode(payload) {
  const wrap = el('div', 'act');
  wrap.append(el('span', 'verb', VERB[payload.name] || 'Called'));
  const call = el('span', 'call');
  call.append(el('span', 'fn', payload.name));
  call.append(el('span', 'arg', `(${JSON.stringify(payload.args ?? {})})`));
  wrap.append(call);
  return wrap;
}

async function renderStages() {
  const data = await (await fetch('/stages')).json();
  stagesEl.replaceChildren(...data.map((s, i) => {
    const li = el('li', `stage ${s.done ? 'done' : (data.slice(0, i).every(p => p.done) ? 'live' : 'locked')}`);
    li.innerHTML =
      `<div class="tile">${i + 1}</div><div>` +
      `<h3></h3><p></p>` +
      `<div class="suggest"><span>Suggested</span><q></q></div></div>`;
    li.querySelector('h3').textContent = s.title;
    li.querySelector('p').textContent = s.blurb;
    li.querySelector('q').textContent = s.suggested;
    li.querySelector('.suggest').onclick = () => { input.value = s.suggested; input.focus(); };
    return li;
  }));
}

async function checkHealth() {
  const { agent, game } = await (await fetch('/health')).json();
  document.body.classList.toggle('is-blocked', !agent.ok);
  if (!agent.ok) document.querySelector('.blocked p').textContent = agent.detail;
  for (const [name, up] of Object.entries(game)) {
    document.querySelector(`.svc[data-service="${name}"]`)
      ?.classList.toggle('down', !up);
  }
}

function setWorking(on, detail) {
  acting.style.display = on ? 'flex' : 'none';
  if (on) acting.querySelector('code').textContent = detail || '';
}

async function send() {
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  setWorking(true, 'thinking');

  const res = await fetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  });

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  let textNode = null;

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    const frames = buffer.split('\n\n');
    buffer = frames.pop();
    for (const frame of frames) {
      const kind = frame.match(/^event: (.+)$/m)?.[1];
      const data = frame.match(/^data: (.+)$/m)?.[1];
      if (!kind || !data) continue;
      const { actor, payload } = JSON.parse(data);
      const minute = '--′';

      if (kind === 'user') { addEvent(actor, minute, el('p', 'say you', payload)); }
      else if (kind === 'thought') { addEvent(actor, minute, el('p', 'thought', payload)); textNode = null; }
      else if (kind === 'tool_call') { addEvent(actor, minute, toolCallNode(payload)); textNode = null; }
      else if (kind === 'text') {
        if (!textNode) { textNode = el('p', 'say', ''); addEvent(actor, minute, textNode); }
        textNode.textContent += payload;
      }
      else if (kind === 'error') { addEvent(actor, minute, el('pre', 'out bad', payload)); }
      else if (kind === 'stage_done') { renderStages(); }
    }
  }
  setWorking(false);
}

sendBtn.onclick = send;
input.onkeydown = (e) => { if (e.key === 'Enter') send(); };
renderStages();
checkHealth();
setWorking(false);
```

- [ ] **Step 4: Verify in a browser**

Run: `cd dugout && ./run.sh` and open http://localhost:8002
Expected: the team sheet renders four stages from `/stages`; if `agy` is not logged in, the red banner shows the real `/health` detail and Send is disabled.

- [ ] **Step 5: Add the index test**

Append to `dugout/tests/test_app.py`:

```python
def test_index_is_served(monkeypatch):
    c = client(monkeypatch)
    r = c.get("/")
    assert r.status_code == 200
    assert "Dugout" in r.text


def test_index_has_no_mocked_trajectory_left(monkeypatch):
    c = client(monkeypatch)
    body = c.get("/").text
    assert "generate_team_avatars" not in body
    assert "handoff" not in body
    assert "—" not in body
```

Run: `cd dugout && uv run pytest tests/ -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add dugout/static/ dugout/tests/test_app.py
git commit -m "Add SSE chat UI with per-actor trajectory rendering"
```

---

### Task 11: Preflight and the smoke checklist

**Files:**
- Modify: `dugout/run.sh`
- Create: `docs/superpowers/SMOKE.md`

- [ ] **Step 1: Add the preflight**

In `dugout/run.sh`, after the existing `uv sync` and before the `exec`:

```bash
if ! command -v agy >/dev/null 2>&1; then
  echo "ERROR: the 'agy' CLI is not on PATH." >&2
  echo "  Install Antigravity, then run: agy login" >&2
  exit 1
fi

uv run playwright install chromium
```

Change `uv sync` to `uv sync --all-groups` so the dev group is available.

Note: the preflight deliberately does not shell out to `agy status`, which opens a TTY UI and fails under a non-interactive shell. Login is verified at runtime by `GET /health`, which is where the UI surfaces it.

- [ ] **Step 2: Write the checklist**

Create `docs/superpowers/SMOKE.md`:

```markdown
# Dugout smoke checklist

Deliberately manual. Agent output is nondeterministic and a flaky test in a
workshop repo is worse than no test.

Prerequisites: `agy login` completed, `dugout/.env` has GOOGLE_CLOUD_PROJECT
and GOOGLE_CLOUD_LOCATION.

1. `cd game && ./run.sh`, wait for Vite on :5173.
2. `cd dugout && ./run.sh`, open http://localhost:8002.
3. Header shows Antigravity lit amber and three green game dots. No red banner.
4. Team sheet lists four stages, none marked done on a clean tree.
5. Send "Kit us out in black and gold with a wolf crest."
   - Trajectory shows a thought, then CALLED generate_team_avatars.
   - Every event names its actor in the gutter.
   - Stage 1 flips to done. Reload the game tab and the new kit is on the pitch.
6. Send "Now get us on the pitch."
   - Antigravity writes a Playwright script and runs it.
   - If it hits the #kick-off-btn stability timeout, it should retry with
     force=True on its own. That self-correction is the moment worth watching.
   - /tmp/futsal_status.json appears and updates.
7. Send "How are we doing?" and confirm it reports a real score.
8. Send "They keep breaking through the middle. Tighten it up."
   - Four subagents run. Each tool call is attributed to its own role.
   - Attribute changes land in the match within about two seconds.
   - Any out-of-range attempt comes back as a violation list, not a crash.
9. Refresh the game tab: baselines restore and the squad is clean again.

Failure to check on purpose: stop the game stack and send a message. The agent
should report game_not_running and tell you to run game/run.sh.
```

- [ ] **Step 3: Run the whole suite one last time**

Run: `cd dugout && uv run pytest tests/ -v && cd ../game/frontend && npm test`
Expected: everything green.

- [ ] **Step 4: Commit**

```bash
git add dugout/run.sh docs/superpowers/SMOKE.md
git commit -m "Add dugout preflight and manual smoke checklist"
```

---

## Deferred, with reasons

- **Stage 4b, shout to the bench.** Needs the `update_profile` allowlist hardening at `game/agents/specialist_agents/tools.py:193-218` and a second event source reading the game's `#terminal-body`. Both are unscoped. The UI mockup's cyan handoff panel is the design of record for when it lands.
- **`ASK_QUESTION`.** Needs a user-response round trip back through the SSE stream.
- **MCP servers for the Antigravity agent.** The game's football MCP server stays reachable only through the game's own agents.

## Known risk

Whether a subagent's tool calls surface in the parent's `ChatResponse.tool_calls`
at all is unverified, because it cannot be checked without a logged-in `agy`. If
they do not surface, Task 6's attribution is correct but never fires, and stage
4a renders as a single aggregated block. The fallback is to watch the four
`player_state/*.json` files for changes and synthesise lane events from them.
Confirm this at Task 7 with a one-off script before building the UI in Task 10.
