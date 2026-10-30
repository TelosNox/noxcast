"""5-minute means of every power sensor, kept for three years (pure Python, no Home Assistant imports).

An hourly mean blurs a shadow edge over the 15° of azimuth the sun moves in an hour; 5 minutes
cover about 1.25°, and a mixed day has clear spells where no hour is clear. Per sensor, two modules
of one array that differ at the same moment show partial shade, which a cloud never does. Home
Assistant keeps 5-minute statistics for only about ten days, so Noxcast keeps its own copy for
sharper shading later. The size stays bounded however long the integration runs: only periods with
the sun in front of the modules, whole watts, and only the last FINE_DAYS days (three times the same
season, e.g. to see a tree grow). About 0.5 kB per sensor and day, some 0.5 MB per sensor in all.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date

from .const import Series

STEP = 300                       # seconds, as Home Assistant's 5-minute statistics
FINE_DAYS = 3 * 366
STATS_DAYS = 9                   # 5-minute statistics Home Assistant keeps (default 10 days), minus a margin


def day_steps(five_min: Series, day_start: float, day_end: float,
              useful: Callable[[float], bool]) -> dict | None:
    """{"t0": first period start, "w": [W or None per period]} from the first to the last useful period.

    five_min: mean power (W) per 5-minute period start. Missing periods, and periods with the sun
    behind the modules, are None. None if nothing was measured.
    """
    starts = [t for t in range(int(day_start), int(day_end), STEP) if useful(t + STEP / 2)]
    if not starts:
        return None
    values = []
    for t in range(starts[0], starts[-1] + STEP, STEP):
        v = five_min.get(t)
        values.append(round(v) if v is not None and useful(t + STEP / 2) else None)
    if all(v is None for v in values):
        return None
    return {"t0": starts[0], "w": values}


def missing_days(store: dict, sensors: set[str], today: date) -> list[date]:
    """Days within reach of the 5-minute statistics that lack at least one sensor."""
    out = []
    for back in range(STATS_DAYS, 0, -1):
        day = date.fromordinal(today.toordinal() - back)
        if not sensors <= set(store.get(day.isoformat(), {})):
            out.append(day)
    return out


def add_day(store: dict, day: date, sensor: str, steps: dict | None) -> None:
    """Keep a day's periods; a day without data is marked empty so it is not fetched again."""
    store.setdefault(day.isoformat(), {})[sensor] = steps or {}


def prune(store: dict, oldest: date, sensors: set[str]) -> None:
    """Drop days before oldest and sensors no array uses any more."""
    for key in [k for k in store if k < oldest.isoformat()]:
        del store[key]
    for day in store.values():
        for sensor in [s for s in day if s not in sensors]:
            del day[sensor]


def series(store: dict, sensor: str) -> Series:
    """All kept 5-minute means of one sensor: {period start: W}."""
    out = {}
    for day in store.values():
        steps = day.get(sensor) or {}
        for i, v in enumerate(steps.get("w", [])):
            if v is not None:
                out[steps["t0"] + i * STEP] = float(v)
    return out
