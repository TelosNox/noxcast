"""Sidebar panel "Noxcast" and the day card, both fed by websocket commands.

A thin adapter: each command gathers its inputs from the coordinators (and the recorder), lets a pure
function of views.py build the payload and sends it with the schema version (see views.py).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import probatio as vol

from homeassistant.components import websocket_api
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import get_metadata, statistics_during_period
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import archive as fc_archive
from . import concepts, databasis, editor, flowchart, store, swapflow, systems, views
from .const import ARCHIVE_DAYS, CONF_GRID_INVERT, DOMAIN, EVAL_DAYS, LEARN_TIME, WS_SCHEMA_VERSION

_LOGGER = logging.getLogger(__name__)

URL_BASE = "/noxcast_panel"
PANEL_PATH = "noxcast"
WS_LEARNED = f"{DOMAIN}/learned"
WS_TODAY = f"{DOMAIN}/today"
WS_ACCURACY = f"{DOMAIN}/accuracy"
WS_BACKTEST = f"{DOMAIN}/backtest"
WS_MODELS = f"{DOMAIN}/weather_models"
WS_COMPARE = f"{DOMAIN}/compare_weather_models"
WS_LOAD = f"{DOMAIN}/load"
WS_DIAGNOSTICS = f"{DOMAIN}/diagnostics"
WS_BATTERY = f"{DOMAIN}/battery"
WS_DAY = f"{DOMAIN}/day"
WS_OVERVIEW = f"{DOMAIN}/overview"
WS_CARD = f"{DOMAIN}/card_config"
WS_LEARNING = f"{DOMAIN}/learning"
WS_RELEARN = f"{DOMAIN}/relearn"
WS_REMOVE_EXCLUSION = f"{DOMAIN}/remove_exclusion"
WS_INSTALLATION = f"{DOMAIN}/installation"
WS_MOVE = f"{DOMAIN}/move_part"
WS_PART_FORM = f"{DOMAIN}/part_form"
WS_PART_PREVIEW = f"{DOMAIN}/part_preview"
WS_PART_SAVE = f"{DOMAIN}/part_save"
WS_PART_REMOVE = f"{DOMAIN}/part_remove"
WS_SWAP_FORM = f"{DOMAIN}/swap_form"
WS_SWAP_SAVE = f"{DOMAIN}/swap_save"
WS_SWAP_UNDO = f"{DOMAIN}/swap_undo"
WS_FRONTEND = f"{DOMAIN}/frontend"
# the cache buster the panel was registered with (hass.data): a tab that runs another one has old scripts
FRONTEND_KEY = f"{DOMAIN}_frontend"
DIAG_DAYS = 30
REVIEW_DAYS = 30


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the websocket commands and, where the frontend is loaded, the panel and the day card."""
    websocket_api.async_register_command(hass, ws_learned)
    websocket_api.async_register_command(hass, ws_frontend)
    websocket_api.async_register_command(hass, ws_today)
    websocket_api.async_register_command(hass, ws_accuracy)
    websocket_api.async_register_command(hass, ws_backtest)
    websocket_api.async_register_command(hass, ws_models)
    websocket_api.async_register_command(hass, ws_compare)
    websocket_api.async_register_command(hass, ws_load)
    websocket_api.async_register_command(hass, ws_diagnostics)
    websocket_api.async_register_command(hass, ws_battery)
    websocket_api.async_register_command(hass, ws_day)
    websocket_api.async_register_command(hass, ws_overview)
    websocket_api.async_register_command(hass, ws_card_config)
    websocket_api.async_register_command(hass, ws_learning)
    websocket_api.async_register_command(hass, ws_relearn)
    websocket_api.async_register_command(hass, ws_remove_exclusion)
    websocket_api.async_register_command(hass, ws_installation)
    websocket_api.async_register_command(hass, ws_move_part)
    websocket_api.async_register_command(hass, ws_part_form)
    websocket_api.async_register_command(hass, ws_part_preview)
    websocket_api.async_register_command(hass, ws_part_save)
    websocket_api.async_register_command(hass, ws_part_remove)
    websocket_api.async_register_command(hass, ws_swap_form)
    websocket_api.async_register_command(hass, ws_swap_save)
    websocket_api.async_register_command(hass, ws_swap_undo)
    if "frontend" not in hass.config.components or getattr(hass, "http", None) is None:
        return                                           # e.g. tests without a frontend
    # after_dependencies only: imported where they are loaded
    from homeassistant.components import panel_custom
    from homeassistant.components.http import StaticPathConfig

    hass.http.register_view(_diagram_view())
    hass.http.register_view(_concept_view())
    hass.http.register_view(_hours_view())

    # the panel loads further section modules with its own cache buster, so it covers all of them
    version = await hass.async_add_executor_job(_version, "noxcast-common.js", "noxcast-panel.js", "noxcast-accuracy.js", "noxcast-load.js",
                                         "noxcast-diagnostics.js", "noxcast-battery.js", "noxcast-installation.js",
                                         "noxcast-theme.js", "noxcast-day.js", "noxcast-overview.js")
    hass.data[FRONTEND_KEY] = version
    card_version = await hass.async_add_executor_job(_version, "noxcast-card.js", "noxcast-common.js")
    weather_version = await hass.async_add_executor_job(_version, "noxcast-weather-card.js", "noxcast-common.js")
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
    parts = store.parts(entry)
    return {sid: parts[sid].title for sid in entry.runtime_data.batteries if sid in parts}


def _day_start(day: date) -> float:
    return dt_util.start_of_local_day(day).timestamp()


def _local_date(ts: float) -> str:
    return dt_util.as_local(dt_util.utc_from_timestamp(ts)).date().isoformat()


@websocket_api.websocket_command({vol.Required("type"): WS_FRONTEND})
@callback
def ws_frontend(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The version of the panel scripts Home Assistant serves now.

    Home Assistant keeps an open tab running across a restart: after an update it still runs the old scripts
    against the new backend, and the panel compares this with its own version to ask for a reload.
    """
    _send(connection, msg, {"version": hass.data.get(FRONTEND_KEY)})


@websocket_api.websocket_command({vol.Required("type"): WS_LEARNED})
@callback
def ws_learned(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Learned state of all arrays of all Noxcast entries (the site is the Home Assistant location)."""
    coordinators = _coordinators(hass)
    arrays = [a for c in coordinators for a in views.learned_arrays(c.arrays, c.learned, c.last_learning, c.learning_changes)]
    _send(connection, msg, {
        "latitude": hass.config.latitude, "longitude": hass.config.longitude,
        "time_zone": hass.config.time_zone, "arrays": arrays,
        # which optional panel sections have something to show: the house load and the batteries
        "sections": {"load": any(c.house_load for c in coordinators), "battery": any(c.batteries for c in coordinators)},
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
        # the page offers up to 90 days; the whole archive would only make the answer larger
        recent = fc_archive.recent(c.forecast_archive, dt_util.now().date() - timedelta(days=EVAL_DAYS))
        days = await hass.async_add_executor_job(views.accuracy_days, recent, c.site)
        try:
            band = await hass.async_add_executor_job(views.band_coverage, c._eval_archive(), c.site)
        except Exception:  # noqa: BLE001 – the rest of the view stays useful
            _LOGGER.exception("Band coverage for the accuracy view failed")
            band = None
        entries.append({"arrays": views.array_names(c.arrays), "days": days, "band": band})
    _send(connection, msg, {"time_zone": hass.config.time_zone, "entries": entries})


@websocket_api.websocket_command({vol.Required("type"): WS_BACKTEST})
@callback
def ws_backtest(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The last PV backtest for the accuracy view: past days with today's model, and unlearned days."""
    _send(connection, msg, {"entries": [views.backtest_entry(c.arrays, c.pv_backtest) for c in _coordinators(hass)]})


@websocket_api.websocket_command({vol.Required("type"): WS_MODELS})
@callback
def ws_models(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The weather model comparison: the last result, a running or failed run, and whether one may start."""
    _send(connection, msg, {"now": dt_util.utcnow().timestamp(), "entries": [
        views.compare_entry(c.model_compare, c.compare_state, *c.compare_blocked(), c.weather_models, c.compare_candidates)
        for c in _coordinators(hass)]})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_COMPARE})
@callback
def ws_compare(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Start the weather model comparison; it asks Open-Meteo once per model, hence admins only."""
    reasons = [c.async_start_compare() for c in _coordinators(hass)]
    _send(connection, msg, {"started": any(r is None for r in reasons), "reasons": [r for r in reasons if r]})


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


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_DIAGNOSTICS})
@websocket_api.async_response
async def ws_diagnostics(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Data quality for the panel: every sensor with unit, state and statistics coverage, learning runs, exclusions."""
    now = dt_util.utcnow()
    start = now - timedelta(days=DIAG_DAYS)
    entries = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        c = entry.runtime_data
        roles = views.sensor_roles(c.arrays, c.house_load, c.consumers, c.batteries,
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
    _send(connection, msg, {"time_zone": hass.config.time_zone, "days": DIAG_DAYS, "version": await _integration_version(hass),
                            "entries": entries})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_REMOVE_EXCLUSION, vol.Required("start"): str, vol.Required("end"): str})
@websocket_api.async_response
async def ws_remove_exclusion(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Take an excluded period (its bounds as the diagnostics list them) back into learning, then relearn."""
    start, end = dt_util.parse_datetime(msg["start"]), dt_util.parse_datetime(msg["end"])
    if start is None or end is None:
        connection.send_error(msg["id"], "invalid_format", "start and end must be ISO timestamps")
        return
    removed = False
    for c in _coordinators(hass):
        if await c.async_remove_exclusion(start.timestamp(), end.timestamp()):
            removed = True
            # a run takes minutes: answer now, the diagnostics show the new run once it is done
            hass.async_create_background_task(c.async_learn(), "noxcast relearn after removing an exclusion")
    _send(connection, msg, {"removed": removed})


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
        result = views.battery_entry(c.batteries, _battery_names(entry), c.data, now.timestamp(), c.battery_capacity)
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


def _subs(entry: Any) -> systems.Subs:
    return store.flat(entry)


def installation_tree(entry: Any) -> dict[str, Any]:
    """A config entry's installation as systems.tree builds it from the subentries."""
    return systems.tree(entry.data, _subs(entry))


def _installation(entries: list, lang: str) -> dict[str, Any]:
    out = []
    for e in entries:
        tree = installation_tree(e)
        # the "+" handles are drawn for everyone; the tab shows them to admins only, who may add parts
        out.append({"entry_id": e.entry_id, "title": e.title, **tree,
                    "diagram": flowchart.render(tree, lang, handles=True), "swaps": swapflow.swaps(e)})
    return {"entries": out}


@websocket_api.websocket_command({vol.Required("type"): WS_INSTALLATION, vol.Optional("language"): str})
@callback
def ws_installation(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The installation as entered (household, systems, arrays, batteries), how far each setting counts, and the
    flow diagram (flowchart.render)."""
    lang = (msg.get("language") or hass.config.language or "en")[:2]
    _send(connection, msg, _installation(hass.config_entries.async_loaded_entries(DOMAIN), lang))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_MOVE, vol.Required("entry_id"): str,
                                  vol.Required("part"): str, vol.Required("to"): str, vol.Optional("language"): str})
@callback
def ws_move_part(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Move a PV array or battery to another system, or a system under another parent ("" for none), from the
    Installation tab. The same rules as the forms (systems.owners); the entry reloads as after editing a part.
    Answers with the installation as it is now, since the entry is not loaded while it reloads."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    subs = _subs(entry) if entry is not None and entry.domain == DOMAIN else {}
    if msg["part"] not in subs:
        connection.send_error(msg["id"], "not_found", "No such part of a Noxcast installation")
        return
    data = systems.moved(subs, msg["part"], msg["to"])
    if data is None:
        connection.send_error(msg["id"], "not_possible", "The part cannot go there")
        return
    kind, title, _ = subs[msg["part"]]
    store.async_store(hass, entry, {**subs, msg["part"]: (kind, title, data)})
    lang = (msg.get("language") or hass.config.language or "en")[:2]
    _send(connection, msg, _installation([entry], lang))


# a part of the installation edited in the Installation tab: its id, "house" for the household, or none for a new one
# (then its kind, and the system it goes into)
_PART = {vol.Required("entry_id"): str, vol.Optional("part"): vol.Any(str, None), vol.Optional("kind"): str,
         vol.Optional("to"): vol.Any(str, None), vol.Optional("language"): str}


def _editable(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> Any:
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], "not_found", "No such Noxcast installation")
        return None
    return entry


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_PART_FORM, **_PART, vol.Optional("draft"): dict})
@websocket_api.async_response
async def ws_part_form(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The form of a part as the configuration dialogs have it (editor.async_form), for ha-form in the panel. With a
    draft (a system whose template changed) the form is built for it."""
    if (entry := _editable(hass, connection, msg)) is None:
        return
    try:
        form = await editor.async_form(hass, entry, msg.get("part"), msg.get("kind"), msg.get("to"), msg.get("draft"),
                                       (msg.get("language") or hass.config.language or "en")[:2])
    except editor.EditError as err:
        connection.send_error(msg["id"], str(err), "The part cannot be edited there")
        return
    _send(connection, msg, form)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_PART_PREVIEW, **_PART, vol.Required("input"): dict})
@websocket_api.async_response
async def ws_part_preview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The diagram as it would look with the form's input, and the form's problems; nothing is stored."""
    if (entry := _editable(hass, connection, msg)) is None:
        return
    try:
        result = await editor.async_preview(hass, entry, msg.get("kind"), msg.get("part"), msg.get("to"), msg["input"],
                                            (msg.get("language") or hass.config.language or "en")[:2])
    except editor.EditError as err:
        connection.send_error(msg["id"], str(err), "The part cannot be edited there")
        return
    _send(connection, msg, result)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_PART_SAVE, **_PART, vol.Required("input"): dict,
                                  vol.Optional("revision"): vol.Any(str, None)})
@websocket_api.async_response
async def ws_part_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Store a part as the dialogs do. Answers with the problems ({"errors": …}) or with the installation as it is
    now; the entry reloads as after a dialog."""
    if (entry := _editable(hass, connection, msg)) is None:
        return
    try:
        errors = await editor.async_save(hass, entry, msg.get("kind"), msg.get("part"), msg.get("to"), msg["input"],
                                         msg.get("revision"))
    except editor.EditError as err:
        connection.send_error(msg["id"], str(err), "The part cannot be edited there")
        return
    lang = (msg.get("language") or hass.config.language or "en")[:2]
    _send(connection, msg, {"errors": errors} if errors else _installation([entry], lang))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_PART_REMOVE, vol.Required("entry_id"): str,
                                  vol.Required("part"): str, vol.Optional("confirm", default=False): bool,
                                  vol.Optional("revision"): vol.Any(str, None), vol.Optional("language"): str})
@callback
def ws_part_remove(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Without confirm: what removing the part would take with it, and the diagram without it; nothing is removed.
    With confirm and that answer's revision: remove it, as the dialog does. Answers then with the installation as it
    is now, or {"errors": {"base": "changed"}} when the part changed meanwhile."""
    if (entry := _editable(hass, connection, msg)) is None:
        return
    lang = (msg.get("language") or hass.config.language or "en")[:2]
    try:
        if not msg["confirm"]:
            _send(connection, msg, editor.removal(entry, msg["part"], lang))
            return
        errors = editor.remove(hass, entry, msg["part"], msg.get("revision"))
    except editor.EditError as err:
        connection.send_error(msg["id"], str(err), "No such part of a Noxcast installation")
        return
    _send(connection, msg, {"errors": errors} if errors else _installation([entry], lang))


_SWAP = {vol.Required("entry_id"): str, vol.Required("system"): str, vol.Optional("language"): str}


def _loaded(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> Any:
    """The entry, loaded: a swap needs its coordinator (the history and the frozen store)."""
    entry = _editable(hass, connection, msg)
    if entry is not None and getattr(entry, "runtime_data", None) is None:
        connection.send_error(msg["id"], "not_loaded", "Noxcast is still starting")
        return None
    return entry


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_SWAP_FORM, **_SWAP})
@websocket_api.async_response
async def ws_swap_form(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The form of a system's device swap (swapflow.async_form), for ha-form in the Installation tab."""
    if (entry := _loaded(hass, connection, msg)) is None:
        return
    try:
        form = await swapflow.async_form(hass, entry, msg["system"], (msg.get("language") or hass.config.language or "en")[:2])
    except editor.EditError as err:
        connection.send_error(msg["id"], str(err), "No such system")
        return
    _send(connection, msg, form)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_SWAP_SAVE, **_SWAP, vol.Required("input"): dict,
                                  vol.Optional("revision"): vol.Any(str, None)})
@websocket_api.async_response
async def ws_swap_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Swap the system's device: freeze its arrays' hours and put the new entities in place. Answers with the problems
    ({"errors": …}) or with the installation as it is now; the entry reloads."""
    if (entry := _loaded(hass, connection, msg)) is None:
        return
    errors = await swapflow.async_save(hass, entry, msg["system"], msg["input"], msg.get("revision"))
    lang = (msg.get("language") or hass.config.language or "en")[:2]
    _send(connection, msg, {"errors": errors} if errors else _installation([entry], lang))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_SWAP_UNDO, **_SWAP})
@websocket_api.async_response
async def ws_swap_undo(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Turn the system's newest swap back while the old statistics exist (swapflow.async_undo)."""
    if (entry := _loaded(hass, connection, msg)) is None:
        return
    errors = await swapflow.async_undo(hass, entry, msg["system"])
    lang = (msg.get("language") or hass.config.language or "en")[:2]
    message = swapflow.error_text(errors.get("base", ""), lang) if errors else ""
    _send(connection, msg, {"errors": errors, "message": message} if errors else _installation([entry], lang))


# no ".svg" ending: the Markdown filter of the forms might drop SVG images by their name; the content type says it
DIAGRAM_URL = "/api/noxcast/installation_diagram"


def diagram_markdown(hass: HomeAssistant, entry: Any) -> str:
    """The flow diagram as a Markdown image for the description of a configuration form: a signed address, since
    an image request carries no token. Empty before the first system exists."""
    if getattr(hass, "http", None) is None or not installation_tree(entry)["systems"]:
        return ""
    from homeassistant.components.http.auth import async_sign_path

    lang = (hass.config.language or "en")[:2]
    path = async_sign_path(hass, f"{DIAGRAM_URL}?entry={entry.entry_id}&lang={lang}", timedelta(minutes=30))
    return f"![]({path})"


CONCEPT_URL = "/api/noxcast/concept"


def concept_markdown(hass: HomeAssistant, *names: str) -> str:
    """Explanatory drawings (concepts.py) as Markdown images for a form's description, one below the other; signed
    like the flow diagram. Empty where the web server is not there (tests without it)."""
    if getattr(hass, "http", None) is None:
        return ""
    from homeassistant.components.http.auth import async_sign_path

    lang = (hass.config.language or "en")[:2]
    return "\n\n".join(f"![]({async_sign_path(hass, f'{CONCEPT_URL}?name={name}&lang={lang}', timedelta(minutes=30))})"
                       for name in names)


def _concept_view() -> Any:
    from homeassistant.components.http import HomeAssistantView
    from aiohttp import web

    class ConceptView(HomeAssistantView):
        """An explanatory drawing as an SVG image, for the configuration forms."""

        url = CONCEPT_URL
        name = "api:noxcast:concept"
        requires_auth = True

        async def get(self, request: web.Request) -> web.Response:
            svg = concepts.render(request.query.get("name", ""), request.query.get("lang", "en"))
            if svg is None:
                return web.Response(status=404)
            return web.Response(text=svg, content_type="image/svg+xml", headers={"Cache-Control": "private, max-age=3600"})

    return ConceptView()


HOURS_URL = "/api/noxcast/hours"


def _hours_view() -> Any:
    from homeassistant.components.http import HomeAssistantView
    from aiohttp import web

    class HoursView(HomeAssistantView):
        """Every daylight hour of one array with its status and reasons as CSV (databasis.py), for admins: it holds
        the measured history. With day (an ISO date from the calendar, a solar day): only that day's rows as JSON,
        for the day view."""

        url = HOURS_URL
        name = "api:noxcast:hours"
        requires_auth = True

        async def get(self, request: web.Request) -> web.Response:
            if not request["hass_user"].is_admin:
                return web.Response(status=403)
            hass, sid = request.app["hass"], request.query.get("array", "")
            c = next((c for c in _coordinators(hass) if sid in c.arrays), None)
            if c is None:
                return web.Response(status=404)
            if "day" in request.query:
                try:
                    day = date.fromisoformat(request.query["day"])
                except ValueError:
                    return web.Response(status=400)
                rows = await c.async_day(sid, day)
                if rows is None:
                    return web.Response(status=404)
                return web.json_response(rows, headers={"Cache-Control": "no-store"})
            tables = await c.async_hour_tables(sid)
            if sid not in tables:
                return web.Response(status=404)
            name = "".join(ch if ch.isascii() and ch.isalnum() else "-" for ch in c.arrays[sid].name).strip("-").lower() or "array"
            text = await hass.async_add_executor_job(databasis.to_csv, tables[sid])
            return web.Response(text=text, content_type="text/csv", headers={
                "Content-Disposition": f'attachment; filename="noxcast-hours-{name}.csv"', "Cache-Control": "no-store"})

    return HoursView()


def _diagram_view() -> Any:
    from homeassistant.components.http import HomeAssistantView
    from aiohttp import web

    class InstallationDiagramView(HomeAssistantView):
        """The flow diagram as an SVG image, for the configuration forms."""

        url = DIAGRAM_URL
        name = "api:noxcast:installation"
        requires_auth = True

        async def get(self, request: web.Request) -> web.Response:
            hass = request.app["hass"]
            entry = hass.config_entries.async_get_entry(request.query.get("entry", ""))
            if entry is None or entry.domain != DOMAIN:
                return web.Response(status=404)
            svg = flowchart.render(installation_tree(entry), request.query.get("lang", "en")[:2])
            return web.Response(text=svg, content_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    return InstallationDiagramView()


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
        c.arrays, c.house_load, c.grid, entry.data.get(CONF_GRID_INVERT), systems.exports(entry.data, systems.systems_of(store.flat(entry))), c.batteries,
        _battery_names(entry), er.async_get(hass).async_get_entity_id("weather", DOMAIN, f"{entry.entry_id}_weather")))


@websocket_api.websocket_command({vol.Required("type"): WS_DAY, vol.Optional("offset", default=0): vol.All(int, vol.Range(-ARCHIVE_DAYS, 2))})
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


async def _integration_version(hass: HomeAssistant) -> str:
    """The installed Noxcast version, from its manifest."""
    return str((await async_get_integration(hass, DOMAIN)).version)


@websocket_api.websocket_command({vol.Required("type"): WS_OVERVIEW})
@websocket_api.async_response
async def ws_overview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """What is due today and in the days ahead, for the overview of the panel; None without a Noxcast entry."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        _send(connection, msg, None)
        return
    entry = entries[0]
    c = entry.runtime_data
    now = dt_util.utcnow()
    roles = views.sensor_roles(c.arrays, c.house_load, c.consumers, c.batteries,
                               _battery_names(entry))
    sensors = [(e, role, owner, (s.state if (s := hass.states.get(e)) else None)) for e, role, owner in roles]
    status = views.learning_status(c.arrays, c.learned, c.last_learning, c.last_learning_seconds, c.last_learning_error,
                                   c.learning_running, _next_learning(now))
    remaining = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_energy_expected_remaining_today")
    result = views.overview_entry(c.arrays, c.data, c.consumers, c.forecast_days, dt_util.now().date(), _day_start,
                                  c.forecast_archive, status, sensors, c.batteries, remaining)
    _send(connection, msg, {"now": now.timestamp(), "time_zone": hass.config.time_zone,
                            "version": await _integration_version(hass), **result})


def _next_learning(now: datetime) -> datetime:
    """Next nightly learning run (LEARN_TIME, local)."""
    local = dt_util.as_local(now)
    run = local.replace(hour=LEARN_TIME[0], minute=LEARN_TIME[1], second=0, microsecond=0)
    return run if run > local else dt_util.as_local(
        datetime.combine(local.date() + timedelta(days=1), time(*LEARN_TIME), tzinfo=local.tzinfo))


@websocket_api.websocket_command({vol.Required("type"): WS_LEARNING})
@callback
def ws_learning(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Learning runs and how far learning has come per array; None without a Noxcast entry."""
    coordinators = _coordinators(hass)
    if not coordinators:
        _send(connection, msg, None)
        return
    c = coordinators[0]
    _send(connection, msg, views.learning_status(c.arrays, c.learned, c.last_learning, c.last_learning_seconds,
                                                 c.last_learning_error, c.learning_running, _next_learning(dt_util.utcnow())))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_RELEARN})
@callback
def ws_relearn(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Start learning in the background (it takes minutes); started is False while a run is already going."""
    started = False
    for c in _coordinators(hass):
        if not c.learning_running:
            c.config_entry.async_create_background_task(hass, c.async_learn(), "noxcast relearn from the panel")
            started = True
    _send(connection, msg, {"started": started})
