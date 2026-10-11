"""Noxcast – transparent PV generation forecast."""
from __future__ import annotations

from datetime import timedelta

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SERVICE_EXCLUDE_PERIOD, SERVICE_RELEARN
from .coordinator import NoxcastConfigEntry, NoxcastCoordinator

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

EXCLUDE_SCHEMA = vol.Schema({
    vol.Required("start_date"): cv.date,
    vol.Required("end_date"): cv.date,
    vol.Optional("note", default=""): cv.string,
})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register domain services."""

    def _coordinators() -> list[NoxcastCoordinator]:
        return [e.runtime_data for e in hass.config_entries.async_loaded_entries(DOMAIN)]

    async def exclude_period(call: ServiceCall) -> None:
        if call.data["end_date"] < call.data["start_date"]:
            raise ServiceValidationError("end_date must not be before start_date")
        start = dt_util.start_of_local_day(call.data["start_date"])
        end = dt_util.start_of_local_day(call.data["end_date"]) + timedelta(days=1)
        for coordinator in _coordinators():
            await coordinator.async_add_exclusion(start, end, call.data["note"])
        await relearn(call)

    async def relearn(call: ServiceCall) -> None:
        coordinators = _coordinators()
        if not coordinators:
            raise ServiceValidationError("Noxcast is not loaded")
        for coordinator in coordinators:
            if not await coordinator.async_learn():
                raise HomeAssistantError(f"Learning failed: {coordinator.last_learning_error}")

    hass.services.async_register(DOMAIN, SERVICE_EXCLUDE_PERIOD, exclude_period, schema=EXCLUDE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RELEARN, relearn)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NoxcastConfigEntry) -> bool:
    coordinator = NoxcastCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: NoxcastConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NoxcastConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
