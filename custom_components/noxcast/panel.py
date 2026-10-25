"""Sidebar panel "Noxcast" and the day card, both fed by websocket commands."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

URL_BASE = "/noxcast_panel"
PANEL_PATH = "noxcast"
WS_LEARNED = f"{DOMAIN}/learned"
WS_TODAY = f"{DOMAIN}/today"
WS_ACCURACY = f"{DOMAIN}/accuracy"
WS_LOAD = f"{DOMAIN}/load"
WS_DIAGNOSTICS = f"{DOMAIN}/diagnostics"
WS_BATTERY = f"{DOMAIN}/battery"
WS_DAY = f"{DOMAIN}/day"
WS_CARD = f"{DOMAIN}/card_config"


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the websocket commands and, where the frontend is loaded, the panel and the day card."""
    websocket_api.async_register_command(hass, ws_learned)
    websocket_api.async_register_command(hass, ws_today)
    websocket_api.async_register_command(hass, ws_accuracy)
    websocket_api.async_register_command(hass, ws_load)
    websocket_api.async_register_command(hass, ws_diagnostics)
    websocket_api.async_register_command(hass, ws_battery)
    websocket_api.async_register_command(hass, ws_day)
    websocket_api.async_register_command(hass, ws_card_config)
    if "frontend" not in hass.config.components or getattr(hass, "http", None) is None:
        return                                           # e.g. tests without a frontend
    from homeassistant.components import panel_custom
    from homeassistant.components.http import StaticPathConfig

    # the panel loads further section modules with its own cache buster, so it covers all of them
    version = await hass.async_add_executor_job(_version, "noxcast-panel.js", "noxcast-accuracy.js", "noxcast-load.js",
                                         "noxcast-diagnostics.js", "noxcast-battery.js",
                                         "noxcast-theme.js", "noxcast-day.js")
    card_version = await hass.async_add_executor_job(_version, "noxcast-card.js")
    weather_version = await hass.async_add_executor_job(_version, "noxcast-weather-card.js")
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_BASE, str(Path(__file__).parent / "frontend"), cache_headers=False)])
    await panel_custom.async_register_panel(
        hass, frontend_url_path=PANEL_PATH, webcomponent_name="noxcast-panel",
        module_url=f"{URL_BASE}/noxcast-panel.js?v={version}", sidebar_title="Noxcast",
        sidebar_icon="mdi:solar-power-variant", require_admin=False, config={})
    await _async_register_card(hass, f"{URL_BASE}/noxcast-card.js?v={card_version}")
    await _async_register_card(hass, f"{URL_BASE}/noxcast-weather-card.js?v={weather_version}")


async def _async_register_card(hass: HomeAssistant, url: str) -> None:
    """Keep the day card in the Lovelace resources, like a card installed through HACS.

    Dashboards load resources once the frontend runs, so the card is known wherever it is used. With
    resources in YAML mode nothing is written; the card is then added there by hand.
    """
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    data = hass.data.get(LOVELACE_DATA)
    if data is None or data.resource_mode != "storage":
        _LOGGER.info("Lovelace resources are not in storage mode; add %s as a module resource", url)
        return
    resources = data.resources
    if not resources.loaded:
        await resources.async_load()
        resources.loaded = True
    base = url.split("?")[0]
    for item in resources.async_items():
        if item["url"].split("?")[0] == base:
            if item["url"] != url:
                await resources.async_update_item(item["id"], {"res_type": "module", "url": url})
            return
    await resources.async_create_item({"res_type": "module", "url": url})


def _version(*names: str) -> str:
    """Cache buster: latest modification time of the given frontend scripts."""
    return str(int(max((Path(__file__).parent / "frontend" / n).stat().st_mtime for n in names)))


@websocket_api.websocket_command({vol.Required("type"): WS_LEARNED})
@callback
def ws_learned(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Learned state of all arrays of all Noxcast entries (the site is the Home Assistant location)."""
    arrays = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator = entry.runtime_data
        for sub_id, arr in coordinator.arrays.items():
            learned = coordinator.learned.get(sub_id)
            arrays.append({
                "id": sub_id, "name": arr.name, "tilt": arr.geometry.tilt, "azimuth": arr.geometry.azimuth,
                "peak_w": arr.geometry.peak_w,
                "learned": learned.as_dict() if learned else None,
                "last_learning": coordinator.last_learning.isoformat() if coordinator.last_learning else None,
            })
    connection.send_result(msg["id"], {
        "latitude": hass.config.latitude, "longitude": hass.config.longitude,
        "time_zone": hass.config.time_zone, "arrays": arrays,
    })


@websocket_api.websocket_command({vol.Required("type"): WS_TODAY})
@callback
def ws_today(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Today per array for the day card: expected, held and morning forecast per hour, effects and tomorrow."""
    tomorrow_start = dt_util.start_of_local_day(dt_util.now().date() + timedelta(days=1)).timestamp()
    tomorrow_end = tomorrow_start + 86400

    def series(hours: dict[float, float]) -> list[list[float]]:
        return [[t, round(v, 1)] for t, v in sorted(hours.items())]

    arrays = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator = entry.runtime_data
        data = coordinator.data
        if data is None:
            continue
        for sub_id, arr in coordinator.arrays.items():
            effects = data.effects.get(sub_id, {})
            arrays.append({
                "id": sub_id, "name": arr.name, "peak_w": arr.geometry.peak_w,
                "expected": series(data.expected.get(sub_id, {})),
                "morning": series(data.morning.get(sub_id, {})),
                "forecast": series(data.held.get(sub_id, {})),
                "tomorrow": series({t: v for t, v in data.forecast.get(sub_id, {}).items()
                                    if tomorrow_start <= t < tomorrow_end}),
                "loss": series(effects.get("loss", {})),
                "gain": series(effects.get("gain", {})),
                "periods": effects.get("periods", []),
                "tomorrow_wh": round(sum(v for t, v in data.forecast.get(sub_id, {}).items()
                                         if tomorrow_start <= t < tomorrow_end)),
                "updated": data.updated.isoformat() if data.updated else None,
            })
    result = {"now": dt_util.utcnow().timestamp(), "time_zone": hass.config.time_zone, "arrays": arrays, "days": []}
    # with a load forecast and batteries the card shows what Noxcast computed instead of its own estimate
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        data = entry.runtime_data.data
        if data is None:
            continue
        result["days"] = day_totals(data, entry.runtime_data.forecast_days)
        if data.load is not None:
            result["load"] = series(data.load.hours)
        if data.band:
            result["band"] = [[t, round(lo, 1), round(hi, 1)] for t, (lo, hi) in sorted(data.band["hours"].items())]
        b = data.battery
        if b:
            sim, storage = b["sim"], b["storage"]
            result["battery"] = {
                "points": [[t, round(f, 4)] for t, f in sim.points], "full": sim.full_at, "empty": sim.empty_at,
                "lost_today": round(sum(h["lost"] for t, h in sim.hours.items() if t < b["day_end"]), 1),
                "charge_start": b["charge_start"], "max": storage.max_soc, "min": storage.min_soc,
            }
        names = {sid: c.name for sid, c in entry.runtime_data.consumers.items()}
        result["plan"] = [{"name": names.get(sid, sid), "now": round(p["now"].pv_share, 3),
                           "start": p["today"].start if p["today"] else None, "end": p["today"].end if p["today"] else None,
                           "pv": round(p["today"].pv_share, 3) if p["today"] else None}
                          for sid, p in (getattr(data, "plan", None) or {}).items()]
        break
    connection.send_result(msg["id"], result)


def day_totals(data, days: int) -> list[dict]:
    """Forecast PV energy per local day over the horizon, with clear-sky energy and the P10-P90 band where known.

    Today counts its forecast too (past hours as they were forecast), not the expected curve, so the days
    stay comparable. A day needs almost all of its hours, so a horizon cut off mid-day does not show as a bad day.
    """
    out = []
    today = dt_util.now().date()
    band = (data.band or {}).get("days", {})
    for offset in range(days):
        day = today + timedelta(days=offset)
        a = dt_util.start_of_local_day(day).timestamp()
        b = dt_util.start_of_local_day(day + timedelta(days=1)).timestamp()
        values = [v for t, v in data.forecast.get(None, {}).items() if a <= t < b]
        if len(values) < 20:
            continue
        clear = sum(v for t, v in data.clear.get(None, {}).items() if a <= t < b)
        p = band.get(offset) or band.get(str(offset))
        out.append({"date": day.isoformat(), "start": a, "wh": round(sum(values)), "clear_wh": round(clear) or None,
                    "p10": round(p[0] * 1000) if p else None, "p90": round(p[1] * 1000) if p else None})
    return out


def accuracy_days(archive: dict, site) -> list[dict]:
    """Archived day-ahead forecasts with actuals, hour by hour, plus clear-sky irradiance for the weather class."""
    from .model import clearsky_weather

    days = []
    for key in sorted(archive):
        day = archive[key]
        hours = []
        for ts_key, h in sorted(day.get("hours", {}).items(), key=lambda kv: float(kv[0])):
            ts = float(ts_key)
            hours.append({
                "t": ts, "ghi": h.get("ghi"), "clear": round(clearsky_weather(site, ts).ghi, 1),
                "forecast": h.get("forecast", {}), "actual": h.get("actual"),
                "alt": {name: a.get("forecast", {}) for name, a in h.get("alt", {}).items()},
                "curtailed": bool(h.get("curtailed")),
                "sat_ghi": h.get("sat_ghi"), "measured": h.get("measured"),
            })
        days.append({"date": key, "issued": day.get("issued"), "hours": hours})
    return days


@websocket_api.websocket_command({vol.Required("type"): WS_ACCURACY})
@websocket_api.async_response
async def ws_accuracy(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Day-ahead forecast archive for the accuracy view of the panel."""
    entries = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator = entry.runtime_data
        days = await hass.async_add_executor_job(accuracy_days, coordinator.forecast_archive, coordinator.site)
        entries.append({"arrays": [{"id": sid, "name": arr.name, "peak_w": arr.geometry.peak_w}
                                   for sid, arr in coordinator.arrays.items()], "days": days})
    connection.send_result(msg["id"], {"time_zone": hass.config.time_zone, "entries": entries})


@websocket_api.websocket_command({vol.Required("type"): WS_LOAD})
@callback
def ws_load(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Load forecast for the panel: profiles, consumers, the forecast by part and the backtest."""
    entries = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator = entry.runtime_data
        if not coordinator.house_load:
            continue
        data = coordinator.data
        fc = data.load if data is not None else None
        profile = coordinator.load_profile
        entries.append({
            "house_load": coordinator.house_load,
            "profile": profile.as_dict() if profile else None,
            "consumers": [{"id": sid, "name": c.name, "timing": c.timing, "sensor": c.sensor or getattr(c, "energy", None),
                           "kind": getattr(c, "kind", "profile"),
                           "model": _model_summary(coordinator, sid, getattr(c, "kind", "profile")),
                           "profile": coordinator.consumer_profiles[sid].as_dict()
                           if sid in coordinator.consumer_profiles else None}
                          for sid, c in coordinator.consumers.items()],
            "forecast": None if fc is None else {
                "k": fc.k,
                "sustained": fc.sustained,
                "sustained_since": fc.sustained_since,
                "base": [[t, v] for t, v in sorted(fc.base.items())],
                "consumers": {sid: [[t, v] for t, v in sorted(series.items())] for sid, series in fc.consumers.items()},
            },
            "backtest": coordinator.load_backtest,
            "events": getattr(coordinator, "load_events", None),
            "last_learning": coordinator.last_learning.isoformat() if coordinator.last_learning else None,
        })
    connection.send_result(msg["id"], {"now": dt_util.utcnow().timestamp(), "time_zone": hass.config.time_zone,
                                       "entries": entries})


def _model_summary(coordinator, sid: str, kind: str) -> dict | None:
    """The learned figures of a fixed program or session for the panel."""
    m = (getattr(coordinator, "consumer_models", {}) or {}).get(sid, {}).get(kind)
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


DIAG_DAYS = 30


def _sensor_roles(entry, coordinator) -> list[tuple[str, str, str]]:
    """(entity, role, owner) for every sensor Noxcast reads."""
    from .const import CONF_SOC_SENSORS

    roles = [(s, "pv", arr.name) for arr in coordinator.arrays.values() for s in arr.sensors]
    roles += [(s, "curtailment_soc", "Noxcast") for s in entry.data.get(CONF_SOC_SENSORS) or []]
    if getattr(coordinator, "house_load", None):
        roles.append((coordinator.house_load, "house_load", "Noxcast"))
    for c in getattr(coordinator, "consumers", {}).values():
        for attr, role in (("sensor", "consumer_power"), ("energy", "consumer_energy"),
                           ("remaining", "remaining_time"), ("elapsed", "elapsed_time")):
            if getattr(c, attr, None):
                roles.append((getattr(c, attr), role, c.name))
    for sid, d in getattr(coordinator, "batteries", {}).items():
        for key, role in (("soc_sensor", "battery_soc"), ("max_soc_entity", "battery_max"), ("min_soc_entity", "battery_min")):
            if d.get(key):
                roles.append((d[key], role, entry.subentries[sid].title if sid in entry.subentries else ""))
    return roles


@websocket_api.websocket_command({vol.Required("type"): WS_DIAGNOSTICS})
@websocket_api.async_response
async def ws_diagnostics(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Data quality for the panel: every sensor with unit, state and statistics coverage, learning runs, exclusions."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import get_metadata, statistics_during_period

    now = dt_util.utcnow()
    start = now - timedelta(days=DIAG_DAYS)
    expected_hours = DIAG_DAYS * 24
    entries = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator = entry.runtime_data
        roles = _sensor_roles(entry, coordinator)
        ids = sorted({e for e, _, _ in roles})
        try:
            meta = await get_instance(hass).async_add_executor_job(lambda: get_metadata(hass, statistic_ids=set(ids)))
            stats = await get_instance(hass).async_add_executor_job(
                statistics_during_period, hass, start, None, set(ids), "hour", None, {"mean", "change"})
        except Exception as err:  # noqa: BLE001 – the view still shows states without statistics
            _LOGGER.debug("Statistics for diagnostics unavailable: %s", err)
            meta, stats = {}, {}
        sensors = []
        for entity_id, role, owner in roles:
            state = hass.states.get(entity_id)
            rows = stats.get(entity_id, [])
            first = rows[0]["start"] if rows else None
            sensors.append({
                "entity_id": entity_id, "role": role, "owner": owner,
                "state": state.state if state else None,
                "unit": state.attributes.get("unit_of_measurement") if state else None,
                "state_class": state.attributes.get("state_class") if state else None,
                "statistics": entity_id in meta,
                "coverage": round(len(rows) / expected_hours, 3) if entity_id in meta else None,
                "first": (first.isoformat() if hasattr(first, "isoformat") else dt_util.utc_from_timestamp(first).isoformat()) if first else None,
                "updated": state.last_updated.isoformat() if state else None,
            })
        archive = coordinator.forecast_archive
        data = coordinator.data
        entries.append({
            "title": entry.title,
            "weather": {"source": "open_meteo", "models": coordinator.weather_models,
                        "updated": data.weather_fetched.isoformat() if data and data.weather_fetched else None,
                        "hours": len((data.forecast or {}).get(None, {})) if data else 0},
            "learning": {"last": coordinator.last_learning.isoformat() if coordinator.last_learning else None,
                         "error": coordinator.last_learning_error, "archive_errors": coordinator.archive_errors},
            "arrays": [{"name": arr.name, "samples": coordinator.learned[sid].samples if sid in coordinator.learned else 0,
                        "coverage": coordinator.learned[sid].coverage if sid in coordinator.learned else None,
                        "efficiency": coordinator.learned[sid].efficiency if sid in coordinator.learned else None}
                       for sid, arr in coordinator.arrays.items()],
            "exclusions": [{"start": dt_util.utc_from_timestamp(e["start"]).isoformat(),
                            "end": dt_util.utc_from_timestamp(e["end"]).isoformat(), "note": e.get("note", "")}
                           for e in coordinator.exclusions],
            "archive": {"days": len(archive),
                        "complete": sum(1 for d in archive.values()
                                        if d.get("hours") and all("actual" in h for h in d["hours"].values()))},
            "sensors": sensors,
        })
    connection.send_result(msg["id"], {"time_zone": hass.config.time_zone, "days": DIAG_DAYS, "entries": entries})


REVIEW_DAYS = 30


@websocket_api.websocket_command({vol.Required("type"): WS_BATTERY})
@websocket_api.async_response
async def ws_battery(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Battery and surplus for the panel: the storage, PV and load per hour for what-if runs, the last 30 days."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    from . import battery as bt
    from .const import CONF_CAPACITY, CONF_SOC_SENSOR

    now = dt_util.utcnow()
    entries = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator = entry.runtime_data
        if not coordinator.batteries:
            continue
        data = coordinator.data
        b = data.battery if data is not None else None
        result: dict[str, Any] = {"batteries": [{"name": entry.subentries[sid].title if sid in entry.subentries else "",
                                                 "capacity_kwh": d.get(CONF_CAPACITY), "soc_sensor": d.get(CONF_SOC_SENSOR)}
                                                for sid, d in coordinator.batteries.items()]}
        if b:
            st, sim = b["storage"], b["sim"]
            pv = dict(data.forecast.get(None, {}))
            pv.update(data.expected.get(None, {}))
            load = data.load.hours
            hours = sorted(t for t in load if t + 3600 > now.timestamp())
            result.update({
                "storage": {"capacity_wh": st.capacity_wh, "soc": st.soc, "max": st.max_soc, "min": st.min_soc,
                            "efficiency": st.efficiency, "max_charge_w": st.max_charge_w,
                            "max_discharge_w": st.max_discharge_w},
                "hours": [[t, round(pv.get(t, 0.0), 1), round(load[t], 1)] for t in hours],
                "day_end": b["day_end"], "charge_start": b["charge_start"],
                "sim": {"full": sim.full_at, "empty": sim.empty_at, "lost_wh": round(sim.lost_wh),
                        "grid_wh": round(sim.grid_wh), "charged_wh": round(sim.charged_wh)},
            })
        # review: lowest and highest state of charge per day from the long-term statistics
        socs = {d[CONF_SOC_SENSOR]: float(d.get(CONF_CAPACITY) or 0) * 1000
                for d in coordinator.batteries.values() if d.get(CONF_SOC_SENSOR)}
        try:
            stats = await get_instance(hass).async_add_executor_job(
                statistics_during_period, hass, now - timedelta(days=REVIEW_DAYS), None, set(socs), "hour", None,
                {"min", "max"})
        except Exception as err:  # noqa: BLE001 – the what-if part works without statistics
            _LOGGER.debug("Statistics for the battery review unavailable: %s", err)
            stats = {}

        def ts(v) -> float:
            return v.timestamp() if hasattr(v, "timestamp") else float(v)

        series = [(cap, [(ts(r["start"]), r["min"] / 100, r["max"] / 100) for r in stats.get(eid, [])
                         if r.get("min") is not None and r.get("max") is not None])
                  for eid, cap in socs.items()]
        result["days"] = bt.soc_days(series, lambda t: dt_util.as_local(dt_util.utc_from_timestamp(t)).date().isoformat())
        # the morning predictions against what happened, from the 5-minute statistics (kept about ten days)
        result["predictions"] = []
        if coordinator.battery_log and socs:
            first = min(coordinator.battery_log)
            try:
                short = await get_instance(hass).async_add_executor_job(
                    statistics_during_period, hass, dt_util.start_of_local_day(date.fromisoformat(first)), None,
                    set(socs), "5minute", None, {"mean"})
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Short-term statistics for the battery predictions unavailable: %s", err)
                short = {}
            soc = bt.combined_soc([(cap, {ts(r["start"]): r["mean"] / 100 for r in short.get(eid, []) if r.get("mean") is not None})
                                   for eid, cap in socs.items()])
            result["predictions"] = bt.prediction_review(
                coordinator.battery_log, soc,
                lambda key: dt_util.start_of_local_day(date.fromisoformat(key) + timedelta(days=1)).timestamp())
        entries.append(result)
    connection.send_result(msg["id"], {"now": now.timestamp(), "time_zone": hass.config.time_zone,
                                       "review_days": REVIEW_DAYS, "entries": entries})


@websocket_api.websocket_command({vol.Required("type"): WS_CARD})
@callback
def ws_card_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The sensors the day card shows, as configured in the integration; the card only changes the display."""
    from .const import (CONF_BATTERY_INVERT, CONF_BATTERY_POWER, CONF_CAPACITY, CONF_FEED_IN, CONF_GRID_INVERT, CONF_MAX_SOC,
                        CONF_MAX_SOC_ENTITY, CONF_MIN_SOC, CONF_MIN_SOC_ENTITY, CONF_RESERVE, CONF_SOC_SENSOR)
    from homeassistant.helpers import entity_registry as er

    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        connection.send_result(msg["id"], None)
        return
    entry = entries[0]
    c = entry.runtime_data
    batteries = []
    for sid, d in c.batteries.items():
        batteries.append({k: v for k, v in {
            "name": entry.subentries[sid].title if sid in entry.subentries else None,
            "entity": d.get(CONF_BATTERY_POWER), "invert": bool(d.get(CONF_BATTERY_INVERT)), "soc": d.get(CONF_SOC_SENSOR),
            "capacity": d.get(CONF_CAPACITY), "max_soc": d.get(CONF_MAX_SOC_ENTITY) or d.get(CONF_MAX_SOC),
            "min_soc": d.get(CONF_MIN_SOC_ENTITY) or d.get(CONF_MIN_SOC), "reserve": d.get(CONF_RESERVE),
        }.items() if v not in (None, "")})
    connection.send_result(msg["id"], {
        "pv": {"groups": [{"entities": list(arr.sensors), "name": arr.name, "array": arr.name} for arr in c.arrays.values()]},
        "home": {"entity": c.house_load} if c.house_load else {},
        "grid": {"entity": c.grid, "invert": bool(entry.data.get(CONF_GRID_INVERT))} if c.grid else {},
        "batteries": batteries,
        "export": bool(entry.data.get(CONF_FEED_IN)),
        "weather": er.async_get(hass).async_get_entity_id("weather", DOMAIN, f"{entry.entry_id}_weather"),
    })


def _series(hours: dict[float, float], a: float, b: float) -> list[list[float]]:
    return [[t, round(v, 1)] for t, v in sorted(hours.items()) if a <= t < b]


def day_view(site, arrays: dict, learned: dict, archive_day: dict | None, data, a: float, b: float, now: float) -> dict:
    """One day for the panel: per array the forecasts, actual, steps of each hour; weather; band.

    Past days come from the day-ahead archive (forecast, actual, weather of the forecast). Today and
    the coming days come from the current data; today's past hours fall back to the archived weather,
    since a weather source may no longer report them. Runs in the executor.
    """
    from . import explain
    from .model import LearnedArray, WeatherHour

    weather: dict[float, WeatherHour] = {}
    for key, h in ((archive_day or {}).get("hours") or {}).items():
        weather[float(key)] = WeatherHour(float(key), h.get("ghi") or 0.0, None, None, h.get("temp"))
    if data is not None and b > now:                     # today and the coming days
        weather.update({w.start: w for w in data.weather if a <= w.start < b})
    hours = [weather[t] for t in sorted(weather) if a <= t < b]
    out = []
    for sid, arr in arrays.items():
        la = learned.get(sid) or LearnedArray()
        entry = {"id": sid, "name": arr.name, "peak_w": arr.geometry.peak_w,
                 "steps": {str(int(t)): s for t, s in explain.hour_steps(site, arr.geometry, la, hours).items()},
                 "efficiency": la.efficiency}
        arch = {float(k): h for k, h in ((archive_day or {}).get("hours") or {}).items()}
        entry["day_ahead"] = [[t, h["forecast"][sid]] for t, h in sorted(arch.items()) if sid in h.get("forecast", {})]
        entry["actual"] = [[t, h["actual"][sid]] for t, h in sorted(arch.items()) if sid in h.get("actual", {})]
        if data is not None and b > now:
            entry["forecast"] = _series(data.forecast.get(sid, {}), a, b)
            if a <= now < b:
                entry["morning"] = _series(data.morning.get(sid, {}), a, b)
                entry["held"] = _series(data.held.get(sid, {}), a, b)
                entry["expected"] = _series(data.expected.get(sid, {}), a, b)
                entry["actual"] = _series(data.actual.get(sid, {}), a, b)
                entry["correction"] = data.correction.get(sid)
                entry["periods"] = (data.effects.get(sid) or {}).get("periods", [])
        out.append(entry)
    band = []
    if data is not None and data.band:
        band = [[t, round(lo, 1), round(hi, 1)] for t, (lo, hi) in sorted(data.band["hours"].items()) if a <= t < b]
    sky = explain.clearness(site, hours)
    history = [[float(k), [[float(t), v] for t, v in sorted(h.items(), key=lambda kv: float(kv[0]))]]
               for k, h in sorted(((archive_day or {}).get("history") or {}).items(), key=lambda kv: float(kv[0]))]
    return {"arrays": out, "band": band, "history": history,
            "weather": [[t, round(g, 1), round(c, 1), weather[t].temp] for t, (g, c) in sorted(sky.items())]}


@websocket_api.websocket_command({vol.Required("type"): WS_DAY, vol.Optional("offset", default=0): vol.All(int, vol.Range(-120, 2))})
@websocket_api.async_response
async def ws_day(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """A day in detail: offset 0 = today, negative = past days from the archive, up to 2 days ahead."""
    day = dt_util.now().date() + timedelta(days=msg["offset"])
    a = dt_util.start_of_local_day(day).timestamp()
    b = dt_util.start_of_local_day(day + timedelta(days=1)).timestamp()
    now = dt_util.utcnow().timestamp()
    result: dict[str, Any] = {"now": now, "time_zone": hass.config.time_zone, "date": day.isoformat(),
                              "offset": msg["offset"], "start": a, "end": b, "entries": []}
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        c = entry.runtime_data
        view = await hass.async_add_executor_job(day_view, c.site, c.arrays, dict(c.learned),
                                                 c.forecast_archive.get(day.isoformat()), c.data, a, b, now)
        view["archive_days"] = sorted(c.forecast_archive)[:1]
        view["flows"] = None
        if a < now:
            pv: dict[float, float] = {}
            for arr in view["arrays"]:
                for t, v in arr.get("actual", []):
                    pv[t] = pv.get(t, 0.0) + v
            try:
                view["flows"] = await c.async_day_flows(a, b, pv)
            except Exception:  # noqa: BLE001 – the day view works without the energy paths
                _LOGGER.exception("Energy paths of %s failed", day)
        result["entries"].append(view)
    connection.send_result(msg["id"], result)
