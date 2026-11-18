"""Local copy of the weather archives learning reads (pure Python, no Home Assistant imports).

Air temperature, snowfall and satellite irradiance of past hours hardly change, yet learning used to fetch the whole
history from Open-Meteo every night, and a single failed answer took the temperature from every hour. Noxcast keeps
what it fetched and asks only for the hours after the copy plus the last REFRESH_DAYS days: the archives fill their
latest days from preliminary data first (ERA5T, near-real-time satellite) and replace it later.

A copy belongs to the source it was fetched from: another archive, satellite model or location gives other values
for the same hours, so a changed key fetches the whole history again. Each series is stored as one hourly list from
its first hour, about 9 000 numbers per year.
"""
from __future__ import annotations

from .const import Series

HOUR = 3600
REFRESH_DAYS = 14


def series(entry: dict | None, name: str) -> Series:
    """The stored series by hour start; empty without one."""
    if not entry or name not in (entry.get("series") or {}):
        return {}
    stored = entry["series"][name]
    t0 = float(stored["t0"])
    return {t0 + i * HOUR: float(v) for i, v in enumerate(stored["v"]) if v is not None}


def fetch_from(entry: dict | None, key: list, start: float) -> float:
    """From when to fetch, so that the copy covers start up to now: start without a usable copy (none, another
    source, or one beginning later), else REFRESH_DAYS before the newest stored value."""
    if not entry or entry.get("key") != key or float(entry.get("since", start)) > start + 86400:
        return start
    newest = max((t for name in entry.get("series") or {} for t in series(entry, name)), default=None)
    if newest is None:
        return start
    first = newest - REFRESH_DAYS * 86400
    return max(start, first - first % 86400)


def merged(entry: dict | None, key: list, fetched: dict[str, Series], fetch_start: float, start: float) -> dict:
    """The copy with the fetched hours from fetch_start on replacing what it held there, and nothing before start.

    fetched: series name -> values by hour start. A copy of another source is dropped."""
    keep = entry if entry and entry.get("key") == key and fetch_start > start else None
    out: dict = {"key": key, "since": start, "series": {}}
    for name in set(fetched) | set((keep or {}).get("series") or {}):
        values = {t: v for t, v in series(keep, name).items() if start <= t < fetch_start}
        values.update({t: v for t, v in (fetched.get(name) or {}).items() if t >= start})
        out["series"][name] = _packed(values)
    return out


def _packed(values: Series) -> dict:
    if not values:
        return {"t0": 0, "v": []}
    t0 = min(values) - min(values) % HOUR
    n = int((max(values) - t0) // HOUR) + 1
    return {"t0": t0, "v": [values.get(t0 + i * HOUR) for i in range(n)]}
