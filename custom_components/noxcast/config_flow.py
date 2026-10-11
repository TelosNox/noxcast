"""Config flow: main entry (weather, battery) and one subentry per PV array."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry, ConfigFlow, ConfigFlowResult, ConfigSubentryFlow, SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AZIMUTH, CONF_NAME, CONF_PEAK_POWER, CONF_POWER_SENSORS, CONF_SOC_FULL, CONF_SOC_SENSORS,
    CONF_TILT, CONF_VALID_SINCE, CONF_WEATHER_ENTITY, CONF_WEATHER_SOURCE, DEFAULT_SOC_FULL, DOMAIN,
    POWER_UNIT_FACTORS, SUBENTRY_ARRAY, WEATHER_SOURCE_ENTITY, WEATHER_SOURCE_OPEN_METEO,
)
from .sources import NoIrradianceError, async_weather_entity


def _main_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_WEATHER_SOURCE, default=defaults.get(CONF_WEATHER_SOURCE, WEATHER_SOURCE_ENTITY)):
            selector.SelectSelector(selector.SelectSelectorConfig(
                options=[WEATHER_SOURCE_ENTITY, WEATHER_SOURCE_OPEN_METEO], translation_key=CONF_WEATHER_SOURCE)),
        vol.Optional(CONF_WEATHER_ENTITY, description={"suggested_value": defaults.get(CONF_WEATHER_ENTITY)}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="weather")),
        vol.Optional(CONF_SOC_SENSORS, default=defaults.get(CONF_SOC_SENSORS, [])):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", multiple=True)),
        vol.Required(CONF_SOC_FULL, default=defaults.get(CONF_SOC_FULL, DEFAULT_SOC_FULL)):
            selector.NumberSelector(selector.NumberSelectorConfig(min=50, max=100, step=1, unit_of_measurement="%")),
    })


def _array_schema(defaults: dict[str, Any]) -> vol.Schema:
    number = selector.NumberSelectorMode.BOX
    return vol.Schema({
        vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): selector.TextSelector(),
        vol.Required(CONF_POWER_SENSORS, default=defaults.get(CONF_POWER_SENSORS, [])):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", multiple=True)),
        vol.Required(CONF_TILT, default=defaults.get(CONF_TILT, 30)):
            selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=90, step=1, unit_of_measurement="°", mode=number)),
        vol.Required(CONF_AZIMUTH, default=defaults.get(CONF_AZIMUTH, 180)):
            selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=359, step=1, unit_of_measurement="°", mode=number)),
        vol.Required(CONF_PEAK_POWER, default=defaults.get(CONF_PEAK_POWER, 800)):
            selector.NumberSelector(selector.NumberSelectorConfig(min=10, max=100000, step=10, unit_of_measurement="Wp", mode=number)),
        vol.Optional(CONF_VALID_SINCE, description={"suggested_value": defaults.get(CONF_VALID_SINCE)}):
            selector.DateSelector(),
    })


async def _validate_main(flow, user_input: dict[str, Any]) -> dict[str, str]:
    for entity_id in user_input.get(CONF_SOC_SENSORS) or []:
        state = flow.hass.states.get(entity_id)
        if state is None or state.attributes.get("unit_of_measurement") != "%":
            return {CONF_SOC_SENSORS: "soc_unit"}
    if user_input[CONF_WEATHER_SOURCE] != WEATHER_SOURCE_ENTITY:
        return {}
    entity = user_input.get(CONF_WEATHER_ENTITY)
    if not entity:
        return {CONF_WEATHER_ENTITY: "weather_entity_required"}
    try:
        await async_weather_entity(flow.hass, entity)
    except NoIrradianceError:
        return {CONF_WEATHER_ENTITY: "no_irradiance"}
    except Exception:  # noqa: BLE001
        return {CONF_WEATHER_ENTITY: "weather_unavailable"}
    return {}


class NoxcastConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors = {}
        if user_input is not None:
            errors = await _validate_main(self, user_input)
            if not errors:
                return self.async_create_entry(title="Noxcast", data=user_input)
        return self.async_show_form(step_id="user", data_schema=_main_schema(user_input or {}), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            errors = await _validate_main(self, user_input)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=user_input)
        return self.async_show_form(step_id="reconfigure", data_schema=_main_schema(user_input or dict(entry.data)),
                                    errors=errors)

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ARRAY: ArraySubentryFlow}


class ArraySubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one PV array."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors = _validate_array(self.hass, user_input)
        if user_input is not None and not errors:
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="user", data_schema=_array_schema(user_input or {}), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors = _validate_array(self.hass, user_input)
        if user_input is not None and not errors:
            return self.async_update_and_abort(self._get_entry(), subentry, title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="reconfigure", data_schema=_array_schema(user_input or dict(subentry.data)),
                                    errors=errors)


def _validate_array(hass: HomeAssistant, user_input: dict[str, Any] | None) -> dict[str, str]:
    """Power sensors must report W/kW as measurement (long-term statistics); date not in the future."""
    if user_input is None:
        return {}
    sensors = user_input.get(CONF_POWER_SENSORS) or []
    if not sensors:
        return {CONF_POWER_SENSORS: "sensor_required"}
    for entity_id in sensors:
        state = hass.states.get(entity_id)
        if state is None:
            return {CONF_POWER_SENSORS: "sensor_missing"}
        if state.attributes.get("unit_of_measurement") not in POWER_UNIT_FACTORS:
            return {CONF_POWER_SENSORS: "sensor_unit"}
        if state.attributes.get("state_class") != "measurement":
            return {CONF_POWER_SENSORS: "sensor_state_class"}
    valid = user_input.get(CONF_VALID_SINCE)
    if valid and (dt_util.parse_date(valid) is None or dt_util.parse_date(valid) > dt_util.now().date()):
        return {CONF_VALID_SINCE: "date_in_future"}
    return {}
