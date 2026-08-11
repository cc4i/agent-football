# Tuning Diff Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draw every attribute a tuner or a shout changed as a marker on its legal range, in a four-lane panel in the dugout's match log.

**Architecture:** The dugout streams `ToolResult` chunks it currently discards, so the match log learns what each value was before it moved. A new `dugout/deltas.py` turns a before/after pair of profiles into a list of deltas, and separately places each value on its track as a percentage. The frontend renders those percentages into the `.lanes` CSS that `chat.css` has shipped, unused, since the original build.

**Tech Stack:** Python 3.13, FastAPI, `google-antigravity` 0.1.9, pytest with `asyncio_mode=auto`, vanilla ES modules and hand-written CSS (no build step, no frontend framework).

**Spec:** `docs/superpowers/specs/2026-08-11-tuning-diff-widget-design.md`

## Global Constraints

- No em dashes anywhere, in code, comments, commit messages or UI copy. Plain `-`.
- Never `innerHTML`. Every node is built with `document.createElement`, because the content is model output and may contain anything. This rule is already stated at `dugout/static/chat.js:64` and `:119`.
- Amber (`--amber`) belongs to Antigravity and nothing else. Cyan (`--sys`) belongs to the game's own agents. Every action names its actor.
- All work happens in `dugout/` and `docs/`. Do not touch `game/`.
- Run tests with `cd dugout && uv run pytest`.
- Commit after every task.

## Two deviations from the spec, already decided

1. **The percentage math lives in a new `dugout/deltas.py`, not in `attributes.py`.** The spec put it in `attributes.py` and its tests in `test_attributes.py`. `attributes.py` is about what an attribute is allowed to be; describing a change is a different job, and it is shared by two callers. The spec's actual requirement - geometry computed in Python where pytest can reach it - is met either way.
2. **A tune's `tool_call` line keeps its arguments suppressed.** The spec said the line would "render its arguments the same way every other call does". That would print `tune_defender({"changes":{...},"reason":"..."})` in mono immediately before the panel draws the same numbers properly, which is the clutter this widget exists to remove. The existing suppression stays; only `changesTable()` goes.

## File Structure

| File | Responsibility |
|---|---|
| `dugout/deltas.py` (new) | Describe what moved between two profiles; place each value on its range |
| `dugout/tests/test_deltas.py` (new) | Cover both, including unit-bearing ranges and clamping |
| `dugout/tools/tuning.py` | `_tune` returns the change it made, not just the new values |
| `dugout/tools/shout.py` | Snapshot the four profiles, diff them once the chain answers |
| `dugout/session.py` | A fourth pump for `ToolResult`; attribute a shout's result to the game |
| `dugout/app.py` | Turn a tool result into drawable panels, or drop it |
| `dugout/static/chat.js` | Build and fill the lanes panel; delete `changesTable` |
| `dugout/static/chat.css` | The bar, and the cyan variant of the panel |
| `docs/superpowers/SMOKE.md` | What the panel should look like when it works |

---

### Task 1: Describe a change and place its markers

**Files:**
- Create: `dugout/deltas.py`
- Test: `dugout/tests/test_deltas.py`

**Interfaces:**
- Consumes: `attributes.baseline_profile(role)`, `attributes.range_for(attribute, baseline_value)`, both existing.
- Produces:
  - `describe_change(role: str, before: dict, after: dict, reason: str | None = None) -> dict | None` returning `{"role", "file", "reason", "deltas": [{"attribute", "before", "after", "baseline", "min", "max"}]}`, or `None` when nothing moved.
  - `marker(value: float, low: float, high: float) -> tuple[float, bool]` returning `(percentage, is_out_of_band)`.
  - `with_markers(change: dict) -> dict`, the same dict with `beforePct`, `afterPct`, `baselinePct` and `off` added to every delta.

- [ ] **Step 1: Write the failing tests**

Create `dugout/tests/test_deltas.py`:

```python
import json

import pytest

from deltas import describe_change, marker, with_markers


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    baseline = {"finishing": 0.5, "shotPower": 0.5,
                "decisionDelay": 80, "tackleCooldown": 800}
    for name in ("defender", "midfielder", "forward", "goalkeeper"):
        (tmp_path / f"{name}_baseline.json").write_text(json.dumps(baseline))
    monkeypatch.setattr("attributes.PLAYER_STATE_DIR", tmp_path)
    return tmp_path


def test_only_attributes_that_moved_are_described():
    change = describe_change("forward", {"finishing": 0.5, "shotPower": 0.5},
                             {"finishing": 0.9, "shotPower": 0.5})
    assert [d["attribute"] for d in change["deltas"]] == ["finishing"]


def test_nothing_moving_is_no_change_at_all():
    assert describe_change("forward", {"finishing": 0.5},
                           {"finishing": 0.5}) is None


def test_a_delta_carries_the_shipped_baseline_and_the_band():
    change = describe_change("forward", {"finishing": 0.7}, {"finishing": 0.9})
    delta = change["deltas"][0]
    assert delta["before"] == 0.7
    assert delta["after"] == 0.9
    assert delta["baseline"] == 0.5
    assert (delta["min"], delta["max"]) == (0.0, 1.0)


def test_the_reason_and_the_file_travel_with_the_change():
    change = describe_change("defender", {"finishing": 0.5},
                             {"finishing": 0.8}, "hold a deeper line")
    assert change["role"] == "defender"
    assert change["file"] == "player_state/defender.json"
    assert change["reason"] == "hold a deeper line"


def test_an_attribute_the_baseline_never_had_still_describes():
    # A shout writes through the game's own agents, which can introduce a key.
    change = describe_change("forward", {}, {"invented": 0.4})
    delta = change["deltas"][0]
    assert delta["before"] is None
    assert delta["baseline"] is None
    assert delta["after"] == 0.4


def test_a_non_numeric_value_is_not_a_delta():
    assert describe_change("forward", {"finishing": 0.5},
                           {"finishing": "fast"}) is None


def test_a_value_at_the_foot_of_its_range_sits_at_zero():
    assert marker(0.0, 0.0, 1.0) == (0.0, False)


def test_a_value_at_the_head_of_its_range_sits_at_one_hundred():
    assert marker(1.0, 0.0, 1.0) == (100.0, False)


def test_a_unit_bearing_range_is_measured_from_its_own_floor():
    # tackleCooldown runs 100 to 2000, so 1050 is the middle of the track.
    assert marker(1050.0, 100.0, 2000.0) == (50.0, False)


def test_a_value_outside_its_band_clamps_and_says_so():
    assert marker(1.4, 0.0, 1.0) == (100.0, True)
    assert marker(-0.2, 0.0, 1.0) == (0.0, True)


def test_a_collapsed_range_does_not_divide_by_zero():
    assert marker(5.0, 5.0, 5.0) == (0.0, True)


def test_markers_are_added_without_disturbing_the_description():
    placed = with_markers(
        describe_change("forward", {"finishing": 0.2}, {"finishing": 0.8}))
    delta = placed["deltas"][0]
    assert delta["beforePct"] == 20.0
    assert delta["afterPct"] == 80.0
    assert delta["baselinePct"] == 50.0
    assert delta["before"] == 0.2
    assert delta["after"] == 0.8


def test_a_missing_before_leaves_its_marker_off_the_track():
    placed = with_markers(describe_change("forward", {}, {"invented": 0.4}))
    delta = placed["deltas"][0]
    assert delta["beforePct"] is None
    assert delta["baselinePct"] is None
    assert delta["afterPct"] == 40.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dugout && uv run pytest tests/test_deltas.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'deltas'`

- [ ] **Step 3: Write the implementation**

Create `dugout/deltas.py`:

```python
"""Describing a change to a player's attributes, in a form the log can draw.

Two steps, deliberately separate. `describe_change` says what moved and goes
back to the model inside the tool result. `with_markers` works out where each
value sits on its range, which only the browser cares about, so the geometry
is added when the frame is built rather than spent on the model's context.

The geometry is computed here rather than in the browser because this is where
the ranges already live, and because the dugout has a pytest suite and no
JavaScript one.
"""

from attributes import baseline_profile, range_for


def _numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def describe_change(role: str, before: dict, after: dict,
                    reason: str | None = None) -> dict | None:
    """What moved between two profiles, or None if nothing did.

    `after` may be a whole profile or only the attributes that were written,
    so a partial dict is not read as every other attribute having vanished.
    """
    baseline = baseline_profile(role)
    deltas = []
    for attribute, new in after.items():
        old = before.get(attribute)
        if not _numeric(new) or old == new:
            continue
        shipped = baseline.get(attribute)
        low, high = range_for(attribute, shipped)
        deltas.append({
            "attribute": attribute,
            "before": old if _numeric(old) else None,
            "after": new,
            "baseline": shipped if _numeric(shipped) else None,
            "min": low,
            "max": high,
        })
    if not deltas:
        return None
    return {"role": role, "file": f"player_state/{role}.json",
            "reason": reason, "deltas": deltas}


def marker(value: float, low: float, high: float) -> tuple[float, bool]:
    """Where a value sits on its range as a percentage, and whether it fits.

    A tuned value is validated and always fits. A shout writes through the
    game's own agents, which can land outside the band, and a marker silently
    parked on the rail would read as being in range.
    """
    span = high - low
    if span <= 0:
        return 0.0, True
    pct = (value - low) / span * 100
    return max(0.0, min(100.0, pct)), not 0.0 <= pct <= 100.0


def with_markers(change: dict) -> dict:
    """The same change with every value placed on its track, ready to draw."""
    placed = []
    for delta in change["deltas"]:
        low, high = delta["min"], delta["max"]
        after_pct, off = marker(delta["after"], low, high)
        placed.append({
            **delta,
            "afterPct": after_pct,
            "off": off,
            "beforePct": (None if delta["before"] is None
                          else marker(delta["before"], low, high)[0]),
            "baselinePct": (None if delta["baseline"] is None
                            else marker(delta["baseline"], low, high)[0]),
        })
    return {**change, "deltas": placed}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd dugout && uv run pytest tests/test_deltas.py -q`
Expected: 13 passed

- [ ] **Step 5: Run the whole suite, to be sure nothing else moved**

Run: `cd dugout && uv run pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add dugout/deltas.py dugout/tests/test_deltas.py
git commit -m "Say what a tuning change actually moved

A change is currently described by its new values alone, which cannot be
drawn: a bar needs the old value, the shipped baseline and the band. All
three are already known server side and none of them leave it.

The geometry is split from the description on purpose. The description goes
back to the model in the tool result, where it is useful. The percentages are
only for the browser, so they are added when the frame is built and never
spent on the model's context."
```

---

### Task 2: A tune reports what it moved

**Files:**
- Modify: `dugout/tools/tuning.py:17-41`
- Test: `dugout/tests/test_tuning_tools.py`

**Interfaces:**
- Consumes: `deltas.describe_change` from Task 1.
- Produces: `_tune` and its four public wrappers return `"changed": [change]` alongside the existing `ok`, `role`, `applied` and `reason`. The list is empty when a call set every value to what it already was.

- [ ] **Step 1: Write the failing tests**

Append to `dugout/tests/test_tuning_tools.py`:

```python
def test_the_result_says_what_each_value_was_before(state):
    result = tuning.tune_forward({"finishing": 0.9}, "needs a goal")
    delta = result["changed"][0]["deltas"][0]
    assert delta["attribute"] == "finishing"
    assert delta["before"] == 0.5
    assert delta["after"] == 0.9
    assert delta["baseline"] == 0.5
    assert (delta["min"], delta["max"]) == (0.0, 1.0)


def test_the_change_names_the_role_the_file_and_the_reason(state):
    change = tuning.tune_defender({"aggression": 0.9}, "hold the line")["changed"][0]
    assert change["role"] == "defender"
    assert change["file"] == "player_state/defender.json"
    assert change["reason"] == "hold the line"


def test_setting_a_value_to_what_it_already_is_moves_nothing(state):
    result = tuning.tune_forward({"finishing": 0.5}, "no change at all")
    assert result["ok"] is True
    assert result["changed"] == []


def test_a_refused_change_reports_no_movement(state):
    result = tuning.tune_forward({"finishing": 2.0}, "score more")
    assert result["ok"] is False
    assert "changed" not in result
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dugout && uv run pytest tests/test_tuning_tools.py -q`
Expected: FAIL, `KeyError: 'changed'`

- [ ] **Step 3: Write the implementation**

In `dugout/tools/tuning.py`, add the import beside the existing ones:

```python
from attributes import PLAYER_STATE_DIR, validate_changes
from deltas import describe_change
from tools.match import CALLED
```

Replace the body of `_tune` from the `path =` line to the end of the function:

```python
    path = PLAYER_STATE_DIR / f"{role}.json"
    profile = json.loads(path.read_text())
    # Captured before the update, because this is the only moment the prior
    # values still exist anywhere.
    before = dict(profile)
    profile.update(changes)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(profile, indent=2))
    os.replace(tmp, path)
    # A shout rewrites these same files through the game's own agents, so the
    # quest can only tell the two routes apart by which tool did the writing.
    CALLED.add("tune")
    change = describe_change(role, before, changes, reason.strip())
    return {"ok": True, "role": role, "applied": changes,
            "reason": reason.strip(),
            "changed": [change] if change else []}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd dugout && uv run pytest tests/test_tuning_tools.py -q`
Expected: all pass, including the eight that already existed

- [ ] **Step 5: Commit**

```bash
git add dugout/tools/tuning.py dugout/tests/test_tuning_tools.py
git commit -m "Have a tune report what it moved, not just where it landed

_tune already reads the whole profile before it writes, so it is holding
every prior value at the moment of the change and throwing them away. It now
returns them."
```

---

### Task 3: A shout diffs the squad it changed

**Files:**
- Modify: `dugout/tools/shout.py`
- Test: `dugout/tests/test_shout.py`

**Interfaces:**
- Consumes: `deltas.describe_change` from Task 1, `attributes.PLAYER_STATE_DIR`, `attributes.ROLES`.
- Produces: `shout_to_the_team` returns `"changed": [...]` with one entry per role the game's agents moved, in the same shape `_tune` produces. Helpers `_profiles() -> dict` and `_diff(before: dict, after: dict) -> list`.

- [ ] **Step 1: Write the failing tests**

Add to `dugout/tests/test_shout.py`, after the existing imports:

```python
import json

from attributes import ROLES
```

and append these tests:

```python
@pytest.fixture
def squad(tmp_path, monkeypatch):
    baseline = {"finishing": 0.5, "shotPower": 0.5}
    for name in ROLES:
        (tmp_path / f"{name}.json").write_text(json.dumps(baseline))
        (tmp_path / f"{name}_baseline.json").write_text(json.dumps(baseline))
    monkeypatch.setattr(shout, "PLAYER_STATE_DIR", tmp_path)
    monkeypatch.setattr("attributes.PLAYER_STATE_DIR", tmp_path)
    return tmp_path


def test_the_squad_is_read_from_disk(squad):
    assert shout._profiles()["forward"]["finishing"] == 0.5
    assert set(shout._profiles()) == set(ROLES)


def test_an_unreadable_profile_is_skipped_not_raised(squad):
    (squad / "forward.json").write_text("{ broken")
    profiles = shout._profiles()
    assert "forward" not in profiles
    assert "defender" in profiles


def test_a_role_the_agents_left_alone_is_not_reported(squad):
    before = {"forward": {"finishing": 0.5}}
    assert shout._diff(before, {"forward": {"finishing": 0.5}}) == []


def test_every_role_the_agents_moved_comes_back(squad):
    before = {"forward": {"finishing": 0.5}, "defender": {"finishing": 0.5}}
    after = {"forward": {"finishing": 0.9}, "defender": {"finishing": 0.5}}
    changed = shout._diff(before, after)
    assert [c["role"] for c in changed] == ["forward"]
    assert changed[0]["deltas"][0]["before"] == 0.5
    assert changed[0]["deltas"][0]["after"] == 0.9


def test_a_shout_carries_no_reason_because_it_gave_none(squad):
    changed = shout._diff({"forward": {"finishing": 0.5}},
                          {"forward": {"finishing": 0.9}})
    assert changed[0]["reason"] is None


def test_a_role_unreadable_before_the_shout_is_skipped(squad):
    # Nothing to measure the move against, so reporting it would invent a
    # before value the manager never had.
    assert shout._diff({}, {"forward": {"finishing": 0.9}}) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dugout && uv run pytest tests/test_shout.py -q`
Expected: FAIL, `AttributeError: module 'tools.shout' has no attribute '_profiles'`

- [ ] **Step 3: Write the implementation**

In `dugout/tools/shout.py`, extend the imports:

```python
import asyncio
import json

from attributes import PLAYER_STATE_DIR, ROLES
from deltas import describe_change
from tools.match import CALLED, read_status
```

Add both helpers below the constants, above `_chain_complete`:

```python
def _profiles() -> dict:
    """The four squad files as they stand. Unreadable ones are skipped.

    A missing or half-written file is not worth failing a shout over: the
    replies are the point of the tool and the diff is the extra.
    """
    squad = {}
    for role in ROLES:
        try:
            squad[role] = json.loads(
                (PLAYER_STATE_DIR / f"{role}.json").read_text())
        except (OSError, json.JSONDecodeError):
            continue
    return squad


def _diff(before: dict, after: dict) -> list:
    """What the game's own agents changed, one entry per role that moved.

    A role missing from `before` is skipped rather than reported as new:
    there is nothing to measure the move against.
    """
    changed = []
    for role, profile in after.items():
        if role not in before:
            continue
        change = describe_change(role, before[role], profile)
        if change:
            changed.append(change)
    return changed
```

In `shout_to_the_team`, capture the squad immediately before the shout is typed. Replace:

```python
            before = await page.inner_text(TERMINAL)
```

with:

```python
            # Snapshot the squad before the chain runs. The game's agents
            # write these same four files, and their replies never say which
            # numbers they chose.
            squad_before = _profiles()
            before = await page.inner_text(TERMINAL)
```

and replace:

```python
            replies = _new_lines(before, seen)
            result = {"shouted": stripped, "replies": replies}
```

with:

```python
            replies = _new_lines(before, seen)
            result = {"shouted": stripped, "replies": replies,
                      "changed": _diff(squad_before, _profiles())}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd dugout && uv run pytest tests/test_shout.py -q`
Expected: all pass, including the six that already existed

- [ ] **Step 5: Commit**

```bash
git add dugout/tools/shout.py dugout/tests/test_shout.py
git commit -m "Report which numbers the game's own agents chose

A shout rewrites the same four files as a tune, but the tool returns only the
chat replies, so the dugout has never been able to say what actually changed.
It now snapshots the squad before it types and diffs it once the chain
answers."
```

---

### Task 4: Stream tool results, and attribute a shout's to the game

**Files:**
- Modify: `dugout/session.py:24-56, 70-96`
- Test: `dugout/tests/test_multiplexer.py`

**Interfaces:**
- Consumes: `google.antigravity.types.ToolResult`.
- Produces: `multiplex` yields a fourth event kind, `tool_result`, whose `data` is the SDK `ToolResult`. New module constant `ACTOR_GAME = "game"` and new function `actor_for_tool_result(name: str) -> str`.

- [ ] **Step 1: Write the failing tests**

In `dugout/tests/test_multiplexer.py`, extend the SDK import:

```python
from google.antigravity.types import Text, Thought, ToolResult
```

and append:

```python
def test_a_shout_result_belongs_to_the_game_not_antigravity():
    # The call is Antigravity's, because Antigravity made it. The numbers are
    # the game's, because its own coach, captain and players chose them.
    assert session.actor_for_tool_call("shout_to_the_team") == session.ACTOR_AGENT
    assert session.actor_for_tool_result("shout_to_the_team") == session.ACTOR_GAME


def test_a_tuning_result_is_attributed_to_its_subagent():
    assert (session.actor_for_tool_result("tune_goalkeeper")
            == "subagent:goalkeeper-tuner")


async def test_tool_results_arrive_on_their_own_stream():
    events = await collect(FakeResponse(chunks=[
        ToolResult(name="tune_forward", result={"ok": True}),
    ]))
    results = [e for e in events if e["kind"] == "tool_result"]
    assert len(results) == 1
    assert results[0]["data"].name == "tune_forward"
    assert results[0]["actor"] == "subagent:forward-tuner"


async def test_a_tool_result_does_not_leak_into_the_text_stream():
    # chunks is the unfiltered stream. An unfiltered pump would print the raw
    # object repr in the match log, the same way a Thought would.
    events = await collect(FakeResponse(chunks=[
        Text(step_index=0, text="done"),
        ToolResult(name="tune_forward", result={"ok": True}),
    ]))
    assert [e["data"] for e in events if e["kind"] == "text"] == ["done"]


async def test_usage_is_still_the_final_event_with_four_pumps():
    events = await collect(FakeResponse(
        thoughts=["t"],
        tool_calls=[FakeToolCall("get_match_status")],
        chunks=[Text(step_index=0, text="c"),
                ToolResult(name="get_match_status", result={})]))
    assert events[-1]["kind"] == "usage"
    assert [e["kind"] for e in events].count("usage") == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dugout && uv run pytest tests/test_multiplexer.py -q`
Expected: FAIL, `AttributeError: module 'session' has no attribute 'actor_for_tool_result'`

- [ ] **Step 3: Write the implementation**

In `dugout/session.py`, extend the SDK types import:

```python
from google.antigravity.types import Text, ToolResult
```

Add the constant beside the existing actors:

```python
ACTOR_USER = "user"
ACTOR_AGENT = "antigravity"
ACTOR_GAME = "game"
```

Add this below `actor_for_tool_call`:

```python
def actor_for_tool_result(name: str) -> str:
    """Attribute a tool's return value to whoever decided it.

    Only the shout differs from its own call. Antigravity types the shout, so
    the call is Antigravity's, but the game's coach, captain and four player
    agents pick the numbers that come back, so the result is theirs.
    """
    if name == "shout_to_the_team":
        return ACTOR_GAME
    return actor_for_tool_call(name)


_ACTOR_BY_KIND = {"tool_call": actor_for_tool_call,
                  "tool_result": actor_for_tool_result}
```

Replace the actor line inside `_pump`:

```python
async def _pump(get_source, kind, queue):
    try:
        async for item in get_source():
            pick = _ACTOR_BY_KIND.get(kind)
            actor = pick(getattr(item, "name", "")) if pick else ACTOR_AGENT
            event = {"kind": kind, "actor": actor, "data": item}
```

Add the filter below `_text_deltas`:

```python
async def _tool_results(response):
    """Tool return values, and nothing else.

    Filtered for the same reason `_text_deltas` is: `chunks` carries every
    kind, so an unfiltered pump would double every event that already has one
    of its own.
    """
    async for chunk in response.chunks:
        if isinstance(chunk, ToolResult):
            yield chunk
```

Add the fourth source in `multiplex`:

```python
    sources = (
        (lambda: response.thoughts, "thought"),
        (lambda: response.tool_calls, "tool_call"),
        (lambda: _text_deltas(response), "text"),
        (lambda: _tool_results(response), "tool_result"),
    )
```

Update `multiplex`'s docstring first line to `"""Fan thoughts, tool calls, text chunks and tool results into one timeline."""`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd dugout && uv run pytest tests/test_multiplexer.py -q`
Expected: all pass, including the fourteen that already existed

- [ ] **Step 5: Commit**

```bash
git add dugout/session.py dugout/tests/test_multiplexer.py
git commit -m "Pump tool results, which the SDK was already carrying

ChatResponse.chunks yields ToolResult alongside Text and Thought, and the
multiplexer has been dropping it on the floor. A fourth pump picks it up, the
same way _text_deltas picks out Text.

A shout's result is the one that does not belong to whoever called the tool.
Antigravity types the shout; the game's own agents choose the numbers."
```

---

### Task 5: Turn a tool result into drawable panels

**Files:**
- Modify: `dugout/app.py:14-27, 141-156`
- Test: `dugout/tests/test_app.py`

**Interfaces:**
- Consumes: `deltas.with_markers` from Task 1, the `tool_result` event kind from Task 4.
- Produces: `tuning_panels(result) -> list` in `app.py`, and a new SSE frame `event: tuning` whose data payload is a list of role panels. Every other tool result yields no frame at all.

- [ ] **Step 1: Write the failing tests**

Append to `dugout/tests/test_app.py`:

```python
def test_a_tuning_result_becomes_panels_with_markers():
    panels = app_module.tuning_panels({"changed": [{
        "role": "forward",
        "file": "player_state/forward.json",
        "reason": "score more",
        "deltas": [{"attribute": "finishing", "before": 0.2, "after": 0.8,
                    "baseline": 0.5, "min": 0.0, "max": 1.0}]}]})
    delta = panels[0]["deltas"][0]
    assert delta["beforePct"] == 20.0
    assert delta["afterPct"] == 80.0
    assert delta["baselinePct"] == 50.0
    assert panels[0]["reason"] == "score more"


def test_a_shout_that_moved_two_roles_becomes_two_panels():
    panels = app_module.tuning_panels({"changed": [
        {"role": "forward", "file": "player_state/forward.json", "reason": None,
         "deltas": [{"attribute": "finishing", "before": 0.2, "after": 0.8,
                     "baseline": 0.5, "min": 0.0, "max": 1.0}]},
        {"role": "defender", "file": "player_state/defender.json", "reason": None,
         "deltas": [{"attribute": "clearance", "before": 0.7, "after": 0.9,
                     "baseline": 0.5, "min": 0.0, "max": 1.0}]}]})
    assert [p["role"] for p in panels] == ["forward", "defender"]


def test_a_refused_tune_becomes_a_panel_of_violations():
    panels = app_module.tuning_panels({
        "ok": False, "role": "defender",
        "violations": ["finishing=2.0 is outside 0.0 to 1.0"]})
    assert panels[0]["role"] == "defender"
    assert panels[0]["deltas"] == []
    assert panels[0]["violations"] == ["finishing=2.0 is outside 0.0 to 1.0"]


def test_every_other_tool_result_stays_out_of_the_log():
    # The log has never shown tool results. Printing them all now would bury
    # the trajectory under shell output.
    assert app_module.tuning_panels({"stdout": "ok"}) == []
    assert app_module.tuning_panels({"changed": []}) == []
    assert app_module.tuning_panels("a string") == []
    assert app_module.tuning_panels(None) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dugout && uv run pytest tests/test_app.py -q`
Expected: FAIL, `AttributeError: module 'app' has no attribute 'tuning_panels'`

- [ ] **Step 3: Write the implementation**

In `dugout/app.py`, add the import below the `session` import block:

```python
from deltas import with_markers
```

Add the function below `kit_preview`:

```python
def tuning_panels(result) -> list:
    """The role panels a tool result carries, ready to draw.

    Empty for everything else. The log has never shown tool results, and
    printing them all now would bury the trajectory under shell output.
    """
    if not isinstance(result, dict):
        return []
    if result.get("changed"):
        return [with_markers(change) for change in result["changed"]]
    role, violations = result.get("role"), result.get("violations")
    if role and violations:
        return [{"role": role, "file": f"player_state/{role}.json",
                 "reason": None, "deltas": [], "violations": violations}]
    return []
```

In `_turn`, add the branch between the `tool_call` and `usage` branches:

```python
            if event["kind"] == "tool_call":
                payload = {"name": payload.name, "args": payload.args}
                if payload["name"] == "generate_team_avatars":
                    rebranded.append(payload["args"].get("team", "blue"))
            elif event["kind"] == "tool_result":
                # Only the two routes that rewrite the squad have anything to
                # draw. The rest of the results never reach the client.
                panels = tuning_panels(getattr(payload, "result", None))
                if panels:
                    yield _frame("tuning", event["actor"], panels)
                continue
            elif event["kind"] == "usage" and payload is not None:
                payload = {"total": getattr(payload, "total_token_count", None)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd dugout && uv run pytest tests/test_app.py -q`
Expected: all pass

- [ ] **Step 5: Run the whole suite**

Run: `cd dugout && uv run pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add dugout/app.py dugout/tests/test_app.py
git commit -m "Send the changed squad to the browser as a tuning frame

Two routes rewrite the squad and both now say what they moved, so the frame
carries either one panel from a tuner or up to four from a shout. Marker
positions are added here rather than in the tool result, so they cost the
browser everything and the model nothing.

Every other tool result is dropped. The log has never shown them and shell
output would bury the trajectory."
```

---

### Task 6: Draw the panel

**Files:**
- Modify: `dugout/static/chat.js:9-15, 74-102, 199-217, 248-301`
- Modify: `dugout/static/chat.css:167-172, 292-298`

**Interfaces:**
- Consumes: the `tuning` SSE frame from Task 5, and the `game` actor from Task 4.
- Produces: no exports. `chat.js` is a browser module with no test runner in this service; its gate is `node --check` plus the manual run in Task 7.

- [ ] **Step 1: Teach the log that the game is an actor**

In `dugout/static/chat.js`, replace lines 9-10:

```js
const ACTOR_CLASS = { user: 'a-you', antigravity: 'a-agy', game: 'a-sys' };
const ACTOR_LABEL = { user: 'You', antigravity: 'Antigravity', game: "The game's agents" };
```

- [ ] **Step 2: Replace changesTable with the panel builder**

Delete `changesTable` entirely (lines 74-87) and replace it with:

```js
const LANE_CLASS = { defender: 'def', midfielder: 'mid', forward: 'fwd', goalkeeper: 'gk' };
const TUNERS = Object.keys(LANE_CLASS).map((role) => `${role}-tuner`);
const PANEL_HEAD = {
  'a-agy': ['Antigravity subagents', 'one player file each'],
  'a-sys': ["The game's agents", 'four player agents, through the coach'],
};

// One panel per turn per system, so four subagents working at once read as
// four lanes filling in rather than a dozen scattered log entries.
let panels = {};

const at = (node, pct) => { node.style.left = `${pct}%`; return node; };
const fmt = (n) => (typeof n === 'number' ? String(Number(n.toFixed(3))) : String(n));

function panelFor(actor, minute) {
  const family = actorClass(actor);
  if (panels[family]) return panels[family];

  const [title, note] = PANEL_HEAD[family] || PANEL_HEAD['a-agy'];
  const head = el('div', 'lanes-hd');
  head.append(el('b', null, title), el('span', null, note));
  const lanes = el('div', 'lanes');
  const wrap = el('div');
  wrap.append(head, lanes);

  panels[family] = { lanes, byRole: {} };
  addEvent(actor, minute, wrap);
  return panels[family];
}

function laneFor(panel, role) {
  if (panel.byRole[role]) return panel.byRole[role];

  const lane = el('div', `lane ${LANE_CLASS[role] || ''}`);
  const header = el('header');
  header.append(el('i'), el('b', null, role));
  const working = el('div', 'working');
  working.append(el('i'), el('span', null, 'working…'));
  const rows = el('div', 'rows');
  const whys = el('div', 'whys');
  lane.append(header, el('div', 'file', `player_state/${role}.json`),
              working, rows, whys);

  panel.lanes.append(lane);
  panel.byRole[role] = { working, rows, whys, byAttribute: {} };
  return panel.byRole[role];
}

function barNode(d) {
  // The bar reinforces the numbers beside it, so it carries the same reading
  // for anyone who cannot see it.
  const bar = el('div', 'bar');
  bar.setAttribute('role', 'img');
  bar.setAttribute('aria-label',
    `${d.attribute} moved from ${d.before == null ? 'unset' : fmt(d.before)}`
    + ` to ${fmt(d.after)}, allowed ${fmt(d.min)} to ${fmt(d.max)}`
    + (d.baseline == null ? '' : `, shipped ${fmt(d.baseline)}`));

  if (d.baselinePct != null) bar.append(at(el('i', 'tick'), d.baselinePct));
  if (d.beforePct != null) {
    const moved = at(el('i', 'moved'), Math.min(d.beforePct, d.afterPct));
    moved.style.width = `${Math.abs(d.afterPct - d.beforePct)}%`;
    bar.append(moved, at(el('i', 'was'), d.beforePct));
  }
  bar.append(at(el('i', `now${d.off ? ' off' : ''}`), d.afterPct));
  return bar;
}

function deltaRow(d) {
  const row = el('div', 'row');
  const line = el('div', 'delta');
  const values = el('span');
  values.append(el('s', null, d.before == null ? '-' : fmt(d.before)),
                document.createTextNode(' → '),
                el('em', null, fmt(d.after)));
  line.append(el('u', null, d.attribute), values);
  row.append(line, barNode(d));
  return row;
}

function drawTuning(actor, minute, entries) {
  const panel = panelFor(actor, minute);
  for (const entry of entries) {
    const lane = laneFor(panel, entry.role);
    lane.working.remove();
    for (const d of entry.deltas) {
      // A second call touching the same attribute keeps one row. What the
      // manager wants measured from is the first value, not the last.
      const seen = lane.byAttribute[d.attribute];
      const merged = seen ? { ...d, before: seen.before, beforePct: seen.beforePct } : d;
      const row = deltaRow(merged);
      if (seen) seen.row.replaceWith(row); else lane.rows.append(row);
      lane.byAttribute[d.attribute] =
        { row, before: merged.before, beforePct: merged.beforePct };
    }
    if (entry.reason) lane.whys.append(el('p', 'why', entry.reason));
    if (entry.violations) {
      // .out .bad is a descendant selector (chat.css:150), so the red text
      // has to be a child node rather than a second class on the same node.
      const out = el('pre', 'out');
      out.append(el('span', 'bad', entry.violations.join('\n')));
      lane.whys.append(out);
    }
  }
  log.scrollTop = log.scrollHeight;
}

function startedTuners(args) {
  // The SDK does not name the subagent in a field worth relying on, so the
  // four tuner names are matched against the whole argument blob.
  const blob = JSON.stringify(args ?? {});
  return TUNERS.filter((name) => blob.includes(name));
}
```

- [ ] **Step 3: Stop the tune call from printing its own arguments**

Replace `toolCallNode` (lines 89-102 before the edit) with:

```js
function toolCallNode(payload) {
  const wrap = el('div', 'act');
  wrap.append(el('span', 'verb', VERB[payload.name] || 'Called'));
  const call = el('span', 'call');
  call.append(el('span', 'fn', payload.name));
  // A tune's arguments are the changed numbers, and the lanes panel draws
  // those properly a moment later. Printing the raw JSON here as well is the
  // clutter this widget exists to remove.
  if (!payload.name.startsWith('tune_'))
    call.append(el('span', 'arg', `(${JSON.stringify(payload.args ?? {})})`));
  wrap.append(call);
  return wrap;
}
```

- [ ] **Step 4: Handle the new frame and open lanes when a tuner starts**

In `send()`, replace the `tool_call` branch and add the `tuning` branch:

```js
        else if (kind === 'tool_call') {
          addEvent(actor, minute, toolCallNode(payload));
          textNode = null;
          // A started tuner gets its lane straight away, so the panel shows
          // four subagents at work rather than appearing only once one lands.
          for (const name of payload.name === 'start_subagent'
            ? startedTuners(payload.args) : [])
            laneFor(panelFor('antigravity', minute), name.replace('-tuner', ''));
        }
        else if (kind === 'tuning') { drawTuning(actor, minute, payload); textNode = null; }
```

In `send()`, reset the panels alongside the text node, immediately after `setWorking(true, 'thinking');`:

```js
  panels = {};
```

In `restart()`, reset them too, immediately after `lastActor = null;`:

```js
    panels = {};
```

- [ ] **Step 5: Style the bar and give the panel a cyan variant**

In `dugout/static/chat.css`, replace the `.delta em` rule (line 170):

```css
/* The actor's colour, not a verdict. lineHeight .6 -> .3 and finishing
   .99 -> .98 are deliberate reductions that make the squad better, so
   colouring by direction would assert a judgement the widget cannot make. */
.delta em{font-style:normal;color:var(--amber)}
```

Append below the `.working` rules (after line 172):

```css
.lane .rows{display:flex;flex-direction:column;gap:1px}
.lane .why{margin:8px 0 0;font-size:12.5px;line-height:1.45;color:var(--chalk-2);font-weight:300}
/* A lane is a quarter of the panel, so the shared .out indent is too deep. */
.lane .out{margin-top:8px;padding-left:9px;font-size:11px}

/* a changed attribute, drawn on the band it must stay inside */
.bar{position:relative;height:9px;margin:1px 0 5px;
     background:linear-gradient(var(--rule),var(--rule)) left center/100% 1px no-repeat}
.bar i{position:absolute;font-style:normal}
.bar .tick{top:0;width:1px;height:9px;background:var(--chalk-3);opacity:.5}
.bar .moved{top:4px;height:1px;background:var(--amber-2)}
.bar .was{top:2px;width:5px;height:5px;margin-left:-2.5px;border-radius:50%;
          border:1px solid var(--chalk-3);background:var(--turf-800)}
.bar .now{top:1px;width:7px;height:7px;margin-left:-3.5px;border-radius:50%;background:var(--amber)}
/* Out of band. A marker parked silently on the rail would read as in range. */
.bar .now.off{top:0;width:3px;height:9px;margin-left:-1.5px;border-radius:1px;background:var(--fail)}

/* the same panel when the game's own agents chose the numbers */
.ev.a-sys .lanes{border-color:var(--sys-3)}
.ev.a-sys .lanes-hd b{color:var(--sys-2)}
.ev.a-sys .delta em{color:var(--sys)}
.ev.a-sys .bar .moved{background:var(--sys-2)}
.ev.a-sys .bar .now{background:var(--sys)}
.ev.a-sys .working{color:var(--sys-2)}
.ev.a-sys .working i{background:var(--sys)}
```

Delete the now-dead `.changes` rules (lines 292-298, the block under `/* ── tuning tables and kit previews in the log ───────────── */`) and retitle that comment to `/* ── kit previews in the log ─────────────────────────────── */`.

- [ ] **Step 6: Check the JavaScript parses**

Run: `node --check dugout/static/chat.js`
Expected: no output, exit 0

- [ ] **Step 7: Check nothing references the deleted rules**

Run: `grep -rn "changesTable\|\.changes" dugout/static/`
Expected: no matches

- [ ] **Step 8: Run the whole Python suite, which must be untouched**

Run: `cd dugout && uv run pytest -q`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add dugout/static/chat.js dugout/static/chat.css
git commit -m "Draw what moved, on the band it had to stay inside

The four-lane panel has been sitting in chat.css since the original build,
styled and never rendered. It is wired up now, with a bar under each row
carrying three marks: the shipped baseline, the value before the call, and
where it landed.

The new value takes the actor's colour rather than green. lineHeight .6 to .3
and finishing .99 to .98 are deliberate reductions that make the squad better,
so colouring by direction would assert a judgement the widget cannot make.

A value outside its band is drawn as a red bar hard against the rail. Tuning
is validated and cannot land there; a shout goes through the game's own agents
and can."
```

---

### Task 7: Say what the panel should look like, and check that it does

**Files:**
- Modify: `docs/superpowers/SMOKE.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing further depends on this task.

- [ ] **Step 1: Update the tuning step**

In `docs/superpowers/SMOKE.md`, in step 9, replace:

```
   - Each one renders its change as a table of attribute and new value with
     the tuner's reason underneath, not a line of JSON.
```

with:

```
   - One panel appears, headed "Antigravity subagents", with a lane per role
     in the role's own colour. A lane opens as soon as its tuner starts and
     shows a pulsing "working" until it reports, so the four are visibly
     running at once rather than appearing one at a time.
   - Each changed attribute is one row: the name, the old value, the new
     value, and a bar underneath. On the bar, a faint tick is the shipped
     baseline, a hollow dot is the value before this call, and the filled dot
     is where it landed. The tuner's reason sits at the foot of its lane.
   - Nothing renders a line of raw JSON. The tune call itself reads only
     "Called tune_defender", because the panel carries the numbers.
   - A tuner that changes the same attribute twice keeps one row, still
     measured from the first value it moved off.
```

- [ ] **Step 2: Add the shout panel to step 8**

In step 8, after the bullet ending "so it should call it once.", insert:

```
   - A second panel appears, headed "The game's agents", in cyan. It shows
     the same bars for whatever the four player agents changed. The
     shout_to_the_team call above it stays amber and named Antigravity,
     because Antigravity made the call; the panel is cyan, because the
     game's agents chose the numbers.
   - A value they picked outside its allowed band draws as a red bar hard
     against the end of the track, not as a dot sitting quietly at the edge.
```

- [ ] **Step 3: Commit the documentation**

```bash
git add docs/superpowers/SMOKE.md
git commit -m "Say what the tuning panel looks like when it is working

Agent output is nondeterministic and this repo checks the visual result by
hand, so the checklist has to describe the panel precisely enough to spot a
lane that never fills or a bar drawn against the wrong band."
```

- [ ] **Step 4: Run it for real**

```bash
cd game && ./run.sh      # wait for Vite on :5173
cd dugout && ./run.sh    # then open http://localhost:8002
```

Send `They keep breaking through the middle. Tighten it up.` and check every bullet added to step 9 above. Then send `Tell the lads to push up and press high.` and check the bullets added to step 8.

Be picky. Things worth failing on: a bar whose markers sit at the wrong end for a unit-bearing attribute like `tackleCooldown`, a lane that keeps its "working" pulse after it has reported, a panel that appears twice in one turn, amber leaking into the shout panel, or a marker overhanging its lane's padding.

- [ ] **Step 5: Restore the squad the smoke run dirtied**

```bash
git status
git restore game/frontend/public/player_state/ game/frontend/public/assets/sprites/
```

The checklist already warns that a smoke run rewrites the shipped baselines. Committing them would ship a tuned squad as the starting eleven.

---

## Self-Review

**Spec coverage.** Every section of the design maps to a task: data flow and geometry to Task 1, `_tune` to Task 2, the shout diff to Task 3, the fourth pump and the split attribution to Task 4, the frame and the violations panel to Task 5, the widget, the lifecycle, the two judgement calls and accessibility to Task 6, and the smoke step to Task 7. The spec's testing table named `test_attributes.py` for the geometry; that moved to `test_deltas.py` with the code, which is noted at the top of this plan.

**Placeholders.** None. Every code step carries the code, every test step carries the assertions, and every run step carries the command and its expected result.

**Type consistency.** `describe_change` produces `{"role", "file", "reason", "deltas"}` in Task 1; Task 2 wraps it in `"changed": [...]`; Task 3 produces a list of the same; Task 5 reads `result["changed"]` and passes each through `with_markers`; Task 6 reads `entry.role`, `entry.reason`, `entry.deltas`, `entry.violations` and the four `*Pct` fields plus `off`. The violations panel synthesised in Task 5 carries `deltas: []` so Task 6's loop over `entry.deltas` is safe. `ACTOR_GAME = "game"` in Task 4 matches the `game` key added to `ACTOR_CLASS` and `ACTOR_LABEL` in Task 6.
