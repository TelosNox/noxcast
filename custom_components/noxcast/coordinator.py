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
    ARCHIVE_DAYS, COMPARE_SOURCE, CONF_AZIMUTH, CONF_COMPARE_OPEN_METEO, MORNING_HOUR, CONF_TEMP_COEFF, CONF_NAME, CONF_PEAK_POWER, CONF_POWER_SENSORS, CONF_SOC_FULL, CONF_SOC_SENSORS,
    CONF_TILT, CONF_VALID_SINCE, CONF_WEATHER_ENTITY, CONF_WEATHER_SOURCE, DEFAULT_SOC_FULL, DOMAIN,
    FORECAST_DAYS, HISTORY_YEARS, LEARN_TIME, STALE_AFTER, STORAGE_VERSION, SUBENTRY_ARRAY, UPDATE_MINUTES,
    WEATHER_SOURCE_OPEN_METEO,
)
from . import archive as fc_archive
from .intraday import Partial, clearness_shift, expected_hours
from .model import (ArrayGeometry, LearnedArray, WeatherHour, clearsky_profile, clearsky_weather, forecast, learn,
                    usable_samples)
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
    """Hourly Wh per array (subentry_id) and as total (key None)."""

    forecast: dict[str | None, dict[float, float]] = field(default_factory=dict)   # weather forecast, 3 days
    morning: dict[str | None, dict[float, float]] = field(default_factory=dict)    # today, fixed in the morning
    expected: dict[str | None, dict[float, float]] = field(default_factory=dict)   # today: actual + corrected rest
    correction: dict[str, float | None] = field(default_factory=dict)              # intraday clearness shift
    morning_fixed: datetime | None = None
    updated: datetime | None = None

    @property
    def arrays(self) -> dict[str, dict[float, float]]:
        return {k: v for k, v in self.forecast.items() if k is not None}

    @property
    def total(self) -> dict[float, float]:
        return self.forecast.get(None, {})


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
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self.site = Site(hass.config.latitude, hass.config.longitude, float(hass.config.elevation or 0))
        self.arrays = array_configs(entry)
        self.learned: dict[str, LearnedArray] = {}
        self.exclusions: list[dict] = []          # {"start": ts, "end": ts, "note": str}
        self.last_learning: datetime | None = None
        self.last_learning_error: str | None = None
        self.archive_errors: dict[str, str] = {}         # archive name -> error of the last learning run
        self._hours: dict[str, dict[float, float]] = {}
        self._morning: dict[str, dict[float, float]] = {}
        self._morning_fixed: datetime | None = None
        self._factors: dict[str, float] | None = None
        self._compare: tuple[float, list[WeatherHour]] | None = None      # (fetched at, hours)
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
            morning = stored.get("morning") or {}
            fixed = dt_util.parse_datetime(morning.get("fixed") or "")
            if fixed and dt_util.as_local(fixed).date() == dt_util.now().date():
                self._morning_fixed = fixed
                self._morning = {sid: {float(t): float(v) for t, v in hours.items()}
                                 for sid, hours in morning.get("hours", {}).items() if sid in self.arrays}
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
        # aligned to the clock, so day change (00:00) and morning fix (06:00) happen on time
        entry.async_on_unload(async_track_time_change(
            self.hass, self._async_scheduled_refresh, minute=UPDATE_MINUTES, second=0))
        entry.async_on_unload(async_at_started(self.hass, self._async_started))

    async def _async_scheduled_refresh(self, _now: datetime) -> None:
        await self.async_refresh()

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
            "morning": {"fixed": self._morning_fixed.isoformat() if self._morning_fixed else None,
                        "hours": {sid: {str(t): v for t, v in h.items()} for sid, h in self._morning.items()}},
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
        self._factors = factors
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

    def _archive_forecast(self, weather: list[WeatherHour], result: NoxcastData,
                          alt: dict[str, tuple[dict, dict]] | None = None) -> None:
        """Keep overwriting tomorrow's forecast; the last version before midnight is the day-ahead forecast."""
        tomorrow = dt_util.start_of_local_day() + timedelta(days=1)
        a = tomorrow.timestamp()
        b = dt_util.start_of_local_day(tomorrow.date() + timedelta(days=1)).timestamp()
        hours = {w.start: (w.ghi, w.temp) for w in weather if a <= w.start < b}
        alt = {name: ({t: g for t, g in ghi.items() if a <= t < b}, fc) for name, (ghi, fc) in (alt or {}).items()}
        if fc_archive.snapshot(self.forecast_archive, tomorrow.date(), dt_util.utcnow().isoformat(), hours,
                               result.arrays, alt):
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

    async def _async_compare_weather(self) -> list[WeatherHour]:
        """Open-Meteo next to a weather entity, for the archive only; fetched hourly, failures are ignored."""
        data = self.config_entry.data
        if (data.get(CONF_WEATHER_SOURCE) == WEATHER_SOURCE_OPEN_METEO
                or not self._option(CONF_COMPARE_OPEN_METEO, True)):
            return []
        now = dt_util.utcnow().timestamp()
        if self._compare is None or now - self._compare[0] > 3300:
            try:
                self._compare = (now, await async_open_meteo(self.hass, self.site.latitude, self.site.longitude))
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Open-Meteo comparison forecast failed: %s", err)
                return self._compare[1] if self._compare else []
        return self._compare[1]

    async def _async_today_actual(self, day_start: float, now: float
                                  ) -> tuple[dict[str, dict[float, float]], dict[str, tuple[float, float, float]]]:
        """Measured mean power (W = Wh) of today's hours per array, and the running hour.

        Hourly statistics are compiled a few minutes after each hour; the hour just ended and the
        running hour therefore come from 5-minute statistics. Returns (hours, running) with
        running = (hour start, mean power of the measured part, measured share).
        """
        sensor_ids = sorted({s for a in self.arrays.values() for s in a.sensors})
        hour_now = now - now % 3600
        try:
            if self._factors is None:
                self._factors = await async_power_factors(self.hass, sensor_ids)
            power = await async_hourly_statistics(self.hass, sensor_ids, dt_util.utc_from_timestamp(day_start), "mean")
            recent = await async_hourly_statistics(self.hass, sensor_ids,
                                                   dt_util.utc_from_timestamp(max(hour_now - 3600, day_start)),
                                                   "mean", "5minute")
        except Exception as err:  # noqa: BLE001 – without measurements there is simply no correction
            _LOGGER.debug("Today's statistics unavailable: %s", err)
            return {}, {}

        def scaled(stats: dict[str, dict[float, float | None]], sensors: list[str]) -> dict[float, float]:
            return _sum_series([{t: v * self._factors.get(s, 1.0) for t, v in stats.get(s, {}).items()
                                 if v is not None} for s in sensors])

        hours: dict[str, dict[float, float]] = {}
        running: dict[str, tuple[float, float, float]] = {}
        for sub_id, arr in self.arrays.items():
            hourly = scaled(power, arr.sensors)
            slots = scaled(recent, arr.sensors)
            prev = hour_now - 3600
            done = [v for t, v in slots.items() if prev <= t < hour_now]
            if prev >= day_start and prev not in hourly and len(done) >= RECENT_MIN_SLOTS_HOUR:
                hourly[prev] = sum(done) / len(done)
            part = [v for t, v in slots.items() if hour_now <= t < now]
            if len(part) >= RECENT_MIN_SLOTS_RUNNING:
                running[sub_id] = (hour_now, sum(part) / len(part), len(part) / 12)
            hours[sub_id] = hourly
        return hours, running

    async def _async_update_data(self) -> NoxcastData:
        try:
            weather = await self._async_fetch_weather()
        except Exception as err:  # noqa: BLE001 – any source failure keeps the last forecast
            raise UpdateFailed(f"Weather source failed: {err}") from err

        local_now = dt_util.now()
        day_start = dt_util.start_of_local_day().timestamp()
        day_end = dt_util.start_of_local_day(local_now.date() + timedelta(days=1)).timestamp()
        horizon = (dt_util.start_of_local_day() + timedelta(days=FORECAST_DAYS)).timestamp()
        weather = [w for w in weather if day_start <= w.start < horizon]
        if not weather:
            raise UpdateFailed("Weather source returned no hours for the forecast horizon")
        compare = [w for w in await self._async_compare_weather() if day_start <= w.start < horizon]

        result = NoxcastData(updated=dt_util.utcnow())
        alt_forecasts: dict[str, dict[float, float]] = {}
        for sub_id, arr in self.arrays.items():
            learned = self.learned.get(sub_id, LearnedArray())
            fresh = await self.hass.async_add_executor_job(forecast, self.site, arr.geometry, learned, weather)
            # keep earlier values for past hours a source no longer reports (e.g. DWD drops past hours)
            merged = {t: v for t, v in self._hours.get(sub_id, {}).items() if t >= day_start}
            merged.update(fresh)
            self._hours[sub_id] = merged
            result.forecast[sub_id] = dict(sorted(merged.items()))
            if compare:
                alt_forecasts[sub_id] = await self.hass.async_add_executor_job(
                    forecast, self.site, arr.geometry, learned, compare)

        # today's forecast is fixed in the morning; before that it follows the weather source
        if self._morning_fixed is None or dt_util.as_local(self._morning_fixed).date() != local_now.date():
            self._morning_fixed = None
            self._morning = {}
        if self._morning_fixed is None and local_now.hour >= MORNING_HOUR:
            self._morning_fixed = dt_util.utcnow()
            self._morning = {sid: {t: v for t, v in h.items() if t < day_end} for sid, h in result.arrays.items()}
        result.morning_fixed = self._morning_fixed

        now = dt_util.utcnow().timestamp()
        actual, running = await self._async_today_actual(day_start, now)
        temps = {w.start: w.temp for w in weather}
        clear_weather = [clearsky_weather(self.site, t, temps.get(t))
                         for t in sorted(result.forecast.get(next(iter(self.arrays), None), {})) if t < day_end]
        for sub_id, arr in self.arrays.items():
            today = {t: v for t, v in result.forecast[sub_id].items() if t < day_end}
            result.morning[sub_id] = self._morning.get(sub_id) or today
            clear = await self.hass.async_add_executor_job(
                forecast, self.site, arr.geometry, self.learned.get(sub_id, LearnedArray()), clear_weather)
            partial = None
            if sub_id in running:
                start, power, share = running[sub_id]
                profile = await self.hass.async_add_executor_job(
                    clearsky_profile, self.site, arr.geometry, self.learned.get(sub_id, LearnedArray()), start,
                    temps.get(start))
                measured = profile[:round(share * len(profile))]
                if measured and sum(profile) > 0:
                    partial = Partial(start, power, share,
                                      (sum(measured) / len(measured)) / (sum(profile) / len(profile)))
            shift = clearness_shift(today, actual.get(sub_id, {}), clear, now, arr.geometry.peak_w, partial)
            result.correction[sub_id] = None if shift is None else round(shift, 3)
            result.expected[sub_id] = expected_hours(today, actual.get(sub_id, {}), clear, now, shift)
        for series in (result.forecast, result.morning, result.expected):
            total: dict[float, float] = {}
            for hours in list(series.values()):
                for t, wh in hours.items():
                    total[t] = total.get(t, 0.0) + wh
            series[None] = dict(sorted(total.items()))

        alt = None
        if alt_forecasts:
            alt = {COMPARE_SOURCE: ({w.start: w.ghi for w in compare}, alt_forecasts)}
        self._archive_forecast(weather, result, alt)
        self._store.async_delay_save(self._store_data, 60)
        return result

    @property
    def forecast_available(self) -> bool:
        """A forecast exists and is recent enough; short weather outages do not blank the sensors."""
        data = self.data
        return bool(data and data.updated and dt_util.utcnow() - data.updated < STALE_AFTER)


RECENT_MIN_SLOTS_HOUR = 10       # 5-minute values needed to stand in for a just-ended hour
RECENT_MIN_SLOTS_RUNNING = 3     # 5-minute values (15 minutes) before the running hour counts


def _sum_series(series: list[dict[float, float]]) -> dict[float, float]:
    """Sum several sensors per hour; hours where any sensor is missing are dropped."""
    if not series or not all(series):
        return {}
    common = set(series[0])
    for s in series[1:]:
        common &= set(s)
    return {t: sum(s[t] for s in series) for t in common}
