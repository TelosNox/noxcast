"""Config flow: main entry (weather, battery) and one subentry per PV array."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry, ConfigFlow, ConfigFlowResult, ConfigSubentryFlow, OptionsFlow, SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from .const import (
    CONF_API_KEY, CONF_ARCHIVE_TEMPERATURE, CONF_COMPARE_OPEN_METEO, CONF_ARCHIVE_URL, CONF_SATELLITE, CONF_SATELLITE_MODEL, CONF_SATELLITE_URL,
    DEFAULT_ARCHIVE_URL, DEFAULT_SATELLITE_MODEL, DEFAULT_SATELLITE_URL,
    CONF_AZIMUTH, CONF_TEMP_COEFF, CONF_NAME, CONF_PEAK_POWER, CONF_POWER_SENSORS, CONF_SOC_FULL, CONF_SOC_SENSORS,
    CONF_TILT, CONF_VALID_SINCE, CONF_WEATHER_ENTITY, CONF_WEATHER_SOURCE, DEFAULT_SOC_FULL, DOMAIN,
    POWER_UNIT_FACTORS, SUBENTRY_ARRAY, WEATHER_SOURCE_ENTITY, WEATHER_SOURCE_OPEN_METEO,
    CONF_HOUSE_LOAD, CONF_GRID, CONF_GRID_INVERT, CONF_DAY_TYPE, CONF_POWER_SENSOR, CONF_TIMING, SUBENTRY_CONSUMER, TIMING_OPTIONS, CONF_KIND, KIND_OPTIONS,
    CONF_ELAPSED_TIME, CONF_ENERGY_SENSOR, CONF_REMAINING_TIME, ENERGY_UNIT_FACTORS, MINUTE_FACTORS,
    CONF_RUN_MINUTES, CONF_RUN_POWER,
    CONF_CAPACITY, CONF_COUPLING, CONF_EFFICIENCY, CONF_MAX_CHARGE, CONF_MAX_DISCHARGE, CONF_MAX_SOC, CONF_MAX_SOC_ENTITY,
    CONF_MIN_SOC, CONF_MIN_SOC_ENTITY, CONF_RESERVE, CONF_SOC_SENSOR, COUPLING_OPTIONS, SUBENTRY_BATTERY,
)
from .sources import (
    NoIrradianceError, async_archive_irradiance, async_archive_temperature, async_weather_entity,
)


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
        vol.Optional(CONF_HOUSE_LOAD, description={"suggested_value": defaults.get(CONF_HOUSE_LOAD)}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Optional(CONF_GRID, description={"suggested_value": defaults.get(CONF_GRID)}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Optional(CONF_GRID_INVERT, default=defaults.get(CONF_GRID_INVERT, False)): selector.BooleanSelector(),
        vol.Optional(CONF_DAY_TYPE, description={"suggested_value": defaults.get(CONF_DAY_TYPE)}):
            selector.EntitySelector(selector.EntitySelectorConfig(
                domain=["binary_sensor", "input_boolean", "input_select", "select", "sensor"])),
    })


def _consumer_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): selector.TextSelector(),
        vol.Optional(CONF_POWER_SENSOR, description={"suggested_value": defaults.get(CONF_POWER_SENSOR)}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Optional(CONF_ENERGY_SENSOR, description={"suggested_value": defaults.get(CONF_ENERGY_SENSOR)}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Optional(CONF_REMAINING_TIME, description={"suggested_value": defaults.get(CONF_REMAINING_TIME)}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Optional(CONF_ELAPSED_TIME, description={"suggested_value": defaults.get(CONF_ELAPSED_TIME)}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Required(CONF_TIMING, default=defaults.get(CONF_TIMING, TIMING_OPTIONS[0])):
            selector.SelectSelector(selector.SelectSelectorConfig(options=list(TIMING_OPTIONS), translation_key=CONF_TIMING)),
        vol.Required(CONF_KIND, default=defaults.get(CONF_KIND, KIND_OPTIONS[0])):
            selector.SelectSelector(selector.SelectSelectorConfig(options=list(KIND_OPTIONS), translation_key=CONF_KIND)),
        vol.Optional(CONF_RUN_POWER, description={"suggested_value": defaults.get(CONF_RUN_POWER)}):
            selector.NumberSelector(selector.NumberSelectorConfig(min=10, max=50000, step=10, unit_of_measurement="W",
                                                                  mode=selector.NumberSelectorMode.BOX)),
        vol.Optional(CONF_RUN_MINUTES, description={"suggested_value": defaults.get(CONF_RUN_MINUTES)}):
            selector.NumberSelector(selector.NumberSelectorConfig(min=5, max=1440, step=5, unit_of_measurement="min",
                                                                  mode=selector.NumberSelectorMode.BOX)),
    })


def _power_sensor_error(hass: HomeAssistant, entity_id: str | None) -> str | None:
    """A power sensor must report W/kW as measurement, so it has long-term statistics."""
    state = hass.states.get(entity_id) if entity_id else None
    if state is None:
        return "sensor_missing"
    if state.attributes.get("unit_of_measurement") not in POWER_UNIT_FACTORS:
        return "sensor_unit"
    if state.attributes.get("state_class") != "measurement":
        return "sensor_state_class"
    return None


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
        vol.Optional(CONF_TEMP_COEFF, description={"suggested_value": defaults.get(CONF_TEMP_COEFF)}):
            selector.NumberSelector(selector.NumberSelectorConfig(min=-1, max=0, step=0.01, unit_of_measurement="%/K", mode=number)),
    })


async def _validate_main(flow, user_input: dict[str, Any]) -> dict[str, str]:
    for key in (CONF_HOUSE_LOAD, CONF_GRID):
        if user_input.get(key) and (err := _power_sensor_error(flow.hass, user_input[key])):
            return {key: err}
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NoxcastOptionsFlow:
        return NoxcastOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ARRAY: ArraySubentryFlow, SUBENTRY_CONSUMER: ConsumerSubentryFlow,
                SUBENTRY_BATTERY: BatterySubentryFlow}


class ConsumerSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a consumer with its own power sensor."""

    def _errors(self, user_input: dict[str, Any] | None) -> dict[str, str]:
        """A power or an energy sensor is needed; time sensors must report a duration."""
        if user_input is None:
            return {}
        power, energy = user_input.get(CONF_POWER_SENSOR), user_input.get(CONF_ENERGY_SENSOR)
        if not power and not energy:
            return {CONF_POWER_SENSOR: "power_or_energy"}
        if power and (err := _power_sensor_error(self.hass, power)):
            return {CONF_POWER_SENSOR: err}
        if energy:
            state = self.hass.states.get(energy)
            if state is None:
                return {CONF_ENERGY_SENSOR: "sensor_missing"}
            if state.attributes.get("unit_of_measurement") not in ENERGY_UNIT_FACTORS:
                return {CONF_ENERGY_SENSOR: "energy_unit"}
            if state.attributes.get("state_class") not in ("total", "total_increasing"):
                return {CONF_ENERGY_SENSOR: "energy_state_class"}
        for key in (CONF_REMAINING_TIME, CONF_ELAPSED_TIME):
            if user_input.get(key):
                state = self.hass.states.get(user_input[key])
                if state is None:
                    return {key: "sensor_missing"}
                if state.attributes.get("unit_of_measurement") not in MINUTE_FACTORS:
                    return {key: "time_unit"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors = self._errors(user_input)
        if user_input is not None and not errors:
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="user", data_schema=_consumer_schema(user_input or {}), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors = self._errors(user_input)
        if user_input is not None and not errors:
            return self.async_update_and_abort(self._get_entry(), subentry, title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="reconfigure", data_schema=_consumer_schema(user_input or dict(subentry.data)),
                                    errors=errors)


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
        if err := _power_sensor_error(hass, entity_id):
            return {CONF_POWER_SENSORS: err}
    valid = user_input.get(CONF_VALID_SINCE)
    if valid and (dt_util.parse_date(valid) is None or dt_util.parse_date(valid) > dt_util.now().date()):
        return {CONF_VALID_SINCE: "date_in_future"}
    return {}


def _options_schema(d: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_ARCHIVE_TEMPERATURE, default=d.get(CONF_ARCHIVE_TEMPERATURE, True)): selector.BooleanSelector(),
        vol.Required(CONF_ARCHIVE_URL, default=d.get(CONF_ARCHIVE_URL, DEFAULT_ARCHIVE_URL)):
            selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
        vol.Required(CONF_SATELLITE, default=d.get(CONF_SATELLITE, False)): selector.BooleanSelector(),
        vol.Required(CONF_SATELLITE_URL, default=d.get(CONF_SATELLITE_URL, DEFAULT_SATELLITE_URL)):
            selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
        vol.Required(CONF_SATELLITE_MODEL, default=d.get(CONF_SATELLITE_MODEL, DEFAULT_SATELLITE_MODEL)):
            selector.TextSelector(),
        vol.Required(CONF_COMPARE_OPEN_METEO, default=d.get(CONF_COMPARE_OPEN_METEO, True)): selector.BooleanSelector(),
        vol.Optional(CONF_API_KEY, description={"suggested_value": d.get(CONF_API_KEY)}):
            selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
    })


class NoxcastOptionsFlow(OptionsFlow):
    """Advanced options: history archives used for learning (temperature, optional satellite filter)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            errors = await self._async_test(user_input)
            if not errors:
                return self.async_create_entry(data=user_input)
        return self.async_show_form(step_id="init", data_schema=_options_schema(user_input or dict(self.config_entry.options)),
                                    errors=errors)

    async def _async_test(self, data: dict[str, Any]) -> dict[str, str]:
        """One test request per enabled archive (a few days, so it stays cheap)."""
        end = dt_util.utcnow() - timedelta(days=7)
        start = end - timedelta(days=1)
        lat, lon, key = self.hass.config.latitude, self.hass.config.longitude, data.get(CONF_API_KEY) or None
        if data[CONF_ARCHIVE_TEMPERATURE]:
            try:
                if not await async_archive_temperature(self.hass, data[CONF_ARCHIVE_URL], lat, lon, start, end, key):
                    return {CONF_ARCHIVE_URL: "archive_empty"}
            except Exception:  # noqa: BLE001
                return {CONF_ARCHIVE_URL: "archive_failed"}
        if data[CONF_SATELLITE]:
            try:
                if not await async_archive_irradiance(self.hass, data[CONF_SATELLITE_URL], data[CONF_SATELLITE_MODEL],
                                                      lat, lon, start, end, key):
                    return {CONF_SATELLITE_URL: "archive_empty"}
            except Exception:  # noqa: BLE001
                return {CONF_SATELLITE_URL: "archive_failed"}
        return {}


def _battery_schema(d: dict[str, Any]) -> vol.Schema:
    box = selector.NumberSelectorMode.BOX
    pct = selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%", mode=box)

    def opt(key, sel):
        return vol.Optional(key, description={"suggested_value": d.get(key)}), sel

    fields = [
        (vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")), selector.TextSelector()),
        (vol.Required(CONF_SOC_SENSOR, default=d.get(CONF_SOC_SENSOR)),
         selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))),
        (vol.Required(CONF_CAPACITY, default=d.get(CONF_CAPACITY, 5)),
         selector.NumberSelector(selector.NumberSelectorConfig(min=0.1, max=1000, step=0.1, unit_of_measurement="kWh", mode=box))),
        (vol.Required(CONF_MAX_SOC, default=d.get(CONF_MAX_SOC, 100)), selector.NumberSelector(pct)),
        opt(CONF_MAX_SOC_ENTITY, selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor", "number", "input_number"]))),
        (vol.Required(CONF_MIN_SOC, default=d.get(CONF_MIN_SOC, 0)), selector.NumberSelector(pct)),
        opt(CONF_MIN_SOC_ENTITY, selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor", "number", "input_number"]))),
        (vol.Required(CONF_RESERVE, default=d.get(CONF_RESERVE, 0)),
         selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=50, step=1, unit_of_measurement="%", mode=box))),
        (vol.Required(CONF_COUPLING, default=d.get(CONF_COUPLING, COUPLING_OPTIONS[0])),
         selector.SelectSelector(selector.SelectSelectorConfig(options=list(COUPLING_OPTIONS), translation_key=CONF_COUPLING))),
        opt(CONF_EFFICIENCY, selector.NumberSelector(selector.NumberSelectorConfig(min=50, max=100, step=1, unit_of_measurement="%", mode=box))),
        opt(CONF_MAX_CHARGE, selector.NumberSelector(selector.NumberSelectorConfig(min=10, max=100000, step=10, unit_of_measurement="W", mode=box))),
        opt(CONF_MAX_DISCHARGE, selector.NumberSelector(selector.NumberSelectorConfig(min=10, max=100000, step=10, unit_of_measurement="W", mode=box))),
    ]
    return vol.Schema(dict(fields))


class BatterySubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a battery for the surplus simulation."""

    def _errors(self, user_input: dict[str, Any] | None) -> dict[str, str]:
        if user_input is None:
            return {}
        state = self.hass.states.get(user_input.get(CONF_SOC_SENSOR) or "")
        if state is None:
            return {CONF_SOC_SENSOR: "sensor_missing"}
        if state.attributes.get("unit_of_measurement") != "%":
            return {CONF_SOC_SENSOR: "soc_unit"}
        if float(user_input.get(CONF_MIN_SOC, 0)) + float(user_input.get(CONF_RESERVE, 0)) >= float(user_input.get(CONF_MAX_SOC, 100)):
            return {CONF_MIN_SOC: "limits_order"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors = self._errors(user_input)
        if user_input is not None and not errors:
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="user", data_schema=_battery_schema(user_input or {}), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors = self._errors(user_input)
        if user_input is not None and not errors:
            return self.async_update_and_abort(self._get_entry(), subentry, title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="reconfigure", data_schema=_battery_schema(user_input or dict(subentry.data)),
                                    errors=errors)
