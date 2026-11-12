"""Config flow: main entry (batteries, house load, grid) and subentries per PV array, consumer and battery."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import probatio as vol

from homeassistant.config_entries import (
    ConfigEntry, ConfigFlow, ConfigFlowResult, ConfigSubentryFlow, OptionsFlow, SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from .const import (
    CONF_API_KEY, CONF_ARCHIVE_TEMPERATURE, CONF_ARCHIVE_URL, CONF_SATELLITE, CONF_SATELLITE_MODEL, CONF_SATELLITE_URL,
    CONF_WEATHER_MODELS, DEFAULT_ARCHIVE_URL, DEFAULT_SATELLITE_MODEL, DEFAULT_SATELLITE_URL, DEFAULT_WEATHER_MODELS,
    WEATHER_MODEL_OPTIONS,
    CONF_AZIMUTH, CONF_HEATING, CONF_ORIENTATIONS, CONF_TEMP_COEFF, CONF_NAME, CONF_PEAK_POWER, CONF_POWER_SENSORS, CONF_SENSOR_LIMITS, CONF_SOC_FULL, CONF_SOC_SENSORS,
    CONF_TILT, CONF_VALID_SINCE, DEFAULT_SOC_FULL, DOMAIN, POWER_UNIT_FACTORS, SUBENTRY_ARRAY,
    CONF_HOUSE_LOAD, CONF_GRID, CONF_GRID_INVERT, CONF_FEED_IN, CONF_FORECAST_DAYS, FORECAST_DAYS, MAX_FORECAST_DAYS, CONF_DAY_TYPE, CONF_POWER_SENSOR, CONF_TIMING, SUBENTRY_CONSUMER, TIMING_OPTIONS, CONF_KIND, KIND_OPTIONS,
    CONF_ELAPSED_TIME, CONF_ENERGY_SENSOR, CONF_REMAINING_TIME, ENERGY_UNIT_FACTORS, MINUTE_FACTORS,
    CONF_RUN_MINUTES, CONF_RUN_POWER, CONF_START_SHARE,
    CONF_CAPACITY, CONF_COUPLING, CONF_EFFICIENCY, CONF_MAX_CHARGE, CONF_MAX_DISCHARGE, CONF_MAX_SOC, CONF_MAX_SOC_ENTITY,
    CONF_MIN_SOC, CONF_MIN_SOC_ENTITY, CONF_RESERVE, CONF_SOC_SENSOR, COUPLING_OPTIONS, SUBENTRY_BATTERY,
    CONF_BATTERY_POWER, CONF_BATTERY_INVERT, CONF_BATTERY_ARRAY, CONF_OUTPUT_LIMIT,
)
from .sources import async_archive_irradiance, async_archive_temperature, async_open_meteo


def _main_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Optional(CONF_SOC_SENSORS, default=defaults.get(CONF_SOC_SENSORS, [])):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", multiple=True)),
        vol.Required(CONF_SOC_FULL, default=defaults.get(CONF_SOC_FULL, DEFAULT_SOC_FULL)):
            selector.NumberSelector(selector.NumberSelectorConfig(min=50, max=100, step=1, unit_of_measurement="%")),
        vol.Optional(CONF_HOUSE_LOAD, description={"suggested_value": defaults.get(CONF_HOUSE_LOAD)}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Optional(CONF_GRID, description={"suggested_value": defaults.get(CONF_GRID)}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Optional(CONF_GRID_INVERT, default=defaults.get(CONF_GRID_INVERT, False)): selector.BooleanSelector(),
        vol.Optional(CONF_FEED_IN, default=defaults.get(CONF_FEED_IN, False)): selector.BooleanSelector(),
        vol.Optional(CONF_FORECAST_DAYS, default=defaults.get(CONF_FORECAST_DAYS, FORECAST_DAYS)):
            selector.NumberSelector(selector.NumberSelectorConfig(min=2, max=MAX_FORECAST_DAYS, step=1,
                                                                  mode=selector.NumberSelectorMode.BOX)),
        vol.Optional(CONF_DAY_TYPE, description={"suggested_value": defaults.get(CONF_DAY_TYPE)}):
            selector.EntitySelector(selector.EntitySelectorConfig(
                domain=["binary_sensor", "input_boolean", "input_select", "select", "sensor"])),
    })


def _flatten(user_input: dict[str, Any], sections: tuple[str, ...]) -> dict[str, Any]:
    """Form sections are only for display: their fields are stored at the top level as before."""
    out = {k: v for k, v in user_input.items() if k not in sections}
    for key in sections:
        out.update(user_input.get(key) or {})
    return out


def _entity(defaults: dict[str, Any], key: str, domain: str | list[str] = "sensor") -> tuple:
    return (vol.Optional(key, description={"suggested_value": defaults.get(key)}),
            selector.EntitySelector(selector.EntitySelectorConfig(domain=domain)))


def _number(defaults: dict[str, Any], key: str, lo: float, hi: float, step: float, unit: str) -> tuple:
    return (vol.Optional(key, description={"suggested_value": defaults.get(key)}),
            selector.NumberSelector(selector.NumberSelectorConfig(min=lo, max=hi, step=step, unit_of_measurement=unit,
                                                                  mode=selector.NumberSelectorMode.BOX)))


def _consumer_schema(defaults: dict[str, Any]) -> vol.Schema:
    """First step: what the consumer is and how it is measured."""
    return vol.Schema(dict([
        (vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")), selector.TextSelector()),
        _entity(defaults, CONF_POWER_SENSOR),
        _entity(defaults, CONF_ENERGY_SENSOR),
        (vol.Required(CONF_TIMING, default=defaults.get(CONF_TIMING, TIMING_OPTIONS[0])),
         selector.SelectSelector(selector.SelectSelectorConfig(options=list(TIMING_OPTIONS), translation_key=CONF_TIMING))),
    ]))


def _consumer_fixed_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(dict([
        (vol.Required(CONF_KIND, default=defaults.get(CONF_KIND, KIND_OPTIONS[0])),
         selector.SelectSelector(selector.SelectSelectorConfig(options=list(KIND_OPTIONS), translation_key=CONF_KIND))),
        _entity(defaults, CONF_REMAINING_TIME),
        _entity(defaults, CONF_ELAPSED_TIME),
    ]))


def _consumer_flexible_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(dict([
        _number(defaults, CONF_RUN_POWER, 10, 50000, 10, "W"),
        _number(defaults, CONF_RUN_MINUTES, 5, 1440, 5, "min"),
        _number(defaults, CONF_START_SHARE, 0, 100, 5, "%"),
        _entity(defaults, CONF_REMAINING_TIME),
        _entity(defaults, CONF_ELAPSED_TIME),
    ]))


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


def _tilt_selector() -> selector.NumberSelector:
    return selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=90, step=1, unit_of_measurement="°",
                                                                 mode=selector.NumberSelectorMode.BOX))


def _azimuth_selector() -> selector.NumberSelector:
    return selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=359, step=1, unit_of_measurement="°",
                                                                 mode=selector.NumberSelectorMode.BOX))


def _peak_selector() -> selector.NumberSelector:
    return selector.NumberSelector(selector.NumberSelectorConfig(min=10, max=100000, step=10, unit_of_measurement="Wp",
                                                                 mode=selector.NumberSelectorMode.BOX))


def _orientation_schema(defaults: dict[str, Any]) -> vol.Schema:
    """A further orientation: all three fields or none (empty = not used)."""
    return vol.Schema({
        vol.Optional(key, description={"suggested_value": defaults.get(key)}): sel
        for key, sel in ((CONF_TILT, _tilt_selector()), (CONF_AZIMUTH, _azimuth_selector()),
                         (CONF_PEAK_POWER, _peak_selector()))
    })


# mounting -> cell heating in K at 1000 W/m²: free behind about 30, on the roof 45–55, in the roof about 60
MOUNTING = {"free": 30.0, "roof": 50.0, "integrated": 60.0}
ARRAY_ADVANCED = "advanced"
ARRAY_LIMITS = "limits"
SENSOR_LIMIT = "sensor_limit_w"       # form field: one limit for every sensor of the array


def _mounting(heating: float | None) -> tuple[str, float | None]:
    """Mounting shown for a stored heating, and the heating left as own value (None when a mounting fits)."""
    if heating is None:
        return "free", None
    for name, k in MOUNTING.items():
        if float(heating) == k:
            return name, None
    return "free", float(heating)


def _array_schema(defaults: dict[str, Any]) -> vol.Schema:
    """defaults: stored (flat) data, or the form's own input when it is shown again with an error."""
    number = selector.NumberSelectorMode.BOX
    if ARRAY_ADVANCED in defaults or "mounting" in defaults:          # the form's input
        mounting = defaults.get("mounting", "free")
        advanced = dict(defaults.get(ARRAY_ADVANCED) or {})
        limit = (defaults.get(ARRAY_LIMITS) or {}).get(SENSOR_LIMIT)
    else:
        stored = set((defaults.get(CONF_SENSOR_LIMITS) or {}).values())
        limit = stored.pop() if len(stored) == 1 else None
        mounting, own = _mounting(defaults.get(CONF_HEATING))
        advanced = {CONF_VALID_SINCE: defaults.get(CONF_VALID_SINCE), CONF_TEMP_COEFF: defaults.get(CONF_TEMP_COEFF),
                    CONF_HEATING: own}
    advanced_schema = vol.Schema({
        vol.Optional(CONF_VALID_SINCE, description={"suggested_value": advanced.get(CONF_VALID_SINCE)}):
            selector.DateSelector(),
        vol.Optional(CONF_TEMP_COEFF, description={"suggested_value": advanced.get(CONF_TEMP_COEFF)}):
            selector.NumberSelector(selector.NumberSelectorConfig(min=-1, max=0, step=0.01, unit_of_measurement="%/K", mode=number)),
        vol.Optional(CONF_HEATING, description={"suggested_value": advanced.get(CONF_HEATING)}):
            selector.NumberSelector(selector.NumberSelectorConfig(min=15, max=80, step=1, unit_of_measurement="K", mode=number)),
    })
    orientations = {
        vol.Optional(key): section(_orientation_schema(defaults.get(key) or {}),
                                   {"collapsed": not defaults.get(key)})
        for key in CONF_ORIENTATIONS
    }
    return vol.Schema({
        vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): selector.TextSelector(),
        vol.Required(CONF_POWER_SENSORS, default=defaults.get(CONF_POWER_SENSORS, [])):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", multiple=True)),
        vol.Required(CONF_TILT, default=defaults.get(CONF_TILT, 30)): _tilt_selector(),
        vol.Required(CONF_AZIMUTH, default=defaults.get(CONF_AZIMUTH, 180)): _azimuth_selector(),
        vol.Required(CONF_PEAK_POWER, default=defaults.get(CONF_PEAK_POWER, 800)): _peak_selector(),
        vol.Required("mounting", default=mounting):
            selector.SelectSelector(selector.SelectSelectorConfig(options=list(MOUNTING), translation_key="mounting")),
        **orientations,
        vol.Optional(ARRAY_LIMITS): section(vol.Schema({
            vol.Optional(SENSOR_LIMIT, description={"suggested_value": limit}):
                selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=100000, step=1, unit_of_measurement="W",
                                                                      mode=number)),
        }), {"collapsed": limit is None}),
        vol.Optional(ARRAY_ADVANCED): section(advanced_schema, {"collapsed": all(v is None for v in advanced.values())}),
    })


async def _validate_main(flow, user_input: dict[str, Any]) -> dict[str, str]:
    for key in (CONF_HOUSE_LOAD, CONF_GRID):
        if user_input.get(key) and (err := _power_sensor_error(flow.hass, user_input[key])):
            return {key: err}
    for entity_id in user_input.get(CONF_SOC_SENSORS) or []:
        state = flow.hass.states.get(entity_id)
        if state is None or state.attributes.get("unit_of_measurement") != "%":
            return {CONF_SOC_SENSORS: "soc_unit"}
    return {}


class NoxcastConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 4              # 1.2: Open-Meteo is the only weather source, 1.3: own weather entity,
                                   # 1.4: discontinued ICON models replaced (migration in __init__)

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
                # the update listener reloads; reloading here as well is an error from Home Assistant 2026.12
                return self.async_update_and_abort(entry, data=user_input)
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
    """Add or reconfigure a consumer with its own sensor: first what it is, then only the fields of its timing."""

    _data: dict[str, Any]
    _old: dict[str, Any]

    def _errors(self, user_input: dict[str, Any] | None) -> dict[str, str]:
        """A power or an energy sensor is needed."""
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
        return {}

    def _time_errors(self, user_input: dict[str, Any] | None) -> dict[str, str]:
        """Time sensors must report a duration."""
        for key in (CONF_REMAINING_TIME, CONF_ELAPSED_TIME):
            if user_input and user_input.get(key):
                state = self.hass.states.get(user_input[key])
                if state is None:
                    return {key: "sensor_missing"}
                if state.attributes.get("unit_of_measurement") not in MINUTE_FACTORS:
                    return {key: "time_unit"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._old = {}
        return await self._basics("user", user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._old = dict(self._get_reconfigure_subentry().data)
        return await self._basics("reconfigure", user_input)

    async def _basics(self, step: str, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        errors = self._errors(user_input)
        if user_input is not None and not errors:
            self._data = dict(user_input)
            return await (self.async_step_flexible() if user_input[CONF_TIMING] == "flexible" else self.async_step_fixed())
        return self.async_show_form(step_id=step, data_schema=_consumer_schema(user_input or self._old), errors=errors)

    async def async_step_fixed(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._details("fixed", _consumer_fixed_schema, user_input)

    async def async_step_flexible(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._details("flexible", _consumer_flexible_schema, user_input)

    async def _details(self, step: str, schema, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        """Only the fields of the chosen timing are stored; those of the other timing are dropped."""
        errors = self._time_errors(user_input)
        if user_input is not None and not errors:
            data = {**self._data, **user_input}
            if self.source == "reconfigure":
                return self.async_update_and_abort(self._get_entry(), self._get_reconfigure_subentry(),
                                                   title=data[CONF_NAME], data=data)
            return self.async_create_entry(title=data[CONF_NAME], data=data)
        return self.async_show_form(step_id=step, data_schema=schema(user_input or self._old), errors=errors,
                                    description_placeholders={"name": self._data[CONF_NAME]})


class ArraySubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one PV array."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors = _validate_array(self.hass, user_input)
        if user_input is not None and not errors:
            return self.async_create_entry(title=user_input[CONF_NAME], data=_array_data(user_input))
        return self.async_show_form(step_id="user", data_schema=_array_schema(user_input or {}), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors = _validate_array(self.hass, user_input)
        if user_input is not None and not errors:
            return self.async_update_and_abort(self._get_entry(), subentry, title=user_input[CONF_NAME],
                                               data=_array_data(user_input))
        return self.async_show_form(step_id="reconfigure", data_schema=_array_schema(user_input or dict(subentry.data)),
                                    errors=errors)


def _array_data(user_input: dict[str, Any]) -> dict[str, Any]:
    """Unused orientation sections (the form sends them empty) are not stored. The advanced section and the
    mounting become the stored fields as before: an own heating in K wins over the mounting's. The limit
    becomes CONF_SENSOR_LIMITS; an empty one removes it."""
    data = _flatten(user_input, (ARRAY_ADVANCED, ARRAY_LIMITS))
    mounting = data.pop("mounting", "free")
    # one limit for every sensor; stored per sensor, so that sensors with different limits fit later
    limit = data.pop(SENSOR_LIMIT, None)
    if limit:
        data[CONF_SENSOR_LIMITS] = {s: float(limit) for s in data.get(CONF_POWER_SENSORS) or []}
    if data.get(CONF_HEATING) is None:
        data[CONF_HEATING] = MOUNTING.get(mounting, MOUNTING["free"])
    return {k: v for k, v in data.items() if k not in CONF_ORIENTATIONS or any(x is not None for x in (v or {}).values())}


def _validate_array(hass: HomeAssistant, user_input: dict[str, Any] | None) -> dict[str, str]:
    """Power sensors must report W/kW as measurement (long-term statistics); date not in the future.

    A further orientation needs tilt, azimuth and peak power together, and must differ from the
    orientations before it (the same orientation twice is one plane with the summed peak power).
    """
    if user_input is None:
        return {}
    user_input = _flatten(user_input, (ARRAY_ADVANCED, ARRAY_LIMITS))
    sensors = user_input.get(CONF_POWER_SENSORS) or []
    if not sensors:
        return {CONF_POWER_SENSORS: "sensor_required"}
    for entity_id in sensors:
        if err := _power_sensor_error(hass, entity_id):
            return {CONF_POWER_SENSORS: err}
    valid = user_input.get(CONF_VALID_SINCE)
    if valid and (dt_util.parse_date(valid) is None or dt_util.parse_date(valid) > dt_util.now().date()):
        return {"base": "date_in_future"}
    seen = [(float(user_input[CONF_TILT]), float(user_input[CONF_AZIMUTH]) % 360)] \
        if user_input.get(CONF_TILT) is not None and user_input.get(CONF_AZIMUTH) is not None else []
    for key in CONF_ORIENTATIONS:
        values = [(user_input.get(key) or {}).get(k) for k in (CONF_TILT, CONF_AZIMUTH, CONF_PEAK_POWER)]
        if all(v is None for v in values):
            continue
        if any(v is None for v in values):
            return {"base": "orientation_incomplete"}
        plane = (float(values[0]), float(values[1]) % 360)
        if any(plane[0] == t and (plane[1] == a or plane[0] == 0) for t, a in seen):
            return {"base": "orientation_duplicate"}
        seen.append(plane)
    return {}


def _options_schema(d: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_WEATHER_MODELS, default=list(d.get(CONF_WEATHER_MODELS) or DEFAULT_WEATHER_MODELS)):
            selector.SelectSelector(selector.SelectSelectorConfig(options=list(WEATHER_MODEL_OPTIONS), multiple=True,
                                                                  custom_value=True, sort=False)),
        vol.Required(CONF_ARCHIVE_TEMPERATURE, default=d.get(CONF_ARCHIVE_TEMPERATURE, True)): selector.BooleanSelector(),
        vol.Required(CONF_ARCHIVE_URL, default=d.get(CONF_ARCHIVE_URL, DEFAULT_ARCHIVE_URL)):
            selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
        vol.Required(CONF_SATELLITE, default=d.get(CONF_SATELLITE, False)): selector.BooleanSelector(),
        vol.Required(CONF_SATELLITE_URL, default=d.get(CONF_SATELLITE_URL, DEFAULT_SATELLITE_URL)):
            selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
        vol.Required(CONF_SATELLITE_MODEL, default=d.get(CONF_SATELLITE_MODEL, DEFAULT_SATELLITE_MODEL)):
            selector.TextSelector(),
        vol.Optional(CONF_API_KEY, description={"suggested_value": d.get(CONF_API_KEY)}):
            selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
    })


class NoxcastOptionsFlow(OptionsFlow):
    """Advanced options: weather models, history archives used for learning (temperature, optional satellite filter)."""

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
        if not data.get(CONF_WEATHER_MODELS):
            return {CONF_WEATHER_MODELS: "models_required"}
        try:
            _, per_model = await async_open_meteo(self.hass, lat, lon, 2, data[CONF_WEATHER_MODELS])
        except Exception:  # noqa: BLE001
            return {CONF_WEATHER_MODELS: "weather_unavailable"}
        if missing := [m for m in data[CONF_WEATHER_MODELS] if m not in per_model]:
            return {CONF_WEATHER_MODELS: "model_unknown"} if len(missing) == len(data[CONF_WEATHER_MODELS])                 else {CONF_WEATHER_MODELS: "model_partial"}
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


# the battery form: what is needed on top, the rest in sections that open when they hold a value
BATTERY_SECTIONS = ("limits", "power", "inverter")
BATTERY_DEFAULTS = {CONF_MAX_SOC: 100, CONF_MIN_SOC: 0, CONF_RESERVE: 0}


def _battery_schema(d: dict[str, Any], arrays: dict[str, str]) -> vol.Schema:
    """d: stored (flat) data; arrays: subentry id -> name of the PV arrays, to pick the one behind the same inverter."""
    box = selector.NumberSelectorMode.BOX
    pct = selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%", mode=box)
    soc_entity = ["sensor", "number", "input_number"]
    limits = dict([
        (vol.Required(CONF_MAX_SOC, default=d.get(CONF_MAX_SOC, 100)), selector.NumberSelector(pct)),
        _entity(d, CONF_MAX_SOC_ENTITY, soc_entity),
        (vol.Required(CONF_MIN_SOC, default=d.get(CONF_MIN_SOC, 0)), selector.NumberSelector(pct)),
        _entity(d, CONF_MIN_SOC_ENTITY, soc_entity),
        (vol.Required(CONF_RESERVE, default=d.get(CONF_RESERVE, 0)),
         selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=50, step=1, unit_of_measurement="%", mode=box))),
    ])
    power = dict([
        _entity(d, CONF_BATTERY_POWER),
        (vol.Optional(CONF_BATTERY_INVERT, default=d.get(CONF_BATTERY_INVERT, False)), selector.BooleanSelector()),
        _number(d, CONF_EFFICIENCY, 50, 100, 1, "%"),
        _number(d, CONF_MAX_CHARGE, 10, 100000, 10, "W"),
        _number(d, CONF_MAX_DISCHARGE, 10, 100000, 10, "W"),
    ])
    inverter = dict([
        (vol.Optional(CONF_BATTERY_ARRAY, description={"suggested_value": d.get(CONF_BATTERY_ARRAY)}),
         selector.SelectSelector(selector.SelectSelectorConfig(
             options=[selector.SelectOptionDict(value=sid, label=name) for sid, name in arrays.items()],
             mode=selector.SelectSelectorMode.DROPDOWN))),
        _number(d, CONF_OUTPUT_LIMIT, 10, 100000, 10, "W"),
    ])
    used = {
        "limits": d.get(CONF_MAX_SOC_ENTITY) or d.get(CONF_MIN_SOC_ENTITY)
        or any(d.get(k, v) != v for k, v in BATTERY_DEFAULTS.items()),
        "power": d.get(CONF_BATTERY_POWER) or any(d.get(k) for k in (CONF_EFFICIENCY, CONF_MAX_CHARGE, CONF_MAX_DISCHARGE)),
        "inverter": d.get(CONF_BATTERY_ARRAY) or d.get(CONF_OUTPUT_LIMIT),
    }
    return vol.Schema(dict([
        (vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")), selector.TextSelector()),
        (vol.Required(CONF_SOC_SENSOR, default=d.get(CONF_SOC_SENSOR)),
         selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))),
        (vol.Required(CONF_CAPACITY, default=d.get(CONF_CAPACITY, 5)),
         selector.NumberSelector(selector.NumberSelectorConfig(min=0.1, max=1000, step=0.1, unit_of_measurement="kWh", mode=box))),
        (vol.Required(CONF_COUPLING, default=d.get(CONF_COUPLING, COUPLING_OPTIONS[0])),
         selector.SelectSelector(selector.SelectSelectorConfig(options=list(COUPLING_OPTIONS), translation_key=CONF_COUPLING))),
        *[(vol.Optional(name), section(vol.Schema(fields), {"collapsed": not used[name]}))
          for name, fields in (("limits", limits), ("power", power), ("inverter", inverter))],
    ]))


def _battery_data(user_input: dict[str, Any]) -> dict[str, Any]:
    """Stored flat as before; a section left out keeps the defaults of its required fields."""
    return {**BATTERY_DEFAULTS, **_flatten(user_input, BATTERY_SECTIONS)}


class BatterySubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a battery for the surplus simulation."""

    def _arrays(self) -> dict[str, str]:
        return {sid: sub.title for sid, sub in self._get_entry().subentries.items() if sub.subentry_type == SUBENTRY_ARRAY}

    def _errors(self, user_input: dict[str, Any] | None, own: str | None = None) -> dict[str, str]:
        """Problems of fields inside a section are shown on top of the form."""
        if user_input is None:
            return {}
        user_input = _battery_data(user_input)
        state = self.hass.states.get(user_input.get(CONF_SOC_SENSOR) or "")
        if state is None:
            return {CONF_SOC_SENSOR: "sensor_missing"}
        if state.attributes.get("unit_of_measurement") != "%":
            return {CONF_SOC_SENSOR: "soc_unit"}
        if float(user_input.get(CONF_MIN_SOC, 0)) + float(user_input.get(CONF_RESERVE, 0)) >= float(user_input.get(CONF_MAX_SOC, 100)):
            return {"base": "limits_order"}
        if (array := user_input.get(CONF_BATTERY_ARRAY)):
            if not user_input.get(CONF_OUTPUT_LIMIT):
                return {"base": "output_needed"}
            # one inverter per array: two batteries behind the same array would share its PV twice
            if any(sid != own and sub.subentry_type == SUBENTRY_BATTERY and sub.data.get(CONF_BATTERY_ARRAY) == array
                   for sid, sub in self._get_entry().subentries.items()):
                return {"base": "array_taken"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors = self._errors(user_input)
        if user_input is not None and not errors:
            return self.async_create_entry(title=user_input[CONF_NAME], data=_battery_data(user_input))
        return self.async_show_form(step_id="user", data_schema=_battery_schema(_battery_data(user_input or {}), self._arrays()),
                                    errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors = self._errors(user_input, subentry.subentry_id)
        if user_input is not None and not errors:
            return self.async_update_and_abort(self._get_entry(), subentry, title=user_input[CONF_NAME],
                                               data=_battery_data(user_input))
        return self.async_show_form(step_id="reconfigure",
                                    data_schema=_battery_schema(_battery_data(user_input) if user_input else dict(subentry.data),
                                                                self._arrays()),
                                    errors=errors)
