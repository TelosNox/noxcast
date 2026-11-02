"""Only an administrator can change what is learned or read the data quality view."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.components import websocket_api
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError, Unauthorized

import custom_components.noxcast as noxcast
from custom_components.noxcast import panel
from custom_components.noxcast.const import DOMAIN, SERVICE_EXCLUDE_PERIOD, SERVICE_RELEARN

PERIOD = {"start_date": "2026-10-01", "end_date": "2026-10-02"}


async def _setup(hass: HomeAssistant) -> None:
    with patch.object(noxcast, "async_setup_panel"):
        assert await noxcast.async_setup(hass, {})


@pytest.mark.parametrize(("service", "data"), [(SERVICE_RELEARN, {}), (SERVICE_EXCLUDE_PERIOD, PERIOD)])
async def test_services_need_an_admin(hass: HomeAssistant, hass_read_only_user, hass_admin_user, service, data) -> None:
    await _setup(hass)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(DOMAIN, service, data, blocking=True, context=Context(user_id=hass_read_only_user.id))
    with pytest.raises(ServiceValidationError):              # past the check; nothing is loaded here
        await hass.services.async_call(DOMAIN, service, data, blocking=True, context=Context(user_id=hass_admin_user.id))


async def test_diagnostics_need_an_admin(hass: HomeAssistant, hass_ws_client, hass_read_only_access_token) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    websocket_api.async_register_command(hass, panel.ws_diagnostics)
    await client.send_json_auto_id({"type": panel.WS_DIAGNOSTICS})
    answer = await client.receive_json()
    assert not answer["success"] and answer["error"]["code"] == "unauthorized"
