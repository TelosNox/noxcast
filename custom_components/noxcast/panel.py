"""Sidebar panel "Noxcast" and the day card, both fed by websocket commands.

A thin adapter: each command gathers its inputs from the coordinators (and the recorder), lets a pure
function of views.py build the payload and sends it with the schema version (see views.py).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import get_metadata, statistics_during_period
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from . import archive as fc_archive
from . import views
from .const import CONF_FEED_IN, CONF_GRID_INVERT, CONF_SOC_SENSORS, DOMAIN, WS_SCHEMA_VERSION

_LOGGER = logging.getLogger(__name__)

URL_BASE = "/noxcast_panel"
PANEL_PATH = "noxcast"
WS_LEARNED = f"{DOMAIN}/learned"
WS_TODAY = f"{DOMAIN}/today"
WS_ACCURACY = f"{DOMAIN}/accuracy"
WS_BACKTEST = f"{DOMAIN}/backtest"
WS_LOAD = f"{DOMAIN}/load"
WS_DIAGNOSTICS = f"{DOMAIN}/diagnostics"
WS_BATTERY = f"{DOMAIN}/battery"
WS_DAY = f"{DOMAIN}/day"
WS_CARD = f"{DOMAIN}/card_config"
DIAG_DAYS = 30
REVIEW_DAYS = 30


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the websocket commands and, where the frontend is loaded, the panel and the day card."""
    websocket_api.async_register_command(hass, ws_learned)
    websocket_api.async_register_command(hass, ws_today)
    websocket_api.async_register_command(hass, ws_accuracy)
    websocket_api.async_register_command(hass, ws_backtest)
    websocket_api.async_register_command(hass, ws_load)
    websocket_api.async_register_command(hass, ws_diagnostics)
    websocket_api.async_register_command(hass, ws_battery)
    websocket_api.async_register_command(hass, ws_day)
    websocket_api.async_register_command(hass, ws_card_config)
    if "frontend" not in hass.config.components or getattr(hass, "http", None) is None:
        return                                           # e.g. tests without a frontend
    # after_dependencies only: imported where they are loaded
    from homeassistant.components import panel_custom
    from homeassistant.components.http import StaticPathConfig

    # the panel loads further section modules with its own cache buster, so it covers all of them
    version = await hass.async_add_executor_job(_version, "noxcast-common.js", "noxcast-panel.js", "noxcast-accuracy.js", "noxcast-load.js",
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


def _send(connection: websocket_api.ActiveConnection, msg: dict[str, Any], result: dict | None) -> None:
    """Send a payload with its schema version first ("v"); None (no Noxcast entry) stays None."""
    connection.send_result(msg["id"], None if result is None else {"v": WS_SCHEMA_VERSION, **result})


def _coordinators(hass: HomeAssistant) -> list:
    return [e.runtime_data for e in hass.config_entries.async_loaded_entries(DOMAIN)]


def _battery_names(entry) -> dict[str, str]:
    """Battery subentry id -> its title."""
    return {sid: entry.subentries[sid].title for sid in entry.runtime_data.batteries if sid in entry.subentries}


def _day_start(day: date) -> float:
    return dt_util.start_of_local_day(day).timestamp()


def _local_date(ts: float) -> str:
    return dt_util.as_local(dt_util.utc_from_timestamp(ts)).date().isoformat()


@websocket_api.websocket_command({vol.Required("type"): WS_LEARNED})
@callback
def ws_learned(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Learned state of all arrays of all Noxcast entries (the site is the Home Assistant location)."""
    arrays = [a for c in _coordinators(hass) for a in views.learned_arrays(c.arrays, c.learned, c.last_learning)]
    _send(connection, msg, {
        "latitude": hass.config.latitude, "longitude": hass.config.longitude,
        "time_zone": hass.config.time_zone, "arrays": arrays,
    })


@websocket_api.websocket_command({vol.Required("type"): WS_TODAY})
@callback
def ws_today(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Today per array for the day card: expected, held and morning forecast per hour, effects and tomorrow."""
    coordinators = [c for c in _coordinators(hass) if c.data is not None]
    # the day the data was computed for: just after midnight the new computation may still be running
    day_start = next((c.data.day_start for c in coordinators if c.data.day_start), None)
    day = dt_util.as_local(dt_util.utc_from_timestamp(day_start)).date() if day_start else dt_util.now().date()
    tomorrow_start = _day_start(day + timedelta(days=1))
    arrays = [a for c in coordinators for a in views.today_arrays(c.arrays, c.data, tomorrow_start, tomorrow_start + 86400)]
    result = {"now": dt_util.utcnow().timestamp(), "day_start": day_start, "time_zone": hass.config.time_zone, "arrays": arrays, "days": []}
    # with a load forecast and batteries the card shows what Noxcast computed instead of its own estimate
    if coordinators:
        c = coordinators[0]
        result.update(views.today_totals(c.data, c.consumers, c.forecast_days, dt_util.now().date(), _day_start))
    _send(connection, msg, result)


@websocket_api.websocket_command({vol.Required("type"): WS_ACCURACY})
@websocket_api.async_response
async def ws_accuracy(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Day-ahead forecast archive for the accuracy view of the panel."""
    entries = []
    for c in _coordinators(hass):
        days = await hass.async_add_executor_job(views.accuracy_days, c.forecast_archive, c.site)
        entries.append({"arrays": views.array_names(c.arrays), "days": days})
    _send(connection, msg, {"time_zone": hass.config.time_zone, "entries": entries})


@websocket_api.websocket_command({vol.Required("type"): WS_BACKTEST})
@callback
def ws_backtest(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The last PV backtest for the accuracy view: past days with today's model, and unlearned days."""
    _send(connection, msg, {"entries": [views.backtest_entry(c.arrays, c.pv_backtest) for c in _coordinators(hass)]})


@websocket_api.websocket_command({vol.Required("type"): WS_LOAD})
@callback
def ws_load(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Load forecast for the panel: profiles, consumers, the forecast by part and the backtest."""
    entries = [views.load_entry(c.house_load, c.load_profile, c.consumers, c.consumer_profiles, c.consumer_models,
                                c.data.load if c.data is not None else None, c.load_backtest, c.load_events,
                                c.last_learning)
               for c in _coordinators(hass) if c.house_load]
    _send(connection, msg, {"now": dt_util.utcnow().timestamp(), "time_zone": hass.config.time_zone,
                                       "entries": entries})


@websocket_api.websocket_command({vol.Required("type"): WS_DIAGNOSTICS})
@websocket_api.async_response
async def ws_diagnostics(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Data quality for the panel: every sensor with unit, state and statistics coverage, learning runs, exclusions."""
    now = dt_util.utcnow()
    start = now - timedelta(days=DIAG_DAYS)
    entries = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        c = entry.runtime_data
        roles = views.sensor_roles(c.arrays, entry.data.get(CONF_SOC_SENSORS), c.house_load, c.consumers, c.batteries,
                                   _battery_names(entry))
        ids = sorted({e for e, _, _ in roles})
        try:
            meta = await get_instance(hass).async_add_executor_job(lambda: get_metadata(hass, statistic_ids=set(ids)))
            stats = await get_instance(hass).async_add_executor_job(
                statistics_during_period, hass, start, None, set(ids), "hour", None, {"mean", "change"})
        except Exception as err:  # noqa: BLE001 – the view still shows states without statistics
            _LOGGER.debug("Statistics for diagnostics unavailable: %s", err)
            meta, stats = {}, {}
        sensors = [views.sensor_row(entity_id, role, owner, hass.states.get(entity_id), stats.get(entity_id, []),
                                    entity_id in meta, DIAG_DAYS * 24) for entity_id, role, owner in roles]
        entries.append(views.diagnostics_entry(
            entry.title, weather_models=c.weather_models, data=c.data, last_learning=c.last_learning,
            learning_seconds=c.last_learning_seconds, backtest_seconds=(c.pv_backtest or {}).get("seconds"),
            learning_error=c.last_learning_error, archive_errors=c.archive_errors, arrays=c.arrays, learned=c.learned,
            exclusions=c.exclusions, archive=c.forecast_archive, fine=c.fine, sensors=sensors))
    _send(connection, msg, {"time_zone": hass.config.time_zone, "days": DIAG_DAYS, "entries": entries})


@websocket_api.websocket_command({vol.Required("type"): WS_BATTERY})
@websocket_api.async_response
async def ws_battery(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Battery and surplus for the panel: the storage, PV and load per hour for what-if runs, the last 30 days."""
    now = dt_util.utcnow()
    entries = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        c = entry.runtime_data
        if not c.batteries:
            continue
        result = views.battery_entry(c.batteries, _battery_names(entry), c.data, now.timestamp())
        # review: lowest and highest state of charge per day from the long-term statistics
        socs = views.soc_capacities(c.batteries)
        try:
            stats = await get_instance(hass).async_add_executor_job(
                statistics_during_period, hass, now - timedelta(days=REVIEW_DAYS), None, set(socs), "hour", None,
                {"min", "max"})
        except Exception as err:  # noqa: BLE001 – the what-if part works without statistics
            _LOGGER.debug("Statistics for the battery review unavailable: %s", err)
            stats = {}
        result["days"] = views.soc_review(stats, socs, _local_date)
        # the morning predictions against what happened, from the 5-minute statistics (kept about ten days)
        result["predictions"] = []
        if c.battery_log and socs:
            first = min(c.battery_log)
            try:
                short = await get_instance(hass).async_add_executor_job(
                    statistics_during_period, hass, dt_util.start_of_local_day(date.fromisoformat(first)), None,
                    set(socs), "5minute", None, {"mean"})
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Short-term statistics for the battery predictions unavailable: %s", err)
                short = {}
            result["predictions"] = views.prediction_review(
                c.battery_log, short, socs, lambda key: _day_start(date.fromisoformat(key) + timedelta(days=1)))
        entries.append(result)
    _send(connection, msg, {"now": now.timestamp(), "time_zone": hass.config.time_zone,
                                       "review_days": REVIEW_DAYS, "entries": entries})


@websocket_api.websocket_command({vol.Required("type"): WS_CARD})
@callback
def ws_card_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The sensors the day card shows, as configured in the integration; the card only changes the display."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        _send(connection, msg, None)
        return
    entry = entries[0]
    c = entry.runtime_data
    _send(connection, msg, views.card_config(
        c.arrays, c.house_load, c.grid, entry.data.get(CONF_GRID_INVERT), entry.data.get(CONF_FEED_IN), c.batteries,
        _battery_names(entry), er.async_get(hass).async_get_entity_id("weather", DOMAIN, f"{entry.entry_id}_weather")))


@websocket_api.websocket_command({vol.Required("type"): WS_DAY, vol.Optional("offset", default=0): vol.All(int, vol.Range(-120, 2))})
@websocket_api.async_response
async def ws_day(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """A day in detail: offset 0 = today, negative = past days from the archive, up to 2 days ahead."""
    day = dt_util.now().date() + timedelta(days=msg["offset"])
    a, b = _day_start(day), _day_start(day + timedelta(days=1))
    now = dt_util.utcnow().timestamp()
    result: dict[str, Any] = {"now": now, "time_zone": hass.config.time_zone, "date": day.isoformat(),
                              "offset": msg["offset"], "start": a, "end": b, "entries": []}
    for c in _coordinators(hass):
        view = await hass.async_add_executor_job(views.day_view, c.site, c.arrays, dict(c.learned),
                                                 fc_archive.day(c.forecast_archive, day), c.data, a, b, now)
        view["archive_days"] = fc_archive.dates(c.forecast_archive)[:1]
        view["flows"] = None
        if a < now:
            try:
                view["flows"] = await c.async_day_flows(a, b, views.day_pv(view))
            except Exception:  # noqa: BLE001 – the day view works without the energy paths
                _LOGGER.exception("Energy paths of %s failed", day)
        result["entries"].append(view)
    _send(connection, msg, result)
