"""Day-ahead forecast archive (pure Python): what was forecast, from which weather, and what happened.

Weather forecasts are not recorded by Home Assistant and the hourly forecast attribute is excluded
from the recorder, so without this archive a past forecast cannot be evaluated. Actual values come
from long-term statistics during the nightly learning run.

Layout: {"YYYY-MM-DD": {"issued": iso, "hours": {"<ts>": {"ghi": W/m², "temp": °C | None,
         "forecast": {array_id: Wh}, "actual": {array_id: Wh},
         "alt": {source: {"ghi": W/m², "forecast": {array_id: Wh}}},
         "sat_ghi": W/m², "measured": {array_id: Wh}}},
         "history": {"<snapshot ts>": {"<ts>": Wh}}}}

"history" holds the expectation for the whole day (actual so far + corrected rest, all arrays) as it
stood at each full hour of that day, so the panel can show how the forecast developed.

"alt" holds the same day computed from another weather source (e.g. Open-Meteo next to DWD),
so sources and their mix can be compared against the actual values.

"measured" is the same model run on the satellite-measured irradiance of the hour: it splits the
deviation into a weather part (forecast − measured) and a model part (measured − actual).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date

MIN_DAY_HOURS = 20
HISTORY_HOURS = range(5, 22)     # local hours at which the day's expectation is recorded


def snapshot(archive: dict, day: date, issued: str, weather: dict[float, tuple[float, float | None]],
             forecasts: dict[str, dict[float, float]],
             alt: dict[str, tuple[dict[float, float], dict[str, dict[float, float]]]] | None = None) -> bool:
    """Store (overwrite) the forecast for a coming day; the last snapshot before the day starts remains."""
    hours = {}
    for ts, (ghi, temp) in sorted(weather.items()):
        hours[str(int(ts))] = {
            "ghi": round(ghi, 1), "temp": None if temp is None else round(temp, 1),
            "forecast": {sid: round(f.get(ts, 0.0), 1) for sid, f in forecasts.items()},
        }
        for name, (alt_ghi, alt_fc) in (alt or {}).items():
            if ts in alt_ghi:
                hours[str(int(ts))].setdefault("alt", {})[name] = {
                    "ghi": round(alt_ghi[ts], 1), "forecast": {sid: round(f.get(ts, 0.0), 1) for sid, f in alt_fc.items()}}
    if len(hours) < MIN_DAY_HOURS:
        return False
    history = archive.get(day.isoformat(), {}).get("history")
    old = archive.get(day.isoformat(), {}).get("hours", {})
    for key, hour in hours.items():                 # keep actuals if a day is snapshotted again
        if "actual" in old.get(key, {}):
            hour["actual"] = old[key]["actual"]
    archive[day.isoformat()] = {"issued": issued, "hours": hours}
    if history:
        archive[day.isoformat()]["history"] = history
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


MIX = "mix"


def _forecast(hour: dict, source: str | None) -> dict[str, float] | None:
    """Forecast of an hour from the primary source, an alternative source or the mean of both."""
    if source is None:
        return hour["forecast"]
    alt = hour.get("alt", {})
    if source == MIX:
        other = next(iter(alt.values()), None)
        if other is None:
            return None
        return {sid: (wh + other["forecast"].get(sid, wh)) / 2 for sid, wh in hour["forecast"].items()}
    return alt[source]["forecast"] if source in alt else None


def day_totals(archive: dict, array_id: str | None = None, source: str | None = None) -> dict[str, tuple[float, float]]:
    """Complete days only: {day: (forecast Wh, actual Wh)}; array_id None sums all arrays.

    source: None = primary weather source, an alternative source name, or MIX (mean of primary and alternative).
    """
    out = {}
    for key, day in archive.items():
        hours = list(day["hours"].values())
        if not hours or any("actual" not in h for h in hours):
            continue
        forecasts = [_forecast(h, source) for h in hours]
        if any(f is None for f in forecasts):
            continue
        ids = [array_id] if array_id else list(hours[0]["forecast"])
        if any(sid not in h["actual"] or sid not in f for h, f in zip(hours, forecasts) for sid in ids):
            continue
        out[key] = (sum(f[sid] for f in forecasts for sid in ids),
                    sum(h["actual"][sid] for h in hours for sid in ids))
    return out


def daily_error(archive: dict, since: date, array_id: str | None = None,
                source: str | None = None) -> tuple[float | None, int]:
    """Relative daily energy error |forecast − actual| / actual over complete days since a date."""
    days = {k: v for k, v in day_totals(archive, array_id, source).items() if k >= since.isoformat()}
    actual = sum(a for _, a in days.values())
    if not days or actual <= 0:
        return None, len(days)
    return sum(abs(f - a) for f, a in days.values()) / actual, len(days)


def add_history(archive: dict, day: date, stamp: float, total: dict[float, float]) -> bool:
    """Record the day's expectation (Wh per hour, all arrays) as it stands at `stamp`, once per stamp.

    Only for a day that is in the archive (its day-ahead forecast is the start of the history).
    """
    entry = archive.get(day.isoformat())
    if entry is None:
        return False
    history = entry.setdefault("history", {})
    key = str(int(stamp))
    if key in history:
        return False
    history[key] = {str(int(t)): round(v) for t, v in sorted(total.items()) if v >= 0.5}
    return True


def add_measured(archive: dict, array_id: str, satellite_ghi: dict[float, float], air_temp: dict[float, float] | None,
                 predict: Callable[[list[tuple[float, float, float | None]]], dict[float, float]], before: float) -> int:
    """Model output from the measured weather for ended hours that have satellite irradiance.

    Computed once per hour and array with the parameters learned at that time, so a later relearn
    does not rewrite history. predict: [(hour start, GHI, air temperature)] -> {hour start: Wh}.
    Returns the number of hours filled.
    """
    todo, slots = [], []
    for day in archive.values():
        for key, hour in day["hours"].items():
            ts = float(key)
            if ts + 3600 > before or ts not in satellite_ghi or array_id in hour.get("measured", {}):
                continue
            temp = (air_temp or {}).get(ts, hour.get("temp"))
            todo.append((ts, max(0.0, satellite_ghi[ts]), temp))
            slots.append((ts, hour))
    if not todo:
        return 0
    out = predict(todo)
    for ts, hour in slots:
        hour["sat_ghi"] = round(max(0.0, satellite_ghi[ts]), 1)
        hour.setdefault("measured", {})[array_id] = round(out.get(ts, 0.0), 1)
    return len(slots)
