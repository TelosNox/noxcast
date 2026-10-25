"""Weather entity from the Open-Meteo data the PV forecast uses, so dashboard weather and yield agree."""
from __future__ import annotations

from homeassistant.components.weather import Forecast, WeatherEntity, WeatherEntityFeature
from homeassistant.const import UnitOfPrecipitationDepth, UnitOfSpeed, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .conditions import day_summary, hour_condition
from .const import DOMAIN
from .coordinator import NoxcastConfigEntry, NoxcastCoordinator
from .model import WeatherHour
from .solar import sun_position


async def async_setup_entry(hass: HomeAssistant, entry: NoxcastConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    device = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Noxcast", manufacturer="Noxcast",
                        entry_type=DeviceEntryType.SERVICE)
    async_add_entities([NoxcastWeather(entry.runtime_data, device)])


class NoxcastWeather(CoordinatorEntity[NoxcastCoordinator], WeatherEntity):
    """Current hour, hourly forecast and daily forecast for a week; the models are averaged as for the PV forecast."""

    _attr_has_entity_name = True
    _attr_translation_key = "weather"
    _attr_attribution = "Weather data by Open-Meteo.com"
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_supported_features = WeatherEntityFeature.FORECAST_HOURLY | WeatherEntityFeature.FORECAST_DAILY

    def __init__(self, coordinator: NoxcastCoordinator, device: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_weather"
        self._attr_device_info = device

    @property
    def available(self) -> bool:
        return self.coordinator.forecast_available and self._now() is not None

    def _hours(self) -> list[WeatherHour]:
        return self.coordinator.data.outlook if self.coordinator.data else []

    def _night(self, start: float) -> bool:
        return sun_position(self.coordinator.site, start + 1800).elevation <= 0

    def _now(self) -> WeatherHour | None:
        now = dt_util.utcnow().timestamp()
        return next((w for w in self._hours() if w.start <= now < w.start + 3600), None)

    @property
    def condition(self) -> str | None:
        w = self._now()
        return hour_condition(w, self._night(w.start)) if w else None

    @property
    def native_temperature(self) -> float | None:
        w = self._now()
        return None if w is None or w.temp is None else round(w.temp, 1)

    @property
    def humidity(self) -> float | None:
        w = self._now()
        return None if w is None or w.humidity is None else round(w.humidity)

    @property
    def cloud_coverage(self) -> float | None:
        w = self._now()
        return None if w is None or w.cloud is None else round(w.cloud)

    @property
    def native_wind_speed(self) -> float | None:
        w = self._now()
        return None if w is None or w.wind is None else round(w.wind, 1)

    @property
    def wind_bearing(self) -> float | None:
        w = self._now()
        return None if w is None or w.wind_dir is None else round(w.wind_dir)

    async def async_forecast_hourly(self) -> list[Forecast] | None:
        now = dt_util.utcnow().timestamp()
        return [Forecast(datetime=dt_util.utc_from_timestamp(w.start).isoformat(),
                         condition=hour_condition(w, self._night(w.start)),
                         native_temperature=None if w.temp is None else round(w.temp, 1),
                         native_precipitation=None if w.precip is None else round(w.precip, 1),
                         cloud_coverage=None if w.cloud is None else round(w.cloud),
                         humidity=None if w.humidity is None else round(w.humidity),
                         native_wind_speed=None if w.wind is None else round(w.wind, 1),
                         wind_bearing=None if w.wind_dir is None else round(w.wind_dir))
                for w in self._hours() if w.start + 3600 > now]

    async def async_forecast_daily(self) -> list[Forecast] | None:
        days: dict = {}
        for w in self._hours():
            days.setdefault(dt_util.as_local(dt_util.utc_from_timestamp(w.start)).date(), []).append(w)
        out = []
        for day, hours in sorted(days.items()):
            if len(hours) < 20:                              # the last day may be cut off by the horizon
                continue
            s = day_summary(hours, lambda t: not self._night(t))
            out.append(Forecast(datetime=dt_util.start_of_local_day(day).isoformat(), condition=s["condition"],
                                native_temperature=None if s["temp_max"] is None else round(s["temp_max"], 1),
                                native_templow=None if s["temp_min"] is None else round(s["temp_min"], 1),
                                native_precipitation=s["precip"], cloud_coverage=s["cloud"],
                                native_wind_speed=None if s["wind"] is None else round(s["wind"], 1)))
        return out

    @callback
    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        self.hass.async_create_task(self.async_update_listeners(None))
