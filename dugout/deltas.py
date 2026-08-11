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
