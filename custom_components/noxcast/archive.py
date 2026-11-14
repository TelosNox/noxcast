"""Day-ahead forecast archive (pure Python): what was forecast, from which weather, and what happened.

Weather forecasts are not recorded by Home Assistant and the hourly forecast attribute is excluded
from the recorder, so without this archive a past forecast cannot be evaluated. Actual values come
from long-term statistics during the nightly learning run.

Layout: {"YYYY-MM-DD": {"issued": iso, "models": [weather model, ...], "band": [P10, P90 kWh],
         "hours": {"<ts>": {"ghi": W/m², "dni": W/m², "dhi": W/m², "temp": °C | None,
         "wx": {"cloud": %, "sun": 0-1, "wind": m/s, "wdir": °},
         "forecast": {array_id: Wh}, "actual": {array_id: Wh}, "steps": {array_id: [Wh, ...]},
         "alt": {source: {"ghi": W/m², "dni": W/m², "dhi": W/m², "wx": {...}, "forecast": {array_id: Wh}}},
         "sat_ghi": W/m², "measured": {array_id: Wh},
         "ens": {ensemble model: [P10, P50, P90 GHI W/m², members]}, "band": [P10, P90 Wh]}},
         "history": {"<snapshot ts>": {"<ts>": Wh}}, "shift": {"<snapshot ts>": {array_id: clearness shift}}}}

Every field but "hours" and the hour's "ghi" and "forecast" may be missing: each was added later, and days
archived before keep their rows without it. The read-only views give None or {} for them.

"history" holds the expectation for the whole day (actual so far + corrected rest, all arrays) as it
stood at each full hour of that day, so the panel can show how the forecast developed.

"wx" holds what the weather source says besides irradiance (cloud cover, sunshine share of the
hour, wind), only for hours with irradiance and only the values the source has – about 40 bytes
per daylight hour and source. It is not used by the model; it allows comparing sources and
predictors (e.g. sunshine duration instead of irradiance, wind cooling) on archived days.

Executor jobs read the live archive while the event loop keeps changing it. So the event loop never changes
a day in place: snapshot() and add_history() put a new day dict, and the caller rebinds a copy of the
top-level dict before adding or removing days (snapshot, prune). A job that adds measured values gets a
shallow copy as its base, works on a deep copy of it, and merged() brings the values back.

"alt" holds the same day computed from each weather model on its own when the forecast averages
several (e.g. ICON and ECMWF IFS), so each model can be compared with their mean and the actual
values. Days archived before Open-Meteo became the only source hold "open_meteo" there, next to a
weather entity as primary source.

"models" names the weather models whose mean the forecast was made from, as configured when it was
issued; days archived before it have none.

"steps" holds how the forecast of an hour comes about (explain.packed: clear sky, clouds, shade,
reflection, direction, heat; their sum is the forecast), only for hours with light, so the day view
explains the forecast that was made and not a replay with the model learned since. Days archived
before it have none.

"ens" holds the spread of the ensemble runs (see ensemble()), for daylight hours only – about 50 bytes
per hour. It is not used by the forecast or the band yet; it allows testing whether the spread of the
day tells a sure forecast from an unsure one better than the weather class does (band.py).

"band" is the P10 / P90 of the total that was published with the forecast (band.forecast_band), per hour
and for the day, so a later calibration can check the band users saw, not one recomputed from the band
learned since. "dni" / "dhi" are the split of the forecast irradiance (mean and per model) for daylight hours
with both values, so a later comparison of weather sources can check the split and not only GHI.

"shift" holds the intraday clearness shift per array (intraday.clearness_shift) at each history stamp, so the
corrections applied during the day can be evaluated against the actual values. Arrays without a shift at that
time are left out.

Days are kept for ARCHIVE_DAYS (400): a full year for seasonal checks. A day takes about 15–30 KB, depending
on daylight and the number of arrays and weather models. The band, the backtest and the accuracy page still
read only the last EVAL_DAYS (recent()), as before the archive grew.

"measured" is the same model run on the satellite-measured irradiance of the hour: it splits the
deviation into a weather part (forecast − measured) and a model part (measured − actual).

The stored dict is written only here (snapshot, add_*, prune, merged) and read elsewhere only through the
read-only views Day / Hour / Source (days(), day(), dates()); they parse the string keys and leave
the stored format untouched. Values are the stored objects themselves: readers must not modify them.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from statistics import quantiles

from .const import Series

MIN_DAY_HOURS = 20
HISTORY_HOURS = range(5, 22)     # local hours at which the day's expectation is recorded


@dataclass(frozen=True, slots=True)
class Source:
    """An alternative weather model's forecast of an hour ("alt")."""
    ghi: float | None                    # forecast GHI of that model, W/m²
    forecast: dict[str, float]           # array id -> Wh from that model's weather
    wx: dict                             # weather details (see details()), {} if none
    dni: float | None = None             # forecast direct normal irradiance of that model, W/m²
    dhi: float | None = None             # forecast diffuse horizontal irradiance of that model, W/m²


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
    steps: dict[str, list[float]]        # array id -> steps of the forecast (explain.packed), {} if not archived
    ens: dict[str, list[float]] = field(default_factory=dict)   # ensemble model -> [P10, P50, P90 GHI, members]
    dni: float | None = None             # forecast direct normal irradiance (mean of the models), W/m²
    dhi: float | None = None             # forecast diffuse horizontal irradiance (mean of the models), W/m²
    band: list[float] | None = None      # published [P10, P90] of the total, Wh; None if not archived
    limited: bool = False                # an array was at a fixed limit: actual and forecast both capped


@dataclass(frozen=True, slots=True)
class Day:
    """One archived day: its day-ahead forecast hour by hour and how the expectation developed."""
    date: str                            # local date, ISO
    issued: str | None                   # when the forecast was made (ISO), the last snapshot before the day
    hours: list[Hour]                    # by hour start
    history: list[tuple[float, list[tuple[float, float]]]]   # (snapshot ts, [(hour start, Wh all arrays)]), by time
    models: list[str]                    # weather models of the forecast, [] if archived before they were kept
    band: list[float] | None = None      # published [P10, P90] of the day's total, kWh; None if not archived
    shift: list[tuple[float, dict[str, float]]] = field(default_factory=list)   # (stamp, array id -> shift), by time

    @property
    def complete(self) -> bool:
        """Every hour has its actual values."""
        return bool(self.hours) and all(h.actual is not None for h in self.hours)


def _hour(key: str, h: dict) -> Hour:
    return Hour(float(key), h.get("ghi"), h.get("temp"), h.get("wx", {}), h.get("forecast", {}), h.get("actual"),
                {name: Source(s.get("ghi"), s.get("forecast", {}), s.get("wx", {}), s.get("dni"), s.get("dhi"))
                 for name, s in h.get("alt", {}).items()},
                bool(h.get("curtailed")), h.get("sat_ghi"), h.get("measured"), h.get("steps", {}), h.get("ens", {}),
                h.get("dni"), h.get("dhi"), h.get("band"), bool(h.get("limited")))


def _day(key: str, d: dict) -> Day:
    hours = sorted((_hour(k, h) for k, h in (d.get("hours") or {}).items()), key=lambda h: h.ts)
    history = [(float(s), sorted((float(t), v) for t, v in snap.items()))
               for s, snap in sorted((d.get("history") or {}).items(), key=lambda kv: float(kv[0]))]
    shift = [(float(s), v) for s, v in sorted((d.get("shift") or {}).items(), key=lambda kv: float(kv[0]))]
    return Day(key, d.get("issued"), hours, history, list(d.get("models") or []), d.get("band"), shift)


def complete_days(archive: dict) -> int:
    """Days whose every hour has its actual values, counted on the stored form without building the days."""
    return sum(1 for d in archive.values()
               if (hours := d.get("hours")) and all(h.get("actual") is not None for h in hours.values()))


def days(archive: dict) -> list[Day]:
    """All archived days, by date."""
    return [_day(k, archive[k]) for k in sorted(archive)]


def day(archive: dict, when: date | str) -> Day | None:
    key = when if isinstance(when, str) else when.isoformat()
    return _day(key, archive[key]) if key in archive else None


def dates(archive: dict) -> list[str]:
    """Archived dates (ISO), in order."""
    return sorted(archive)


def recent(archive: dict, oldest: date) -> dict:
    """The days from `oldest` on, as a new top-level dict of the same (shared) day dicts."""
    return {k: v for k, v in archive.items() if k >= oldest.isoformat()}


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


MIN_MEMBERS = 10           # fewer runs give no usable P10 / P90


def ensemble(members: list[float]) -> list[float] | None:
    """[P10, P50, P90, number of runs] of the GHI of an hour's ensemble runs (W/m², rounded); None if too few.

    Linear interpolation between the sorted runs, as for the band's quantiles (band.py).
    """
    if len(members) < MIN_MEMBERS:
        return None
    q = quantiles(members, n=10, method="inclusive")
    return [round(q[0]), round(q[4]), round(q[8]), len(members)]


def snapshot(archive: dict, day: date, issued: str, weather: dict[float, tuple[float, float | None]],
             forecasts: dict[str, Series],
             alt: dict[str, tuple[Series, dict[str, Series]]] | None = None,
             wx: dict[float, dict] | None = None, alt_wx: dict[str, dict[float, dict]] | None = None,
             steps: dict[str, dict[float, list[float]]] | None = None, models: list[str] | None = None,
             ens: dict[str, dict[float, list[float]]] | None = None,
             split: dict[float, tuple[float | None, float | None]] | None = None,
             alt_split: dict[str, dict[float, tuple[float | None, float | None]]] | None = None,
             band: dict[float, tuple[float, float]] | None = None, day_band: list[float] | None = None) -> bool:
    """Store (overwrite) the forecast for a coming day; the last snapshot before the day starts remains.

    wx / alt_wx: weather details per hour (see details()) of the primary / an alternative source;
    steps: per array and hour the steps of the forecast (explain.packed); models: the weather models;
    ens: per ensemble model and hour the GHI of its runs, kept as ensemble() for hours with light; without
    them an hour keeps the spread of its earlier snapshot. split / alt_split: per hour (DNI, DHI) of the primary
    / an alternative source; band / day_band: the published P10 / P90 of the total per hour (Wh) and for the
    day (kWh).
    """
    def add_split(entry: dict, ghi: float, values: tuple[float | None, float | None] | None) -> None:
        if ghi > 0 and values and values[0] is not None and values[1] is not None:
            entry["dni"], entry["dhi"] = round(values[0], 1), round(values[1], 1)

    hours = {}
    for ts, (ghi, temp) in sorted(weather.items()):
        hours[str(int(ts))] = hour = {
            "ghi": round(ghi, 1), "temp": None if temp is None else round(temp, 1),
            "forecast": {sid: round(f.get(ts, 0.0), 1) for sid, f in forecasts.items()},
        }
        if ghi > 0 and (wx or {}).get(ts):
            hour["wx"] = wx[ts]
        add_split(hour, ghi, (split or {}).get(ts))
        if (band or {}).get(ts, (0.0, 0.0))[1] > 0:           # dark hours have no spread to keep
            hour["band"] = [round(band[ts][0], 1), round(band[ts][1], 1)]
        packed = {sid: [round(v, 1) for v in by_hour[ts]] for sid, by_hour in (steps or {}).items() if ts in by_hour}
        if packed:
            hour["steps"] = packed
        for name, (alt_ghi, alt_fc) in (alt or {}).items():
            if ts in alt_ghi:
                hour.setdefault("alt", {})[name] = entry = {
                    "ghi": round(alt_ghi[ts], 1), "forecast": {sid: round(f.get(ts, 0.0), 1) for sid, f in alt_fc.items()}}
                if alt_ghi[ts] > 0 and (alt_wx or {}).get(name, {}).get(ts):
                    entry["wx"] = alt_wx[name][ts]
                add_split(entry, alt_ghi[ts], (alt_split or {}).get(name, {}).get(ts))
        for name, by_hour in (ens or {}).items():
            if (spread := ensemble(by_hour.get(ts, []))) and (ghi > 0 or spread[2] > 0):
                hour.setdefault("ens", {})[name] = spread
    if len(hours) < MIN_DAY_HOURS:
        return False
    history = archive.get(day.isoformat(), {}).get("history")
    shift = archive.get(day.isoformat(), {}).get("shift")
    old = archive.get(day.isoformat(), {}).get("hours", {})
    for key, hour in hours.items():                 # keep actuals if a day is snapshotted again
        if "actual" in old.get(key, {}):
            hour["actual"] = old[key]["actual"]
        if "ens" not in hour and "ens" in old.get(key, {}):     # the ensembles failed (e.g. after a restart)
            hour["ens"] = old[key]["ens"]
    archive[day.isoformat()] = {"issued": issued, "hours": hours}
    if models:
        archive[day.isoformat()]["models"] = list(models)
    if day_band:
        archive[day.isoformat()]["band"] = [round(day_band[0], 3), round(day_band[1], 3)]
    if history:
        archive[day.isoformat()]["history"] = history
    if shift:
        archive[day.isoformat()]["shift"] = shift
    return True


def add_actuals(archive: dict, array_id: str, actual_w: Series, before: float,
                curtailed: set[float] = frozenset(), limited: set[float] = frozenset()) -> None:
    """Add measured hourly mean power (W = Wh per hour) for hours that have ended.

    Hours with possible curtailment (full battery) are flagged, so an evaluation can tell
    a forecast error from energy that could not be used. Hours in which the array was at a fixed limit
    are flagged as limited: the forecast is capped the same way (a limit shared by several arrays caps
    no forecast yet), so its error still counts, but the actual value does not show how clear the sky was.
    Both flags hold for the hour, not per array: one array's full battery marks it for all.
    """
    for day in archive.values():
        for key, hour in day["hours"].items():
            ts = float(key)
            if ts + 3600 <= before and ts in actual_w:
                hour.setdefault("actual", {})[array_id] = round(actual_w[ts], 1)
                if ts in curtailed:
                    hour["curtailed"] = True
                if ts in limited:
                    hour["limited"] = True


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
    return _error({k: v for k, v in day_totals(archive, array_id, source).items() if k >= since.isoformat()})


def _error(days: dict[str, tuple[float, float]]) -> tuple[float | None, int]:
    actual = sum(a for _, a in days.values())
    if not days or actual <= 0:
        return None, len(days)
    return sum(abs(f - a) for f, a in days.values()) / actual, len(days)


def _percent(error: float | None) -> float | None:
    return None if error is None else round(error * 100, 1)


def error_report(archive: dict, since: date, arrays: dict[str, str]) -> dict:
    """The forecast error sensor: error in % over complete days since a date, per array and per alternative source.

    arrays: {array id: name}. Each source's totals are computed once; the archive holds months of days, so
    recomputing them per day or per value took about a second.
    """
    def recent(totals: dict) -> dict:
        return {k: v for k, v in totals.items() if k >= since.isoformat()}

    primary = recent(day_totals(archive))
    error, days = _error(primary)
    per_array = {name: _percent(_error(recent(day_totals(archive, sid)))[0]) for sid, name in arrays.items()}
    comparison = {}
    for name in alt_sources(archive, since):        # each model, on the days it has
        alt = recent(day_totals(archive, source=name))
        alt_error, n = _error(alt)
        if n:
            comparison[name] = {"error": _percent(alt_error), "days": n,
                                "primary_error": _percent(_error({k: v for k, v in primary.items() if k in alt})[0])}
    return {"error": _percent(error), "days": days, "per_array": per_array, "comparison": comparison}


def add_history(archive: dict, day: date, stamp: float, total: Series,
                shift: dict[str, float | None] | None = None) -> bool:
    """Record the day's expectation (Wh per hour, all arrays) as it stands at `stamp`, once per stamp.

    Only for a day that is in the archive (its day-ahead forecast is the start of the history). shift: the
    intraday clearness shift per array at that time (None: no correction), kept next to the expectation.
    """
    entry = archive.get(day.isoformat())
    if entry is None:
        return False
    key = str(int(stamp))
    if key in entry.get("history", {}):
        return False
    # a new day dict, never a change in place: an executor job may be reading the old one
    history = {**entry.get("history", {}), key: {str(int(t)): round(v) for t, v in sorted(total.items()) if v >= 0.5}}
    new = {**entry, "history": history}
    if applied := {sid: round(v, 3) for sid, v in (shift or {}).items() if v is not None}:
        new["shift"] = {**entry.get("shift", {}), key: applied}
    archive[day.isoformat()] = new
    return True


MEASURED_FIELDS = ("actual", "curtailed", "limited", "sat_ghi", "measured")


def merged(current: dict, base: dict, work: dict) -> dict:
    """The live archive with what a job added to `work`, its deep copy of `base` (add_actuals, add_measured).

    A day the event loop has not replaced since is taken from work. One it replaced (a new snapshot, a history
    stamp) keeps the loop's version with the measured fields of work; days the loop pruned stay away.
    """
    out = {}
    for key, day in current.items():
        done = work.get(key)
        if done is None:
            out[key] = day
        elif day is base.get(key):
            out[key] = done
        else:
            hours = {}
            for k, hour in day.get("hours", {}).items():
                add = {f: v for f in MEASURED_FIELDS if (v := done.get("hours", {}).get(k, {}).get(f)) is not None}
                hours[k] = {**hour, **add} if add else hour
            out[key] = {**day, "hours": hours}
    return out


def add_measured(archive: dict, array_id: str, satellite_ghi: Series, air_temp: Series | None,
                 predict: Callable[[list[tuple[float, float, float | None]]], Series], before: float) -> int:
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
