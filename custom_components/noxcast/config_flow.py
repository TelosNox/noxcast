"""Config flow: main entry (the household: house load, grid, feed-in) and subentries per system of the top level
(its subsystems, PV arrays and battery inside, edited through a menu) and per consumer."""
from __future__ import annotations

import logging
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
from homeassistant.util.ulid import ulid_now

from .const import (
    CONF_API_KEY, CONF_ARCHIVE_TEMPERATURE, CONF_ARCHIVE_URL, CONF_SATELLITE, CONF_SATELLITE_MODEL, CONF_SATELLITE_URL,
    CONF_WEATHER_MODELS, DEFAULT_ARCHIVE_URL, DEFAULT_SATELLITE_MODEL, DEFAULT_SATELLITE_URL, DEFAULT_WEATHER_MODELS,
    WEATHER_MODEL_OPTIONS,
    CONF_AZIMUTH, CONF_HEATING, CONF_ORIENTATIONS, CONF_TEMP_COEFF, CONF_NAME, CONF_PEAK_POWER, CONF_POWER_SENSORS, CONF_SENSOR_LIMITS, CONF_SOC_FULL, CONF_UNENTERED_SOC,
    CONF_TILT, CONF_VALID_SINCE, DEFAULT_SOC_FULL, DOMAIN, POWER_UNIT_FACTORS, SUBENTRY_ARRAY,
    CONF_HOUSE_LOAD, CONF_GRID, CONF_GRID_INVERT, CONF_FORECAST_DAYS, FORECAST_DAYS, MAX_FORECAST_DAYS, CONF_DAY_TYPE, CONF_POWER_SENSOR, CONF_TIMING, SUBENTRY_CONSUMER, TIMING_OPTIONS, CONF_KIND, KIND_OPTIONS,
    CONF_ELAPSED_TIME, CONF_ENERGY_SENSOR, CONF_REMAINING_TIME, ENERGY_UNIT_FACTORS, MINUTE_FACTORS,
    CONF_RUN_MINUTES, CONF_RUN_POWER, CONF_START_SHARE,
    CONF_CAPACITY, CONF_EFFICIENCY, CONF_MAX_CHARGE, CONF_MAX_DISCHARGE, CONF_MAX_SOC, CONF_MAX_SOC_ENTITY,
    CONF_MIN_SOC, CONF_MIN_SOC_ENTITY, CONF_RESERVE, CONF_SOC_SENSOR, SUBENTRY_BATTERY,
    CONF_BATTERY_POWER, CONF_BATTERY_INVERT, CONF_OUTPUT_LIMIT,
    CONF_FEED_IN_MODE, CONF_FEED_IN_LIMIT, CONF_FEED_IN_PCT, FEED_IN_OPTIONS, SUBENTRY_SYSTEM, CONF_SYSTEM, CONF_PARENT, CONF_TEMPLATE,
    TEMPLATE_OPTIONS, CONF_LIMIT_PER_PHASE, CONF_PHASE, PHASE_OPTIONS, CONF_REGULATION, REGULATION_OPTIONS,
    CONF_FIXED_POWER, CONF_OWN_FEED_IN, OWN_FEED_IN_OPTIONS, CONF_CHARGES_FROM, CHARGES_FROM_OPTIONS, CONF_BALANCE,
)
from . import store
from . import systems as sy
from .sources import async_archive_irradiance, async_archive_temperature, async_open_meteo

_LOGGER = logging.getLogger(__name__)


def _select(options, key: str) -> selector.SelectSelector:
    return selector.SelectSelector(selector.SelectSelectorConfig(options=list(options), translation_key=key))


MAIN_SECTIONS = ("feed_in_limit",)


def _main_schema(defaults: dict[str, Any]) -> vol.Schema:
    """The household. The limit of the feed-in rule is rare: its section opens where one is set."""
    box = selector.NumberSelectorMode.BOX
    limited = defaults.get(CONF_FEED_IN_MODE) in ("limited", "percent") or any(
        defaults.get(k) for k in (CONF_FEED_IN_LIMIT, CONF_FEED_IN_PCT))
    return vol.Schema({
        vol.Required(CONF_SOC_FULL, default=defaults.get(CONF_SOC_FULL, DEFAULT_SOC_FULL)):
            selector.NumberSelector(selector.NumberSelectorConfig(min=50, max=100, step=1, unit_of_measurement="%")),
        vol.Optional(CONF_HOUSE_LOAD, description={"suggested_value": defaults.get(CONF_HOUSE_LOAD)}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Optional(CONF_GRID, description={"suggested_value": defaults.get(CONF_GRID)}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Optional(CONF_GRID_INVERT, default=defaults.get(CONF_GRID_INVERT, False)): selector.BooleanSelector(),
        vol.Required(CONF_FEED_IN_MODE, default=defaults.get(CONF_FEED_IN_MODE, "none")):
            _select(FEED_IN_OPTIONS, CONF_FEED_IN_MODE),
        vol.Optional("feed_in_limit"): section(vol.Schema({
            vol.Optional(CONF_FEED_IN_LIMIT, description={"suggested_value": defaults.get(CONF_FEED_IN_LIMIT)}):
                selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100000, step=10,
                                                                      unit_of_measurement="W", mode=box)),
            vol.Optional(CONF_FEED_IN_PCT, description={"suggested_value": defaults.get(CONF_FEED_IN_PCT)}):
                selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100, step=1,
                                                                      unit_of_measurement="%", mode=box)),
        }), {"collapsed": not limited}),
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


def _system_choice(defaults: dict[str, Any], choices: dict[str, str]) -> dict:
    """The system a part belongs to; left empty, the part gets a system of its own (see systems.normalize)."""
    return {vol.Optional(CONF_SYSTEM, description={"suggested_value": defaults.get(CONF_SYSTEM)}):
            selector.SelectSelector(selector.SelectSelectorConfig(
                options=[selector.SelectOptionDict(value=sid, label=name) for sid, name in choices.items()],
                mode=selector.SelectSelectorMode.DROPDOWN))}


def _array_schema(defaults: dict[str, Any], choices: dict[str, str] | None = None) -> vol.Schema:
    """defaults: stored (flat) data, or the form's own input when it is shown again with an error;
    choices: the systems that can take PV arrays (id -> name)."""
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
        **_system_choice(defaults, choices or {}),
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
    # the limit sits in a section: its problem is shown on top of the form
    if user_input.get(CONF_FEED_IN_MODE) == "limited" and not user_input.get(CONF_FEED_IN_LIMIT):
        return {"base": "feed_in_limit_needed"}
    if user_input.get(CONF_FEED_IN_MODE) == "percent" and not user_input.get(CONF_FEED_IN_PCT):
        return {"base": "feed_in_pct_needed"}
    return {}


class NoxcastConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 8              # 1.2: Open-Meteo is the only weather source, 1.3: own weather entity,
                                   # 1.4: discontinued ICON models replaced, 1.5: systems, 1.6: one entry per
                                   # system with its parts inside, 1.7: feed-in deviation per system,
                                   # 1.8: the household's old state-of-charge sensors gone (migrations in __init__)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors = {}
        if user_input is not None:
            user_input = _flatten(user_input, MAIN_SECTIONS)
            errors = await _validate_main(self, user_input)
            if not errors:
                return self.async_create_entry(title="Noxcast", data=user_input)
        return self.async_show_form(step_id="user", data_schema=_main_schema(user_input or {}), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            user_input = _flatten(user_input, MAIN_SECTIONS)
            errors = await _validate_main(self, user_input)
            if not errors:
                # the update listener reloads; reloading here as well is an error from Home Assistant 2026.12
                # the sensors the migration could not give a battery stay while the repair about them holds
                kept = {k: entry.data[k] for k in (CONF_UNENTERED_SOC,) if k in entry.data}
                return self.async_update_and_abort(entry, data={**user_input, **kept})
        return self.async_show_form(step_id="reconfigure", data_schema=_main_schema(user_input or dict(entry.data)),
                                    errors=errors, description_placeholders={"concept": _concepts(self.hass, "blocks")})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NoxcastOptionsFlow:
        return NoxcastOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_SYSTEM: SystemSubentryFlow, SUBENTRY_CONSUMER: ConsumerSubentryFlow}


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


class _DiagramMixin:
    """The installation's flow diagram on top of a subentry form, so the form shows where the part sits."""

    def _diagram(self, *concepts: str) -> dict[str, str]:
        """{diagram}: the installation; {concept}: the explanatory drawings named (concepts.py)."""
        return {"diagram": _image(lambda: _panel().diagram_markdown(self.hass, self._get_entry())),  # type: ignore[attr-defined]
                "concept": _concepts(self.hass, *concepts)}  # type: ignore[attr-defined]


def _panel():
    from . import panel
    return panel


def _image(make) -> str:
    try:
        return make()
    except Exception:  # noqa: BLE001 – the form works without the picture
        _LOGGER.debug("Picture for the form unavailable", exc_info=True)
        return ""


def _concepts(hass: HomeAssistant, *names: str) -> str:
    return _image(lambda: _panel().concept_markdown(hass, *names)) if names else ""


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
BATTERY_SECTIONS = ("limits", "power")
BATTERY_DEFAULTS = {CONF_MAX_SOC: 100, CONF_MIN_SOC: 0, CONF_RESERVE: 0}


def _battery_schema(d: dict[str, Any], choices: dict[str, str]) -> vol.Schema:
    """d: stored (flat) data; choices: the systems that can take this battery (id -> name)."""
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
    used = {
        "limits": d.get(CONF_MAX_SOC_ENTITY) or d.get(CONF_MIN_SOC_ENTITY)
        or any(d.get(k, v) != v for k, v in BATTERY_DEFAULTS.items()),
        "power": d.get(CONF_BATTERY_POWER) or any(d.get(k) for k in (CONF_EFFICIENCY, CONF_MAX_CHARGE, CONF_MAX_DISCHARGE)),
    }
    return vol.Schema(dict([
        (vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")), selector.TextSelector()),
        (vol.Required(CONF_SOC_SENSOR, default=d.get(CONF_SOC_SENSOR)),
         selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))),
        (vol.Required(CONF_CAPACITY, default=d.get(CONF_CAPACITY, 5)),
         selector.NumberSelector(selector.NumberSelectorConfig(min=0.1, max=1000, step=0.1, unit_of_measurement="kWh", mode=box))),
        *_system_choice(d, choices).items(),
        *[(vol.Optional(name), section(vol.Schema(fields), {"collapsed": not used[name]}))
          for name, fields in (("limits", limits), ("power", power))],
    ]))


def _battery_data(user_input: dict[str, Any]) -> dict[str, Any]:
    """Stored flat as before; a section left out keeps the defaults of its required fields."""
    return {**BATTERY_DEFAULTS, **_flatten(user_input, BATTERY_SECTIONS)}


def _system_basics(d: dict[str, Any], parents: dict[str, str]) -> vol.Schema:
    fields = {
        vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")): selector.TextSelector(),
        vol.Required(CONF_TEMPLATE, default=d.get(CONF_TEMPLATE, TEMPLATE_OPTIONS[0])): _select(TEMPLATE_OPTIONS, CONF_TEMPLATE),
    }
    if parents:
        fields.update({vol.Optional(CONF_PARENT, description={"suggested_value": d.get(CONF_PARENT)}):
                       selector.SelectSelector(selector.SelectSelectorConfig(
                           options=[selector.SelectOptionDict(value=sid, label=name) for sid, name in parents.items()],
                           mode=selector.SelectSelectorMode.DROPDOWN))})
    return vol.Schema(fields)


SYSTEM_SECTIONS = ("fixed", "deviation", "phases")


def _system_settings(d: dict[str, Any], *, battery: bool, group: bool, sub: bool) -> vol.Schema:
    """Only the settings that can apply: battery settings for templates with a battery, the shared settings for
    systems with subsystems, the phase for subsystems. Regulation, fixed power and the feed-in deviation only on a
    system of the top level: a subsystem follows its system (systems.behaviour). What most systems need is on top;
    what few need sits in sections that open where they hold a value."""
    def choice(key: str, options) -> tuple:
        return vol.Optional(key, description={"suggested_value": d.get(key)}), _select(options, key)

    fields = dict([_number(d, CONF_OUTPUT_LIMIT, 10, 100000, 10, "W")])
    if not sub:
        fields.update(dict([choice(CONF_REGULATION, REGULATION_OPTIONS)]))
    if battery:
        fields.update(dict([choice(CONF_CHARGES_FROM, CHARGES_FROM_OPTIONS)]))
    if group:
        fields[vol.Optional(CONF_BALANCE, default=bool(d.get(CONF_BALANCE)))] = selector.BooleanSelector()
    if not sub:
        fields[vol.Optional("fixed")] = section(vol.Schema(dict([_number(d, CONF_FIXED_POWER, 0, 100000, 10, "W")])),
                                                {"collapsed": not (d.get(CONF_FIXED_POWER) or d.get(CONF_REGULATION) == "fixed")})
        fields[vol.Optional("deviation")] = section(vol.Schema(dict([choice(CONF_OWN_FEED_IN, OWN_FEED_IN_OPTIONS)])),
                                                    {"collapsed": not d.get(CONF_OWN_FEED_IN)})
    phases = {}
    if group:
        phases[vol.Optional(CONF_LIMIT_PER_PHASE, default=bool(d.get(CONF_LIMIT_PER_PHASE)))] = selector.BooleanSelector()
    if sub:
        phases.update(dict([choice(CONF_PHASE, PHASE_OPTIONS)]))
    if phases:
        fields[vol.Optional("phases")] = section(vol.Schema(phases),
                                                 {"collapsed": not (d.get(CONF_LIMIT_PER_PHASE) or d.get(CONF_PHASE))})
    return vol.Schema(fields)




def _battery_errors(hass: HomeAssistant, user_input: dict[str, Any] | None, choices: dict[str, str]) -> dict[str, str]:
    """Problems of fields inside a section are shown on top of the form."""
    if user_input is None:
        return {}
    user_input = _battery_data(user_input)
    state = hass.states.get(user_input.get(CONF_SOC_SENSOR) or "")
    if state is None:
        return {CONF_SOC_SENSOR: "sensor_missing"}
    if state.attributes.get("unit_of_measurement") != "%":
        return {CONF_SOC_SENSOR: "soc_unit"}
    if float(user_input.get(CONF_MIN_SOC, 0)) + float(user_input.get(CONF_RESERVE, 0)) >= float(user_input.get(CONF_MAX_SOC, 100)):
        return {"base": "limits_order"}
    # one battery per system: a second one is a subsystem of its own
    if user_input.get(CONF_SYSTEM) and user_input[CONF_SYSTEM] not in choices:
        return {CONF_SYSTEM: "system_taken"}
    return {}


def _system_defaults(systems: dict[str, sy.System], flat: dict[str, tuple[str, str, Any]], own: str | None,
                     template: str, sub: bool) -> dict[str, Any]:
    """What the settings of a system show: a changed template fills in its settings anew, the same template keeps
    what was set."""
    old = dict(flat[own][2]) if own else {}
    defaults = old if old.get(CONF_TEMPLATE) == template else {**old, **sy.template_data(template)}
    before = old.get(CONF_PARENT)
    if not sub and before in systems:
        # leaving its system, it keeps behaving as that made it until changed here
        defaults = {**{k: systems[before].get(k) for k in sy.BEHAVIOUR if systems[before].get(k) is not None},
                    **{k: v for k, v in defaults.items() if k not in sy.BEHAVIOUR or v is not None}}
    return defaults


def _system_kinds(systems: dict[str, sy.System], own: str | None, template: str) -> tuple[bool, bool]:
    """(holds subsystems, holds a battery): which settings a system's form offers."""
    group = template == "coupled" or bool(own in systems and systems[own].children)
    battery = template in ("pv_battery", "ac_battery") or bool(own in systems and systems[own].battery)
    return group, battery


def _system_data(basics: dict[str, Any], user_input: dict[str, Any],
                 sub: bool) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """The stored data of a system from what it is (basics) and its settings form; a subsystem keeps no behaviour
    of its own (it follows its system)."""
    user_input = _flatten(user_input, SYSTEM_SECTIONS)
    if user_input.get(CONF_REGULATION) == "fixed" and not user_input.get(CONF_FIXED_POWER):
        return None, {"base": "fixed_power_needed"}
    data = {k: v for k, v in {**basics, **user_input}.items() if v not in (None, "", False)}
    if sub:
        data = {k: v for k, v in data.items() if k not in sy.BEHAVIOUR}
    return data, {}


# the menu of a system; its items carry the names of the parts, so they are built here, not in the translations.
# Each starts with what it does (a menu has no icons): ✎ changes something that exists, + adds, − removes
MENU_TEXT = {
    "de": {"edit": "✎ {name} ändern", "system": "✎ Teilsystem {name} ändern", "array": "✎ PV-Gruppe {name} ändern",
           "battery": "✎ Akku {name} ändern", "add_sub": "+ Teilsystem hinzufügen",
           "add_array": "+ PV-Gruppe hinzufügen", "add_battery": "+ Akku hinzufügen", "remove": "− Teil entfernen …",
           "back": "← Zurück zu {name}", "finish": "✓ Fertig", "remove_yes": "− {name} entfernen",
           "cancel": "← Abbrechen, nichts entfernen"},
    "en": {"edit": "✎ Change {name}", "system": "✎ Change subsystem {name}", "array": "✎ Change PV array {name}",
           "battery": "✎ Change battery {name}", "add_sub": "+ Add subsystem", "add_array": "+ Add PV array",
           "add_battery": "+ Add battery", "remove": "− Remove a part …", "back": "← Back to {name}",
           "finish": "✓ Done", "remove_yes": "− Remove {name}", "cancel": "← Cancel, remove nothing"},
}
OPEN = "open_"               # menu item of a part: OPEN + its id (SystemSubentryFlow.__getattr__)


class SystemSubentryFlow(_DiagramMixin, ConfigSubentryFlow):
    """A system of the top level with everything inside it. A menu per system lists its settings, subsystems,
    PV arrays and battery; behind each item is the form of that part. Each form saves when it is sent
    (store.async_store), so closing the dialog loses nothing; "Done" only closes it."""

    _flat: dict[str, tuple[str, str, Any]]
    _node: str | None = None         # the system whose menu is shown
    _edit: str | None = None         # the part a form edits; None for a new one
    _parent: str | None = None       # where a new subsystem goes
    _basics: dict[str, Any]
    _remove: str | None = None       # the part chosen for removal, until confirmed

    def __getattr__(self, name: str) -> Any:
        # the menu item of a part is a step of its own; its id is part of the step's name
        if name.startswith("async_step_" + OPEN):
            part = name.removeprefix("async_step_" + OPEN)

            async def step(user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
                return await self._open(part)
            return step
        raise AttributeError(name)

    @property
    def _text(self) -> dict[str, str]:
        return MENU_TEXT.get((self.hass.config.language or "en")[:2], MENU_TEXT["en"])

    def _systems(self) -> dict[str, sy.System]:
        return sy.systems_of(self._flat)

    def _save(self, changed: dict[str, tuple[str, str, Any] | None]) -> None:
        """Store the parts this form changed (None: removed) on top of what is stored now, so that a change made
        elsewhere meanwhile (the Installation tab, a repair, another dialog) is kept."""
        entry = self._get_entry()
        flat = dict(store.flat(entry))
        for pid, part in changed.items():
            if part is None:
                flat.pop(pid, None)
            else:
                flat[pid] = part
        store.async_store(self.hass, entry, flat)
        self._flat = dict(store.flat(entry))

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is None:
            self._flat = dict(store.flat(self._get_entry()))
        return await self._basics_form("user", user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._flat = dict(store.flat(self._get_entry()))
        self._node = self._get_reconfigure_subentry().subentry_id
        return await self.async_step_menu()

    async def async_step_menu(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        systems = self._systems()
        if self._node not in systems:
            return self.async_abort(reason="saved")
        s, t = systems[self._node], self._text
        options = {"edit": t["edit"].format(name=s.name)}
        options.update({OPEN + c: t["system"].format(name=systems[c].name) for c in s.children})
        options.update({OPEN + a: t["array"].format(name=self._flat[a][1]) for a in s.arrays})
        if s.battery:
            options[OPEN + s.battery] = t["battery"].format(name=self._flat[s.battery][1])
        # a system holds either subsystems, or PV arrays and a battery (two levels at most)
        if s.parent is None and not s.arrays and not s.battery:
            options["add_sub"] = t["add_sub"]
        if not s.children:
            options["add_array"] = t["add_array"]
            if not s.battery:
                options["add_battery"] = t["add_battery"]
        if s.children or s.arrays or s.battery:
            options["remove"] = t["remove"]
        if s.parent is not None:
            options["back"] = t["back"].format(name=systems[s.parent].name)
        else:
            options["finish"] = t["finish"]
        return self.async_show_menu(step_id="menu", menu_options=options,
                                    description_placeholders={"name": s.name, **self._diagram()})

    async def _open(self, part: str) -> SubentryFlowResult:
        kind = self._flat[part][0] if part in self._flat else None
        if kind == SUBENTRY_SYSTEM:
            self._node = part
            return await self.async_step_menu()
        self._edit = part
        if kind == SUBENTRY_ARRAY:
            return await self.async_step_array()
        if kind == SUBENTRY_BATTERY:
            return await self.async_step_battery()
        return await self.async_step_menu()

    async def async_step_edit(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._edit, self._parent = self._node, None
        return await self._basics_form("basics", None)

    async def async_step_add_sub(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._edit, self._parent = None, self._node
        return await self._basics_form("basics", None)

    async def async_step_add_array(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._edit = None
        return await self.async_step_array()

    async def async_step_add_battery(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._edit = None
        return await self.async_step_battery()

    async def async_step_back(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        systems = self._systems()
        if self._node in systems:
            self._node = systems[self._node].parent
        return await self.async_step_menu()

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_abort(reason="saved")

    async def async_step_basics(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._basics_form("basics", user_input)

    async def _basics_form(self, step: str, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        """What a system is: name, template, and for an existing one where it sits (its parent). A new subsystem
        goes under self._parent; a new system of the top level has none."""
        own = self._edit
        old = dict(self._flat[own][2]) if own else {}
        parents = sy.parent_owners(self._systems(), own) if own else {}
        errors = {}
        if user_input is not None:
            if user_input.get(CONF_PARENT) and user_input[CONF_PARENT] not in parents:
                errors = {CONF_PARENT: "parent_not_possible"}
            else:
                self._basics = {k: v for k, v in user_input.items() if k != CONF_PARENT}
                parent = self._parent if not own else user_input.get(CONF_PARENT)
                if parent:
                    self._basics[CONF_PARENT] = parent
                return await self.async_step_settings()
        # a new system of the top level: how the parts nest, then the templates; otherwise the templates
        drawings = ("blocks", "templates") if step == "user" else ("templates",)
        return self.async_show_form(step_id=step, data_schema=_system_basics(user_input or old, parents),
                                    errors=errors, description_placeholders=self._diagram(*drawings))

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        template = self._basics[CONF_TEMPLATE]
        own = self._edit
        systems = self._systems()
        sub = bool(self._basics.get(CONF_PARENT))
        defaults = _system_defaults(systems, self._flat, own, template, sub)
        group, battery = _system_kinds(systems, own, template)
        errors = {}
        if user_input is not None:
            data, errors = _system_data(self._basics, user_input, sub)
            if data is not None:
                sid = own or ulid_now()
                self._save({sid: (SUBENTRY_SYSTEM, data[CONF_NAME], data)})
                self._node = sid
                return await self.async_step_menu()
        # two drawings at most: what the form's own settings are about
        drawings = ("subsystems", "limits") if sub else ("subsystems", "regulation") if group else ("regulation", "limits")
        return self.async_show_form(
            step_id="settings", errors=errors,
            description_placeholders={"name": self._basics[CONF_NAME], "concept": _concepts(self.hass, *drawings)},
            data_schema=_system_settings(_flatten(user_input, SYSTEM_SECTIONS) if user_input else defaults,
                                        battery=battery, group=group, sub=sub))

    async def async_step_array(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        own = self._edit
        old = dict(self._flat[own][2]) if own else {CONF_SYSTEM: self._node}
        choices = sy.part_owners(self._systems())
        errors = _validate_array(self.hass, user_input)
        if not errors and user_input and user_input.get(CONF_SYSTEM) and user_input[CONF_SYSTEM] not in choices:
            errors = {CONF_SYSTEM: "system_has_subsystems"}
        if user_input is not None and not errors:
            data = _array_data(user_input)
            data.setdefault(CONF_SYSTEM, self._node)
            self._save({own or ulid_now(): (SUBENTRY_ARRAY, data[CONF_NAME], data)})
            return await self.async_step_menu()
        return self.async_show_form(step_id="array", errors=errors, description_placeholders=self._diagram("array"),
                                    data_schema=_array_schema(user_input or old, choices))

    async def async_step_battery(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        own = self._edit
        old = dict(self._flat[own][2]) if own else {CONF_SYSTEM: self._node}
        choices = sy.part_owners(self._systems(), own or "")
        errors = _battery_errors(self.hass, user_input, choices)
        if user_input is not None and not errors:
            data = _battery_data(user_input)
            data.setdefault(CONF_SYSTEM, self._node)
            self._save({own or ulid_now(): (SUBENTRY_BATTERY, data[CONF_NAME], data)})
            return await self.async_step_menu()
        return self.async_show_form(step_id="battery", errors=errors, description_placeholders=self._diagram(),
                                    data_schema=_battery_schema(_battery_data(user_input) if user_input else old,
                                                                choices))

    def _inside(self, part: str) -> set[str]:
        return {p for p, (kind, _, d) in self._flat.items()
                if kind in (SUBENTRY_ARRAY, SUBENTRY_BATTERY) and d.get(CONF_SYSTEM) == part}

    async def async_step_remove_confirm(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """What goes with the chosen part, before anything is removed: it cannot be undone."""
        # what is stored now: another dialog or the Installation tab may have changed the system meanwhile
        self._flat = dict(store.flat(self._get_entry()))
        part = self._remove
        if part not in self._flat:
            return await self.async_step_menu()
        systems = self._systems()
        name = systems[part].name if part in systems else self._flat[part][1]
        inside = sorted(self._flat[p][1] for p in self._inside(part))
        return self.async_show_menu(
            step_id="remove_confirm",
            menu_options={"remove_yes": self._text["remove_yes"].format(name=name), "menu": self._text["cancel"]},
            description_placeholders={"name": name, "inside": ", ".join(inside) if inside else "–"})

    async def async_step_remove_yes(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        part, self._remove = self._remove, None
        self._flat = dict(store.flat(self._get_entry()))
        if part in self._flat:
            self._save(dict.fromkeys({part} | self._inside(part)))
        return await self.async_step_menu()

    async def async_step_remove(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """A subsystem, PV array or battery of this system, with everything inside it."""
        systems = self._systems()
        if self._node not in systems:
            return await self.async_step_menu()
        s = systems[self._node]
        options = {**{c: systems[c].name for c in s.children}, **{a: self._flat[a][1] for a in s.arrays},
                   **({s.battery: self._flat[s.battery][1]} if s.battery else {})}
        if user_input is not None:
            part = user_input.get("part")
            if part in options:
                self._remove = part
                return await self.async_step_remove_confirm()
            return await self.async_step_menu()
        return self.async_show_form(
            step_id="remove", description_placeholders={"name": s.name},
            data_schema=vol.Schema({vol.Required("part"): selector.SelectSelector(selector.SelectSelectorConfig(
                options=[selector.SelectOptionDict(value=p, label=n) for p, n in options.items()],
                mode=selector.SelectSelectorMode.LIST))}))
