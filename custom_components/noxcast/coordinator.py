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
    DEFAULT_ARCHIVE_URL, DEFAULT_SATELLITE_MODEL, DEFAULT_SATELLITE_URL, CONF_WEATHER_MODELS, DEFAULT_WEATHER_MODELS,
    WEATHER_DAYS, WEATHER_MAX_AGE, ARCHIVE_DAYS, CONF_AZIMUTH, CONF_HEATING, CONF_ORIENTATIONS, MORNING_HOUR, CONF_TEMP_COEFF, CONF_NAME, CONF_PEAK_POWER, CONF_POWER_SENSORS, CONF_SOC_FULL, CONF_SOC_SENSORS,
    CONF_TILT, CONF_VALID_SINCE, DEFAULT_SOC_FULL, DOMAIN,
    CONF_FORECAST_DAYS, FORECAST_DAYS, HISTORY_YEARS, MAX_FORECAST_DAYS, LEARN_TIME, POWER_UNIT_FACTORS, STALE_AFTER, STORAGE_VERSION, SUBENTRY_ARRAY, UPDATE_MINUTES,
    CONF_HOUSE_LOAD, CONF_GRID, CONF_GRID_INVERT, CONF_DAY_TYPE, CONF_POWER_SENSOR, CONF_TIMING, SUBENTRY_CONSUMER,
    CONF_ELAPSED_TIME, CONF_ENERGY_SENSOR, CONF_REMAINING_TIME, ENERGY_UNIT_FACTORS, MINUTE_FACTORS,
    CONF_CAPACITY, CONF_COUPLING, CONF_EFFICIENCY, CONF_MAX_CHARGE, CONF_MAX_DISCHARGE, CONF_MAX_SOC, CONF_MAX_SOC_ENTITY,
    CONF_MIN_SOC, CONF_MIN_SOC_ENTITY, CONF_RESERVE, CONF_SOC_SENSOR, SUBENTRY_BATTERY, CONF_RUN_MINUTES, CONF_RUN_POWER, CONF_KIND,
)
from . import archive as fc_archive
from . import band as bd
from . import fine as fn
from . import events as ev
from . import flows as fl
from . import battery as bt
from . import arraycheck as ac
from . import load as lf
from . import plan as pl
from .effects import hourly_effects, significant_periods
from .intraday import Partial, clearness_shift, expected_hours, held_forecast
from .model import (HEATING_K, ArrayGeometry, LearnedArray, Plane, WeatherHour, clearsky_profile, clearsky_weather, forecast,
                    learn, steady_hours, usable_samples)
from .solar import Site, angle_of_incidence, sun_position
from .sources import (
    async_archive_irradiance, async_archive_temperature, async_hourly_statistics,
)
from .sources import async_open_meteo, async_power_factors

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArrayConfig:
    subentry_id: str
    name: str
    sensors: tuple[str, ...]
    geometry: ArrayGeometry
    valid_since: float | None
    temp_coeff: float | None = None          # per K, from the datasheet


@dataclass(frozen=True)
class ConsumerConfig:
    subentry_id: str
    name: str
    sensor: str | None                   # power (W)
    timing: str
    energy: str | None = None            # energy (kWh); history from its hourly change when there is no power sensor
    remaining: str | None = None         # minutes a running program still needs
    elapsed: str | None = None           # minutes it has run
    run_power: float | None = None       # flexible: a run's power and duration, None = learned
    run_minutes: float | None = None
    kind: str = "profile"                # fixed: profile, program or session

    @property
    def series_id(self) -> str:
        return self.sensor or self.energy


def consumer_configs(entry: ConfigEntry) -> dict[str, ConsumerConfig]:
    return {sub_id: ConsumerConfig(sub_id, sub.data[CONF_NAME], sub.data.get(CONF_POWER_SENSOR) or None,
                                   sub.data.get(CONF_TIMING, lf.TIMING_FIXED), sub.data.get(CONF_ENERGY_SENSOR) or None,
                                   sub.data.get(CONF_REMAINING_TIME) or None, sub.data.get(CONF_ELAPSED_TIME) or None,
                                   float(sub.data[CONF_RUN_POWER]) if sub.data.get(CONF_RUN_POWER) else None,
                                   float(sub.data[CONF_RUN_MINUTES]) if sub.data.get(CONF_RUN_MINUTES) else None,
                                   sub.data.get(CONF_KIND, lf.KIND_PROFILE))
            for sub_id, sub in entry.subentries.items()
            if sub.subentry_type == SUBENTRY_CONSUMER and (sub.data.get(CONF_POWER_SENSOR) or sub.data.get(CONF_ENERGY_SENSOR))}


def _models_from_dict(d: dict) -> dict:
    return {"program": lf.ProgramModel.from_dict(d["program"]) if d.get("program") else None,
            "session": lf.SessionModel.from_dict(d["session"]) if d.get("session") else None,
            "regulated": lf.RegulatedModel.from_dict(d["regulated"]) if d.get("regulated") else None}


@dataclass
class NoxcastData:
    """Hourly Wh per array (subentry_id) and as total (key None)."""

    forecast: dict[str | None, dict[float, float]] = field(default_factory=dict)   # weather forecast, 3 days
    morning: dict[str | None, dict[float, float]] = field(default_factory=dict)    # today, fixed in the morning
    expected: dict[str | None, dict[float, float]] = field(default_factory=dict)   # today: actual + corrected rest
    clear: dict[str | None, dict[float, float]] = field(default_factory=dict)      # clear-sky output over the horizon
    held: dict[str, dict[float, float]] = field(default_factory=dict)  # today: latest forecast, held once the hour began
    correction: dict[str, float | None] = field(default_factory=dict)              # intraday clearness shift
    effects: dict[str, dict] = field(default_factory=dict)        # today per array: loss, gain (Wh/h), periods
    morning_fixed: datetime | None = None
    updated: datetime | None = None
    day_start: float | None = None            # local midnight of the day these values were computed for
    weather_fetched: datetime | None = None                                # last successful Open-Meteo answer
    load: lf.LoadForecast | None = None       # house load from the running hour until the end of tomorrow
    battery: dict | None = None               # simulation until tomorrow's charge start (see _battery)
    plan: dict[str, dict] = field(default_factory=dict)   # flexible consumer -> windows today and tomorrow
    band: dict | None = None                  # P10 / P90 of the total: hours, days, rest of today (see _band)
    actual: dict[str, dict[float, float]] = field(default_factory=dict)   # today's measured hours per array
    weather: list[WeatherHour] = field(default_factory=list)              # weather hours from today on
    outlook: list[WeatherHour] = field(default_factory=list)              # the same for a week: the weather entity

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
        # further orientations: complete sections only (the config flow accepts no others)
        extra = tuple(Plane(float(o[CONF_TILT]), float(o[CONF_AZIMUTH]), float(o[CONF_PEAK_POWER]))
                      for o in (d.get(key) or {} for key in CONF_ORIENTATIONS)
                      if all(o.get(k) is not None for k in (CONF_TILT, CONF_AZIMUTH, CONF_PEAK_POWER)))
        configs[sub_id] = ArrayConfig(
            subentry_id=sub_id, name=d[CONF_NAME], sensors=tuple(d[CONF_POWER_SENSORS]),
            geometry=ArrayGeometry(float(d[CONF_TILT]), float(d[CONF_AZIMUTH]),
                                   float(d[CONF_PEAK_POWER]) + sum(p.peak_w for p in extra),
                                   float(d[CONF_HEATING]) if d.get(CONF_HEATING) is not None else HEATING_K, extra),
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
        self.house_load: str | None = entry.data.get(CONF_HOUSE_LOAD) or None
        self.grid: str | None = entry.data.get(CONF_GRID) or None
        self.grid_sign = -1.0 if entry.data.get(CONF_GRID_INVERT) else 1.0
        self.forecast_days = max(2, min(MAX_FORECAST_DAYS, int(entry.data.get(CONF_FORECAST_DAYS) or FORECAST_DAYS)))
        self.day_type_entity: str | None = entry.data.get(CONF_DAY_TYPE) or None
        self.day_types: dict = {}                    # local date -> day type from the day-type entity
        self.consumers = consumer_configs(entry)
        self.batteries = {sid: dict(sub.data) for sid, sub in entry.subentries.items() if sub.subentry_type == SUBENTRY_BATTERY}
        self.load_profile: lf.Profile | None = None
        self.floor_profile: lf.Profile | None = None    # base load from the hourly minimum, for sustained loads
        self.consumer_profiles: dict[str, lf.Profile] = {}
        self.run_profiles: dict[str, dict] = {}      # learned run of flexible consumers: power_w, minutes
        self.consumer_models: dict[str, dict] = {}   # learned program and session model per consumer
        self.load_events: dict | None = None         # recurring device runs in the house load (suggestions)
        self.band: dict | None = None                # learned forecast uncertainty (band.learn_band)
        self.load_backtest: dict | None = None
        self.learned: dict[str, LearnedArray] = {}
        self.exclusions: list[dict] = []          # {"start": ts, "end": ts, "note": str}
        self.last_learning: datetime | None = None
        self.last_learning_error: str | None = None
        self.archive_errors: dict[str, str] = {}         # archive name -> error of the last learning run
        self._hours: dict[str, dict[float, float]] = {}
        self._morning: dict[str, dict[float, float]] = {}
        self._held: dict[str, dict[float, float]] = {}
        self._morning_fixed: datetime | None = None
        self._factors: dict[str, float] | None = None
        # last Open-Meteo answer: (fetched at, mean of the models, each model); stands in during outages
        self._weather: tuple[float, list[WeatherHour], dict[str, list[WeatherHour]]] | None = None
        self._learn_lock = asyncio.Lock()
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        # separate file: larger and written less often than the learned state
        self._archive_store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.archive")
        self.forecast_archive: dict = {}
        # 5-minute means per power sensor for sharper shading later (fine.py); bounded to three years
        self._fine_store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.fine")
        self.fine: dict = {}
        self.battery_log: dict[str, dict] = {}       # local date -> the morning's battery prediction

    # ------------------------------------------------------------------ lifecycle

    async def async_load(self) -> None:
        """Restore learned state. A damaged store is ignored – everything can be relearned from history."""
        try:
            stored = await self._store.async_load() or {}
            self.exclusions = [e for e in stored.get("exclusions", []) if {"start", "end"} <= set(e)]
            for sub_id, data in stored.get("arrays", {}).items():
                if sub_id in self.arrays and data.get("geometry") == self._geometry_key(sub_id):
                    self.learned[sub_id] = LearnedArray.from_dict(data["learned"])
            self.band = bd.valid(stored.get("band"))
            self.battery_log = dict(stored.get("battery_log") or {})
            load = stored.get("load") or {}
            if self.house_load and load.get("key") == self._load_key():
                self.load_profile = lf.Profile.from_dict(load["base"])
                self.floor_profile = lf.Profile.from_dict(load["floor"]) if load.get("floor") else None
                self.consumer_profiles = {sid: lf.Profile.from_dict(p) for sid, p in load.get("consumers", {}).items()
                                          if sid in self.consumers}
                self.load_backtest = load.get("backtest")
                self.run_profiles = {sid: r for sid, r in (load.get("runs") or {}).items() if sid in self.consumers}
                self.load_events = load.get("events")
                self.consumer_models = {sid: _models_from_dict(m) for sid, m in (load.get("models") or {}).items()
                                        if sid in self.consumers}
            if stored.get("last_learning"):
                self.last_learning = dt_util.parse_datetime(stored["last_learning"])
            day_start = dt_util.start_of_local_day().timestamp()
            for sub_id, hours in stored.get("hours", {}).items():
                if sub_id in self.arrays:
                    self._hours[sub_id] = {float(t): float(v) for t, v in hours.items() if float(t) >= day_start}
            if cached := stored.get("weather"):
                self._weather = (float(cached["fetched"]), [_weather_hour(r) for r in cached["mean"]], {})
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
        try:
            self.fine = (await self._fine_store.async_load() or {}).get("days", {})
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Noxcast 5-minute archive could not be read; starting a new one", exc_info=True)
            self.fine = {}
        if self.band is None and self.forecast_archive and self.learned:     # stored in an older form
            self.band = await self.hass.async_add_executor_job(self._learn_band)

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
        if set(self.learned) != set(self.arrays) or (self.house_load and self.load_profile is None):
            self.config_entry.async_create_background_task(self.hass, self.async_learn(), "noxcast initial learning")
        self.config_entry.async_create_background_task(self.hass, self._async_record_fine(), "noxcast 5-minute archive")

    async def _async_nightly(self, _now: datetime) -> None:
        await self._async_record_fine()
        await self.async_learn()

    async def _async_record_fine(self) -> None:
        """Keep yesterday's (and any missed day's) 5-minute means per power sensor while the statistics exist."""
        today = dt_util.now().date()
        sensor_ids = sorted({s for a in self.arrays.values() for s in a.sensors})
        days = fn.missing_days(self.fine, set(sensor_ids), today)
        if not days:
            return
        try:
            if self._factors is None:
                self._factors = await async_power_factors(self.hass, sensor_ids)
            stats = await async_hourly_statistics(self.hass, sensor_ids, dt_util.start_of_local_day(days[0]), "mean",
                                                  "5minute", dt_util.start_of_local_day(today))
        except Exception as err:  # noqa: BLE001 – the archive simply misses these days
            _LOGGER.debug("5-minute statistics unavailable: %s", err)
            return
        for arr in self.arrays.values():
            planes = arr.geometry.planes

            def useful(ts: float, planes=planes) -> bool:
                sun = sun_position(self.site, ts)
                return sun.elevation > 0 and any(
                    angle_of_incidence(tilt, azimuth, 90.0 - sun.elevation, sun.azimuth) < 90.0 for tilt, azimuth, _ in planes)

            for sensor in arr.sensors:
                series = {t: v * self._factors.get(sensor, 1.0) for t, v in stats.get(sensor, {}).items() if v is not None}
                for day in days:
                    a = dt_util.start_of_local_day(day).timestamp()
                    b = dt_util.start_of_local_day(day + timedelta(days=1)).timestamp()
                    fn.add_day(self.fine, day, sensor, fn.day_steps(series, a, b, useful))
        fn.prune(self.fine, today - timedelta(days=fn.FINE_DAYS), set(sensor_ids))
        self._fine_store.async_delay_save(lambda: {"days": self.fine}, 60)

    def _geometry_key(self, sub_id: str) -> list:
        a = self.arrays[sub_id]
        key = [list(a.sensors), a.geometry.tilt, a.geometry.azimuth, a.geometry.peak_w, a.valid_since, a.temp_coeff,
               bool(self._option(CONF_ARCHIVE_TEMPERATURE, True)), bool(self._option(CONF_SATELLITE, False)),
               a.geometry.heating]
        # further orientations only when there are any, so the key of a single-plane array stays as it was
        if a.geometry.extra:
            key.append([[p.tilt, p.azimuth, p.peak_w] for p in a.geometry.extra])
        return key

    def _load_key(self) -> list:
        return [self.house_load, sorted([c.subentry_id, c.series_id] for c in self.consumers.values())]

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
            "band": self.band,
            "battery_log": self.battery_log,
            "exclusions": self.exclusions,
            "last_learning": self.last_learning.isoformat() if self.last_learning else None,
            "arrays": {sid: {"geometry": self._geometry_key(sid), "learned": la.as_dict()}
                       for sid, la in self.learned.items() if sid in self.arrays},
            # today's hours survive a restart even if the weather source no longer reports past hours
            "hours": {sid: {str(t): v for t, v in hours.items()} for sid, hours in self._hours.items()},
            "load": {"key": self._load_key(), "base": self.load_profile.as_dict(),
                     "floor": self.floor_profile.as_dict() if self.floor_profile else None,
                     "consumers": {sid: p.as_dict() for sid, p in self.consumer_profiles.items()},
                     "backtest": self.load_backtest, "runs": self.run_profiles,
                     "models": {sid: {k: m.as_dict() for k, m in ms.items() if m} for sid, ms in self.consumer_models.items()},
                     "events": self.load_events}
            if self.load_profile else None,
            "morning": {"fixed": self._morning_fixed.isoformat() if self._morning_fixed else None,
                        "hours": {sid: {str(t): v for t, v in h.items()} for sid, h in self._morning.items()}},
            # only the mean: the single models serve the archive, which can skip an outage
            "weather": {"fetched": self._weather[0], "mean": [_weather_row(w) for w in self._weather[1]]}
            if self._weather else None,
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
        # hourly minimum and maximum tell steady hours from hours with passing clouds or dropouts
        power_min = await async_hourly_statistics(self.hass, sensor_ids, start, "min")
        power_max = await async_hourly_statistics(self.hass, sensor_ids, start, "max")
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
            steady = steady_hours([(power.get(s, {}), power_min.get(s, {}), power_max.get(s, {})) for s in arr.sensors])
            result = await self.hass.async_add_executor_job(functools.partial(
                learn, self.site, arr.geometry, samples, now.timestamp(), air_temp=air_temp, satellite_ghi=satellite_ghi,
                datasheet_temp_coeff=arr.temp_coeff, steady=steady))
            if result.samples and not 0.5 <= result.efficiency <= 1.2:
                _LOGGER.warning("%s: implausible system efficiency %.2f – check peak power and power sensor units",
                                arr.name, result.efficiency)
            if result.samples:
                await self.hass.async_add_executor_job(
                    _explain, self.site, arr.geometry, result, samples, air_temp, satellite_ghi, now.year, steady)
            learned[sub_id] = result
            fc_archive.add_actuals(self.forecast_archive, sub_id, summed, now.timestamp(),
                                   {t for t, v in soc.items() if v >= soc_full})
            if satellite_ghi:
                predict = functools.partial(_measured_forecast, self.site, arr.geometry, result)
                await self.hass.async_add_executor_job(
                    fc_archive.add_measured, self.forecast_archive, sub_id, satellite_ghi, air_temp, predict,
                    now.timestamp())
            _LOGGER.info("Learned %s: efficiency %.3f, temperature coefficient %.3f %%/K, %d shading cells, "
                         "coverage %.0f %%, %d samples, clear filter %s, steady filter %s, agreement %s",
                         arr.name, result.efficiency, result.temp_coeff * 100, len(result.cells),
                         result.coverage * 100, result.samples, result.clear_filter, result.steady_filter,
                         result.agreement)
        self.learned.update(learned)          # applied in one step on the event loop
        self.band = await self.hass.async_add_executor_job(self._learn_band)
        if self.house_load:
            await self._async_learn_load(now, air_temp)
        self.last_learning = now
        self._archive_store.async_delay_save(self._archive_data, 5)

    async def _async_load_series(self, start: datetime, period: str = "hour") -> dict[str, dict[float, float]]:
        """House load and consumer power in W: {sensor: {hour start: mean W}}.

        Consumers without a power sensor use their energy sensor: the hourly change in Wh is the mean W.
        """
        ids = [self.house_load] + [c.sensor for c in self.consumers.values() if c.sensor]
        energy = [c.energy for c in self.consumers.values() if not c.sensor]
        factors = await async_power_factors(self.hass, ids)
        stats = await async_hourly_statistics(self.hass, ids, start, "mean", period)
        if energy:
            factors.update(await async_power_factors(self.hass, energy, ENERGY_UNIT_FACTORS))
            stats.update(await async_hourly_statistics(self.hass, energy, start, "change", period))
        return {sid: {t: max(v, 0.0) * factors.get(sid, 1.0) if sid in energy else v * factors.get(sid, 1.0)
                      for t, v in stats.get(sid, {}).items() if v is not None}
                for sid in ids + energy}

    async def _async_floor_series(self, start: datetime, series: dict[str, dict[float, float]]) -> dict[float, float]:
        """Base load from the hourly minimum of the house load (consumers subtracted by their mean)."""
        factor = (await async_power_factors(self.hass, [self.house_load])).get(self.house_load, 1.0)
        stats = await async_hourly_statistics(self.hass, [self.house_load], start, "min")
        floor = {t: v * factor for t, v in stats.get(self.house_load, {}).items() if v is not None}
        return lf.base_series(floor, [series.get(c.series_id, {}) for c in self.consumers.values()])

    async def async_day_flows(self, start: float, end: float, pv: dict[float, float]) -> dict | None:
        """Where the energy of [start, end) went (see flows.py); None without house load and batteries."""
        socs = {d[CONF_SOC_SENSOR]: float(d.get(CONF_CAPACITY) or 0) * 1000
                for d in self.batteries.values() if d.get(CONF_SOC_SENSOR)}
        if not self.house_load and not socs and not self.grid:
            return None
        a, b = dt_util.utc_from_timestamp(start - 3600), dt_util.utc_from_timestamp(end + 3600)
        load = battery = grid = None
        if self.grid:
            factor = (await async_power_factors(self.hass, [self.grid])).get(self.grid, 1.0) * self.grid_sign
            stats = await async_hourly_statistics(self.hass, [self.grid], a, "mean", end=b)
            grid = {t: v * factor for t, v in stats.get(self.grid, {}).items() if v is not None}
        if self.house_load:
            factor = (await async_power_factors(self.hass, [self.house_load])).get(self.house_load, 1.0)
            stats = await async_hourly_statistics(self.hass, [self.house_load], a, "mean", end=b)
            load = {t: v * factor for t, v in stats.get(self.house_load, {}).items() if v is not None}
        if socs:
            stats = await async_hourly_statistics(self.hass, list(socs), a, "mean", end=b)
            parts: dict[float, list[float]] = {}
            for sid, capacity in socs.items():
                soc = {t: v for t, v in stats.get(sid, {}).items() if v is not None}
                for t, wh in fl.battery_energy(soc, capacity).items():
                    parts.setdefault(t, []).append(wh)
            battery = {t: sum(v) for t, v in parts.items() if len(v) == len(socs)}   # every battery known
        return fl.day_flows(pv, load, battery, start, end, grid)

    async def _async_learn_load(self, now: datetime, air_temp: dict[float, float] | None = None) -> None:
        """Base-load and consumer profiles from the recent weeks, plus a replay of the last days."""
        days = lf.PROFILE_DAYS + lf.BACKTEST_DAYS + 1
        series = await self._async_load_series(now - timedelta(days=days))
        tz = dt_util.get_default_time_zone()
        until = dt_util.start_of_local_day().timestamp()
        load = series.get(self.house_load, {})
        if not load:
            _LOGGER.warning("No long-term statistics for the house load %s; load forecast disabled", self.house_load)
            return
        consumers = {sid: series.get(c.series_id, {}) for sid, c in self.consumers.items()}
        if self.day_type_entity:
            self.day_types = await self._async_day_types(now - timedelta(days=days), now)
            tz = lf.DayTypeZone(tz, self.day_types)
        base = lf.base_series(load, list(consumers.values()))
        profile = await self.hass.async_add_executor_job(lf.learn_profile, base, tz, until)
        floor = await self._async_floor_series(now - timedelta(days=days), series)
        self.floor_profile = await self.hass.async_add_executor_job(lf.learn_profile, floor, tz, until)
        cons = {sid: await self.hass.async_add_executor_job(lf.learn_profile, s, tz, until)
                for sid, s in consumers.items()}
        models = {}
        for sid, s in consumers.items():
            models[sid] = {"program": await self.hass.async_add_executor_job(lf.learn_program, s, tz, until),
                           "session": await self.hass.async_add_executor_job(lf.learn_session, s, tz, until)}
        # regulated consumers: a year of history, so the relation to the temperature spans the seasons
        regulated = [sid for sid, c in self.consumers.items() if c.kind == lf.KIND_REGULATED and c.timing == lf.TIMING_FIXED]
        if regulated and air_temp:
            year = await self._async_load_series(now - timedelta(days=lf.REGULATED_DAYS))
            for sid in regulated:
                models[sid]["regulated"] = await self.hass.async_add_executor_job(
                    lf.learn_regulated, year.get(self.consumers[sid].series_id, {}), air_temp, tz, until)
        backtest = await self.hass.async_add_executor_job(
            lf.backtest, load, consumers, {sid: c.timing for sid, c in self.consumers.items()}, tz, now.timestamp(),
            lf.BACKTEST_DAYS, {sid: c.kind for sid, c in self.consumers.items()}, air_temp)
        self.consumer_models = models
        self.load_profile, self.consumer_profiles, self.load_backtest = profile, cons, backtest
        runs = {}
        for sid, c in self.consumers.items():
            if c.timing == lf.TIMING_FLEXIBLE and (run := pl.learn_run(c.name, consumers.get(sid, {}))):
                runs[sid] = {"power_w": run.power_w, "minutes": run.minutes}
        self.run_profiles = runs
        _LOGGER.info("Learned load profile from %d days; backtest %s", profile.days, backtest)
        await self._async_learn_events(now)

    async def _async_learn_events(self, now: datetime) -> None:
        """Recurring device runs in the 5-minute house load (the recorder keeps about ten days)."""
        try:
            series = await self._async_load_series(now - timedelta(days=ev.DAYS), "5minute")
        except Exception as err:  # noqa: BLE001 – only the suggestions are missing
            _LOGGER.debug("5-minute statistics unavailable: %s", err)
            return
        load = series.get(self.house_load, {})
        # energy sensors give Wh per 5 minutes, not mean W
        consumers = [{t: v * (3600 / ev.SLOT if not c.sensor else 1.0) for t, v in series.get(c.series_id, {}).items()}
                     for c in self.consumers.values()]
        tz = dt_util.get_default_time_zone()
        self.load_events = await self.hass.async_add_executor_job(ev.suggestions, load, consumers, tz) if load else None

    async def _async_day_types(self, start: datetime, end: datetime) -> dict:
        """Day type per local date from the history of the day-type entity; empty if there is none."""
        from homeassistant.components.recorder import get_instance, history

        try:
            states = await get_instance(self.hass).async_add_executor_job(
                history.state_changes_during_period, self.hass, start, end, self.day_type_entity,
                True, False, None, True)
        except Exception as err:  # noqa: BLE001 – without history the weekday decides
            _LOGGER.warning("No history for the day-type entity %s: %s", self.day_type_entity, err)
            return {}
        changes = [(s.last_changed.timestamp(), s.state) for s in states.get(self.day_type_entity, [])]
        return lf.day_types_from_states(changes, dt_util.get_default_time_zone(), start.timestamp(), end.timestamp())

    def _zone(self, now: float):
        """The local time zone, with the known day types and today's from the entity's current state."""
        tz = dt_util.get_default_time_zone()
        if not self.day_type_entity:
            return tz
        types = dict(self.day_types)
        state = self.hass.states.get(self.day_type_entity)
        if state is not None and state.state not in ("unknown", "unavailable"):
            types[dt_util.as_local(dt_util.utc_from_timestamp(now)).date()] = lf.DAY_TYPE_STATES.get(state.state, state.state)
        return lf.DayTypeZone(tz, types)

    async def _async_load_forecast(self, now: float, end: float,
                                   temps: dict[float, float] | None = None) -> lf.LoadForecast | None:
        """Load forecast with the correction from the last hours and the consumers running now."""
        if not self.house_load or self.load_profile is None:
            return None
        tz = self._zone(now)
        try:
            # from midnight on, so today's program runs can be counted
            start = min(now - (lf.K_HOURS + 2) * 3600, dt_util.start_of_local_day().timestamp())
            series = await self._async_load_series(dt_util.utc_from_timestamp(start))
        except Exception as err:  # noqa: BLE001 – without recent values the profile alone is used
            _LOGGER.debug("Recent load statistics unavailable: %s", err)
            series = {}
        base = lf.base_series(series.get(self.house_load, {}),
                              [series.get(c.series_id, {}) for c in self.consumers.values()])
        sustained = (0.0, None)
        if self.floor_profile is not None and series:
            try:
                floor = await self._async_floor_series(dt_util.utc_from_timestamp(start), series)
                sustained = lf.sustained(floor, self.floor_profile, tz, now)
            except Exception as err:  # noqa: BLE001 – without the floor only the correction factor applies
                _LOGGER.debug("Load floor statistics unavailable: %s", err)
        # the correction factor follows the events on top of a sustained load
        k = lf.correction({t: max(0.0, v - sustained[0]) for t, v in base.items()}, self.load_profile, tz, now)
        running = {sid: run for sid, c in self.consumers.items() if (run := self._run(c))}
        day_start = dt_util.start_of_local_day().timestamp()
        # mean forecast temperature per local day, for regulated consumers (partial days count with what they have)
        by_day: dict = {}
        for t, v in (temps or {}).items():
            if v is not None:
                by_day.setdefault(dt_util.as_local(dt_util.utc_from_timestamp(t)).date(), []).append(v)
        day_temp = {d: sum(v) / len(v) for d, v in by_day.items()}
        consumers = []
        for sid, c in self.consumers.items():
            m = self.consumer_models.get(sid, {})
            consumers.append(lf.Consumer(sid, c.timing, self.consumer_profiles.get(sid, lf.Profile()), c.kind,
                                         m.get("program"), m.get("session"),
                                         lf.runs_since(series.get(c.series_id, {}), day_start, now, sid in running),
                                         m.get("regulated"), day_temp))
        return lf.forecast(self.load_profile, consumers, tz, now, end, k, running, sustained)

    def _limit(self, number, entity: str | None, default: float) -> float:
        """A limit in percent: the entity's current value if set and readable, else the number."""
        state = self.hass.states.get(entity) if entity else None
        try:
            return float(state.state)
        except (AttributeError, TypeError, ValueError):
            return float(number) if number is not None else default

    def _battery(self, result: NoxcastData, now: float, day_end: float) -> dict | None:
        """All batteries as one storage, simulated with expected PV against the load forecast."""
        if not self.batteries or result.load is None:
            return None
        units = []
        for d in self.batteries.values():
            state = self.hass.states.get(d.get(CONF_SOC_SENSOR) or "")
            try:
                soc = float(state.state) / 100
            except (AttributeError, TypeError, ValueError):
                soc = None
            coupling = d.get(CONF_COUPLING, bt.COUPLING_DC)
            eff = d.get(CONF_EFFICIENCY)
            units.append(bt.Battery(
                capacity_wh=float(d.get(CONF_CAPACITY, 0)) * 1000, soc=soc,
                max_soc=self._limit(d.get(CONF_MAX_SOC), d.get(CONF_MAX_SOC_ENTITY), 100) / 100,
                min_soc=min(100.0, self._limit(d.get(CONF_MIN_SOC), d.get(CONF_MIN_SOC_ENTITY), 0) + float(d.get(CONF_RESERVE) or 0)) / 100,
                efficiency=float(eff) / 100 if eff else bt.DEFAULT_EFFICIENCY.get(coupling, 0.95),
                max_charge_w=float(d[CONF_MAX_CHARGE]) if d.get(CONF_MAX_CHARGE) else None,
                max_discharge_w=float(d[CONF_MAX_DISCHARGE]) if d.get(CONF_MAX_DISCHARGE) else None))
        storage = bt.combine(units)
        if storage is None:
            return None
        pv = dict(result.forecast.get(None, {}))
        pv.update(result.expected.get(None, {}))          # today: actual so far and the corrected rest
        load = result.load.hours
        tomorrow_end = day_end + 86400
        start = bt.charge_start(pv, load, day_end, tomorrow_end) or tomorrow_end
        sim = bt.simulate(storage, pv, load, now, start)
        out = {"sim": sim, "charge_start": start, "storage": storage, "day_end": day_end}
        hours = (result.band or {}).get("hours")
        if hours:
            # the same simulation with PV at P10 and at P90: how much surplus is lost, and when full, in a weak and a strong case
            sims = [bt.simulate(storage, {**pv, **{t: lh[i] for t, lh in hours.items()}}, load, now, start) for i in (0, 1)]
            out["band"] = {"lost_today": [round(sum(h["lost"] for t, h in s.hours.items() if t < day_end) / 1000, 3)
                                          for s in sims],
                           "full_at": [s.full_at for s in sims]}
        return out

    def _log_battery(self, battery: dict | None, local_now: datetime, now: float, day_end: float) -> None:
        """Keep the first battery prediction after the morning fix, to compare it with what happens."""
        key = local_now.date().isoformat()
        if battery is None or self._morning_fixed is None or key in self.battery_log:
            return
        self.battery_log[key] = bt.prediction(battery["sim"], battery["storage"], now, day_end, battery["charge_start"])
        oldest = (local_now.date() - timedelta(days=bt.PREDICTION_DAYS)).isoformat()
        self.battery_log = {k: v for k, v in self.battery_log.items() if k >= oldest}
        self._store.async_delay_save(self._store_data, 60)

    def run_profile(self, sid: str) -> pl.RunProfile | None:
        """A flexible consumer's run: configured values first, learned ones otherwise."""
        c, learned = self.consumers[sid], self.run_profiles.get(sid) or {}
        power, minutes = c.run_power or learned.get("power_w"), c.run_minutes or learned.get("minutes")
        return pl.RunProfile(sid, float(power), float(minutes)) if power and minutes else None

    def _plan(self, result: NoxcastData, now: float, day_end: float) -> dict[str, dict]:
        """Best windows for flexible consumers in the surplus of today and of tomorrow (in subentry order)."""
        if result.load is None:
            return {}
        runs = [r for sid, c in self.consumers.items() if c.timing == lf.TIMING_FLEXIBLE and (r := self.run_profile(sid))]
        if not runs:
            return {}
        pv = dict(result.forecast.get(None, {}))
        pv.update(result.expected.get(None, {}))
        load = result.load.hours
        surplus = {t: max(pv.get(t, 0.0) - w, 0.0) for t, w in load.items()}
        lost = {t: h["lost"] for t, h in result.battery["sim"].hours.items()} if result.battery else {}
        today = pl.plan(runs, {t: v for t, v in surplus.items() if t < day_end}, lost, now, day_end)
        tomorrow = pl.plan(runs, {t: v for t, v in surplus.items() if day_end <= t}, {}, day_end, day_end + 86400)
        return {r.name: {"run": r, "now": today[r.name]["now"], "today": today[r.name]["best"],
                         "tomorrow": tomorrow[r.name]["best"]} for r in runs}

    def _learn_band(self) -> dict:
        """Forecast band from the archive, with the clear-sky output of the current learned model. Executor."""
        def clear_wh(ts: float, ids: list[str]) -> float:
            sky = [clearsky_weather(self.site, ts)]
            return sum(forecast(self.site, self.arrays[sid].geometry, self.learned.get(sid, LearnedArray()), sky).get(ts, 0.0)
                       for sid in ids if sid in self.arrays)
        return bd.learn_band(self.forecast_archive, lambda ts: clearsky_weather(self.site, ts).ghi, clear_wh)

    def _band(self, result: NoxcastData, weather: list[WeatherHour], now: float, day_end: float) -> dict | None:
        """P10 / P90 of the total forecast: per hour from now on, per day, and for the rest of today.

        Today's measured part is certain; only the rest gets a band. Runs in the executor.
        """
        if not self.band:
            return None
        clear = {w.start: clearsky_weather(self.site, w.start).ghi for w in weather}
        kt = {w.start: w.ghi / clear[w.start] for w in weather if clear[w.start] > 0}
        total = dict(result.forecast.get(None, {}))
        total.update(result.expected.get(None, {}))
        sky = result.clear.get(None, {})                  # clear-sky output: the band never goes above it
        hour = now - now % 3600
        hours = bd.hour_band(self.band, {t: v for t, v in total.items() if t >= hour}, sky, kt)

        def clearness(ts: list[float]) -> float:
            c = sum(clear.get(t, 0.0) for t in ts)
            return sum(kt.get(t, 0.0) * clear.get(t, 0.0) for t in ts) / c if c > 0 else 0.0

        days = {}
        for offset in (1, 2):
            a, b = day_end + (offset - 1) * 86400, day_end + offset * 86400
            ts = [t for t in total if a <= t < b]
            if len(ts) >= 20 and (r := bd.day_band(self.band, sum(total[t] for t in ts),
                                                   sum(sky.get(t, total[t]) for t in ts), clearness(ts))):
                days[offset] = [round(r[0] / 1000, 3), round(r[1] / 1000, 3)]
        today = result.expected.get(None, {})
        rest_ts = [t for t in today if t + 3600 > now and t < day_end]
        rest = sum(today[t] * min(1.0, (t + 3600 - now) / 3600) for t in rest_ts)
        done = sum(today[t] for t in today if t < day_end) - sum(today[t] for t in rest_ts) + \
            sum(today[t] * (1 - min(1.0, (t + 3600 - now) / 3600)) for t in rest_ts)
        rest_sky = sum(sky.get(t, today[t]) * min(1.0, (t + 3600 - now) / 3600) for t in rest_ts)
        r = bd.day_band(self.band, rest, rest_sky, clearness(rest_ts)) if rest > 0 else (0.0, 0.0)
        rest_band = [round(r[0] / 1000, 3), round(r[1] / 1000, 3)] if r else None
        if rest_band:
            days[0] = [round(done / 1000 + rest_band[0], 3), round(done / 1000 + rest_band[1], 3)]
        return {"hours": hours, "days": days, "rest": rest_band}

    def _value(self, entity_id: str | None, table: dict[str, float]) -> float | None:
        state = self.hass.states.get(entity_id) if entity_id else None
        try:
            return float(state.state) * table.get(state.attributes.get("unit_of_measurement"), 1.0)
        except (AttributeError, TypeError, ValueError):
            return None

    def _run(self, c: ConsumerConfig) -> lf.Run | None:
        """The consumer's run now: power from its sensor or energy / elapsed time, minutes still to go."""
        power = self._value(c.sensor, POWER_UNIT_FACTORS)
        minutes = self._value(c.remaining, MINUTE_FACTORS)
        minutes = minutes if minutes and minutes > 0 else None
        if power is None and c.energy and c.elapsed:
            energy, elapsed = self._value(c.energy, ENERGY_UNIT_FACTORS), self._value(c.elapsed, MINUTE_FACTORS)
            if energy is not None and elapsed and elapsed > 0:
                power = energy / (elapsed / 60)            # mean power of the program so far
        if power is None or (power < RUNNING_W and minutes is None):
            return None
        return lf.Run(power, minutes)

    def _archive_data(self) -> dict:
        return {"days": self.forecast_archive}

    def _archive_forecast(self, weather: list[WeatherHour], result: NoxcastData,
                          alt: dict[str, tuple[dict, dict]] | None = None,
                          alt_weather: dict[str, list[WeatherHour]] | None = None) -> None:
        """Keep overwriting tomorrow's forecast; the last version before midnight is the day-ahead forecast."""
        tomorrow = dt_util.start_of_local_day() + timedelta(days=1)
        a = tomorrow.timestamp()
        b = dt_util.start_of_local_day(tomorrow.date() + timedelta(days=1)).timestamp()
        hours = {w.start: (w.ghi, w.temp) for w in weather if a <= w.start < b}
        alt = {name: ({t: g for t, g in ghi.items() if a <= t < b}, fc) for name, (ghi, fc) in (alt or {}).items()}

        def wx(series: list[WeatherHour]) -> dict[float, dict]:
            return {w.start: fc_archive.details(w.cloud, w.sun, w.wind, w.wind_dir) for w in series if a <= w.start < b}

        if fc_archive.snapshot(self.forecast_archive, tomorrow.date(), dt_util.utcnow().isoformat(), hours,
                               result.arrays, alt, wx(weather),
                               {name: wx(series) for name, series in (alt_weather or {}).items()}):
            fc_archive.prune(self.forecast_archive, dt_util.now().date() - timedelta(days=ARCHIVE_DAYS))
            self._archive_store.async_delay_save(self._archive_data, 300)

    async def async_add_exclusion(self, start: datetime, end: datetime, note: str = "") -> None:
        self.exclusions.append({"start": start.timestamp(), "end": end.timestamp(), "note": note})
        self._store.async_delay_save(self._store_data, 5)

    # ------------------------------------------------------------------ forecast

    @property
    def weather_models(self) -> list[str]:
        return list(self._option(CONF_WEATHER_MODELS) or DEFAULT_WEATHER_MODELS)

    async def _async_fetch_weather(self) -> tuple[float, list[WeatherHour], dict[str, list[WeatherHour]]]:
        """Open-Meteo forecast; when it fails, the last answer stands in for up to WEATHER_MAX_AGE."""
        try:
            mean, per_model = await async_open_meteo(self.hass, self.site.latitude, self.site.longitude,
                                                     max(self.forecast_days, WEATHER_DAYS), self.weather_models)
        except Exception as err:  # noqa: BLE001
            age = dt_util.utcnow().timestamp() - self._weather[0] if self._weather else None
            if age is None or age > WEATHER_MAX_AGE.total_seconds():
                raise
            _LOGGER.warning("Open-Meteo failed (%s); using the forecast from %.1f h ago", err, age / 3600)
            return self._weather
        fetched = (dt_util.utcnow().timestamp(), mean, per_model)
        if mean:
            self._weather = fetched
        return fetched

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
            fetched, weather, per_model = await self._async_fetch_weather()
        except Exception as err:  # noqa: BLE001 – any source failure keeps the last forecast
            raise UpdateFailed(f"Weather source failed: {err}") from err

        local_now = dt_util.now()
        day_start = dt_util.start_of_local_day().timestamp()
        day_end = dt_util.start_of_local_day(local_now.date() + timedelta(days=1)).timestamp()
        horizon = dt_util.start_of_local_day(local_now.date() + timedelta(days=self.forecast_days)).timestamp()
        outlook = [w for w in weather if w.start >= day_start]
        weather = [w for w in outlook if w.start < horizon]
        if not weather:
            raise UpdateFailed("Weather source returned no hours for the forecast horizon")
        # each model on its own, only for the archive (tomorrow) and only when several are averaged
        tomorrow = (day_end, dt_util.start_of_local_day(local_now.date() + timedelta(days=2)).timestamp())
        models = {m: [w for w in hours if tomorrow[0] <= w.start < tomorrow[1]]
                  for m, hours in per_model.items()} if len(per_model) > 1 else {}

        result = NoxcastData(updated=dt_util.utcnow(), day_start=day_start,
                             weather_fetched=dt_util.utc_from_timestamp(fetched), outlook=outlook)
        alt_forecasts: dict[str, dict[str, dict[float, float]]] = {m: {} for m in models}
        for sub_id, arr in self.arrays.items():
            learned = self.learned.get(sub_id, LearnedArray())
            fresh = await self.hass.async_add_executor_job(forecast, self.site, arr.geometry, learned, weather)
            # keep earlier values for past hours the source no longer reports
            merged = {t: v for t, v in self._hours.get(sub_id, {}).items() if t >= day_start}
            merged.update(fresh)
            self._hours[sub_id] = merged
            result.forecast[sub_id] = dict(sorted(merged.items()))
            for m, hours in models.items():
                alt_forecasts[m][sub_id] = await self.hass.async_add_executor_job(
                    forecast, self.site, arr.geometry, learned, hours)

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
        result.actual, result.weather = actual, weather
        temps = {w.start: w.temp for w in weather}
        clear_weather = [clearsky_weather(self.site, t, temps.get(t))
                         for t in sorted(result.forecast.get(next(iter(self.arrays), None), {}))]
        for sub_id, arr in self.arrays.items():
            today = {t: v for t, v in result.forecast[sub_id].items() if t < day_end}
            result.morning[sub_id] = self._morning.get(sub_id) or today
            result.clear[sub_id] = await self.hass.async_add_executor_job(
                forecast, self.site, arr.geometry, self.learned.get(sub_id, LearnedArray()), clear_weather)
            clear = {t: v for t, v in result.clear[sub_id].items() if t < day_end}
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
            corrected = {t: v for t, v in result.expected[sub_id].items() if t > now}
            self._held[sub_id] = held_forecast(self._held.get(sub_id) or result.morning[sub_id], corrected, now,
                                               day_start)
            result.held[sub_id] = self._held[sub_id]
            result.effects[sub_id] = await self._async_effects(sub_id, arr, weather, today,
                                                               result.expected[sub_id],
                                                               day_start, day_end)
        for series in (result.forecast, result.morning, result.expected, result.clear):
            total: dict[float, float] = {}
            for hours in list(series.values()):
                for t, wh in hours.items():
                    total[t] = total.get(t, 0.0) + wh
            series[None] = dict(sorted(total.items()))

        if local_now.hour in fc_archive.HISTORY_HOURS and fc_archive.add_history(
                self.forecast_archive, local_now.date(), local_now.replace(minute=0, second=0, microsecond=0).timestamp(),
                result.expected[None]):
            self._archive_store.async_delay_save(self._archive_data, 300)

        result.load = await self._async_load_forecast(
            now, dt_util.start_of_local_day(local_now.date() + timedelta(days=2)).timestamp(), temps)
        result.band = await self.hass.async_add_executor_job(self._band, result, weather, now, day_end)
        result.battery = self._battery(result, now, day_end)
        self._log_battery(result.battery, local_now, now, day_end)
        result.plan = self._plan(result, now, day_end)

        alt = {m: ({w.start: w.ghi for w in hours}, alt_forecasts[m]) for m, hours in models.items()}
        self._archive_forecast(weather, result, alt, models)
        self._store.async_delay_save(self._store_data, 60)
        return result

    async def _async_effects(self, sub_id: str, arr: ArrayConfig, weather: list[WeatherHour],
                             today: dict[float, float], expected: dict[float, float],
                             day_start: float, day_end: float) -> dict:
        """Shade loss and reflection gain of today, scaled like the expected output, with significant periods."""
        learned = self.learned.get(sub_id)
        if learned is None:
            return {"loss": {}, "gain": {}, "periods": []}
        hours = [w for w in weather if day_start <= w.start < day_end]
        loss, gain = await self.hass.async_add_executor_job(hourly_effects, self.site, arr.geometry, learned, hours)

        def scaled(series: dict[float, float]) -> dict[float, float]:
            return {t: v * (expected.get(t, 0.0) / today[t] if today.get(t) else 1.0) for t, v in series.items()}

        loss, gain = scaled(loss), scaled(gain)
        periods = await self.hass.async_add_executor_job(
            significant_periods, self.site, arr.geometry, learned, day_start, loss, gain, sum(expected.values()))
        return {"loss": loss, "gain": gain, "periods": periods}

    @property
    def forecast_available(self) -> bool:
        """A forecast exists and is recent enough; short weather outages do not blank the sensors."""
        data = self.data
        return bool(data and data.updated and dt_util.utcnow() - data.updated < STALE_AFTER)


RECENT_MIN_SLOTS_HOUR = 10       # 5-minute values needed to stand in for a just-ended hour
RECENT_MIN_SLOTS_RUNNING = 3     # 5-minute values (15 minutes) before the running hour counts
RUNNING_W = 20                   # a consumer drawing at least this much counts as running


_WEATHER_FIELDS = ("start", "ghi", "dni", "dhi", "temp", "cloud", "sun", "wind", "wind_dir", "precip", "code", "humidity")


def _weather_row(w: WeatherHour) -> list:
    return [getattr(w, f) for f in _WEATHER_FIELDS]


def _weather_hour(row: list) -> WeatherHour:
    return WeatherHour(**dict(zip(_WEATHER_FIELDS, row)))


def _sum_series(series: list[dict[float, float]]) -> dict[float, float]:
    """Sum several sensors per hour; hours where any sensor is missing are dropped."""
    if not series or not all(series):
        return {}
    common = set(series[0])
    for s in series[1:]:
        common &= set(s)
    return {t: sum(s[t] for s in series) for t in common}


def _measured_forecast(site, geo, learned: LearnedArray, hours: list[tuple[float, float, float | None]]) -> dict[float, float]:
    """The model on measured weather: what the forecast would have been with a perfect weather forecast."""
    return forecast(site, geo, learned, [WeatherHour(t, g, None, None, temp) for t, g, temp in hours])


def _explain(site, geo, learned: LearnedArray, samples, air_temp, satellite_ghi, year: int,
             steady: set[float] | None = None) -> None:
    """Panel explanations of a learned array; a failure never stops the learning."""
    try:
        learned.months = ac.month_balance(site, geo, learned, year, air_temp)
        learned.efficiency_months = ac.efficiency_by_month(site, geo, learned, samples, satellite_ghi, air_temp, steady)
        learned.geometry_check = ac.check_geometry(site, geo, learned, samples, satellite_ghi, air_temp, steady)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Array check failed")
