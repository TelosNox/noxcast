"""Coordinator: schedules learning and updates, fetches from Home Assistant and the web, keeps the state.

The computations live in pure modules (learning.py, forecasting.py and below); this is the shell
around them: statistics, weather, current states, stores, and turning the config into dataclasses.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import time
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    Series,
    TOTAL,
    CONF_API_KEY, CONF_ARCHIVE_TEMPERATURE, CONF_ARCHIVE_URL, CONF_SATELLITE, CONF_SATELLITE_MODEL, CONF_SATELLITE_URL,
    DEFAULT_ARCHIVE_URL, DEFAULT_SATELLITE_MODEL, DEFAULT_SATELLITE_URL, CONF_WEATHER_MODELS, DEFAULT_WEATHER_MODELS,
    WEATHER_DAYS, WEATHER_MAX_AGE, ARCHIVE_DAYS, CONF_AZIMUTH, CONF_HEATING, CONF_ORIENTATIONS, CONF_TEMP_COEFF, CONF_NAME, CONF_PEAK_POWER, CONF_POWER_SENSORS, CONF_SOC_FULL, CONF_SOC_SENSORS,
    CONF_TILT, CONF_VALID_SINCE, DEFAULT_SOC_FULL, DOMAIN,
    CONF_FORECAST_DAYS, FORECAST_DAYS, HISTORY_YEARS, MAX_FORECAST_DAYS, LEARN_TIME, POWER_UNIT_FACTORS, STALE_AFTER, STORAGE_VERSION, SUBENTRY_ARRAY, UPDATE_MINUTES,
    CONF_HOUSE_LOAD, CONF_GRID, CONF_GRID_INVERT, CONF_DAY_TYPE, CONF_POWER_SENSOR, CONF_TIMING, SUBENTRY_CONSUMER,
    CONF_ELAPSED_TIME, CONF_ENERGY_SENSOR, CONF_REMAINING_TIME, ENERGY_UNIT_FACTORS, MINUTE_FACTORS,
    CONF_CAPACITY, CONF_COUPLING, CONF_EFFICIENCY, CONF_MAX_CHARGE, CONF_MAX_DISCHARGE, CONF_MAX_SOC, CONF_MAX_SOC_ENTITY,
    CONF_MIN_SOC, CONF_MIN_SOC_ENTITY, CONF_RESERVE, CONF_SOC_SENSOR, SUBENTRY_BATTERY, CONF_RUN_MINUTES, CONF_RUN_POWER, CONF_KIND,
)
from . import archive as fc_archive
from . import backtest as pv_backtest
from . import band as bd
from . import fine as fn
from . import events as ev
from . import flows as fl
from . import battery as bt
from . import forecasting as fc
from . import learning as ln
from . import load as lf
from . import plan as pl
from .forecasting import NoxcastData
from .learning import ArrayConfig, ConsumerConfig
from .model import HEATING_K, LEARNED_VERSION, ArrayGeometry, LearnedArray, Plane, WeatherHour
from .solar import Site, angle_of_incidence, sun_position
from .sources import (
    async_archive_irradiance, async_archive_temperature, async_hourly_statistics,
)
from .sources import async_open_meteo, async_power_factors

_LOGGER = logging.getLogger(__name__)

__all__ = ["ArrayConfig", "ConsumerConfig", "NoxcastConfigEntry", "NoxcastCoordinator", "NoxcastData"]


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
        self.pv_backtest: dict | None = None         # last result of backtest.run; never read by the forecast
        self._backtest_task: asyncio.Task | None = None
        self.learned: dict[str, LearnedArray] = {}
        self.exclusions: list[dict] = []          # {"start": ts, "end": ts, "note": str}
        self.last_learning: datetime | None = None
        self.last_learning_seconds: float | None = None  # duration of the last successful run, fetches included
        self.last_learning_error: str | None = None
        self.archive_errors: dict[str, str] = {}         # archive name -> error of the last learning run
        self._today = fc.Today()                  # today's hours, morning fix and held values between updates
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
            arrays = stored.get("arrays", {}) if stored.get("learned_version", 1) == LEARNED_VERSION else {}
            for sub_id, data in arrays.items():
                if sub_id in self.arrays and data.get("geometry") == self._geometry_key(sub_id):
                    self.learned[sub_id] = LearnedArray.from_dict(data["learned"])
            self.band = bd.valid(stored.get("band"))
            self.pv_backtest = pv_backtest.valid(stored.get("pv_backtest"))
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
            self.last_learning_seconds = stored.get("last_learning_seconds")
            day_start = dt_util.start_of_local_day().timestamp()
            for sub_id, hours in stored.get("hours", {}).items():
                if sub_id in self.arrays:
                    self._today.hours[sub_id] = {float(t): float(v) for t, v in hours.items() if float(t) >= day_start}
            if cached := stored.get("weather"):
                self._weather = (float(cached["fetched"]), [_weather_hour(r) for r in cached["mean"]], {})
            morning = stored.get("morning") or {}
            fixed = dt_util.parse_datetime(morning.get("fixed") or "")
            if fixed and dt_util.as_local(fixed).date() == dt_util.now().date():
                self._today.morning_fixed = fixed
                self._today.morning = {sid: {float(t): float(v) for t, v in hours.items()}
                                       for sid, hours in morning.get("hours", {}).items() if sid in self.arrays}
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Stored Noxcast state could not be read; relearning from history", exc_info=True)
            self.learned, self._today.hours = {}, {}
        try:
            self.forecast_archive = fc_archive.from_store(await self._archive_store.async_load())
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
        await self.async_learn(backtest=True)

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
        today = self._today
        return {
            "band": self.band,
            "pv_backtest": self.pv_backtest,
            "battery_log": self.battery_log,
            "exclusions": self.exclusions,
            "last_learning": self.last_learning.isoformat() if self.last_learning else None,
            "last_learning_seconds": self.last_learning_seconds,
            "learned_version": LEARNED_VERSION,
            "arrays": {sid: {"geometry": self._geometry_key(sid), "learned": la.as_dict()}
                       for sid, la in self.learned.items() if sid in self.arrays},
            # today's hours survive a restart even if the weather source no longer reports past hours
            "hours": {sid: {str(t): v for t, v in hours.items()} for sid, hours in today.hours.items()},
            "load": {"key": self._load_key(), "base": self.load_profile.as_dict(),
                     "floor": self.floor_profile.as_dict() if self.floor_profile else None,
                     "consumers": {sid: p.as_dict() for sid, p in self.consumer_profiles.items()},
                     "backtest": self.load_backtest, "runs": self.run_profiles,
                     "models": {sid: {k: m.as_dict() for k, m in ms.items() if m} for sid, ms in self.consumer_models.items()},
                     "events": self.load_events}
            if self.load_profile else None,
            "morning": {"fixed": today.morning_fixed.isoformat() if today.morning_fixed else None,
                        "hours": {sid: {str(t): v for t, v in h.items()} for sid, h in today.morning.items()}},
            # only the mean: the single models serve the archive, which can skip an outage
            "weather": {"fetched": self._weather[0], "mean": [_weather_row(w) for w in self._weather[1]]}
            if self._weather else None,
        }

    # ------------------------------------------------------------------ learning

    async def async_learn(self, backtest: bool = False) -> bool:
        """Relearn all arrays from history. Keeps the previous state on failure.

        backtest: afterwards start the backtest in the background (the nightly run), on the same history.
        """
        if not self.arrays:
            return True
        async with self._learn_lock:
            start = time.monotonic()
            try:
                inputs = await self._async_learn()
            except Exception as err:  # noqa: BLE001
                self.last_learning_error = str(err) or type(err).__name__
                _LOGGER.exception("Noxcast learning failed; keeping the previous learned state")
                return False
        self.last_learning_error = None
        self.last_learning_seconds = round(time.monotonic() - start, 1)
        self._store.async_delay_save(self._store_data, 5)
        await self.async_request_refresh()
        if backtest and (self._backtest_task is None or self._backtest_task.done()):
            self._backtest_task = self.config_entry.async_create_background_task(
                self.hass, self._async_backtest(*inputs), "noxcast backtest")
        return True

    async def _async_backtest(self, history: ln.PvHistory, now: float, soc_full: float,
                              exclusions: list[tuple[float, float]]) -> None:
        """Compare the model with the past (backtest.py); a failure leaves the last result in place."""
        try:
            result = await self.hass.async_add_executor_job(
                self._run_backtest, dict(self.learned), dict(self.forecast_archive), history, now, soc_full, exclusions)
        except Exception:  # noqa: BLE001 – learning and forecast do not depend on it
            _LOGGER.exception("Noxcast backtest failed; keeping the last result")
            return
        self.pv_backtest = result
        self._store.async_delay_save(self._store_data, 5)

    def _run_backtest(self, learned: dict[str, LearnedArray], archive: dict, history: ln.PvHistory, now: float,
                      soc_full: float, exclusions: list[tuple[float, float]]) -> dict:
        """Executor. On copies: whatever happens here cannot reach the learned state or the archive."""
        start = time.monotonic()
        result = pv_backtest.run(self.site, self.arrays, copy.deepcopy(learned), history, copy.deepcopy(archive), now,
                                 soc_full, exclusions, dt_util.get_default_time_zone())
        seconds = round(time.monotonic() - start, 1)
        _LOGGER.info("PV backtest took %.1f s: replay %s, hold-out %s", seconds, result["replay"]["total"],
                     result["holdout"]["total"])
        return {**result, "seconds": seconds}

    async def _async_learn(self) -> tuple[ln.PvHistory, float, float, list[tuple[float, float]]]:
        """Learn everything; returns what the backtest needs: history, time, full state of charge, exclusions."""
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
        soc = await async_hourly_statistics(self.hass, list(options.get(CONF_SOC_SENSORS) or []), start, "max")
        air_temp, satellite_ghi = await self._async_archives(start, now)
        history = ln.PvHistory(power, power_min, power_max, factors, ln.highest(soc), air_temp, satellite_ghi)
        soc_full = float(options.get(CONF_SOC_FULL, DEFAULT_SOC_FULL))
        exclusions = [(e["start"], e["end"]) for e in self.exclusions]
        learned = await self.hass.async_add_executor_job(
            ln.learn_arrays, self.site, self.arrays, history, now.timestamp(), soc_full, exclusions)
        await self.hass.async_add_executor_job(
            ln.record_archive, self.forecast_archive, self.site, self.arrays, learned, history, now.timestamp(), soc_full)
        self.learned.update(learned)          # applied in one step on the event loop
        self.band = await self.hass.async_add_executor_job(self._learn_band)
        if self.house_load:
            await self._async_learn_load(now, air_temp)
        self.last_learning = now
        self._archive_store.async_delay_save(self._archive_data, 5)
        return history, now.timestamp(), soc_full, exclusions

    def _learn_band(self) -> dict:
        """Forecast band from the archive, with the clear-sky output of the current learned model. Executor."""
        return bd.from_archive(self.forecast_archive, self.site, {sid: a.geometry for sid, a in self.arrays.items()},
                               self.learned)

    async def _async_load_series(self, start: datetime, period: str = "hour") -> dict[str, Series]:
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

    async def _async_load_floor(self, start: datetime) -> Series:
        """Hourly minimum of the house load in W (the base-load floor before consumers are subtracted)."""
        factor = (await async_power_factors(self.hass, [self.house_load])).get(self.house_load, 1.0)
        stats = await async_hourly_statistics(self.hass, [self.house_load], start, "min")
        return {t: v * factor for t, v in stats.get(self.house_load, {}).items() if v is not None}

    async def async_day_flows(self, start: float, end: float, pv: Series) -> dict | None:
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

    async def _async_learn_load(self, now: datetime, air_temp: Series | None = None) -> None:
        """Base-load and consumer profiles from the recent weeks, plus a replay of the last days."""
        days = lf.PROFILE_DAYS + lf.BACKTEST_DAYS + 1
        series = await self._async_load_series(now - timedelta(days=days))
        if not series.get(self.house_load):
            _LOGGER.warning("No long-term statistics for the house load %s; load forecast disabled", self.house_load)
            return
        tz = zone = dt_util.get_default_time_zone()
        if self.day_type_entity:
            self.day_types = await self._async_day_types(now - timedelta(days=days), now)
            zone = lf.DayTypeZone(tz, self.day_types)
        floor = await self._async_load_floor(now - timedelta(days=days))
        # regulated consumers: a year of history, so the relation to the temperature spans the seasons
        year = None
        if air_temp and any(c.kind == lf.KIND_REGULATED and c.timing == lf.TIMING_FIXED for c in self.consumers.values()):
            year = await self._async_load_series(now - timedelta(days=lf.REGULATED_DAYS))
        try:
            fine = await self._async_load_series(now - timedelta(days=ev.DAYS), "5minute")
        except Exception as err:  # noqa: BLE001 – only the suggestions are missing
            _LOGGER.debug("5-minute statistics unavailable: %s", err)
            fine = None
        history = ln.LoadHistory(series, floor, year, fine)
        learned = await self.hass.async_add_executor_job(
            ln.learn_load, self.house_load, self.consumers, history, zone, tz, dt_util.start_of_local_day().timestamp(),
            now.timestamp(), air_temp)
        self.load_profile, self.floor_profile = learned.profile, learned.floor
        self.consumer_profiles, self.consumer_models = learned.consumers, learned.models
        self.load_backtest, self.run_profiles = learned.backtest, learned.runs
        if fine is not None:                  # unavailable: the last suggestions stay
            self.load_events = learned.events

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
                                   temps: Series | None = None) -> lf.LoadForecast | None:
        """Load forecast with the correction from the last hours and the consumers running now."""
        if not self.house_load or self.load_profile is None:
            return None
        try:
            # from midnight on, so today's program runs can be counted
            start = min(now - (lf.K_HOURS + 2) * 3600, dt_util.start_of_local_day().timestamp())
            series = await self._async_load_series(dt_util.utc_from_timestamp(start))
        except Exception as err:  # noqa: BLE001 – without recent values the profile alone is used
            _LOGGER.debug("Recent load statistics unavailable: %s", err)
            series = {}
        floor = None
        if self.floor_profile is not None and series:
            try:
                floor = await self._async_load_floor(dt_util.utc_from_timestamp(start))
            except Exception as err:  # noqa: BLE001 – without the floor only the correction factor applies
                _LOGGER.debug("Load floor statistics unavailable: %s", err)
        running = {sid: run for sid, c in self.consumers.items() if (run := self._run(c))}
        return fc.load(self.house_load, self.consumers, self.load_profile, self.floor_profile, self.consumer_profiles,
                       self.consumer_models, series, floor, running, self._zone(now), dt_util.get_default_time_zone(),
                       now, end, dt_util.start_of_local_day().timestamp(), temps)

    def _limit(self, number, entity: str | None, default: float) -> float:
        """A limit in percent: the entity's current value if set and readable, else the number."""
        state = self.hass.states.get(entity) if entity else None
        try:
            return float(state.state)
        except (AttributeError, TypeError, ValueError):
            return float(number) if number is not None else default

    def _battery_units(self) -> list[bt.Battery]:
        """The configured batteries with their current state of charge and limits."""
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
        return units

    def _log_battery(self, battery: dict | None, local_now: datetime, now: float) -> None:
        """Keep the first battery prediction after the morning fix, to compare it with what happens."""
        if battery is None or self._today.morning_fixed is None:
            return
        if (log := bt.remember(self.battery_log, local_now.date(), battery, now)) is not None:
            self.battery_log = log
            self._store.async_delay_save(self._store_data, 60)

    def run_profile(self, sid: str) -> pl.RunProfile | None:
        """A flexible consumer's run: configured values first, learned ones otherwise."""
        c = self.consumers[sid]
        return pl.run_profile(sid, c.run_power, c.run_minutes, self.run_profiles.get(sid))

    def _value(self, entity_id: str | None, table: dict[str, float]) -> float | None:
        state = self.hass.states.get(entity_id) if entity_id else None
        try:
            return float(state.state) * table.get(state.attributes.get("unit_of_measurement"), 1.0)
        except (AttributeError, TypeError, ValueError):
            return None

    def _run(self, c: ConsumerConfig) -> lf.Run | None:
        """The consumer's run now: power from its sensor or energy / elapsed time, minutes still to go."""
        return lf.current_run(self._value(c.sensor, POWER_UNIT_FACTORS), self._value(c.remaining, MINUTE_FACTORS),
                              self._value(c.energy, ENERGY_UNIT_FACTORS), self._value(c.elapsed, MINUTE_FACTORS))

    def _archive_data(self) -> dict:
        return fc_archive.to_store(self.forecast_archive)

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
                                  ) -> tuple[dict[str, Series], dict[str, tuple[float, float, float]]]:
        """Today's measured hours per array and the running hour (see forecasting.today_actual)."""
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
        return fc.today_actual(self.arrays, power, recent, self._factors, day_start, now)

    async def _async_update_data(self) -> NoxcastData:
        try:
            fetched, weather, per_model = await self._async_fetch_weather()
        except Exception as err:  # noqa: BLE001 – any source failure keeps the last forecast
            raise UpdateFailed(f"Weather source failed: {err}") from err

        utc_now = dt_util.utcnow()
        local_now = dt_util.as_local(utc_now)
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

        now = utc_now.timestamp()
        actual, running = await self._async_today_actual(day_start, now)
        result, self._today, alt_forecasts = await self.hass.async_add_executor_job(
            fc.pv, self.site, self.arrays, self.learned, weather, models, self._today, actual, running, utc_now,
            local_now, day_start, day_end)
        result.updated, result.weather_fetched = utc_now, dt_util.utc_from_timestamp(fetched)
        result.outlook = outlook

        if local_now.hour in fc_archive.HISTORY_HOURS and fc_archive.add_history(
                self.forecast_archive, local_now.date(), local_now.replace(minute=0, second=0, microsecond=0).timestamp(),
                result.expected[TOTAL]):
            self._archive_store.async_delay_save(self._archive_data, 300)

        result.load = await self._async_load_forecast(
            now, dt_util.start_of_local_day(local_now.date() + timedelta(days=2)).timestamp(),
            {w.start: w.temp for w in weather})
        runs = [r for sid, c in self.consumers.items() if c.timing == lf.TIMING_FLEXIBLE and (r := self.run_profile(sid))]
        await self.hass.async_add_executor_job(
            fc.surplus, result, self.site, self.band, self._battery_units(), runs, now, day_end)
        self._log_battery(result.battery, local_now, now)

        alt = {m: ({w.start: w.ghi for w in hours}, alt_forecasts[m]) for m, hours in models.items()}
        self._archive_forecast(weather, result, alt, models)
        self._store.async_delay_save(self._store_data, 60)
        return result

    @property
    def forecast_available(self) -> bool:
        """A forecast exists and is recent enough; short weather outages do not blank the sensors."""
        data = self.data
        return bool(data and data.updated and dt_util.utcnow() - data.updated < STALE_AFTER)


_WEATHER_FIELDS = ("start", "ghi", "dni", "dhi", "temp", "cloud", "sun", "wind", "wind_dir", "precip", "code", "humidity")


def _weather_row(w: WeatherHour) -> list:
    return [getattr(w, f) for f in _WEATHER_FIELDS]


def _weather_hour(row: list) -> WeatherHour:
    return WeatherHour(**dict(zip(_WEATHER_FIELDS, row)))
