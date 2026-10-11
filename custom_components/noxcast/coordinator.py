"""Coordinator: learns from history, fetches weather and computes the forecast."""
from __future__ import annotations

import asyncio
import functools
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_API_KEY, CONF_ARCHIVE_TEMPERATURE, CONF_ARCHIVE_URL, CONF_SATELLITE, CONF_SATELLITE_MODEL, CONF_SATELLITE_URL,
    DEFAULT_ARCHIVE_URL, DEFAULT_SATELLITE_MODEL, DEFAULT_SATELLITE_URL,
    ARCHIVE_DAYS, CONF_AZIMUTH, CONF_TEMP_COEFF, CONF_NAME, CONF_PEAK_POWER, CONF_POWER_SENSORS, CONF_SOC_FULL, CONF_SOC_SENSORS,
    CONF_TILT, CONF_VALID_SINCE, CONF_WEATHER_ENTITY, CONF_WEATHER_SOURCE, DEFAULT_SOC_FULL, DOMAIN,
    FORECAST_DAYS, HISTORY_YEARS, LEARN_TIME, STALE_AFTER, STORAGE_VERSION, SUBENTRY_ARRAY, UPDATE_INTERVAL,
    WEATHER_SOURCE_OPEN_METEO,
)
from . import archive as fc_archive
from .model import ArrayGeometry, LearnedArray, WeatherHour, forecast, learn, usable_samples
from .solar import Site
from .sources import (
    async_archive_irradiance, async_archive_temperature, async_hourly_statistics,
)
from .sources import async_open_meteo, async_power_factors, async_weather_entity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArrayConfig:
    subentry_id: str
    name: str
    sensors: tuple[str, ...]
    geometry: ArrayGeometry
    valid_since: float | None
    temp_coeff: float | None = None          # per K, from the datasheet


@dataclass
class NoxcastData:
    arrays: dict[str, dict[float, float]] = field(default_factory=dict)   # subentry_id -> {hour_ts: Wh}
    total: dict[float, float] = field(default_factory=dict)
    updated: datetime | None = None


type NoxcastConfigEntry = ConfigEntry[NoxcastCoordinator]


def array_configs(entry: ConfigEntry) -> dict[str, ArrayConfig]:
    configs = {}
    for sub_id, sub in entry.subentries.items():
        if sub.subentry_type != SUBENTRY_ARRAY:
            continue
        d = sub.data
        valid = dt_util.parse_date(d[CONF_VALID_SINCE]) if d.get(CONF_VALID_SINCE) else None
        configs[sub_id] = ArrayConfig(
            subentry_id=sub_id, name=d[CONF_NAME], sensors=tuple(d[CONF_POWER_SENSORS]),
            geometry=ArrayGeometry(float(d[CONF_TILT]), float(d[CONF_AZIMUTH]), float(d[CONF_PEAK_POWER])),
            valid_since=dt_util.start_of_local_day(valid).timestamp() if valid else None,
            temp_coeff=float(d[CONF_TEMP_COEFF]) / 100 if d.get(CONF_TEMP_COEFF) is not None else None)
    return configs


class NoxcastCoordinator(DataUpdateCoordinator[NoxcastData]):
    """Single writer for learned state; learning runs in the executor on immutable inputs."""

    config_entry: NoxcastConfigEntry

    def __init__(self, hass: HomeAssistant, entry: NoxcastConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.site = Site(hass.config.latitude, hass.config.longitude, float(hass.config.elevation or 0))
        self.arrays = array_configs(entry)
        self.learned: dict[str, LearnedArray] = {}
        self.exclusions: list[dict] = []          # {"start": ts, "end": ts, "note": str}
        self.last_learning: datetime | None = None
        self.last_learning_error: str | None = None
        self.archive_errors: dict[str, str] = {}         # archive name -> error of the last learning run
        self._hours: dict[str, dict[float, float]] = {}
        self._learn_lock = asyncio.Lock()
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        # separate file: larger and written less often than the learned state
        self._archive_store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.archive")
        self.forecast_archive: dict = {}

    # ------------------------------------------------------------------ lifecycle

    async def async_load(self) -> None:
        """Restore learned state. A damaged store is ignored – everything can be relearned from history."""
        try:
            stored = await self._store.async_load() or {}
            self.exclusions = [e for e in stored.get("exclusions", []) if {"start", "end"} <= set(e)]
            for sub_id, data in stored.get("arrays", {}).items():
                if sub_id in self.arrays and data.get("geometry") == self._geometry_key(sub_id):
                    self.learned[sub_id] = LearnedArray.from_dict(data["learned"])
            if stored.get("last_learning"):
                self.last_learning = dt_util.parse_datetime(stored["last_learning"])
            day_start = dt_util.start_of_local_day().timestamp()
            for sub_id, hours in stored.get("hours", {}).items():
                if sub_id in self.arrays:
                    self._hours[sub_id] = {float(t): float(v) for t, v in hours.items() if float(t) >= day_start}
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Stored Noxcast state could not be read; relearning from history", exc_info=True)
            self.learned, self._hours = {}, {}
        try:
            self.forecast_archive = (await self._archive_store.async_load() or {}).get("days", {})
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Noxcast forecast archive could not be read; starting a new one", exc_info=True)
            self.forecast_archive = {}

        entry = self.config_entry
        entry.async_on_unload(async_track_time_change(
            self.hass, self._async_nightly, hour=LEARN_TIME[0], minute=LEARN_TIME[1], second=0))
        entry.async_on_unload(async_at_started(self.hass, self._async_started))

    @callback
    def _async_started(self, _hass: HomeAssistant) -> None:
        if set(self.learned) != set(self.arrays):
            self.config_entry.async_create_background_task(self.hass, self.async_learn(), "noxcast initial learning")

    async def _async_nightly(self, _now: datetime) -> None:
        await self.async_learn()

    def _geometry_key(self, sub_id: str) -> list:
        a = self.arrays[sub_id]
        return [list(a.sensors), a.geometry.tilt, a.geometry.azimuth, a.geometry.peak_w, a.valid_since, a.temp_coeff,
                bool(self._option(CONF_ARCHIVE_TEMPERATURE, True)), bool(self._option(CONF_SATELLITE, False))]

    def _option(self, key: str, default=None):
        return self.config_entry.options.get(key, default)

    async def _async_archives(self, start: datetime, end: datetime) -> tuple[dict | None, dict | None]:
        """Optional history archives; a failure only disables that refinement."""
        lat, lon, key = self.site.latitude, self.site.longitude, self._option(CONF_API_KEY) or None
        self.archive_errors = {}
        temp = sat = None
        if self._option(CONF_ARCHIVE_TEMPERATURE, True):
            try:
                temp = await async_archive_temperature(
                    self.hass, self._option(CONF_ARCHIVE_URL) or DEFAULT_ARCHIVE_URL, lat, lon, start, end, key)
            except Exception as err:  # noqa: BLE001
                self.archive_errors["temperature"] = str(err) or type(err).__name__
                _LOGGER.warning("Temperature archive unavailable, learning without temperature: %s", err)
        if self._option(CONF_SATELLITE, False):
            try:
                sat = await async_archive_irradiance(
                    self.hass, self._option(CONF_SATELLITE_URL) or DEFAULT_SATELLITE_URL,
                    self._option(CONF_SATELLITE_MODEL) or DEFAULT_SATELLITE_MODEL, lat, lon, start, end, key)
            except Exception as err:  # noqa: BLE001
                self.archive_errors["satellite"] = str(err) or type(err).__name__
                _LOGGER.warning("Satellite archive unavailable, learning without clear-hour filter: %s", err)
        return temp, sat

    def _store_data(self) -> dict:
        return {
            "exclusions": self.exclusions,
            "last_learning": self.last_learning.isoformat() if self.last_learning else None,
            "arrays": {sid: {"geometry": self._geometry_key(sid), "learned": la.as_dict()}
                       for sid, la in self.learned.items() if sid in self.arrays},
            # today's hours survive a restart even if the weather source no longer reports past hours
            "hours": {sid: {str(t): v for t, v in hours.items()} for sid, hours in self._hours.items()},
        }

    # ------------------------------------------------------------------ learning

    async def async_learn(self) -> bool:
        """Relearn all arrays from history. Keeps the previous state on failure."""
        if not self.arrays:
            return True
        async with self._learn_lock:
            try:
                await self._async_learn()
            except Exception as err:  # noqa: BLE001
                self.last_learning_error = str(err) or type(err).__name__
                _LOGGER.exception("Noxcast learning failed; keeping the previous learned state")
                return False
        self.last_learning_error = None
        self._store.async_delay_save(self._store_data, 5)
        await self.async_request_refresh()
        return True

    async def _async_learn(self) -> None:
        now = dt_util.utcnow()
        start = now - timedelta(days=365 * HISTORY_YEARS)
        options = self.config_entry.data
        sensor_ids = sorted({s for a in self.arrays.values() for s in a.sensors})
        factors = await async_power_factors(self.hass, sensor_ids)
        power = await async_hourly_statistics(self.hass, sensor_ids, start, "mean")
        soc_series = await async_hourly_statistics(self.hass, list(options.get(CONF_SOC_SENSORS) or []), start, "max")
        soc: dict[float, float] = {}
        for series in soc_series.values():
            for ts, value in series.items():
                if value is not None:
                    soc[ts] = max(value, soc.get(ts, value))
        exclusions = [(e["start"], e["end"]) for e in self.exclusions]
        soc_full = float(options.get(CONF_SOC_FULL, DEFAULT_SOC_FULL))
        air_temp, satellite_ghi = await self._async_archives(start, now)

        learned = {}
        for sub_id, arr in self.arrays.items():
            missing = [s for s in arr.sensors if not power.get(s)]
            if missing:
                _LOGGER.warning("%s: no long-term statistics for %s (state_class measurement required)",
                                arr.name, ", ".join(missing))
            summed = _sum_series([{t: v * factors.get(s, 1.0) for t, v in power.get(s, {}).items() if v is not None}
                                  for s in arr.sensors])
            samples = usable_samples(summed, soc=soc, soc_full=soc_full, exclusions=exclusions,
                                     valid_since=arr.valid_since)
            result = await self.hass.async_add_executor_job(functools.partial(
                learn, self.site, arr.geometry, samples, now.timestamp(), air_temp=air_temp, satellite_ghi=satellite_ghi,
                datasheet_temp_coeff=arr.temp_coeff))
            if result.samples and not 0.5 <= result.efficiency <= 1.2:
                _LOGGER.warning("%s: implausible system efficiency %.2f – check peak power and power sensor units",
                                arr.name, result.efficiency)
            learned[sub_id] = result
            fc_archive.add_actuals(self.forecast_archive, sub_id, summed, now.timestamp(),
                                   {t for t, v in soc.items() if v >= soc_full})
            _LOGGER.info("Learned %s: efficiency %.3f, temperature coefficient %.3f %%/K, %d shading cells, "
                         "coverage %.0f %%, %d samples, clear filter %s, agreement %s",
                         arr.name, result.efficiency, result.temp_coeff * 100, len(result.cells),
                         result.coverage * 100, result.samples, result.clear_filter, result.agreement)
        self.learned.update(learned)          # applied in one step on the event loop
        self.last_learning = now
        self._archive_store.async_delay_save(self._archive_data, 5)

    def _archive_data(self) -> dict:
        return {"days": self.forecast_archive}

    def _archive_forecast(self, weather: list[WeatherHour], result: NoxcastData) -> None:
        """Keep overwriting tomorrow's forecast; the last version before midnight is the day-ahead forecast."""
        tomorrow = dt_util.start_of_local_day() + timedelta(days=1)
        a = tomorrow.timestamp()
        b = dt_util.start_of_local_day(tomorrow.date() + timedelta(days=1)).timestamp()
        hours = {w.start: (w.ghi, w.temp) for w in weather if a <= w.start < b}
        if fc_archive.snapshot(self.forecast_archive, tomorrow.date(), dt_util.utcnow().isoformat(), hours,
                               result.arrays):
            fc_archive.prune(self.forecast_archive, dt_util.now().date() - timedelta(days=ARCHIVE_DAYS))
            self._archive_store.async_delay_save(self._archive_data, 300)

    async def async_add_exclusion(self, start: datetime, end: datetime, note: str = "") -> None:
        self.exclusions.append({"start": start.timestamp(), "end": end.timestamp(), "note": note})
        self._store.async_delay_save(self._store_data, 5)

    # ------------------------------------------------------------------ forecast

    async def _async_fetch_weather(self) -> list[WeatherHour]:
        data = self.config_entry.data
        if data.get(CONF_WEATHER_SOURCE) == WEATHER_SOURCE_OPEN_METEO:
            return await async_open_meteo(self.hass, self.site.latitude, self.site.longitude)
        return await async_weather_entity(self.hass, data[CONF_WEATHER_ENTITY])

    async def _async_update_data(self) -> NoxcastData:
        try:
            weather = await self._async_fetch_weather()
        except Exception as err:  # noqa: BLE001 – any source failure keeps the last forecast
            raise UpdateFailed(f"Weather source failed: {err}") from err

        day_start = dt_util.start_of_local_day().timestamp()
        horizon = (dt_util.start_of_local_day() + timedelta(days=FORECAST_DAYS)).timestamp()
        weather = [w for w in weather if day_start <= w.start < horizon]
        if not weather:
            raise UpdateFailed("Weather source returned no hours for the forecast horizon")

        result = NoxcastData(updated=dt_util.utcnow())
        for sub_id, arr in self.arrays.items():
            learned = self.learned.get(sub_id, LearnedArray())
            fresh = await self.hass.async_add_executor_job(forecast, self.site, arr.geometry, learned, weather)
            # keep earlier values for past hours a source no longer reports (e.g. DWD drops past hours)
            merged = {t: v for t, v in self._hours.get(sub_id, {}).items() if t >= day_start}
            merged.update(fresh)
            self._hours[sub_id] = merged
            result.arrays[sub_id] = dict(sorted(merged.items()))
            for t, wh in merged.items():
                result.total[t] = result.total.get(t, 0.0) + wh
        result.total = dict(sorted(result.total.items()))
        self._archive_forecast(weather, result)
        self._store.async_delay_save(self._store_data, 60)
        return result

    @property
    def forecast_available(self) -> bool:
        """A forecast exists and is recent enough; short weather outages do not blank the sensors."""
        data = self.data
        return bool(data and data.updated and dt_util.utcnow() - data.updated < STALE_AFTER)


def _sum_series(series: list[dict[float, float]]) -> dict[float, float]:
    """Sum several sensors per hour; hours where any sensor is missing are dropped."""
    if not series or not all(series):
        return {}
    common = set(series[0])
    for s in series[1:]:
        common &= set(s)
    return {t: sum(s[t] for s in series) for t in common}
