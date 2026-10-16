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


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the websocket commands and, where the frontend is loaded, the panel and the day card."""
    websocket_api.async_register_command(hass, ws_learned)
    websocket_api.async_register_command(hass, ws_today)
    if "frontend" not in hass.config.components or getattr(hass, "http", None) is None:
        return                                           # e.g. tests without a frontend
    from homeassistant.components import panel_custom
    from homeassistant.components.http import StaticPathConfig

    version = await hass.async_add_executor_job(_version, "noxcast-panel.js")
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


def _version(name: str) -> str:
    """Cache buster: modification time of a frontend script."""
    return str(int((Path(__file__).parent / "frontend" / name).stat().st_mtime))


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
                "id": sub_id, "name": arr.name,
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
