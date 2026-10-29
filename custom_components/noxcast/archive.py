"""Day-ahead forecast archive (pure Python): what was forecast, from which weather, and what happened.

Weather forecasts are not recorded by Home Assistant and the hourly forecast attribute is excluded
from the recorder, so without this archive a past forecast cannot be evaluated. Actual values come
from long-term statistics during the nightly learning run.

Layout: {"YYYY-MM-DD": {"issued": iso, "hours": {"<ts>": {"ghi": W/m², "temp": °C | None,
         "wx": {"cloud": %, "sun": 0-1, "wind": m/s, "wdir": °},
         "forecast": {array_id: Wh}, "actual": {array_id: Wh},
         "alt": {source: {"ghi": W/m², "wx": {...}, "forecast": {array_id: Wh}}},
         "sat_ghi": W/m², "measured": {array_id: Wh}}},
         "history": {"<snapshot ts>": {"<ts>": Wh}}}}

"history" holds the expectation for the whole day (actual so far + corrected rest, all arrays) as it
stood at each full hour of that day, so the panel can show how the forecast developed.

"wx" holds what the weather source says besides irradiance (cloud cover, sunshine share of the
hour, wind), only for hours with irradiance and only the values the source has – about 40 bytes
per daylight hour and source. It is not used by the model; it allows comparing sources and
predictors (e.g. sunshine duration instead of irradiance, wind cooling) on archived days.

"alt" holds the same day computed from each weather model on its own when the forecast averages
several (e.g. ICON and ECMWF IFS), so each model can be compared with their mean and the actual
values. Days archived before Open-Meteo became the only source hold "open_meteo" there, next to a
weather entity as primary source.

"measured" is the same model run on the satellite-measured irradiance of the hour: it splits the
deviation into a weather part (forecast − measured) and a model part (measured − actual).

The stored dict is written only here (snapshot, add_*, prune) and read elsewhere only through the
read-only views Day / Hour / Source (days(), day(), dates()); they parse the string keys and leave
the stored format untouched. Values are the stored objects themselves: readers must not modify them.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

MIN_DAY_HOURS = 20
HISTORY_HOURS = range(5, 22)     # local hours at which the day's expectation is recorded


@dataclass(frozen=True, slots=True)
class Source:
    """An alternative weather model's forecast of an hour ("alt")."""
    ghi: float | None                    # forecast GHI of that model, W/m²
    forecast: dict[str, float]           # array id -> Wh from that model's weather
    wx: dict                             # weather details (see details()), {} if none


@dataclass(frozen=True, slots=True)
class Hour:
    """One archived hour of a day-ahead forecast."""
    ts: float                            # hour start (UTC timestamp)
    ghi: float | None                    # forecast GHI (mean of the models), W/m²
    temp: float | None                   # forecast air temperature, °C
    wx: dict                             # weather details (see details()), {} if none
    forecast: dict[str, float]           # array id -> day-ahead forecast, Wh
    actual: dict[str, float] | None      # array id -> measured Wh; None until the hour is learned
    alt: dict[str, Source]               # weather model name -> its own forecast, {} if one model
    curtailed: bool                      # possibly curtailed (full battery): actual is no measure of the sun
    sat_ghi: float | None                # satellite-measured GHI, W/m²
    measured: dict[str, float] | None    # array id -> model output from sat_ghi, Wh


@dataclass(frozen=True, slots=True)
class Day:
    """One archived day: its day-ahead forecast hour by hour and how the expectation developed."""
    date: str                            # local date, ISO
    issued: str | None                   # when the forecast was made (ISO), the last snapshot before the day
    hours: list[Hour]                    # by hour start
    history: list[tuple[float, list[tuple[float, float]]]]   # (snapshot ts, [(hour start, Wh all arrays)]), by time

    @property
    def complete(self) -> bool:
        """Every hour has its actual values."""
        return bool(self.hours) and all(h.actual is not None for h in self.hours)


def _hour(key: str, h: dict) -> Hour:
    return Hour(float(key), h.get("ghi"), h.get("temp"), h.get("wx", {}), h.get("forecast", {}), h.get("actual"),
                {name: Source(s.get("ghi"), s.get("forecast", {}), s.get("wx", {})) for name, s in h.get("alt", {}).items()},
                bool(h.get("curtailed")), h.get("sat_ghi"), h.get("measured"))


def _day(key: str, d: dict) -> Day:
    hours = sorted((_hour(k, h) for k, h in (d.get("hours") or {}).items()), key=lambda h: h.ts)
    history = [(float(s), sorted((float(t), v) for t, v in snap.items()))
               for s, snap in sorted((d.get("history") or {}).items(), key=lambda kv: float(kv[0]))]
    return Day(key, d.get("issued"), hours, history)


def days(archive: dict) -> list[Day]:
    """All archived days, by date."""
    return [_day(k, archive[k]) for k in sorted(archive)]


def day(archive: dict, when: date | str) -> Day | None:
    key = when if isinstance(when, str) else when.isoformat()
    return _day(key, archive[key]) if key in archive else None


def dates(archive: dict) -> list[str]:
    """Archived dates (ISO), in order."""
    return sorted(archive)


def from_store(data: dict | None) -> dict:
    """The archive from its stored form ({"days": archive})."""
    return (data or {}).get("days", {})


def to_store(archive: dict) -> dict:
    return {"days": archive}


def details(cloud: float | None = None, sun: float | None = None, wind: float | None = None,
            wind_dir: float | None = None) -> dict:
    """Compact weather details of an hour; values the source does not have are left out."""
    out = {}
    if cloud is not None:
        out["cloud"] = round(cloud)
    if sun is not None:
        out["sun"] = round(sun, 2)
    if wind is not None:
        out["wind"] = round(wind, 1)
    if wind_dir is not None:
        out["wdir"] = round(wind_dir) % 360
    return out


def snapshot(archive: dict, day: date, issued: str, weather: dict[float, tuple[float, float | None]],
             forecasts: dict[str, dict[float, float]],
             alt: dict[str, tuple[dict[float, float], dict[str, dict[float, float]]]] | None = None,
             wx: dict[float, dict] | None = None, alt_wx: dict[str, dict[float, dict]] | None = None) -> bool:
    """Store (overwrite) the forecast for a coming day; the last snapshot before the day starts remains.

    wx / alt_wx: weather details per hour (see details()) of the primary / an alternative source.
    """
    hours = {}
    for ts, (ghi, temp) in sorted(weather.items()):
        hours[str(int(ts))] = hour = {
            "ghi": round(ghi, 1), "temp": None if temp is None else round(temp, 1),
            "forecast": {sid: round(f.get(ts, 0.0), 1) for sid, f in forecasts.items()},
        }
        if ghi > 0 and (wx or {}).get(ts):
            hour["wx"] = wx[ts]
        for name, (alt_ghi, alt_fc) in (alt or {}).items():
            if ts in alt_ghi:
                hour.setdefault("alt", {})[name] = entry = {
                    "ghi": round(alt_ghi[ts], 1), "forecast": {sid: round(f.get(ts, 0.0), 1) for sid, f in alt_fc.items()}}
                if alt_ghi[ts] > 0 and (alt_wx or {}).get(name, {}).get(ts):
                    entry["wx"] = alt_wx[name][ts]
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


def forecast(hour: Hour, source: str | None = None) -> dict[str, float] | None:
    """Forecast of an hour from the primary source or an alternative source (None if not archived)."""
    if source is None:
        return hour.forecast
    return hour.alt[source].forecast if source in hour.alt else None


def alt_sources(archive: dict, since: date | None = None) -> list[str]:
    """Names of the alternative sources archived (since a date), in order of appearance."""
    names: dict[str, None] = {}
    for d in days(archive):
        if since is None or d.date >= since.isoformat():
            for hour in d.hours:
                names.update(dict.fromkeys(hour.alt))
    return list(names)


def day_totals(archive: dict, array_id: str | None = None, source: str | None = None) -> dict[str, tuple[float, float]]:
    """Complete days only: {day: (forecast Wh, actual Wh)}; array_id None sums all arrays.

    source: None = primary weather source or an alternative source name.
    """
    out = {}
    for d in days(archive):
        if not d.complete:
            continue
        hours = d.hours
        forecasts = [forecast(h, source) for h in hours]
        if any(f is None for f in forecasts):
            continue
        ids = [array_id] if array_id else list(hours[0].forecast)
        if any(sid not in h.actual or sid not in f for h, f in zip(hours, forecasts) for sid in ids):
            continue
        out[d.date] = (sum(f[sid] for f in forecasts for sid in ids),
                       sum(h.actual[sid] for h in hours for sid in ids))
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
