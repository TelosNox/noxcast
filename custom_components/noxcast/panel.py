"""Sidebar panel "Noxcast" and the day card, both fed by websocket commands."""
from __future__ import annotations

import logging
from datetime import timedelta
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


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the websocket commands and, where the frontend is loaded, the panel and the day card."""
    websocket_api.async_register_command(hass, ws_learned)
    websocket_api.async_register_command(hass, ws_today)
    websocket_api.async_register_command(hass, ws_accuracy)
    websocket_api.async_register_command(hass, ws_load)
    websocket_api.async_register_command(hass, ws_diagnostics)
    if "frontend" not in hass.config.components or getattr(hass, "http", None) is None:
        return                                           # e.g. tests without a frontend
    from homeassistant.components import panel_custom
    from homeassistant.components.http import StaticPathConfig

    # the panel loads further section modules with its own cache buster, so it covers all of them
    version = await hass.async_add_executor_job(_version, "noxcast-panel.js", "noxcast-accuracy.js", "noxcast-load.js",
                                         "noxcast-diagnostics.js")
    card_version = await hass.async_add_executor_job(_version, "noxcast-card.js")
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_BASE, str(Path(__file__).parent / "frontend"), cache_headers=False)])
    await panel_custom.async_register_panel(
        hass, frontend_url_path=PANEL_PATH, webcomponent_name="noxcast-panel",
        module_url=f"{URL_BASE}/noxcast-panel.js?v={version}", sidebar_title="Noxcast",
        sidebar_icon="mdi:solar-power-variant", require_admin=False, config={})
    await _async_register_card(hass, f"{URL_BASE}/noxcast-card.js?v={card_version}")


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
    connection.send_result(msg["id"], {"now": dt_util.utcnow().timestamp(), "time_zone": hass.config.time_zone,
                                       "arrays": arrays})


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
                           "profile": coordinator.consumer_profiles[sid].as_dict()
                           if sid in coordinator.consumer_profiles else None}
                          for sid, c in coordinator.consumers.items()],
            "forecast": None if fc is None else {
                "k": fc.k,
                "base": [[t, v] for t, v in sorted(fc.base.items())],
                "consumers": {sid: [[t, v] for t, v in sorted(series.items())] for sid, series in fc.consumers.items()},
            },
            "backtest": coordinator.load_backtest,
            "last_learning": coordinator.last_learning.isoformat() if coordinator.last_learning else None,
        })
    connection.send_result(msg["id"], {"now": dt_util.utcnow().timestamp(), "time_zone": hass.config.time_zone,
                                       "entries": entries})


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
    for d in getattr(coordinator, "batteries", {}).values():
        for key, role in (("soc_sensor", "battery_soc"), ("max_soc_entity", "battery_max"), ("min_soc_entity", "battery_min")):
            if d.get(key):
                roles.append((d[key], role, d.get("name", "")))
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
            "weather": {"source": entry.data.get("weather_source"), "entity": entry.data.get("weather_entity"),
                        "updated": data.updated.isoformat() if data and data.updated else None,
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
