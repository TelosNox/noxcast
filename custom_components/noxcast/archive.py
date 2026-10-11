"""Day-ahead forecast archive (pure Python): what was forecast, from which weather, and what happened.

Weather forecasts are not recorded by Home Assistant and the hourly forecast attribute is excluded
from the recorder, so without this archive a past forecast cannot be evaluated. Actual values come
from long-term statistics during the nightly learning run.

Layout: {"YYYY-MM-DD": {"issued": iso, "hours": {"<ts>": {"ghi": W/m², "temp": °C | None,
         "forecast": {array_id: Wh}, "actual": {array_id: Wh}}}}}
"""
from __future__ import annotations

from datetime import date

MIN_DAY_HOURS = 20


def snapshot(archive: dict, day: date, issued: str, weather: dict[float, tuple[float, float | None]],
             forecasts: dict[str, dict[float, float]]) -> bool:
    """Store (overwrite) the forecast for a coming day; the last snapshot before the day starts remains."""
    hours = {}
    for ts, (ghi, temp) in sorted(weather.items()):
        hours[str(int(ts))] = {
            "ghi": round(ghi, 1), "temp": None if temp is None else round(temp, 1),
            "forecast": {sid: round(f.get(ts, 0.0), 1) for sid, f in forecasts.items()},
        }
    if len(hours) < MIN_DAY_HOURS:
        return False
    old = archive.get(day.isoformat(), {}).get("hours", {})
    for key, hour in hours.items():                 # keep actuals if a day is snapshotted again
        if "actual" in old.get(key, {}):
            hour["actual"] = old[key]["actual"]
    archive[day.isoformat()] = {"issued": issued, "hours": hours}
    return True


def add_actuals(archive: dict, array_id: str, actual_w: dict[float, float], before: float,
                curtailed: set[float] = frozenset()) -> None:
    """Add measured hourly mean power (W = Wh per hour) for hours that have ended.

    Hours with possible curtailment (full battery) are flagged, so an evaluation can tell
    a forecast error from energy that could not be used.
    """
    for day in archive.values():
        for key, hour in day["hours"].items():
            ts = float(key)
            if ts + 3600 <= before and ts in actual_w:
                hour.setdefault("actual", {})[array_id] = round(actual_w[ts], 1)
                if ts in curtailed:
                    hour["curtailed"] = True


def prune(archive: dict, oldest: date) -> None:
    for key in [k for k in archive if k < oldest.isoformat()]:
        del archive[key]


def day_totals(archive: dict, array_id: str | None = None) -> dict[str, tuple[float, float]]:
    """Complete days only: {day: (forecast Wh, actual Wh)}; array_id None sums all arrays."""
    out = {}
    for key, day in archive.items():
        hours = day["hours"].values()
        if not hours or any("actual" not in h for h in hours):
            continue
        ids = [array_id] if array_id else list(next(iter(hours))["forecast"])
        if any(sid not in h["actual"] or sid not in h["forecast"] for h in hours for sid in ids):
            continue
        out[key] = (sum(h["forecast"][sid] for h in hours for sid in ids),
                    sum(h["actual"][sid] for h in hours for sid in ids))
    return out


def daily_error(archive: dict, since: date, array_id: str | None = None) -> tuple[float | None, int]:
    """Relative daily energy error |forecast − actual| / actual over complete days since a date."""
    days = {k: v for k, v in day_totals(archive, array_id).items() if k >= since.isoformat()}
    actual = sum(a for _, a in days.values())
    if not days or actual <= 0:
        return None, len(days)
    return sum(abs(f - a) for f, a in days.values()) / actual, len(days)
