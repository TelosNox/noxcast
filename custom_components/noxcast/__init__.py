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
from .panel import async_setup_panel

PLATFORMS = [Platform.IMAGE, Platform.SENSOR, Platform.WEATHER]
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
    await async_setup_panel(hass)
    return True


def _config_state(entry: NoxcastConfigEntry) -> tuple:
    return (dict(entry.data), dict(entry.options), {sid: (sub.title, dict(sub.data)) for sid, sub in entry.subentries.items()})


async def async_migrate_entry(hass: HomeAssistant, entry: NoxcastConfigEntry) -> bool:
    """1.1 -> 1.3: Open-Meteo is the only weather source, and Noxcast's own weather entity feeds the cards."""
    if entry.version > 1:
        return False
    if entry.minor_version < 3:
        data = {k: v for k, v in entry.data.items() if k not in ("weather_source", "weather_entity")}
        options = {k: v for k, v in entry.options.items() if k != "compare_open_meteo"}
        hass.config_entries.async_update_entry(entry, data=data, options=options, minor_version=3)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NoxcastConfigEntry) -> bool:
    built_from = _config_state(entry)
    coordinator = NoxcastCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    # a change saved while this setup ran (e.g. a second subentry edited during the reload of the
    # first) found no listener; reload once more so it is not lost until the next restart
    if _config_state(entry) != built_from:
        hass.config_entries.async_schedule_reload(entry.entry_id)
    return True


async def _async_reload(hass: HomeAssistant, entry: NoxcastConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NoxcastConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
