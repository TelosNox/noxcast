"""Data sources: Home Assistant long-term statistics and weather forecasts."""
from __future__ import annotations

import logging
import math
from datetime import datetime

import aiohttp

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import get_metadata, statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import (COMPASS, ENERGY_UNIT_FACTORS, FORECAST_DAYS, IRRADIANCE_KEYS, POWER_UNIT_FACTORS,
                    SPEED_UNIT_FACTORS)
from .model import WeatherHour

_LOGGER = logging.getLogger(__name__)
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


class NoIrradianceError(HomeAssistantError):
    """The weather entity provides no irradiance in its hourly forecast."""


def _start_ts(value) -> float:
    return value.timestamp() if isinstance(value, datetime) else float(value)


def _number(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _mid_hour(instant: dict[float, float]) -> dict[float, float]:
    """Instantaneous values at full hours -> value at the middle of the hour starting there."""
    return {t: (v + instant[t + 3600]) / 2 for t, v in instant.items() if t + 3600 in instant}


def _bearing(value) -> float | None:
    """Wind direction in degrees from a number or a compass point ("SW")."""
    v = _number(value)
    if v is not None:
        return v % 360
    text = str(value or "").strip().upper()
    return COMPASS.index(text) * 22.5 if text in COMPASS else None


def _mid_wind(instant: dict[float, tuple[float, float | None]]) -> dict[float, tuple[float, float | None]]:
    """Wind (speed, direction) at full hours -> middle of the hour; the direction as vector mean."""
    out = {}
    for t, (s1, d1) in instant.items():
        if t + 3600 not in instant:
            continue
        s2, d2 = instant[t + 3600]
        direction = None
        if d1 is not None and d2 is not None:
            x = s1 * math.sin(math.radians(d1)) + s2 * math.sin(math.radians(d2))
            y = s1 * math.cos(math.radians(d1)) + s2 * math.cos(math.radians(d2))
            direction = math.degrees(math.atan2(x, y)) % 360 if x or y else None
        out[t] = ((s1 + s2) / 2, direction)
    return out


def _irradiance(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return max(v, 0.0) if math.isfinite(v) else None


async def async_hourly_statistics(hass: HomeAssistant, statistic_ids: list[str], start: datetime,
                                  kind: str, period: str = "hour",
                                  end: datetime | None = None) -> dict[str, dict[float, float | None]]:
    """Long-term statistics per statistic id: {period_start_ts: value} (period "hour" or "5minute")."""
    if not statistic_ids:
        return {}
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, end, set(statistic_ids), period, None, {kind})
    return {sid: {_start_ts(row["start"]): row.get(kind) for row in rows} for sid, rows in result.items()}


async def async_power_factors(hass: HomeAssistant, statistic_ids: list[str],
                              table: dict[str, float] = POWER_UNIT_FACTORS) -> dict[str, float]:
    """Factor converting each sensor's statistics unit to W, or to Wh with the energy table."""
    meta = await get_instance(hass).async_add_executor_job(
        lambda: get_metadata(hass, statistic_ids=set(statistic_ids)))
    factors = {}
    for sid, (_, data) in meta.items():
        unit = data.get("unit_of_measurement")
        if unit not in table:
            _LOGGER.warning("%s has unit %r; expected one of %s – values are used unscaled", sid, unit, ", ".join(table))
        factors[sid] = table.get(unit, 1.0)
    return factors


async def async_weather_entity(hass: HomeAssistant, entity_id: str) -> list[WeatherHour]:
    """Hourly global irradiance from a weather entity.

    The value at a forecast time is taken as the mean of the preceding hour (DWD MOSMIX convention).
    Cloud cover, sunshine duration and wind are passed along for the archive when the entity has them
    (sunshine duration in seconds per hour as with DWD, or in minutes when no value exceeds 60).
    """
    response = await hass.services.async_call(
        "weather", "get_forecasts", {"type": "hourly"}, target={"entity_id": entity_id},
        blocking=True, return_response=True)
    items = (response or {}).get(entity_id, {}).get("forecast") or []
    state = hass.states.get(entity_id)
    fahrenheit = bool(state and state.attributes.get("temperature_unit") == "°F")
    speed = SPEED_UNIT_FACTORS.get(state.attributes.get("wind_speed_unit")) if state else None
    key = next((k for k in IRRADIANCE_KEYS if any(k in item for item in items)), None)
    if key is None:
        raise NoIrradianceError(f"{entity_id} provides no irradiance in its hourly forecast")
    rows, temps, clouds, sun, wind = [], {}, {}, {}, {}
    for item in items:
        when = dt_util.parse_datetime(str(item.get("datetime", "")))
        if when is None:
            continue
        ts = dt_util.as_utc(when).timestamp()
        temp = _number(item.get("temperature"))
        if temp is not None:
            temps[ts] = (temp - 32) / 1.8 if fahrenheit else temp
        cloud = _number(item.get("cloud_coverage"))
        if cloud is not None:
            clouds[ts] = cloud
        duration = _number(item.get("sun_duration"))
        if duration is not None:
            sun[ts] = max(duration, 0.0)
        wind_speed = _number(item.get("wind_speed"))
        if wind_speed is not None and speed is not None:
            wind[ts] = (wind_speed * speed, _bearing(item.get("wind_bearing")))
        value = _irradiance(item.get(key))
        if value is not None:
            rows.append((ts, value))
    per_hour = 60.0 if sun and 0 < max(sun.values()) <= 60 else 3600.0
    clouds, wind = _mid_hour(clouds), _mid_wind(wind)
    # irradiance and sunshine are for the preceding hour; temperature, clouds and wind are instantaneous
    temps = {ts - 3600: (temps[ts] + temps[ts - 3600]) / 2 if ts - 3600 in temps else temps[ts] for ts in temps}
    return [WeatherHour(start=ts - 3600, ghi=value, temp=temps.get(ts - 3600),
                        cloud=clouds.get(ts - 3600),
                        sun=min(sun[ts] / per_hour, 1.0) if ts in sun else None,
                        wind=wind[ts - 3600][0] if ts - 3600 in wind else None,
                        wind_dir=wind[ts - 3600][1] if ts - 3600 in wind else None)
            for ts, value in rows]


async def async_open_meteo(hass: HomeAssistant, latitude: float, longitude: float) -> list[WeatherHour]:
    """Hourly GHI/DNI/DHI from Open-Meteo (mean of the preceding hour), including yesterday."""
    params = {
        "latitude": latitude, "longitude": longitude, "timezone": "UTC", "past_days": 1,
        "forecast_days": FORECAST_DAYS,
        "hourly": "shortwave_radiation,direct_normal_irradiance,diffuse_radiation,temperature_2m,"
                  "cloud_cover,sunshine_duration,wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "ms",
    }
    session = async_get_clientsession(hass)
    async with session.get(OPEN_METEO_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        resp.raise_for_status()
        data = (await resp.json())["hourly"]
    times = [_parse_utc(t) for t in data["time"]]
    temps = _mid_hour({ts: v for ts, v in zip(times, map(_number, data.get("temperature_2m") or []))
                       if ts is not None and v is not None})
    clouds = _mid_hour({ts: v for ts, v in zip(times, map(_number, data.get("cloud_cover") or []))
                        if ts is not None and v is not None})
    sun = {ts: v for ts, v in zip(times, map(_number, data.get("sunshine_duration") or []))
           if ts is not None and v is not None}
    wind = _mid_wind({ts: (v, _bearing(d)) for ts, v, d in zip(times, map(_number, data.get("wind_speed_10m") or []),
                                                               data.get("wind_direction_10m") or [])
                      if ts is not None and v is not None})
    hours = []
    for ts, ghi, dni, dhi in zip(times, data["shortwave_radiation"],
                                 data["direct_normal_irradiance"], data["diffuse_radiation"]):
        g, n, d = _irradiance(ghi), _irradiance(dni), _irradiance(dhi)
        if ts is None or g is None:
            continue
        both = n is not None and d is not None
        start = ts - 3600
        hours.append(WeatherHour(start=start, ghi=g, dni=n if both else None, dhi=d if both else None,
                                 temp=temps.get(start), cloud=clouds.get(start),
                                 sun=min(max(sun[ts], 0.0) / 3600, 1.0) if ts in sun else None,
                                 wind=wind[start][0] if start in wind else None,
                                 wind_dir=wind[start][1] if start in wind else None))
    return hours


def _parse_utc(text: str) -> float | None:
    when = dt_util.parse_datetime(text if text.endswith("Z") else f"{text}Z")
    return when.timestamp() if when else None


async def _async_archive(hass: HomeAssistant, url: str, params: dict, api_key: str | None) -> dict:
    params = {**params, "timezone": "UTC"}
    if api_key:
        params["apikey"] = api_key
    session = async_get_clientsession(hass)
    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=120)) as resp:
        resp.raise_for_status()
        return (await resp.json())["hourly"]


async def async_archive_temperature(hass: HomeAssistant, url: str, latitude: float, longitude: float,
                                    start: datetime, end: datetime, api_key: str | None = None) -> dict[float, float]:
    """Air temperature (°C) at the middle of each hour, keyed by hour start (reanalysis archive)."""
    data = await _async_archive(hass, url, {
        "latitude": latitude, "longitude": longitude, "start_date": f"{start:%Y-%m-%d}",
        "end_date": f"{end:%Y-%m-%d}", "hourly": "temperature_2m"}, api_key)
    instant = {ts: v for ts, v in zip(map(_parse_utc, data["time"]), map(_number, data["temperature_2m"]))
               if ts is not None and v is not None}
    return _mid_hour(instant)


async def async_archive_irradiance(hass: HomeAssistant, url: str, model: str, latitude: float, longitude: float,
                                   start: datetime, end: datetime, api_key: str | None = None) -> dict[float, float]:
    """Measured hourly mean GHI (W/m²) keyed by hour start (satellite archive)."""
    params = {"latitude": latitude, "longitude": longitude, "start_date": f"{start:%Y-%m-%d}",
              "end_date": f"{end:%Y-%m-%d}", "hourly": "shortwave_radiation"}
    if model:
        params["models"] = model
    data = await _async_archive(hass, url, params, api_key)
    return {ts - 3600: v for ts, v in zip(map(_parse_utc, data["time"]), map(_irradiance, data["shortwave_radiation"]))
            if ts is not None and v is not None}
