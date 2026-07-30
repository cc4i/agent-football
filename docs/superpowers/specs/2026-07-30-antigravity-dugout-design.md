# Rebuild `dugout/` as an Antigravity CLI 2.0 showcase

Date: 2026-07-30
Status: Approved, ready for implementation planning. Two questions under
"Event multiplexer" are open and gate stage 4b: how subagent actions are
attributed, and whether the dugout reads the game's own agent terminal.

## Purpose

Replace the current form-based avatar generator with a chat-driven
application that showcases the Antigravity CLI 2.0 agent. The agent, not the
Python backend, does the work.

The directory is renamed `branding/` -> `dugout/` as part of this work, because
it no longer only handles branding: the dugout is where you kit out the team,
send them onto the pitch, watch the match and shout instructions, which is
exactly the five stages below. `coach`, `captain` and `manager` were all
unavailable as names, being taken by ADK agents in `game/agents/`.

What the agent does:

1. Generates player avatars with Gemini image generation and rebrands the team.
2. Writes and runs its own Playwright script to play the Futsal WorldCup game.
3. Reads live match status and tunes the squad to win, both by editing player
   attributes directly and by shouting instructions through the game UI.

The chat UI guides the user through these capabilities as a staged quest and
renders the agent's live trajectory (thoughts and tool calls) as it works.

## Audience and success criteria

This must work both as a hands-on lab (participants run it on their own
machines) and as a presented live demo. That sets the bar:

- Setup failures are loud, immediate, and name their fix.
- The happy path is predictable enough to run on stage.
- The agent's reasoning is visible, because watching it work *is* the product.

Success: a participant with a logged-in `agy` and a running game stack can go
from a stock team to a rebranded, tuned, winning team entirely through chat,
and can see every step the agent took.

## Key technical findings

These were verified against the installed toolchain, not assumed.

**The `google-antigravity` Python SDK is the right integration point.** It is
published on PyPI (v0.1.9, Python >=3.10, platform wheels including macOS
arm64). It runs the agent *in-process*, so there is no shelling out to `agy`
and no parsing of the CLI's protobuf conversation store.

`ChatResponse` exposes `.thoughts`, `.tool_calls`, `.chunks`, `.text`,
`.structured_output`, `.usage_metadata`, `.cancel()`, `.resolve()`.
`ToolCall` carries `.name`, `.args`, `.id`, `.canonical_path`, `.server_name`.
Together these give a first-class live trajectory feed.

`LocalAgentConfig` accepts custom Python `tools`, `subagents`, `workspaces`,
`mcp_servers`, `hooks`, `policies`, `model`, and Vertex settings
(`vertex`, `project`, `location`).

`BuiltinTools` is: `RUN_COMMAND`, `CREATE_FILE`, `EDIT_FILE`, `VIEW_FILE`,
`FIND_FILE`, `LIST_DIR`, `SEARCH_DIR`, `SEARCH_WEB`, `READ_URL_CONTENT`,
`GENERATE_IMAGE`, `START_SUBAGENT`, `ASK_QUESTION`, `FINISH`.

**There is no builtin browser tool.** Playwright must therefore be driven via
`RUN_COMMAND` against a script the agent authors. This matches the intended
showcase rather than working against it.

**The game hot-reloads player attributes.** `game/frontend/src/main.js` polls
`player_state/*.json` during a match and applies changes live, so stat edits
take effect within roughly two seconds without restarting the match.

**The live score is not observable from outside the Phaser canvas.** The score
is drawn on the canvas, and the Phaser instance is module-scoped in `main.js`.
Neither the DOM nor `window` exposes it mid-match; only `window.currentProfiles`
is global. This requires a small change to the game (see below).

**The kick-off button cannot be clicked with a default Playwright click.**
`#kick-off-btn` carries a `pulse` CSS animation, so Playwright's actionability
check never reports the element as stable and `click()` times out. `click(force=True)`
works. This was reproduced directly.

## Architecture

Three processes.

```
+-- game/ (existing, unchanged except one observability hook) ------+
|  Vite :5173     ADK coach :8000     Captain A2A :8001             |
|  polls player_state/*.json every ~2s and hot-reloads              |
+-------------------------------------------------------------------+
        ^ Playwright drives              ^ edits json
        |                                |
+-- dugout/ :8002 (rebuilt) --------------------------------------+
|  FastAPI  --serves-->  chat UI (SSE)                              |
|     |                                                             |
|     +-- embeds Antigravity Agent (in-process SDK)                 |
|            curated tools + START_SUBAGENT                         |
+-------------------------------------------------------------------+
```

### File layout

Each module has one responsibility and can be understood without reading the
others.

| File | Responsibility |
| --- | --- |
| `app.py` | FastAPI routes only: `GET /`, `GET /health`, `GET /stages`, `POST /chat` (SSE) |
| `session.py` | Agent lifecycle and the event multiplexer |
| `stages.py` | The five stage definitions as data: id, title, suggested prompt, done-predicate |
| `instructions.md` | Agent system instructions |
| `tools/avatars.py` | `generate_team_avatars()`, wrapping existing `prompts.py` and `utils.py` |
| `tools/match.py` | `get_match_status()`, `read_player_stats()` |
| `subagents.py` | Four role-tuner `SubagentConfig` definitions |
| `static/` | `index.html`, `chat.js`, `chat.css` |

`prompts.py` and `utils.py` are kept as-is, except that `get_index_html` is
removed from `utils.py` in favour of ordinary static file serving.

### Agent configuration

```python
LocalAgentConfig(
    system_instructions=Path("instructions.md").read_text(),
    capabilities=CapabilitiesConfig(
        enable_subagents=True,
        enabled_tools=[RUN_COMMAND, CREATE_FILE, EDIT_FILE,
                       VIEW_FILE, LIST_DIR, START_SUBAGENT, FINISH],
    ),
    tools=[generate_team_avatars, get_match_status, read_player_stats],
    subagents=[...four role tuners...],
    workspaces=[REPO_ROOT],
    vertex=True, project=..., location=...,   # from dugout/.env
)
```

Deliberately disabled, with reasons:

- `GENERATE_IMAGE`: the curated avatar tool owns image generation so the
  chroma-key and resize pipeline is always applied.
- `SEARCH_WEB`, `READ_URL_CONTENT`: not needed; they only add latency and
  trajectory noise.
- `ASK_QUESTION`: would require a user-response round-trip back through the SSE
  stream, roughly doubling protocol complexity for no demo value. Revisit later.

### Event multiplexer

This is the one non-obvious piece of the implementation. `ChatResponse` exposes
`.thoughts`, `.tool_calls`, and `.chunks` as three independent async iterators.
To render a single coherent timeline, `session.py` fans all three into one
`asyncio.Queue` and drains it to SSE:

```python
async def pump(src, kind, actor):
    async for item in src:
        await q.put({"kind": kind, "actor": actor, "data": item})
```

SSE event kinds emitted to the browser: `thought`, `tool_call`, `text`,
`stage_done`, `error`, `usage`.

Every event also carries an `actor`, because the interface attributes each one
to whoever performed it (see Interface below). The values are `user`,
`antigravity`, `subagent:<name>`, and `game`.

Two things about `actor` are unresolved and should be settled before
`session.py` is written:

- **Subagent attribution.** It is not established that the SDK distinguishes a
  subagent's thoughts and tool calls from the parent's in the merged stream.
  `ToolCall` exposes `.name`, `.args`, `.id`, `.canonical_path` and
  `.server_name`, none of which is a subagent identifier. If no such handle
  exists, the fallback is to infer the actor from the file each subagent owns,
  which works only because the guardrails give each one exactly one file.
- **The game's own agents are not in this stream at all.** The coach, captain
  and specialists emit to the game's `#terminal-body`, not to Antigravity. To
  render stage 4b as a handoff rather than a gap, the dugout has to read that
  terminal as a second source. This is new scope. The cheaper option is to drop
  it and show only Antigravity's polled observation of the outcome, at the cost
  of the most interesting moment in the demo.

### Change required in `game/`

One observability hook in `game/frontend/src/main.js`, because the score is
otherwise unreadable from outside the canvas:

```js
// main.js already holds the Phaser game in a module-scoped `gameInstance`.
window.__futsal = {
  status() {
    const s = gameInstance?.scene.getScene('SoccerGameScene');
    return s ? { score1: s.score1, score2: s.score2, matchTime: s.matchTime } : null;
  },
};
```

The scene fields are `score1`, `score2` and `matchTime` (seconds remaining,
initialised from `GAME_DURATION_SEC`). `status()` returns `null` before kick-off
so callers can distinguish "not started" from "0-0".

Without this the agent can only read the final-score overlay at full time,
which removes the mid-match tuning loop entirely.

## The quest

Five stages. Each has a suggested prompt the user can send or edit; freeform
chat is always available, so the stages scaffold without constraining.

| # | Stage | Mechanism | Done when |
| --- | --- | --- | --- |
| 1 | Rebrand the team | `generate_team_avatars()` curated tool | both sprite PNGs rewritten |
| 2 | Take the field | agent authors and runs Playwright via `CREATE_FILE` + `RUN_COMMAND` | `/tmp/futsal_status.json` shows a live match |
| 3 | Read the game | `get_match_status()` + `read_player_stats()` | analysis returned |
| 4a | Tune the squad | `START_SUBAGENT` x4 in parallel, one file each | stats changed, effect visible in ~2s |
| 4b | Shout to the bench | Playwright drives `#shout-message-input` and `#shout-send-btn` | huddle rendered in `#terminal-body` |

### The two tuning levers

Stage 4 is split deliberately, because the two levers teach different lessons.

**4a, direct stat editing.** Four subagents each own one
`game/frontend/public/player_state/<role>.json`. Fast, deterministic, and
effectively god-mode. Demonstrates parallel subagents.

**4b, shouting through the UI.** Playwright types an instruction into the game's
coach shout bar. That fires the game's *own* multi-agent chain: ADK coach ->
team captain over A2A -> four specialist player agents in parallel -> huddle JSON
applied to the simulation. Specialists may additionally call the MCP tools
`report_injury` and `request_substitution`, which write `substitutions.json` and
cause live substitutions. Slower (roughly 10-30s) and emergent, but it shows
Antigravity driving another multi-agent system through its user interface, with
the whole chain visible in `#terminal-body`.

Substitutions are reachable only as a consequence of 4b, never edited directly.

### Stage-4 closed loop

1. The agent's Playwright script polls `window.__futsal.status()` and writes
   `/tmp/futsal_status.json`.
2. `get_match_status()` reads that file.
3. Subagents read status plus their own role's stats and edit their own file.
4. The running game picks the change up within ~2s.
5. Repeat until full time.

### Subagent guardrails

Four near-identical configs:

```python
SubagentConfig(
    name="forward-tuner",
    description="Tune the forward to increase goal threat",
    system_instructions=(
        "Edit ONLY player_state/forward.json. "
        "Keep every value in its existing numeric range. "
        "Change at most 3 attributes and state why for each."
    ),
    tools=[get_match_status, read_player_stats, "edit_file"],
)
```

One file per subagent means four parallel writers cannot collide, and a bad run
cannot corrupt the whole team. The three-attribute cap keeps the effect legible,
which is the pedagogical point: you should be able to see *why* the team improved.

## Interface

The trajectory is the product, so it gets the space: a narrow team-sheet rail
for the stages, and the rest of the window for the log. The chat composer is a
strip at the bottom, not the centre of the screen.

### Attribution is the organising principle

The point of the showcase is that Antigravity does the work, so the interface
must never render an action without saying who took it. Three rules:

**Every event names its actor.** The log's left gutter carries the match minute
and the actor together, and the rule running down it is tinted by actor. The
name prints only when control changes hands, so `you -> antigravity -> game ->
antigravity` reads as a sequence of handoffs instead of a stamp repeated on
every line.

**Actions are verb-led, not bare code.** `CALLED generate_team_avatars(...)`,
`RAN python /tmp/play_futsal.py`, `WROTE /tmp/play_futsal.py`. A tool call
rendered on its own reads as output from nowhere.

**Amber is reserved for Antigravity, cyan for the game's agents.** This one is
load-bearing rather than decorative. Stage 4b puts two multi-agent systems on
screen at once, and the game's existing terminal already colours its coach
amber (`#f59e0b` in `game/frontend/src/style.css`). Reusing amber for both would
make Antigravity and the game's coach indistinguishable at exactly the moment
the demo is trying to show one driving the other. The game's chain is therefore
rendered inside its own bordered, cyan panel, captioned as not being
Antigravity.

The four stage-4a subagents keep the game's existing role hues, since they act
on the same four players. They sit inside an amber-bordered container, because
they belong to Antigravity.

### Supporting states

- Antigravity has its own status chip in the header, separate from a group
  labelled for the three game services. Without it the header describes only
  the game and the agent is invisible.
- A persistent "Antigravity is working" bar sits above the composer, so the
  current actor is visible when the log is scrolled away.
- The match clock, not wall time, stamps every event. Before kick-off it reads
  `--'`, which is the same distinction `window.__futsal.status()` returns as
  `null`. During stage 4 the tuning loop is racing the clock, so the timestamp
  carries information rather than decorating.

The approved static mockup is `specs/assets/dugout-mockup.html`, with `?at=end`
and `?state=blocked` for the stage-4b and not-logged-in states. Its player
attribute values are illustrative only and do not match the real ones; see the
plan.

## Error handling

Ordered by likelihood of occurring in practice.

**Antigravity not logged in.** The most likely lab failure; the reference
machine is currently in this state. `GET /health` attempts to start the SDK
agent. Until it succeeds the chat input is disabled behind a banner naming the
exact fix. Fail at startup, loudly, not silently at stage 1.

**Game stack not running.** The UI shows three status dots for :5173, :8000 and
:8001. `get_match_status()` returns a typed `{"error": "game_not_running"}`
rather than raising, so the agent can read it and tell the user to run
`game/run.sh`.

**Avatar generation returns no image.** The curated tool raises a typed error
that surfaces as a tool-error event in the trajectory. The stage stays
incomplete and is retryable.

**Agent-authored Playwright fails.** `RUN_COMMAND` streams the non-zero exit and
stderr into the trajectory. This is desirable: the agent sees the error and
self-corrects, which is compelling to watch. Capped at three self-correction
attempts so a bad run cannot loop forever.

**Subagent writes an out-of-range value.** `read_player_stats()` returns each
attribute's valid range, and a post-write validator reports violations into the
trajectory. Recovery is already free: the game backs up `*_baseline.json` and
restores on refresh or rematch, so a wrecked squad is one page reload from clean.

### Prerequisite hardening in `game/`

Stage 4b routes agent-authored text into the game's own agent chain, which ends
at `update_profile(role, changes)` in
`game/agents/specialist_agents/tools.py`. That function builds a path with
`os.path.join(PLAYER_STATE_DIR, f"{role}.json")` and never validates `role`, so
a hallucinated or injected role escapes `player_state/`. Today the only caller
is the game's own specialists; stage 4b adds a second, agent-driven path into
it, so this must be fixed before 4b ships:

- reject any `role` outside `{defender, midfielder, forward, goalkeeper}`
  before building the path;
- validate `changes` keys against the known attribute schema for that role.

This also bounds the blast radius of prompt injection through the shout bar,
which is otherwise inherent to the feature: shout text is user-controlled and
reaches an LLM holding file-writing tools. The server-side allowlist is the real
defence there, not prompt wording.

## Setup

`dugout/pyproject.toml` gains two dependencies:

- `google-antigravity==0.1.9`, **pinned exactly**. This is a 0.x SDK with ten
  releases, and the surface we depend on (`.thoughts`, `.tool_calls`,
  `SubagentConfig`) is young. An unpinned range means the demo breaks silently
  on a fresh install.
- `playwright`, because the agent's authored scripts run inside the dugout venv.

`dugout/run.sh` gains a preflight that checks for `agy` login and runs
`playwright install chromium`.

Credentials reuse the existing `dugout/.env` (`GOOGLE_CLOUD_PROJECT`,
`GOOGLE_CLOUD_LOCATION`).

## Testing

**Unit.** Stage done-predicates are pure functions over filesystem state and are
tested without an agent. Curated tools are tested against a fake genai client,
plus chroma-key and resize against a fixture image.

**Integration.** The event multiplexer is tested against a stub `ChatResponse`
that yields known thoughts, tool calls and chunks; assert that every event
arrives, that ordering is sane, and that each one carries the right `actor`.
This is the component most likely to hide a subtle bug and the easiest to
isolate. Actor attribution is worth asserting explicitly: a silently wrong
actor still renders, it just renders a lie about who did the work.

**End to end.** A documented manual smoke checklist, deliberately not automated.
Agent nondeterminism would make such assertions flaky, and a flaky test in a
workshop repo is worse than no test.

**Regression.** The existing `game/frontend` vitest suite must still pass. Add
one test asserting `window.__futsal.status()` returns `null` before kick-off and
numeric `score1`, `score2` and `matchTime` once a scene is running.

## Out of scope

- Renaming internal code symbols (`SoccerGameScene`, the `soccer-game-over`
  event, `football_mcp_server.py`).
- The stale `LAB01`/`LAB02` architecture section in the root `README.md`.
- `mask.sh`, which still references `LAB02/football_agents/` paths that no longer
  exist after the repository restructure.
- MCP server integration for the Antigravity agent. The game's football MCP
  server stays reachable only through the game's own agents.
