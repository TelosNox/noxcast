"""What the sidebar panel and the day card show: the websocket payloads, built from plain data.

Pure functions, no Home Assistant imports. panel.py validates a command, gathers the inputs from the
coordinator (and the recorder), calls one of these and sends the result. The payloads are what the
frontend reads (custom_components/noxcast/frontend/*.js): keys and rounding are part of that contract
and change only together with the frontend.

Payload conventions (schema WS_SCHEMA_VERSION in const.py, sent as "v" in every answer; raise it with
every change the frontend has to follow):
- Every value is named. No positional tuples: what was [ts, a, b] is an object.
- A time series is a column table: {"t": [hour starts], "<name>": [values], ...}, all lists of equal
  length, by time. One name per quantity: "wh" (energy of the hour), "w" (mean power), "p10"/"p90",
  "soc" (fraction), "ghi"/"clear" (W/m²), "temp" (°C), "pv"/"load" (Wh). Columns keep the bulk of the
  payloads (hourly Wh of every array) compact: no brackets per hour, the names once per series.
  The frontend reads them with tsRows(series, names...) at the boundary.
- Lists indexed by hour of day (0-23), such as load profiles, are plain lists; maps keyed by timestamp
  or id stay maps.

Local days come in as timestamps or as a callable (date -> local midnight), so the time zone stays
with the caller.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import archive as fc_archive
from . import battery as bt
from . import explain
from .const import (TOTAL, Series, CONF_BATTERY_INVERT, CONF_BATTERY_POWER, CONF_CAPACITY, CONF_MAX_SOC, CONF_MAX_SOC_ENTITY,
                    CONF_MIN_SOC, CONF_MIN_SOC_ENTITY, CONF_RESERVE, CONF_SOC_SENSOR)
from .model import LearnedArray, clearsky_weather, facade_cells
from .solar import Site


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def columns(rows, *names: str) -> dict[str, list]:
    """A time series as a column table: rows (t, value per name) -> {"t": [...], name: [...], ...}."""
    out: dict[str, list] = {"t": [], **{n: [] for n in names}}
    for row in rows:
        for key, v in zip(out, row, strict=True):
            out[key].append(v)
    return out


def series(hours: Series, a: float = float("-inf"), b: float = float("inf")) -> dict:
    """{"t": hour starts, "wh": values rounded to 0.1} by time, optionally only hours in [a, b)."""
    return columns(((t, round(v, 1)) for t, v in sorted(hours.items()) if a <= t < b), "wh")


def band_series(band: dict | None, a: float = float("-inf"), b: float = float("inf")) -> dict:
    """{"t", "p10", "p90"} of the total, by time."""
    return columns(((t, round(lo, 1), round(hi, 1)) for t, (lo, hi) in sorted((band or {}).get("hours", {}).items())
                    if a <= t < b), "p10", "p90")


# ---------------------------------------------------------------- noxcast/learned

def learned_arrays(arrays: dict, learned: dict[str, LearnedArray], last_learning: datetime | None) -> list[dict]:
    """Geometry and learned state per array, with the facade cells filled for display."""
    out = []
    for sub_id, arr in arrays.items():
        la = learned.get(sub_id)
        out.append({
            "id": sub_id, "name": arr.name, "tilt": arr.geometry.tilt, "azimuth": arr.geometry.azimuth,
            "peak_w": arr.geometry.peak_w, "planes": arr.geometry.describe(),
            "learned": learned_dict(la) if la else None,
            "facade_cells": facade_cells(la) if la else {},
            "last_learning": last_learning.isoformat() if last_learning else None,
        })
    return out


def learned_dict(la: LearnedArray) -> dict:
    """The learned state as stored, with the months named: "YYYY-MM" -> {"efficiency", "hours"}."""
    out = la.as_dict()
    out["efficiency_months"] = {m: {"efficiency": e, "hours": n} for m, (e, n) in (la.efficiency_months or {}).items()}
    return out


# ---------------------------------------------------------------- noxcast/today (day card)

def today_arrays(arrays: dict, data, tomorrow_start: float, tomorrow_end: float) -> list[dict]:
    """Per array: expected, held and morning forecast per hour, effects, tomorrow."""
    out = []
    for sub_id, arr in arrays.items():
        effects = data.effects.get(sub_id, {})
        tomorrow = {t: v for t, v in data.forecast.get(sub_id, {}).items() if tomorrow_start <= t < tomorrow_end}
        out.append({
            "id": sub_id, "name": arr.name, "peak_w": arr.geometry.peak_w,
            "expected": series(data.expected.get(sub_id, {})),
            "morning": series(data.morning.get(sub_id, {})),
            "forecast": series(data.held.get(sub_id, {})),
            "tomorrow": series(tomorrow),
            "loss": series(effects.get("loss", {})),
            "gain": series(effects.get("gain", {})),
            "periods": effects.get("periods", []),
            "tomorrow_wh": round(sum(tomorrow.values())),
            "updated": data.updated.isoformat() if data.updated else None,
        })
    return out


def today_totals(data, consumers: dict, days: int, today: date, day_start: Callable[[date], float]) -> dict:
    """What Noxcast computed for the whole site: days, load, band, battery, plan (in this key order)."""
    out: dict[str, Any] = {"days": day_totals(data, days, today, day_start)}
    if data.load is not None:
        out["load"] = series(data.load.hours)
    if data.band:
        out["band"] = band_series(data.band)
    b = data.battery
    if b:
        sim, storage = b["sim"], b["storage"]
        out["battery"] = {
            "points": columns(((t, round(f, 4)) for t, f in sim.points), "soc"),
            "full": sim.full_at, "empty": sim.empty_at,
            "lost_today": round(sum(h["lost"] for t, h in sim.hours.items() if t < b["day_end"]), 1),
            "charge_start": b["charge_start"], "max": storage.max_soc, "min": storage.min_soc,
        }
    names = {sid: c.name for sid, c in consumers.items()}
    out["plan"] = [{"name": names.get(sid, sid), "now": round(p["now"].pv_share, 3),
                    "start": p["today"].start if p["today"] else None, "end": p["today"].end if p["today"] else None,
                    "pv": round(p["today"].pv_share, 3) if p["today"] else None}
                   for sid, p in (getattr(data, "plan", None) or {}).items()]
    return out


def day_totals(data, days: int, today: date, day_start: Callable[[date], float]) -> list[dict]:
    """Forecast PV energy per local day over the horizon, with clear-sky energy and the P10-P90 band where known.

    Today counts its forecast too (past hours as they were forecast), not the expected curve, so the days
    stay comparable. A day needs almost all of its hours, so a horizon cut off mid-day does not show as a bad day.
    """
    out = []
    band = (data.band or {}).get("days", {})
    for offset in range(days):
        day = today + timedelta(days=offset)
        a = day_start(day)
        b = day_start(day + timedelta(days=1))
        values = [v for t, v in data.forecast.get(TOTAL, {}).items() if a <= t < b]
        if len(values) < 20:
            continue
        clear = sum(v for t, v in data.clear.get(TOTAL, {}).items() if a <= t < b)
        p = band.get(offset) or band.get(str(offset))
        out.append({"date": day.isoformat(), "start": a, "wh": round(sum(values)), "clear_wh": round(clear) or None,
                    "p10": round(p[0] * 1000) if p else None, "p90": round(p[1] * 1000) if p else None})
    return out


# ---------------------------------------------------------------- noxcast/accuracy

def accuracy_days(archive: dict, site: Site) -> list[dict]:
    """Archived day-ahead forecasts with actuals, hour by hour, plus clear-sky irradiance for the weather class."""
    days = []
    for day in fc_archive.days(archive):
        hours = []
        for h in day.hours:
            hours.append({
                "t": h.ts, "ghi": h.ghi, "clear": round(clearsky_weather(site, h.ts).ghi, 1),
                "forecast": h.forecast, "actual": h.actual,
                "alt": {name: s.forecast for name, s in h.alt.items()},
                "curtailed": h.curtailed, "sat_ghi": h.sat_ghi, "measured": h.measured,
            })
        days.append({"date": day.date, "issued": day.issued, "hours": hours})
    return days


def array_names(arrays: dict) -> list[dict]:
    return [{"id": sid, "name": arr.name, "peak_w": arr.geometry.peak_w} for sid, arr in arrays.items()]


# ---------------------------------------------------------------- noxcast/load

# ---------------------------------------------------------------- noxcast/backtest

def backtest_entry(arrays: dict, result: dict | None) -> dict:
    """The last result of backtest.run for the arrays still configured; replay and holdout are None before the first."""
    def part(name: str) -> dict | None:
        p = (result or {}).get(name)
        return {"total": p["total"], "arrays": {sid: r for sid, r in p["arrays"].items() if sid in arrays}} if p else None
    return {"arrays": array_names(arrays), "computed_at": (result or {}).get("computed_at"),
            "replay": part("replay"), "holdout": part("holdout")}


def load_entry(house_load: str, profile, consumers: dict, consumer_profiles: dict, consumer_models: dict, forecast,
               backtest: dict | None, events: dict | None, last_learning: datetime | None) -> dict:
    """Load forecast of one entry: profiles, consumers with their learned model, the forecast by part, backtest."""
    return {
        "house_load": house_load,
        "profile": profile.as_dict() if profile else None,
        "consumers": [{"id": sid, "name": c.name, "timing": c.timing, "sensor": c.sensor or getattr(c, "energy", None),
                       "kind": getattr(c, "kind", "profile"),
                       "model": model_summary((consumer_models or {}).get(sid, {}).get(getattr(c, "kind", "profile")),
                                              getattr(c, "kind", "profile")),
                       "profile": consumer_profiles[sid].as_dict() if sid in consumer_profiles else None}
                      for sid, c in consumers.items()],
        "forecast": None if forecast is None else {
            "k": forecast.k,
            "sustained": forecast.sustained,
            "sustained_since": forecast.sustained_since,
            "base": columns(sorted(forecast.base.items()), "w"),
            "consumers": {sid: columns(sorted(s.items()), "w") for sid, s in forecast.consumers.items()},
        },
        "backtest": backtest,
        "events": load_events(events),
        "last_learning": last_learning.isoformat() if last_learning else None,
    }


def load_events(events: dict | None) -> dict | None:
    """Recurring device runs as stored (events.suggestions), the usual start hours named: {"q1", "q3"}."""
    if not events:
        return events
    return {**events, "groups": [{**g, "hours": {"q1": g["hours"][0], "q3": g["hours"][1]}}
                                 for g in events.get("groups", [])]}


def model_summary(m, kind: str) -> dict | None:
    """The learned figures of a fixed program, regulated consumer or session for the panel."""
    if m is None:
        return None
    if kind == "program":
        per_day = {k: round(sum(v), 2) for k, v in m.starts.items()}
        # expected mean W per local hour on a workday: start rates folded with the run's power curve
        expected = [round(sum(m.starts["workday"][(h - i) % 24] * w for i, w in enumerate(m.shape)), 1) for h in range(24)]
        return {"runs": m.runs, "per_day": per_day, "runs_max": m.runs_max, "energy_wh": round(sum(m.shape)),
                "hours": len(m.shape), "expected": expected}
    if kind == "regulated":
        return {"mode": m.mode, "threshold": m.threshold, "base_wh": m.base_wh, "slope_wh": m.slope_wh,
                "r2": m.r2, "days": m.days}
    return {"power": m.power, "p_on": m.p_on, "remaining": m.remaining,
            "expected": [round((p or 0) * m.power, 1) for p in m.p_on["workday"]]}


# ---------------------------------------------------------------- noxcast/diagnostics

def sensor_roles(arrays: dict, soc_sensors: list[str] | None, house_load: str | None, consumers: dict,
                 batteries: dict[str, dict], battery_names: dict[str, str]) -> list[tuple[str, str, str]]:
    """(entity, role, owner) for every sensor Noxcast reads."""
    roles = [(s, "pv", arr.name) for arr in arrays.values() for s in arr.sensors]
    roles += [(s, "curtailment_soc", "Noxcast") for s in soc_sensors or []]
    if house_load:
        roles.append((house_load, "house_load", "Noxcast"))
    for c in consumers.values():
        for attr, role in (("sensor", "consumer_power"), ("energy", "consumer_energy"),
                           ("remaining", "remaining_time"), ("elapsed", "elapsed_time")):
            if getattr(c, attr, None):
                roles.append((getattr(c, attr), role, c.name))
    for sid, d in batteries.items():
        for key, role in (("soc_sensor", "battery_soc"), ("max_soc_entity", "battery_max"), ("min_soc_entity", "battery_min")):
            if d.get(key):
                roles.append((d[key], role, battery_names.get(sid, "")))
    return roles


def sensor_row(entity_id: str, role: str, owner: str, state, rows: list[dict], has_statistics: bool,
               expected_hours: int) -> dict:
    """One sensor: its state (state, attributes, last_updated or None) and hourly statistics coverage."""
    first = rows[0]["start"] if rows else None
    return {
        "entity_id": entity_id, "role": role, "owner": owner,
        "state": state.state if state else None,
        "unit": state.attributes.get("unit_of_measurement") if state else None,
        "state_class": state.attributes.get("state_class") if state else None,
        "statistics": has_statistics,
        "coverage": round(len(rows) / expected_hours, 3) if has_statistics else None,
        "first": (first.isoformat() if hasattr(first, "isoformat") else _iso(first)) if first else None,
        "updated": state.last_updated.isoformat() if state else None,
    }


def diagnostics_entry(title: str, *, weather_models, data, last_learning: datetime | None, learning_seconds: float | None,
                      backtest_seconds: float | None, learning_error: str | None, archive_errors: dict, arrays: dict, learned: dict[str, LearnedArray], exclusions: list[dict],
                      archive: dict, fine: dict, sensors: list[dict]) -> dict:
    """Data quality of one entry: weather source, learning runs, arrays, exclusions, archive, sensors."""
    return {
        "title": title,
        "weather": {"source": "open_meteo", "models": weather_models,
                    "updated": data.weather_fetched.isoformat() if data and data.weather_fetched else None,
                    "hours": len((data.forecast or {}).get(TOTAL, {})) if data else 0},
        "learning": {"last": last_learning.isoformat() if last_learning else None,
                     "seconds": learning_seconds, "backtest_seconds": backtest_seconds,
                     "error": learning_error, "archive_errors": archive_errors},
        "arrays": [{"name": arr.name, "samples": learned[sid].samples if sid in learned else 0,
                    "coverage": learned[sid].coverage if sid in learned else None,
                    "efficiency": learned[sid].efficiency if sid in learned else None}
                   for sid, arr in arrays.items()],
        "exclusions": [{"start": _iso(e["start"]), "end": _iso(e["end"]), "note": e.get("note", "")} for e in exclusions],
        "archive": {"days": len(archive), "complete": sum(1 for d in fc_archive.days(archive) if d.complete)},
        "fine": {"days": sum(1 for d in fine.values() if any(d.values()))},
        "sensors": sensors,
    }


# ---------------------------------------------------------------- noxcast/battery

def soc_capacities(batteries: dict[str, dict]) -> dict[str, float]:
    """State of charge sensor -> capacity in Wh, for every battery with a sensor."""
    return {d[CONF_SOC_SENSOR]: float(d.get(CONF_CAPACITY) or 0) * 1000
            for d in batteries.values() if d.get(CONF_SOC_SENSOR)}


def battery_entry(batteries: dict[str, dict], names: dict[str, str], data, now: float) -> dict:
    """The storage, PV and load per hour for what-if runs and the current simulation (see battery.day_simulation)."""
    out: dict[str, Any] = {"batteries": [{"name": names.get(sid, ""), "capacity_kwh": d.get(CONF_CAPACITY),
                                          "soc_sensor": d.get(CONF_SOC_SENSOR)} for sid, d in batteries.items()]}
    b = data.battery if data is not None else None
    if b:
        st, sim = b["storage"], b["sim"]
        pv = dict(data.forecast.get(TOTAL, {}))
        pv.update(data.expected.get(TOTAL, {}))
        load = data.load.hours
        hours = sorted(t for t in load if t + 3600 > now)
        out.update({
            "storage": {"capacity_wh": st.capacity_wh, "soc": st.soc, "max": st.max_soc, "min": st.min_soc,
                        "efficiency": st.efficiency, "max_charge_w": st.max_charge_w,
                        "max_discharge_w": st.max_discharge_w},
            "hours": columns(((t, round(pv.get(t, 0.0), 1), round(load[t], 1)) for t in hours), "pv", "load"),
            "day_end": b["day_end"], "charge_start": b["charge_start"],
            "sim": {"full": sim.full_at, "empty": sim.empty_at, "lost_wh": round(sim.lost_wh),
                    "grid_wh": round(sim.grid_wh), "charged_wh": round(sim.charged_wh)},
        })
    return out


def _ts(v) -> float:
    return v.timestamp() if hasattr(v, "timestamp") else float(v)


def soc_review(stats: dict[str, list[dict]], capacities: dict[str, float], day_of: Callable[[float], str]) -> list[dict]:
    """Lowest and highest state of charge per day (battery.soc_days) from hourly min / max statistics in %."""
    series_ = [(cap, [(_ts(r["start"]), r["min"] / 100, r["max"] / 100) for r in stats.get(eid, [])
                      if r.get("min") is not None and r.get("max") is not None])
               for eid, cap in capacities.items()]
    return bt.soc_days(series_, day_of)


def prediction_review(log: dict[str, dict], stats: dict[str, list[dict]], capacities: dict[str, float],
                      day_end_of: Callable[[str], float]) -> list[dict]:
    """The morning predictions against the combined state of charge from 5-minute mean statistics in %."""
    soc = bt.combined_soc([(cap, {_ts(r["start"]): r["mean"] / 100 for r in stats.get(eid, []) if r.get("mean") is not None})
                           for eid, cap in capacities.items()])
    return bt.prediction_review(log, soc, day_end_of)


# ---------------------------------------------------------------- noxcast/card_config

def card_config(arrays: dict, house_load: str | None, grid: str | None, grid_invert: bool, feed_in: bool,
                batteries: dict[str, dict], names: dict[str, str], weather: str | None) -> dict:
    """The sensors the day card shows, as configured in the integration."""
    out_batteries = []
    for sid, d in batteries.items():
        out_batteries.append({k: v for k, v in {
            "name": names.get(sid),
            "entity": d.get(CONF_BATTERY_POWER), "invert": bool(d.get(CONF_BATTERY_INVERT)), "soc": d.get(CONF_SOC_SENSOR),
            "capacity": d.get(CONF_CAPACITY), "max_soc": d.get(CONF_MAX_SOC_ENTITY) or d.get(CONF_MAX_SOC),
            "min_soc": d.get(CONF_MIN_SOC_ENTITY) or d.get(CONF_MIN_SOC), "reserve": d.get(CONF_RESERVE),
        }.items() if v not in (None, "")})
    return {
        "pv": {"groups": [{"entities": list(arr.sensors), "name": arr.name, "array": arr.name} for arr in arrays.values()]},
        "home": {"entity": house_load} if house_load else {},
        "grid": {"entity": grid, "invert": bool(grid_invert)} if grid else {},
        "batteries": out_batteries,
        "export": bool(feed_in),
        "weather": weather,
    }


# ---------------------------------------------------------------- noxcast/day

def day_view(site: Site, arrays: dict, learned: dict[str, LearnedArray], archive_day: fc_archive.Day | None, data,
             a: float, b: float, now: float) -> dict:
    """One day [a, b): per array the forecasts, actual, steps of each hour; weather; band; history.

    Past days come from the day-ahead archive (forecast, actual, weather of the forecast), their steps
    replayed with the current learned model (explain.replay). Today and the coming days come from the
    current data; today's past hours fall back to the archived weather, since a weather source may no
    longer report them.
    """
    current = data is not None and b > now                 # today and the coming days
    arch = archive_day.hours if archive_day else []
    weather = {w.start: w for w in explain.archived_weather(archive_day)}
    if current:
        weather.update({w.start: w for w in data.weather if a <= w.start < b})
    hours = [weather[t] for t in sorted(weather) if a <= t < b]
    steps = explain.replay(site, {sid: arr.geometry for sid, arr in arrays.items()}, learned, hours)
    out = []
    for sid, arr in arrays.items():
        entry = {"id": sid, "name": arr.name, "peak_w": arr.geometry.peak_w,
                 "steps": {str(int(t)): s for t, s in steps[sid].items()},
                 "efficiency": (learned.get(sid) or LearnedArray()).efficiency}
        entry["day_ahead"] = columns(((h.ts, h.forecast[sid]) for h in arch if sid in h.forecast), "wh")
        entry["actual"] = columns(((h.ts, h.actual[sid]) for h in arch if sid in (h.actual or {})), "wh")
        if current:
            entry["forecast"] = series(data.forecast.get(sid, {}), a, b)
            if a <= now < b:
                entry["morning"] = series(data.morning.get(sid, {}), a, b)
                entry["held"] = series(data.held.get(sid, {}), a, b)
                entry["expected"] = series(data.expected.get(sid, {}), a, b)
                entry["actual"] = series(data.actual.get(sid, {}), a, b)
                entry["correction"] = data.correction.get(sid)
                entry["periods"] = (data.effects.get(sid) or {}).get("periods", [])
        out.append(entry)
    band = band_series(data.band if data is not None else None, a, b)
    sky = explain.clearness(site, hours)
    # forecasts of the day as they were at each snapshot: {"at": snapshot time, "t", "wh"}
    history = [{"at": s, **columns(snap, "wh")} for s, snap in (archive_day.history if archive_day else [])]
    return {"arrays": out, "band": band, "history": history,
            "weather": columns(((t, round(g, 1), round(c, 1), weather[t].temp) for t, (g, c) in sorted(sky.items())),
                               "ghi", "clear", "temp")}


def day_pv(view: dict) -> Series:
    """Measured PV of all arrays per hour of a day view, for the energy paths."""
    pv: Series = {}
    for arr in view["arrays"]:
        actual = arr.get("actual") or {"t": [], "wh": []}
        for t, v in zip(actual["t"], actual["wh"], strict=True):
            pv[t] = pv.get(t, 0.0) + v
    return pv
