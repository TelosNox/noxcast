"""What a learning run changed per array: the learned state before and after, and what it means for a clear day.

Pure functions, no Home Assistant imports. The coordinator keeps the state of the previous run, compares it with
the new one and stores the result for the panel; nothing here feeds back into learning or the forecast.

Why a value moved is told by what it rests on: the hours learning used (samples, and per shading cell its own hours
and their origin, model.LearnedArray.cell_info) and the days left out. The energy of a clear day per month, with the
old and with the new state, says what the change is worth in kWh, the same way for every parameter.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .model import AZ_STEP, EL_STEP, ArrayGeometry, LearnedArray, clearsky_profile
from .solar import Site

H = 3600.0
CELL_MOVED = 0.05        # a shading cell counts as changed from 5 points of transmission (noise of a median below)
TOP_CELLS = 5            # the cells listed with their values, largest change first
MONTH_DAY = 15           # the clear day that stands for its month


def _pair(old: LearnedArray | None, new: LearnedArray, attr: str, digits: int = 4) -> list:
    def value(la):
        v = getattr(la, attr) if la is not None else None
        return round(v, digits) if isinstance(v, float) else v
    return [value(old), value(new)]


def clear_day_kwh(site: Site, geo: ArrayGeometry, learned: LearnedArray, day_start: float) -> float:
    """Energy (kWh) of a clear day from day_start (UTC) on, with what was learned (efficiency, shade, sky view)."""
    return sum(sum(clearsky_profile(site, geo, learned, day_start + h * H, None, 6)) / 6
               for h in range(24)) / 1000


def _solar_midnight(site: Site, year: int, month: int) -> float:
    """UTC start of the solar day (local mean time) on the month's MONTH_DAY."""
    return datetime(year, month, MONTH_DAY, tzinfo=timezone.utc).timestamp() - site.longitude / 15 * H


def cell_label(key: str) -> dict:
    """A shading cell key "az_idx,el_idx" as the sun directions it covers (degrees)."""
    ai, ei = (int(x) for x in key.split(","))
    return {"key": key, "azimuth": [ai * AZ_STEP, (ai + 1) * AZ_STEP], "elevation": [ei * EL_STEP, (ei + 1) * EL_STEP]}


def changes(site: Site, geo: ArrayGeometry, old: LearnedArray | None, new: LearnedArray, year: int) -> dict:
    """What changed from old to new (old None: the first run, or the state was dropped).

    cells: how many shading cells moved by CELL_MOVED or more, up or down, were added or removed; cell_before: their
    value before (None: not learned before) for the panel's change layer; top: the largest with hours and origin.
    clear_days: per month the energy of a clear day with the old and the new state, the effect of all changes at once.
    """
    out: dict = {"at": new.learned_at, "first": old is None,
                 "samples": _pair(old, new, "samples"), "efficiency": _pair(old, new, "efficiency"),
                 "efficiency_source": _pair(old, new, "efficiency_source"), "temp_coeff": _pair(old, new, "temp_coeff", 5),
                 "sky_view": _pair(old, new, "sky_view"), "coverage": _pair(old, new, "coverage"),
                 "facade": [bool(old.facade) if old else None, bool(new.facade)],
                 "limit_cells": [len(old.limit_cells) if old else None, len(new.limit_cells)],
                 "dead_days": sorted(set(new.dead_days) - set(old.dead_days if old else ()))}
    before = old.cells if old else {}
    moved: dict[str, float | None] = {}
    up = down = added = removed = 0
    for key in sorted(set(before) | set(new.cells)):
        a, b = before.get(key), new.cells.get(key)
        if a is None and b is not None:
            if old is not None:
                added += 1
                moved[key] = None
        elif b is None:
            removed += 1
            moved[key] = a
        elif abs(b - a) >= CELL_MOVED:
            up, down = up + (b > a), down + (b < a)
            moved[key] = a
    out["cells"] = {"moved": up + down, "up": up, "down": down, "added": added, "removed": removed}
    out["cell_before"] = {k: (round(v, 3) if v is not None else None) for k, v in moved.items()}

    def size(key: str) -> float:
        a, b = moved[key], new.cells.get(key)
        return abs((b if b is not None else 1.0) - (a if a is not None else 1.0))
    out["top"] = [{**cell_label(k), "before": out["cell_before"][k],
                   "after": round(new.cells[k], 3) if k in new.cells else None,
                   "hours": (new.cell_info.get(k) or [None])[0], "origin": (new.cell_info.get(k) or [None, None])[1]}
                  for k in sorted(moved, key=size, reverse=True)[:TOP_CELLS] if size(k) > 0]
    out["clear_days"] = []
    for month in range(1, 13):
        start = _solar_midnight(site, year, month)
        after = clear_day_kwh(site, geo, new, start)
        out["clear_days"].append({"month": month, "before": round(clear_day_kwh(site, geo, old, start), 3) if old else None,
                                  "after": round(after, 3)})
    return out
