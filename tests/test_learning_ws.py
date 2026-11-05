"""The panel's learning status and its "learn again now" button."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.noxcast import panel


def _coordinator(running: bool = False):
    c = SimpleNamespace(arrays={"r": SimpleNamespace(name="Roof")}, learned={}, last_learning=None,
                        last_learning_seconds=None, last_learning_error=None, learning_running=running,
                        async_learn=AsyncMock(return_value=True))
    c.config_entry = MagicMock()
    c.config_entry.async_create_background_task = lambda hass, coro, name: hass.async_create_task(coro)
    return c


async def test_next_learning_is_tonight_or_tomorrow_night(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    evening = datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc)
    night = datetime(2026, 7, 1, 22, 0, tzinfo=timezone.utc)           # 00:00 local, before 01:30
    assert dt_util.as_local(panel._next_learning(evening)).isoformat() == "2026-07-02T01:30:00+02:00"
    assert dt_util.as_local(panel._next_learning(night)).isoformat() == "2026-07-02T01:30:00+02:00"


async def test_status_and_relearn(hass: HomeAssistant, hass_ws_client) -> None:
    for cmd in (panel.ws_learning, panel.ws_relearn):
        websocket_api.async_register_command(hass, cmd)
    client = await hass_ws_client(hass)
    c = _coordinator()
    with patch.object(panel, "_coordinators", return_value=[c]):
        await client.send_json_auto_id({"type": panel.WS_LEARNING})
        status = (await client.receive_json())["result"]
        assert status["running"] is False and status["arrays"][0]["stage"] == "waiting"
        await client.send_json_auto_id({"type": panel.WS_RELEARN})
        assert (await client.receive_json())["result"]["started"] is True
        await hass.async_block_till_done()
        c.async_learn.assert_awaited_once()
        c.learning_running = True                                    # a second click while it runs starts nothing
        await client.send_json_auto_id({"type": panel.WS_RELEARN})
        assert (await client.receive_json())["result"]["started"] is False


async def test_relearn_needs_an_admin(hass: HomeAssistant, hass_ws_client, hass_read_only_access_token) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    websocket_api.async_register_command(hass, panel.ws_relearn)
    await client.send_json_auto_id({"type": panel.WS_RELEARN})
    answer = await client.receive_json()
    assert not answer["success"] and answer["error"]["code"] == "unauthorized"
