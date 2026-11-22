"""The panel learns which version of its scripts Home Assistant serves now, to ask an old tab for a reload."""
from __future__ import annotations

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from custom_components.noxcast import panel


async def test_frontend_version_is_the_registered_one(hass: HomeAssistant, hass_ws_client) -> None:
    websocket_api.async_register_command(hass, panel.ws_frontend)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": panel.WS_FRONTEND})
    assert (await client.receive_json())["result"]["version"] is None      # no panel registered (no frontend)
    hass.data[panel.FRONTEND_KEY] = "1763800000"
    await client.send_json_auto_id({"type": panel.WS_FRONTEND})
    result = (await client.receive_json())["result"]
    assert result["version"] == "1763800000" and result["v"] == panel.WS_SCHEMA_VERSION
