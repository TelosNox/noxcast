"""Coordinator: schedules learning and updates, fetches from Home Assistant and the web, keeps the state.

The computations live in pure modules (learning.py, forecasting.py and below); this is the shell
around them: statistics, weather, current states, stores, and turning the config into dataclasses.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import random
import time
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    Series,
    TOTAL,
    CONF_API_KEY, CONF_ARCHIVE_TEMPERATURE, CONF_ARCHIVE_URL, CONF_SATELLITE, CONF_SATELLITE_MODEL, CONF_SATELLITE_URL,
    DEFAULT_ARCHIVE_URL, DEFAULT_SATELLITE_MODEL, DEFAULT_SATELLITE_URL, CONF_WEATHER_MODELS, DEFAULT_WEATHER_MODELS,
    WEATHER_DAYS, WEATHER_MAX_AGE, WEATHER_RETRY, ARCHIVE_DAYS, ENSEMBLE_INTERVAL, EVAL_DAYS, ENSEMBLE_MODELS, ERROR_DAYS, CONF_AZIMUTH, CONF_HEATING, CONF_ORIENTATIONS, CONF_TEMP_COEFF, CONF_NAME, CONF_PEAK_POWER, CONF_POWER_SENSORS, CONF_SOC_FULL, CONF_SOC_SENSORS, CONF_SENSOR_LIMITS,
    CONF_TILT, CONF_VALID_SINCE, DEFAULT_SOC_FULL, DOMAIN,
    CONF_FORECAST_DAYS, FORECAST_DAYS, HISTORY_YEARS, MAX_FORECAST_DAYS, LEARN_TIME, POWER_UNIT_FACTORS, STALE_AFTER, STORAGE_VERSION, SUBENTRY_ARRAY, UPDATE_MINUTES,
    CONF_HOUSE_LOAD, CONF_GRID, CONF_GRID_INVERT, CONF_DAY_TYPE, CONF_POWER_SENSOR, CONF_TIMING, SUBENTRY_CONSUMER,
    CONF_ELAPSED_TIME, CONF_ENERGY_SENSOR, CONF_REMAINING_TIME, ENERGY_UNIT_FACTORS, MINUTE_FACTORS,
    CONF_CAPACITY, CONF_COUPLING, CONF_EFFICIENCY, CONF_MAX_CHARGE, CONF_MAX_DISCHARGE, CONF_MAX_SOC, CONF_MAX_SOC_ENTITY,
    CONF_MIN_SOC, CONF_MIN_SOC_ENTITY, CONF_RESERVE, CONF_SOC_SENSOR, SUBENTRY_BATTERY, CONF_BATTERY_ARRAY, CONF_OUTPUT_LIMIT, CONF_RUN_MINUTES, CONF_RUN_POWER, CONF_KIND, CONF_START_SHARE,
    COMPARE_COOLDOWN, COMPARE_DAYS, COMPARE_PAUSE, WEATHER_MODEL_OPTIONS,
)
from . import archive as fc_archive
from . import backtest as pv_backtest
from . import band as bd
from . import fine as fn
from . import events as ev
from . import explain
from . import flows as fl
from . import battery as bt
from . import curtailment as ct
from . import installation as inst
from . import forecasting as fc
from . import hindcast as hc
from . import learning as ln
from . import load as lf
from . import plan as pl
from . import plateau as pt
from .forecasting import NoxcastData
from .learning import ArrayConfig, ConsumerConfig
from .model import HEATING_K, LEARNED_VERSION, ArrayGeometry, LearnedArray, Plane, WeatherHour
from .solar import Site, angle_of_incidence, sun_position
from .sources import (
    async_archive_irradiance, async_archive_temperature, async_hourly_statistics,
)
from .sources import SourceError, async_ensemble, async_open_meteo, async_power_factors, async_previous_runs

_LOGGER = logging.getLogger(__name__)

__all__ = ["ArrayConfig", "ConsumerConfig", "NoxcastConfigEntry", "NoxcastCoordinator", "NoxcastData"]


def consumer_configs(entry: ConfigEntry) -> dict[str, ConsumerConfig]:
    return {sub_id: ConsumerConfig(sub_id, sub.data[CONF_NAME], sub.data.get(CONF_POWER_SENSOR) or None,
                                   sub.data.get(CONF_TIMING, lf.TIMING_FIXED), sub.data.get(CONF_ENERGY_SENSOR) or None,
                                   sub.data.get(CONF_REMAINING_TIME) or None, sub.data.get(CONF_ELAPSED_TIME) or None,
                                   float(sub.data[CONF_RUN_POWER]) if sub.data.get(CONF_RUN_POWER) else None,
                                   float(sub.data[CONF_RUN_MINUTES]) if sub.data.get(CONF_RUN_MINUTES) else None,
                                   sub.data.get(CONF_KIND, lf.KIND_PROFILE),
                                   float(sub.data[CONF_START_SHARE]) if sub.data.get(CONF_START_SHARE) is not None else None)
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
            temp_coeff=float(d[CONF_TEMP_COEFF]) / 100 if d.get(CONF_TEMP_COEFF) is not None else None,
            limits=tuple((s, float(w)) for s, w in sorted((d.get(CONF_SENSOR_LIMITS) or {}).items())
                         if s in d[CONF_POWER_SENSORS] and w is not None and float(w) > 0))
    return configs


def installation_of(entry: ConfigEntry) -> inst.Installation:
    """The installation as configured: the arrays' sensors with their limits, the house's batteries."""
    arrays, limits = {}, {}
    for sub_id, sub in entry.subentries.items():
        if sub.subentry_type == SUBENTRY_ARRAY:
            arrays[sub_id] = list(sub.data[CONF_POWER_SENSORS])
            limits.update(sub.data.get(CONF_SENSOR_LIMITS) or {})
    return inst.build(arrays, limits, entry.data.get(CONF_SOC_SENSORS) or [],
                      float(entry.data.get(CONF_SOC_FULL, DEFAULT_SOC_FULL)))


def weather_days(forecast_days: int) -> int:
    """Days to ask Open-Meteo for. It counts days in UTC, so the last local day would end after its data west
    of UTC, and east of UTC in the hours after local midnight: one more day keeps it complete. The PV forecast
    stops at its horizon and the weather entity at a week, so the extra day shows nowhere."""
    return max(forecast_days, WEATHER_DAYS) + 1


class NoxcastCoordinator(DataUpdateCoordinator[NoxcastData]):
    """Single writer for learned state; learning runs in the executor on immutable inputs."""

    config_entry: NoxcastConfigEntry

    def __init__(self, hass: HomeAssistant, entry: NoxcastConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self.site = Site(hass.config.latitude, hass.config.longitude, float(hass.config.elevation or 0))
        self.arrays = array_configs(entry)
        self.installation = installation_of(entry)
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
        self.forecast_error: dict | None = None      # archive.error_report, renewed when actuals are added
        self.load_backtest: dict | None = None
        self.pv_backtest: dict | None = None         # last result of backtest.run; never read by the forecast
        self._backtest_task: asyncio.Task | None = None
        self.model_compare: dict | None = None       # last successful weather model comparison (hindcast.run)
        # the running or last failed comparison: {"running", "done", "total", "error", "limited"}
        self.compare_state: dict = {"running": False, "done": 0, "total": 0, "error": None, "limited": False}
        self._compare_task: asyncio.Task | None = None
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
        self._weather_retry = None                # cancels the pending retry after a failed fetch
        self._ensemble: tuple[float, dict] = (0.0, {})   # (fetched, async_ensemble answer), only for the archive
        self._weather_retrying = False            # the refresh running now is that retry
        self._weather_warned = False              # the current outage is in the log already
        self._learn_lock = asyncio.Lock()
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        # separate file: larger and written less often than the learned state
        self._archive_store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.archive")
        self.forecast_archive: dict = {}
        # 5-minute means per power sensor for sharper shading later (fine.py); bounded to three years
        self._fine_store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.fine")
        self.fine: dict = {}
        self.battery_log: dict[str, dict] = {}       # local date -> the morning's battery prediction
        # power limits seen in the measurements (plateau.py): {"detected": {sensor: Plateau.as_dict()},
        # "dismissed": {sensor: W the user said is no limit}}; only suggested, see issues.py
        self.limits: dict = {"detected": {}, "dismissed": {}}

    # ------------------------------------------------------------------ lifecycle

    async def async_load(self) -> None:
        """Restore learned state. A damaged store is ignored – everything can be relearned from history."""
        stored: dict = {}
        try:
            stored = await self._store.async_load() or {}
            self.exclusions = [e for e in stored.get("exclusions", []) if {"start", "end"} <= set(e)]
            arrays = stored.get("arrays", {}) if stored.get("learned_version", 1) == LEARNED_VERSION else {}
            for sub_id, data in arrays.items():
                if sub_id in self.arrays and data.get("geometry") == self._geometry_key(sub_id):
                    self.learned[sub_id] = LearnedArray.from_dict(data["learned"])
            self.band = bd.valid(stored.get("band"))
            self.pv_backtest = pv_backtest.valid(stored.get("pv_backtest"))
            self.model_compare = hc.valid(stored.get("model_compare"))
            self.battery_log = dict(stored.get("battery_log") or {})
            limits = stored.get("limits") or {}
            self.limits = {"detected": dict(limits.get("detected") or {}),
                           "dismissed": {s: float(w) for s, w in (limits.get("dismissed") or {}).items()}}
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
        # only for the day view: unreadable steps must not cost what was learned
        if self._today.morning_fixed is not None:
            try:
                self._today.morning_steps = {
                    sid: {float(t): explain.unpacked(v) for t, v in hours.items()}
                    for sid, hours in (stored.get("morning") or {}).get("steps", {}).items() if sid in self.arrays}
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Stored derivation of the morning forecast could not be read", exc_info=True)
                self._today.morning_steps = {}
            try:
                late = {sid: dt_util.parse_datetime(v) for sid, v in ((stored.get("morning") or {}).get("late") or {}).items()}
                self._today.morning_late = {sid: t for sid, t in late.items() if t and sid in self._today.morning}
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Stored fix times of arrays added later could not be read", exc_info=True)
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
        await self._async_forecast_error()

        entry = self.config_entry
        entry.async_on_unload(async_track_time_change(
            self.hass, self._async_nightly, hour=LEARN_TIME[0], minute=LEARN_TIME[1], second=0))
        # aligned to the clock, so day change (00:00) and morning fix (06:00) happen on time
        entry.async_on_unload(async_track_time_change(
            self.hass, self._async_scheduled_refresh, minute=UPDATE_MINUTES, second=0))
        entry.async_on_unload(async_at_started(self.hass, self._async_started))
        entry.async_on_unload(self._cancel_weather_retry)

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
        bounds = [(day, dt_util.start_of_local_day(day).timestamp(),
                   dt_util.start_of_local_day(day + timedelta(days=1)).timestamp()) for day in days]
        # a sun position per 5-minute step and plane: after a few missed days too much for the event loop
        steps = await self.hass.async_add_executor_job(self._fine_steps, stats, dict(self._factors), bounds)
        for day, sensor, day_steps in steps:
            fn.add_day(self.fine, day, sensor, day_steps)
        fn.prune(self.fine, today - timedelta(days=fn.FINE_DAYS), set(sensor_ids))
        self._fine_store.async_delay_save(lambda: {"days": self.fine}, 60)

    def _fine_steps(self, stats: dict[str, Series], factors: dict[str, float],
                    bounds: list[tuple[date, float, float]]) -> list[tuple[date, str, dict | None]]:
        """Executor. The useful 5-minute periods per day and sensor; the archive itself is changed on the event loop."""
        out = []
        for arr in self.arrays.values():
            planes = arr.geometry.planes

            def useful(ts: float, planes=planes) -> bool:
                sun = sun_position(self.site, ts)
                return sun.elevation > 0 and any(
                    angle_of_incidence(tilt, azimuth, 90.0 - sun.elevation, sun.azimuth) < 90.0 for tilt, azimuth, _ in planes)

            for sensor in arr.sensors:
                series = {t: v * factors.get(sensor, 1.0) for t, v in stats.get(sensor, {}).items() if v is not None}
                out += [(day, sensor, fn.day_steps(series, a, b, useful)) for day, a, b in bounds]
        return out

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
            "model_compare": self.model_compare,
            "battery_log": self.battery_log,
            "limits": self.limits,
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
                        "hours": {sid: {str(t): v for t, v in h.items()} for sid, h in today.morning.items()},
                        "steps": {sid: {str(t): explain.packed(st) for t, st in h.items()}
                                  for sid, h in today.morning_steps.items()},
                        "late": {sid: t.isoformat() for sid, t in today.morning_late.items()}},
            # only the mean: the single models serve the archive, which can skip an outage
            "weather": {"fetched": self._weather[0], "mean": [_weather_row(w) for w in self._weather[1]]}
            if self._weather else None,
        }

    # ------------------------------------------------------------------ learning

    @property
    def learning_running(self) -> bool:
        return self._learn_lock.locked()

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
                self._run_backtest, dict(self.learned), self._eval_archive(), history, now, soc_full, exclusions)
        except Exception:  # noqa: BLE001 – learning and forecast do not depend on it
            _LOGGER.exception("Noxcast backtest failed; keeping the last result")
            return
        self.pv_backtest = result
        self._store.async_delay_save(self._store_data, 5)

    def _near_limit(self, history: ln.PvHistory) -> pv_backtest.NearLimit | None:
        """(array id, hour start) -> the hour was at the array's fixed limit; None without limits."""
        limited = self.installation.limited_arrays()
        if not limited:
            return None
        peaks = {s: {t: v * history.factors.get(s, 1.0) for t, v in values.items() if v is not None}
                 for s, values in history.power_max.items()}
        hours = {sid: ct.limited_hours(self.installation, sid, peaks) for sid in limited}
        return lambda sid, t: t in hours.get(sid, ())

    def _detect_limits(self, history: ln.PvHistory, now: float, soc_full: float,
                       exclusions: list[tuple[float, float]], fine_days: dict) -> dict[str, dict]:
        """Executor. Sensor -> limit seen in its measurements (plateau.py), for every array's sensors.

        Hours that learning leaves out do not count either: before the array's valid-since date, in excluded
        periods, and with a full battery, whose cut (zero export, feed-in limit) can look flat but is no fixed limit.
        fine_days: the days of the 5-minute archive within the window.
        """
        day_of = pv_backtest.local_day(dt_util.get_default_time_zone())
        full = {t for t, v in history.soc.items() if v is not None and v >= soc_full}
        out = {}
        for arr in self.arrays.values():
            change = pt.clear_change(self.site, arr.geometry)

            def keep(t: float, since=arr.valid_since) -> bool:
                return ((since is None or t >= since) and t - t % 3600 not in full
                        and not any(a <= t < b for a, b in exclusions))

            for s in arr.sensors:
                f = history.factors.get(s, 1.0)
                mean, low, high = ({t: v * f for t, v in (series.get(s) or {}).items() if v is not None and keep(t)}
                                   for series in (history.power, history.power_min, history.power_max))
                found = pt.detect(mean, low, high, now, day_of, change,
                                  {t: v for t, v in fn.series(fine_days, s).items() if keep(t)})
                if found is not None:
                    out[s] = found.as_dict()
        return out

    async def async_dismiss_limit(self, sensors: list[str], peak_w: float) -> None:
        """The user said these sensors have no limit at this level: no notice until the level moves (plateau.py).

        Saved at once: a reload right after (another notice answered with a limit) reads the file anew.
        """
        configured = {s for a in self.arrays.values() for s in a.sensors}
        dismissed = {s: w for s, w in self.limits["dismissed"].items() if s in configured}
        self.limits["dismissed"] = {**dismissed, **{s: float(peak_w) for s in sensors}}
        await self._store.async_save(self._store_data())
        self.async_update_listeners()

    def _run_backtest(self, learned: dict[str, LearnedArray], archive: dict, history: ln.PvHistory, now: float,
                      soc_full: float, exclusions: list[tuple[float, float]]) -> dict:
        """Executor. On copies: whatever happens here cannot reach the learned state or the archive."""
        start = time.monotonic()
        result = pv_backtest.run(self.site, self.arrays, copy.deepcopy(learned), history, copy.deepcopy(archive), now,
                                 soc_full, exclusions, dt_util.get_default_time_zone(),
                                 limits=self.installation.limited_arrays(), near_limit=self._near_limit(history))
        seconds = round(time.monotonic() - start, 1)
        _LOGGER.info("PV backtest took %.1f s: replay %s, hold-out %s", seconds, result["replay"]["total"],
                     result["holdout"]["total"])
        return {**result, "seconds": seconds}

    async def _async_learn(self) -> tuple[ln.PvHistory, float, float, list[tuple[float, float]]]:
        """Learn everything; returns what the backtest needs: history, time, full state of charge, exclusions."""
        now = dt_util.utcnow()
        history, soc_full, exclusions = await self._async_pv_history(now)
        air_temp = history.air_temp
        learned = await self.hass.async_add_executor_job(
            ln.learn_arrays, self.site, self.arrays, history, now.timestamp(), soc_full, exclusions)
        # a copy: add_history replaces days of the live dict, and merged() tells those by identity
        base = dict(self.forecast_archive)
        work = await self.hass.async_add_executor_job(self._record_archive, base, learned, history, now.timestamp(),
                                                      soc_full)
        # the forecast may have stored a new snapshot or history stamp in the meantime (archive.py, merged)
        self.forecast_archive = fc_archive.merged(self.forecast_archive, base, work)
        self.learned.update(learned)          # applied in one step on the event loop
        try:
            # only the window's days of the 5-minute archive, which holds years; expanded in the executor
            first = (dt_util.now() - timedelta(days=pt.WINDOW_DAYS + 1)).date().isoformat()
            fine_days = {d: dict(v) for d, v in self.fine.items() if d >= first}
            self.limits["detected"] = await self.hass.async_add_executor_job(
                self._detect_limits, history, now.timestamp(), soc_full, exclusions, fine_days)
        except Exception:  # noqa: BLE001 – only a suggestion; learning and forecast do not depend on it
            _LOGGER.exception("Noxcast could not look for power limits; keeping the last result")
        self.band = await self.hass.async_add_executor_job(self._learn_band)
        await self._async_forecast_error()
        if self.house_load:
            await self._async_learn_load(now, air_temp)
        self.last_learning = now
        self._archive_store.async_delay_save(self._archive_data, 5)
        return history, now.timestamp(), soc_full, exclusions

    async def _async_pv_history(self, now: datetime, start: datetime | None = None, archives: bool = True
                                ) -> tuple[ln.PvHistory, float, list[tuple[float, float]]]:
        """The arrays' history as learning sees it, the full state of charge and the exclusions.

        start: from when (default: the whole learning history); archives: also fetch the temperature and
        satellite archives, which only learning needs.
        """
        start = start or now - timedelta(days=365 * HISTORY_YEARS)
        options = self.config_entry.data
        sensor_ids = sorted({s for a in self.arrays.values() for s in a.sensors})
        factors = await async_power_factors(self.hass, sensor_ids)
        self._factors = factors
        power = await async_hourly_statistics(self.hass, sensor_ids, start, "mean")
        # hourly minimum and maximum tell steady hours from hours with passing clouds or dropouts
        power_min = await async_hourly_statistics(self.hass, sensor_ids, start, "min")
        power_max = await async_hourly_statistics(self.hass, sensor_ids, start, "max")
        soc = await async_hourly_statistics(self.hass, list(options.get(CONF_SOC_SENSORS) or []), start, "max")
        air_temp, satellite_ghi = await self._async_archives(start, now) if archives else (None, None)
        history = ln.PvHistory(power, power_min, power_max, factors, ln.highest(soc), air_temp, satellite_ghi)
        soc_full = float(options.get(CONF_SOC_FULL, DEFAULT_SOC_FULL))
        return history, soc_full, [(e["start"], e["end"]) for e in self.exclusions]

    async def async_learning_hours(self) -> dict[str, dict]:
        """Per array the hours learning used, for the diagnostics download (fetched anew, nothing is kept)."""
        if not self.learned:
            return {}
        history, soc_full, exclusions = await self._async_pv_history(dt_util.utcnow())
        return await self.hass.async_add_executor_job(
            ln.learning_hours, self.site, self.arrays, dict(self.learned), history, soc_full, exclusions)

    async def _async_forecast_error(self) -> None:
        """Renew the forecast error sensor's figures; they change only when the nightly run adds actuals."""
        since = dt_util.now().date() - timedelta(days=ERROR_DAYS)
        try:
            self.forecast_error = await self.hass.async_add_executor_job(
                fc_archive.error_report, dict(self.forecast_archive), since,
                {sid: a.name for sid, a in self.arrays.items()})
        except Exception:  # noqa: BLE001
            # a diagnostic figure: it must not stop setup or the rest of the learning run
            _LOGGER.warning("Forecast error could not be computed from the archive", exc_info=True)

    def _record_archive(self, base: dict, learned: dict[str, LearnedArray], history: ln.PvHistory, now: float,
                        soc_full: float) -> dict:
        """Executor. On a copy: the event loop reads and stores the live archive meanwhile."""
        work = copy.deepcopy(base)
        ln.record_archive(work, self.site, self.arrays, learned, history, now, soc_full, self.installation)
        return work

    def _eval_archive(self) -> dict:
        """The archived days the band and the backtest learn from (EVAL_DAYS)."""
        return fc_archive.recent(self.forecast_archive, dt_util.now().date() - timedelta(days=EVAL_DAYS))

    def _learn_band(self) -> dict:
        """Forecast band from the archive, with the clear-sky output of the current learned model. Executor."""
        return bd.from_archive(self._eval_archive(), self.site, {sid: a.geometry for sid, a in self.arrays.items()},
                               self.learned, limits=self.installation.limited_arrays())

    async def _async_load_series(self, start: datetime, period: str = "hour") -> dict[str, Series]:
        """House load and consumer power in W: {sensor: {hour start: mean W}}.

        Consumers without a power sensor use their energy sensor: the hourly change in Wh is the mean W, and an hour
        without a statistic counts as 0 (see load.fill_counter_gaps).
        """
        ids = [self.house_load] + [c.sensor for c in self.consumers.values() if c.sensor]
        energy = [c.energy for c in self.consumers.values() if not c.sensor]
        factors = await async_power_factors(self.hass, ids)
        stats = await async_hourly_statistics(self.hass, ids, start, "mean", period)
        if energy:
            factors.update(await async_power_factors(self.hass, energy, ENERGY_UNIT_FACTORS))
            stats.update(await async_hourly_statistics(self.hass, energy, start, "change", period))
        out = {sid: {t: max(v, 0.0) * factors.get(sid, 1.0) if sid in energy else v * factors.get(sid, 1.0)
                     for t, v in stats.get(sid, {}).items() if v is not None}
               for sid in ids + energy}
        hours = out.get(self.house_load, {})
        for sid in energy:
            out[sid] = lf.fill_counter_gaps(out[sid], hours)
        return out

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
        running = {sid: run for sid, c in self.consumers.items() if (run := await self._async_run(c, now))}
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
                max_discharge_w=float(d[CONF_MAX_DISCHARGE]) if d.get(CONF_MAX_DISCHARGE) else None,
                output_w=float(d[CONF_OUTPUT_LIMIT]) if d.get(CONF_OUTPUT_LIMIT) else None,
                array=d.get(CONF_BATTERY_ARRAY) or None))
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

    async def _async_run(self, c: ConsumerConfig, now: float) -> lf.Run | None:
        """The consumer's run now: power from its sensor or energy / elapsed time, minutes still to go."""
        power, energy = self._value(c.sensor, POWER_UNIT_FACTORS), self._value(c.energy, ENERGY_UNIT_FACTORS)
        elapsed = self._value(c.elapsed, MINUTE_FACTORS)
        if power is None and energy is not None and elapsed and elapsed > 0:
            energy = lf.run_energy(energy, await self._async_energy_since(c.energy, now - elapsed * 60))
        return lf.current_run(power, self._value(c.remaining, MINUTE_FACTORS), energy, elapsed)

    async def _async_energy_since(self, entity_id: str, start: float) -> list[float]:
        """The energy sensor's values in Wh since start, including the one it had then; empty without history."""
        from homeassistant.components.recorder import get_instance, history
        try:
            states = await get_instance(self.hass).async_add_executor_job(
                history.state_changes_during_period, self.hass, dt_util.utc_from_timestamp(start), None, entity_id,
                False, False, None, True)
        except Exception as err:  # noqa: BLE001 – without history the run counts with its learned profile only
            _LOGGER.debug("No history for the energy sensor %s: %s", entity_id, err)
            return []
        out = []
        for s in states.get(entity_id, []):
            try:
                out.append(float(s.state) * ENERGY_UNIT_FACTORS.get(s.attributes.get("unit_of_measurement"), 1.0))
            except (TypeError, ValueError):
                continue
        return out

    def _archive_data(self) -> dict:
        return fc_archive.to_store(self.forecast_archive)

    def _archive_forecast(self, weather: list[WeatherHour], result: NoxcastData,
                          alt: dict[str, tuple[dict, dict]] | None = None,
                          alt_weather: dict[str, list[WeatherHour]] | None = None,
                          steps: dict[str, dict[float, dict]] | None = None, models: list[str] | None = None,
                          ens: dict[str, dict[float, list[float]]] | None = None) -> None:
        """Keep overwriting tomorrow's forecast; the last version before midnight is the day-ahead forecast."""
        tomorrow = dt_util.start_of_local_day() + timedelta(days=1)
        a = tomorrow.timestamp()
        b = dt_util.start_of_local_day(tomorrow.date() + timedelta(days=1)).timestamp()
        hours = {w.start: (w.ghi, w.temp) for w in weather if a <= w.start < b}
        alt = {name: ({t: g for t, g in ghi.items() if a <= t < b}, fc) for name, (ghi, fc) in (alt or {}).items()}

        def wx(series: list[WeatherHour]) -> dict[float, dict]:
            return {w.start: fc_archive.details(w.cloud, w.sun, w.wind, w.wind_dir) for w in series if a <= w.start < b}

        def split(series: list[WeatherHour]) -> dict[float, tuple[float | None, float | None]]:
            return {w.start: (w.dni, w.dhi) for w in series if a <= w.start < b}

        # the band as published with this forecast (surplus() has set it), tomorrow only
        band = result.band or {}
        hour_band = {t: r for t, r in (band.get("hours") or {}).items() if a <= t < b}

        # a new top-level dict: executor jobs may be iterating the current one (archive.py)
        archive = dict(self.forecast_archive)
        if fc_archive.snapshot(archive, tomorrow.date(), dt_util.utcnow().isoformat(), hours,
                               result.arrays, alt, wx(weather),
                               {name: wx(series) for name, series in (alt_weather or {}).items()},
                               {sid: {t: explain.packed(st) for t, st in h.items()} for sid, h in (steps or {}).items()},
                               models, ens, split(weather),
                               {name: split(series) for name, series in (alt_weather or {}).items()},
                               hour_band, (band.get("days") or {}).get(1)):
            fc_archive.prune(archive, dt_util.now().date() - timedelta(days=ARCHIVE_DAYS))
            self.forecast_archive = archive
            self._archive_store.async_delay_save(self._archive_data, 300)

    async def async_add_exclusion(self, start: datetime, end: datetime, note: str = "") -> None:
        self.exclusions.append({"start": start.timestamp(), "end": end.timestamp(), "note": note})
        self._store.async_delay_save(self._store_data, 5)

    async def async_remove_exclusion(self, start: float, end: float) -> bool:
        """Drop the excluded period with these bounds (seconds, ±1 s); False if there is none."""
        keep = [e for e in self.exclusions if abs(e["start"] - start) > 1 or abs(e["end"] - end) > 1]
        if len(keep) == len(self.exclusions):
            return False
        self.exclusions = keep
        self._store.async_delay_save(self._store_data, 5)
        return True

    # ------------------------------------------------------------------ weather model comparison

    @property
    def compare_candidates(self) -> list[str]:
        """Every offered model and the configured ones (also a model typed in by hand)."""
        return list(dict.fromkeys([*WEATHER_MODEL_OPTIONS, *self.weather_models]))

    def compare_blocked(self) -> tuple[str | None, float | None]:
        """Why a comparison cannot start now, and until when (UNIX seconds) for the waiting time."""
        if self.compare_state["running"]:
            return "running", None
        if not self.learned:
            return "not_learned", None
        # only a usable result for the models chosen now starts the waiting time: "not enough data" may be
        # repeated once there is more, and after a change of models the comparison may be run for the new choice
        last = self.model_compare or {}
        done = last.get("computed_at") if last.get("status") == hc.OK and set(last.get("current") or []) == set(
            self.weather_models) else None
        if done is not None and dt_util.utcnow().timestamp() < done + COMPARE_COOLDOWN.total_seconds():
            return "cooldown", done + COMPARE_COOLDOWN.total_seconds()
        return None, None

    def async_start_compare(self) -> str | None:
        """Start a comparison in the background; returns why not, if it cannot start."""
        reason, _ = self.compare_blocked()
        if reason:
            return reason
        candidates = self.compare_candidates
        self.compare_state = {"running": True, "done": 0, "total": len(candidates), "error": None, "limited": False}
        self._compare_task = self.config_entry.async_create_background_task(
            self.hass, self._async_compare(candidates), "noxcast weather model comparison")
        return None

    async def _async_compare(self, candidates: list[str]) -> None:
        """Fetch each model's day-ahead forecasts of the past year, one request per model, then score them.

        Only a successful run replaces the stored result and starts the waiting time. A refusal for the
        request limit stops at once: the forecast needs the same quota. A model Open-Meteo refuses for
        itself (an answer 4xx, e.g. unknown here) is skipped with the reason; any other failure stops the run.
        """
        state = self.compare_state
        end = dt_util.now().date() - timedelta(days=1)
        start = end - timedelta(days=COMPARE_DAYS - 1)
        weather: dict[str, list[WeatherHour]] = {}
        refused: dict[str, str] = {}
        try:
            for i, model in enumerate(candidates):
                if i:
                    await asyncio.sleep(COMPARE_PAUSE)
                try:
                    weather[model] = await async_previous_runs(self.hass, self.site.latitude, self.site.longitude,
                                                               model, start, end)
                except SourceError as err:
                    # the limit, a blocked address or key, and server errors concern every model: stop
                    if err.limited or err.status is None or err.status >= 500 or err.status in (401, 403, 408):
                        raise
                    weather[model], refused[model] = [], str(err)
                state["done"] = i + 1
            if len(refused) == len(candidates):
                raise SourceError(next(iter(refused.values())))
            now = dt_util.utcnow()
            history, soc_full, exclusions = await self._async_pv_history(
                now, dt_util.start_of_local_day(start), archives=False)
            result = await self.hass.async_add_executor_job(
                hc.run, self.site, self.arrays, copy.deepcopy(dict(self.learned)), history, weather,
                self.weather_models, soc_full, exclusions, dt_util.get_default_time_zone(), now.timestamp())
        except SourceError as err:
            state.update(running=False, error=str(err), limited=err.limited)
            _LOGGER.warning("Weather model comparison stopped: %s", err)
            return
        except Exception as err:  # noqa: BLE001 – the forecast does not depend on it
            state.update(running=False, error=str(err) or type(err).__name__)
            _LOGGER.exception("Weather model comparison failed")
            return
        for item in result["skipped"]:
            if item["model"] in refused:
                item["error"] = refused[item["model"]]
        if result["status"] != hc.OK and (self.model_compare or {}).get("status") == hc.OK:
            # a usable result is not replaced by "too few days" (e.g. after the history was cut short)
            state.update(running=False, error=f"{result['status']}: {result.get('days', 0)} of {result.get('needed', 0)} days")
            return
        self.model_compare = result
        state.update(running=False, error=None)
        self._store.async_delay_save(self._store_data, 5)
        _LOGGER.info("Weather model comparison: %s, recommendation %s", result["status"], result.get("recommendation"))

    # ------------------------------------------------------------------ forecast

    @property
    def weather_models(self) -> list[str]:
        return list(self._option(CONF_WEATHER_MODELS) or DEFAULT_WEATHER_MODELS)

    async def _async_fetch_weather(self) -> tuple[float, list[WeatherHour], dict[str, list[WeatherHour]]]:
        """Open-Meteo forecast; when it fails, the last answer stands in for up to WEATHER_MAX_AGE."""
        try:
            mean, per_model = await async_open_meteo(self.hass, self.site.latitude, self.site.longitude,
                                                     weather_days(self.forecast_days),
                                                     self.weather_models)
        except Exception as err:  # noqa: BLE001
            age = dt_util.utcnow().timestamp() - self._weather[0] if self._weather else None
            if age is None or age > WEATHER_MAX_AGE.total_seconds():
                raise
            self._weather_failed(err, age)
            return self._weather
        if self._weather_warned:
            _LOGGER.info("Open-Meteo answers again")
        self._weather_warned = False
        self._cancel_weather_retry()
        fetched = (dt_util.utcnow().timestamp(), mean, per_model)
        if mean:
            self._weather = fetched
        return fetched

    async def _async_ensemble(self, now: float) -> dict[str, dict[float, list[float]]]:
        """The ensemble runs for the archive, fetched every ENSEMBLE_INTERVAL; a failure only leaves them out.

        Nothing but the archive depends on them, so an outage is no reason to warn or to fail the forecast.
        """
        fetched, runs = self._ensemble
        if now - fetched < ENSEMBLE_INTERVAL.total_seconds():
            return runs
        try:
            runs = await async_ensemble(self.hass, self.site.latitude, self.site.longitude, ENSEMBLE_MODELS)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Open-Meteo ensemble failed (%s); tomorrow's archive keeps the earlier spread", err)
            # try again with the next update but one, not with every update while the source is down
            self._ensemble = (now - ENSEMBLE_INTERVAL.total_seconds() + 3600, runs)
            return runs
        self._ensemble = (now, runs)
        return runs

    def _weather_failed(self, err: Exception, age: float) -> None:
        """One retry a few minutes later; the log hears of an outage once, when the retry fails too.

        The updates run on the full and half hour, when Open-Meteo is busiest and often answers 503; a moment
        later it usually answers again. The random delay keeps installations from retrying all at once.
        """
        if self._weather_warned:
            _LOGGER.debug("Open-Meteo still failing (%s); forecast from %.1f h ago", err, age / 3600)
        elif self._weather_retrying:
            self._weather_warned = True
            _LOGGER.warning("Open-Meteo failed (%s); using the forecast from %.1f h ago until it answers again",
                            err, age / 3600)
        elif self._weather_retry is None:
            delay = random.uniform(*WEATHER_RETRY)
            _LOGGER.debug("Open-Meteo failed (%s); retrying in %.0f s", err, delay)
            self._weather_retry = async_call_later(self.hass, delay, self._async_weather_retry)
        else:
            _LOGGER.debug("Open-Meteo failed (%s); a retry is pending", err)

    async def _async_weather_retry(self, _now: datetime) -> None:
        self._weather_retry = None
        self._weather_retrying = True
        try:
            await self.async_refresh()
        finally:
            self._weather_retrying = False

    @callback
    def _cancel_weather_retry(self) -> None:
        if self._weather_retry is not None:
            self._weather_retry()
            self._weather_retry = None

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

    async def _async_curtailed(self, day_start: float, now: float) -> set[float]:
        """Today's hours with possibly curtailed PV (see forecasting.curtailed_hours)."""
        options = self.config_entry.data
        soc_ids = list(options.get(CONF_SOC_SENSORS) or [])
        if not soc_ids:
            return set()
        live = []
        for sid in soc_ids:
            try:
                live.append(float(self.hass.states.get(sid).state))
            except (AttributeError, TypeError, ValueError):
                pass
        hour_now = now - now % 3600
        try:
            hourly = await async_hourly_statistics(self.hass, soc_ids, dt_util.utc_from_timestamp(day_start), "max")
            recent = await async_hourly_statistics(self.hass, soc_ids,
                                                   dt_util.utc_from_timestamp(max(hour_now - 3600, day_start)),
                                                   "max", "5minute")
        except Exception as err:  # noqa: BLE001 – the live state still covers the running hour
            _LOGGER.debug("Today's state of charge statistics unavailable: %s", err)
            hourly, recent = {}, {}
        return fc.curtailed_hours(hourly, recent, live, float(options.get(CONF_SOC_FULL, DEFAULT_SOC_FULL)), now)

    async def _async_limited(self, day_start: float, now: float) -> dict[str, set[float]]:
        """Per array today's hours in which a sensor was at its fixed limit (see curtailment.limited_hours).

        Like _async_curtailed: hourly maxima, 5-minute maxima for the hours not yet compiled, and the current
        state for the running hour.
        """
        limited = self.installation.limited_arrays()
        sensors = sorted({i.sensor for a in limited for i in self.installation.inputs_of(a)})
        if not sensors:
            return {}
        factors = self._factors or {}
        hour_now = now - now % 3600
        try:
            hourly = await async_hourly_statistics(self.hass, sensors, dt_util.utc_from_timestamp(day_start), "max")
            recent = await async_hourly_statistics(self.hass, sensors,
                                                   dt_util.utc_from_timestamp(max(hour_now - 3600, day_start)),
                                                   "max", "5minute")
        except Exception as err:  # noqa: BLE001 – without them the correction reads every hour as clouds
            _LOGGER.debug("Today's power maxima unavailable: %s", err)
            hourly, recent = {}, {}
        peaks: dict[str, dict[float, float]] = {}
        for s in sensors:
            f = factors.get(s, 1.0)
            values = {t: v * f for t, v in (hourly.get(s) or {}).items() if v is not None}
            for t, v in (recent.get(s) or {}).items():
                if v is not None:
                    hour = t - t % 3600
                    values[hour] = max(values.get(hour, v * f), v * f)
            try:
                live = float(self.hass.states.get(s).state) * f
                values[hour_now] = max(values.get(hour_now, live), live)
            except (AttributeError, TypeError, ValueError):
                pass
            peaks[s] = values
        return {a: ct.limited_hours(self.installation, a, peaks) for a in limited}

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
        curtailed = await self._async_curtailed(day_start, now)
        limited = await self._async_limited(day_start, now)
        learned = dict(self.learned)        # the same model for the forecast and its archived steps
        result, self._today, alt_forecasts = await self.hass.async_add_executor_job(
            fc.pv, self.site, self.arrays, learned, weather, models, self._today, actual, running, utc_now,
            local_now, day_start, day_end, curtailed, self.installation, limited)
        result.updated, result.weather_fetched = utc_now, dt_util.utc_from_timestamp(fetched)
        result.outlook = outlook

        if local_now.hour in fc_archive.HISTORY_HOURS and fc_archive.add_history(
                self.forecast_archive, local_now.date(), local_now.replace(minute=0, second=0, microsecond=0).timestamp(),
                result.expected[TOTAL], result.correction):
            self._archive_store.async_delay_save(self._archive_data, 300)

        result.load = await self._async_load_forecast(
            now, dt_util.start_of_local_day(local_now.date() + timedelta(days=2)).timestamp(),
            {w.start: w.temp for w in weather})
        runs = [r for sid, c in self.consumers.items() if c.timing == lf.TIMING_FLEXIBLE and (r := self.run_profile(sid))]
        await self.hass.async_add_executor_job(
            fc.surplus, result, self.site, self.band, self._battery_units(), runs, now, day_end)
        self._log_battery(result.battery, local_now, now)

        alt = {m: ({w.start: w.ghi for w in hours}, alt_forecasts[m]) for m, hours in models.items()}
        # how tomorrow's forecast comes about, archived with it (the same weather and model as result)
        steps = await self.hass.async_add_executor_job(
            explain.replay, self.site, {sid: arr.geometry for sid, arr in self.arrays.items()}, learned,
            [w for w in weather if tomorrow[0] <= w.start < tomorrow[1]])
        self._archive_forecast(weather, result, alt, models, steps, list(per_model) or self.weather_models,
                               await self._async_ensemble(now))
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
