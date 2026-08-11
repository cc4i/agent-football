# Show changed attributes as a visual delta widget

Date: 2026-08-11
Status: Approved, ready for implementation planning.

## Purpose

Stage 4 is the stage people are here to watch: four subagents in parallel, one
player file each, moving numbers that decide whether the team wins. Right now
the match log renders that as a flat two-column table of key and new value
(`dugout/static/chat.js:74`). It says what the value became. It never says what
the value was, how far it moved, or whether that is a large move or a nudge.

This adds a widget that shows the move. One panel per tuning round, four lanes,
one per role, each changed attribute drawn as a bar within its legal range with
the shipped baseline, the value before this call, and the new value marked.

The same widget, in cyan, covers stage 5. A shout rewrites the same four files
through the game's own agent chain, and the dugout currently shows only the
chat replies - never the numbers those agents chose. Rendering both routes in
the same form is what makes them comparable, which is the point the README
draws between them.

## What already exists

This widget was designed and styled during the original dugout build, then
never wired up.

- `dugout/static/chat.css:152-172` ships `.lanes-hd`, `.lanes`, `.lane` with
  `.def`/`.mid`/`.fwd`/`.gk` role hues, `.lane .file`, `.delta` with its `u`,
  `s` and `em` children, and `.working` with a pulsing dot. None of it is
  referenced by `chat.js`.
- `docs/superpowers/specs/assets/dugout-mockup.html:424-455` renders those
  rules as a four-column panel of `attr  old -> new` rows.
- The responsive breakpoints for `.lanes` already exist: two columns at 1180px
  (`chat.css:251`), one at 800px (`chat.css:256`).

The work is therefore mostly wiring plus the bar, not new visual design.

## What is missing

The frontend cannot currently know the "before" value. The `tool_call` payload
carries only `name` and `args` (`dugout/app.py:146`), and `args` for a tune is
`changes` plus `reason`. Nothing in the stream carries the prior value or the
attribute's legal band.

Both are available server-side and are not being surfaced:

- `tools/tuning.py:33` reads the whole profile before it writes, so it holds
  every prior value at the moment of the change.
- `attributes.range_for()` already derives each attribute's legal band, and
  `attributes.baseline_profile()` already reads the shipped `*_baseline.json`.

## Data flow

Three options were considered.

**A. Stream `ToolResult` (chosen).** `_tune()` returns the per-attribute
before, after, baseline and band alongside what it already returns. A fourth
pump in `multiplex()` forwards `ToolResult` chunks, and `app.py` emits them as
a `tuning` event. `ToolResult` is already carried on `response.chunks`
(`google/antigravity/types.py:924`) but is filtered out today, because
`_text_deltas` keeps only `Text` and the other two pumps read `.thoughts` and
`.tool_calls`. Surfacing it needs a `_tool_results()` generator alongside
`_text_deltas` and one more entry in the `sources` tuple.

**B. Client-side snapshot.** The frontend fetches the four profiles at page
load and tracks state as tunes stream in. No server change, but "before" is
inferred rather than observed: it is wrong after a shout, wrong after a reload
mid-conversation, and it still needs a second fetch to learn the legal band.
Rejected.

**C. Per-turn buffer drained after the stream.** `_tune()` appends to a
module-level list that `app.py` reads once the turn ends. Simple, but the panel
could only render after everything finished, which contradicts a live panel.
Rejected.

### Marker geometry is computed server-side

`attributes.py` already owns `range_for()`, so it also computes each marker's
position as a percentage of the track. The frontend applies the number to
`style.left` and does no arithmetic.

This is a testability decision. The dugout is a Python service with a pytest
suite and no JavaScript test runner; `game/frontend` has vitest, but adding a
node toolchain to `dugout/` for one function is not worth it. Putting the math
in Python means the geometry - including the unit-bearing ranges and the
clamping - is covered by the suite that already exists.

### Event shape

One `tuning` event per completed tool result:

```json
{
  "role": "defender",
  "file": "player_state/defender.json",
  "reason": "hold a deeper line and clear the ball early",
  "deltas": [
    {"attribute": "clearance", "before": 0.7, "after": 0.9,
     "baseline": 0.5, "min": 0.0, "max": 1.0,
     "beforePct": 70.0, "afterPct": 90.0, "baselinePct": 50.0,
     "off": false}
  ]
}
```

`baseline` and `baselinePct` are null for an attribute absent from the shipped
baseline file, which a shout can produce. `off` is true when the raw value
falls outside its band.

## The widget

```
ANTIGRAVITY SUBAGENTS                          one player file each
┌──────────────────────┬──────────────────────┬─── … ───┐
│ ● DEFENDER           │ ● MIDFIELDER         │         │
│ player_state/        │ player_state/        │         │
│   defender.json      │   midfielder.json    │         │
│                      │                      │         │
│ clearance   .7 → .9  │ speed       .5 → .8  │  ● …    │
│ ▕━━━━━━━━━━╵━○══●▏   │ ▕━━━━━━━━╵━━○═══●▏   │ working │
│ lineHeight  .6 → .3  │ pressing    .5 → .7  │         │
│ ▕━━●══○━━╵━━━━━━━▏   │ ▕━━━━━━━━╵○══●━━━▏   │         │
└──────────────────────┴──────────────────────┴─── … ───┘
```

Each row keeps the mockup's elements so the shipped `.delta` rules still
apply: `u` for the attribute name, `s` for the old value, `em` for the new one.
Below them sits a new `div.bar` holding

- `.tick` - the shipped baseline, a faint vertical rule
- `.moved` - the segment spanning old to new
- `.was` - a hollow marker at the value before this call
- `.now` - a filled marker at the new value

End labels show the real band, so `tackleCooldown` reads 100 to 2000 rather
than 0 to 1.

The reason the subagent gave sits at the foot of its lane as the existing
`.why` paragraph, one per call, so a role that tunes twice shows both reasons
in the order they arrived.

Every node is built with `document.createElement`, never `innerHTML`,
following the rule already stated at `chat.js:64` and `chat.js:119`: this is
model output and may contain anything.

### What this replaces

`changesTable()` and the `isTune` branch of `toolCallNode()` (`chat.js:74-101`)
both go. The panel owns the changed attributes now, and leaving the inline
table in place would print every change twice - once when the call is
dispatched and again when its result lands. The `tool_call` line for a tune
keeps its verb and function name, and renders its arguments the same way every
other call does.

## Panel lifecycle

- The panel is created by the first `start_subagent` call whose subagent name
  matches `<role>-tuner`, or by the first `tuning` event if none was seen -
  the main agent holds all four tuning tools (`session.py:151`) and may tune
  directly.
- A lane exists only for a role that has been started or has reported. Asking
  to tune only the forward yields one lane, not three permanently empty ones.
- A started but silent lane shows the existing `.working` pulse.
- The same attribute tuned twice within a turn keeps one row: `before` stays
  the first value observed, `after` updates.
- The panel resets per turn. A second tuning round in the same conversation
  gets its own panel further down the log, alongside `textNode` in `send()`.

## The shout variant

`shout_to_the_team` reads the four profiles before it types into the game's
shout bar, and diffs them once the chain completes. It returns the same
`deltas` shape, so the same renderer draws it.

Attribution splits, following the rule that amber is Antigravity and nothing
else:

- the `tool_call` line for `shout_to_the_team` stays amber and named
  **Antigravity**, because Antigravity made that call;
- the resulting panel is cyan and headed **the game's agents · four player
  agents, through the coach**, because they chose the numbers.

The panel therefore carries actor `game`, which maps to the existing `.a-sys`
class. `actorClass()` currently falls back to `a-agy` for anything unknown, so
`game` needs an explicit entry.

An attribute a shout introduces that is not in the baseline renders with no
`was` marker and no baseline tick, just the new value.

## Two decisions about how the shipped design reads

**Direction is not coloured.** The dead CSS sets `.delta em{color:var(--ok)}`,
green for every new value. That reads as improvement, but `lineHeight .6 → .3`
and `finishing .99 → .98` are deliberate reductions that make the squad
better - colouring by direction would assert a judgement the widget has no
basis for. `em` becomes the actor colour: amber, or cyan under `.a-sys`.
Marker positions and the arrow between the numbers carry direction.

**Out-of-range values clamp and say so.** Tuning is validated by
`validate_changes`, so it cannot land outside the band. A shout writes through
the game's own agents and can. Percentages clamp to 0-100 and the marker takes
an `.off` class that renders it hard against the rail, rather than sitting at
the edge as though it were in band.

## Failure and accessibility

- A failed tune returns `ok: false` with no `applied`. The lane renders the
  violations as an `.out.bad` line, not a bar.
- A `ToolResult` carrying `error` renders the same way.
- Each bar is `role="img"` with an `aria-label` of the form `clearance moved
  from 0.7 to 0.9, allowed 0 to 1, shipped 0.5`. The numbers already sit in
  text beside it, so the bar reinforces rather than solely carries.
- The existing `prefers-reduced-motion` rule (`chat.css:247`) already disables
  the working pulse.

## Testing

| File | Covers |
|---|---|
| `tests/test_attributes.py` | marker percentages, unit-bearing ranges, clamping, an attribute missing from the baseline |
| `tests/test_tuning_tools.py` | `_tune` returns per-attribute before, baseline and band alongside `applied` |
| `tests/test_multiplexer.py` | `ToolResult` chunks surface as `tuning` events and do not leak into the text stream |
| `tests/test_shout.py` | the before/after profile diff, against a stubbed page |
| `tests/test_app.py` | a `tuning` frame is emitted with the expected payload |

`docs/superpowers/SMOKE.md` gains a step for the panel, because the visual
result is the deliverable and no automated test in this repo looks at pixels.
