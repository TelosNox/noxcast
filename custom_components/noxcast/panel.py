"""Sidebar panel "Noxcast": what the system has learned, read through a websocket command."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

URL_BASE = "/noxcast_panel"
PANEL_PATH = "noxcast"
WS_LEARNED = f"{DOMAIN}/learned"


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the websocket command and, where the frontend is loaded, the sidebar panel."""
    websocket_api.async_register_command(hass, ws_learned)
    if "frontend" not in hass.config.components or getattr(hass, "http", None) is None:
        return                                           # e.g. tests without a frontend
    from homeassistant.components import panel_custom
    from homeassistant.components.http import StaticPathConfig

    version = (await hass.async_add_executor_job(_version))
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_BASE, str(Path(__file__).parent / "frontend"), cache_headers=False)])
    await panel_custom.async_register_panel(
        hass, frontend_url_path=PANEL_PATH, webcomponent_name="noxcast-panel",
        module_url=f"{URL_BASE}/noxcast-panel.js?v={version}", sidebar_title="Noxcast",
        sidebar_icon="mdi:solar-power-variant", require_admin=False, config={})


def _version() -> str:
    """Cache buster: modification time of the panel script."""
    return str(int((Path(__file__).parent / "frontend" / "noxcast-panel.js").stat().st_mtime))


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
