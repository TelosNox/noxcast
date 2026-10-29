"""10-minute means of each array's power, kept for a year (pure Python, no Home Assistant imports).

An hourly mean blurs a shadow edge over the 15° of azimuth the sun moves in an hour; 10 minutes
cover 2.5°, and a mixed day has clear 10-minute spells where no hour is clear. Home Assistant keeps
5-minute statistics for only about ten days, so Noxcast keeps its own 10-minute means for sharper
shading later. The size stays bounded however long the integration runs: only steps with the sun in
front of the modules, whole watts, and only the last FINE_DAYS days (the sun path repeats every
year, and recent data describe the surroundings as they are now). About 300 bytes per array and
day, some 120 kB per array in all.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date

STEP = 600                       # seconds
FINE_DAYS = 400                  # a year and some weeks
STATS_DAYS = 9                   # 5-minute statistics Home Assistant keeps (default 10 days), minus a margin


def day_steps(five_min: dict[float, float], day_start: float, day_end: float,
              useful: Callable[[float], bool]) -> dict | None:
    """{"t0": first step start, "w": [W or None per step]} from the first to the last useful step of a day.

    five_min: mean power (W) per 5-minute period start. A step is the mean of its two periods; steps
    with a period missing, or with the sun behind the modules, are None. None if nothing was measured.
    """
    starts = [t for t in range(int(day_start), int(day_end), STEP) if useful(t + STEP / 2)]
    if not starts:
        return None
    values = []
    for t in range(starts[0], starts[-1] + STEP, STEP):
        a, b = five_min.get(t), five_min.get(t + STEP / 2)
        values.append(round((a + b) / 2) if a is not None and b is not None and useful(t + STEP / 2) else None)
    if all(v is None for v in values):
        return None
    return {"t0": starts[0], "w": values}


def missing_days(store: dict, arrays: set[str], today: date) -> list[date]:
    """Days within reach of the 5-minute statistics that lack at least one array."""
    out = []
    for back in range(STATS_DAYS, 0, -1):
        day = date.fromordinal(today.toordinal() - back)
        if not arrays <= set(store.get(day.isoformat(), {})):
            out.append(day)
    return out


def add_day(store: dict, day: date, array_id: str, steps: dict | None) -> None:
    """Keep a day's steps; a day without data is marked empty so it is not fetched again."""
    store.setdefault(day.isoformat(), {})[array_id] = steps or {}


def prune(store: dict, oldest: date, arrays: set[str]) -> None:
    """Drop days before oldest and arrays that no longer exist."""
    for key in [k for k in store if k < oldest.isoformat()]:
        del store[key]
    for day in store.values():
        for array_id in [a for a in day if a not in arrays]:
            del day[array_id]


def series(store: dict, array_id: str) -> dict[float, float]:
    """All kept 10-minute means of one array: {step start: W}."""
    out = {}
    for day in store.values():
        steps = day.get(array_id) or {}
        for i, v in enumerate(steps.get("w", [])):
            if v is not None:
                out[steps["t0"] + i * STEP] = float(v)
    return out
