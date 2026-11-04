"""An excluded period can be taken back into learning from the diagnostics."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from custom_components.noxcast import panel
from custom_components.noxcast.coordinator import NoxcastCoordinator

A = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()
B = A + 86400


def _coordinator() -> NoxcastCoordinator:
    c = object.__new__(NoxcastCoordinator)
    c.exclusions = [{"start": A, "end": B, "note": "away"}, {"start": B, "end": B + 86400, "note": ""}]
    c._store = MagicMock()
    c._store_data = lambda: {}
    c.async_learn = AsyncMock(return_value=True)
    return c


async def test_remove_exclusion_drops_only_that_period(hass: HomeAssistant) -> None:
    c = _coordinator()
    assert await c.async_remove_exclusion(A + 0.5, B)
    assert c.exclusions == [{"start": B, "end": B + 86400, "note": ""}]
    assert not await c.async_remove_exclusion(A, B)                 # already gone
    c._store.async_delay_save.assert_called_once()


async def test_remove_exclusion_over_websocket_relearns(hass: HomeAssistant, hass_ws_client) -> None:
    c = _coordinator()
    websocket_api.async_register_command(hass, panel.ws_remove_exclusion)
    client = await hass_ws_client(hass)
    iso = lambda ts: datetime.fromtimestamp(ts, timezone.utc).isoformat()      # as the diagnostics list them
    with patch.object(panel, "_coordinators", return_value=[c]):
        await client.send_json_auto_id({"type": panel.WS_REMOVE_EXCLUSION, "start": iso(A), "end": iso(B)})
        answer = await client.receive_json()
        await hass.async_block_till_done()
    assert answer["success"] and answer["result"]["removed"]
    assert len(c.exclusions) == 1
    c.async_learn.assert_awaited_once()


async def test_remove_exclusion_needs_an_admin(hass: HomeAssistant, hass_ws_client, hass_read_only_access_token) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    websocket_api.async_register_command(hass, panel.ws_remove_exclusion)
    await client.send_json_auto_id({"type": panel.WS_REMOVE_EXCLUSION, "start": "2026-10-01T00:00:00+00:00",
                                    "end": "2026-10-02T00:00:00+00:00"})
    answer = await client.receive_json()
    assert not answer["success"] and answer["error"]["code"] == "unauthorized"
