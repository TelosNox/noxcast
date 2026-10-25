"""Data sources: Home Assistant long-term statistics and weather forecasts."""
from __future__ import annotations

import logging
import math
from datetime import datetime

import aiohttp

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import get_metadata, statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import DEFAULT_WEATHER_MODELS, FORECAST_DAYS, POWER_UNIT_FACTORS
from .model import WeatherHour

_LOGGER = logging.getLogger(__name__)
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


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
    v = _number(value)
    return None if v is None else v % 360


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


HOURLY = ("shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation", "temperature_2m", "cloud_cover",
          "sunshine_duration", "wind_speed_10m", "wind_direction_10m")


async def async_open_meteo(hass: HomeAssistant, latitude: float, longitude: float, days: int = FORECAST_DAYS,
                           models: tuple[str, ...] | list[str] = DEFAULT_WEATHER_MODELS
                           ) -> tuple[list[WeatherHour], dict[str, list[WeatherHour]]]:
    """Hourly weather from Open-Meteo, including yesterday: (mean of the models, each model on its own).

    Irradiance is the mean of the preceding hour; GHI, DNI and DHI are averaged alike, so the mean stays
    consistent (GHI = DHI + DNI·cos z is linear). An hour a model lacks is averaged over the others.
    """
    models = list(models) or list(DEFAULT_WEATHER_MODELS)
    params = {
        "latitude": latitude, "longitude": longitude, "timezone": "UTC", "past_days": 1,
        "forecast_days": days, "hourly": ",".join(HOURLY), "models": ",".join(models), "wind_speed_unit": "ms",
    }
    session = async_get_clientsession(hass)
    async with session.get(OPEN_METEO_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        resp.raise_for_status()
        data = (await resp.json())["hourly"]
    times = [_parse_utc(t) for t in data["time"]]

    def column(var: str, model: str) -> list:
        # several models: one column per model with its name as suffix; a single model: plain names
        return data.get(f"{var}_{model}") or (data.get(var) if len(models) == 1 else None) or []

    per_model = {m: _hours(times, {var: column(var, m) for var in HOURLY}) for m in models}
    per_model = {m: hours for m, hours in per_model.items() if hours}
    return _mean(list(per_model.values())), per_model


def _hours(times: list[float | None], data: dict[str, list]) -> list[WeatherHour]:
    temps = _mid_hour({ts: v for ts, v in zip(times, map(_number, data["temperature_2m"]))
                       if ts is not None and v is not None})
    clouds = _mid_hour({ts: v for ts, v in zip(times, map(_number, data["cloud_cover"]))
                        if ts is not None and v is not None})
    sun = {ts: v for ts, v in zip(times, map(_number, data["sunshine_duration"])) if ts is not None and v is not None}
    wind = _mid_wind({ts: (v, _bearing(d)) for ts, v, d in zip(times, map(_number, data["wind_speed_10m"]),
                                                               data["wind_direction_10m"] or [None] * len(times))
                      if ts is not None and v is not None})
    hours = []
    for ts, ghi, dni, dhi in zip(times, data["shortwave_radiation"], data["direct_normal_irradiance"] or [None] * len(times),
                                 data["diffuse_radiation"] or [None] * len(times)):
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


def _mean(series: list[list[WeatherHour]]) -> list[WeatherHour]:
    """Hour by hour mean of several models; wind direction as vector mean."""
    if len(series) == 1:
        return series[0]
    by_start: dict[float, list[WeatherHour]] = {}
    for hours in series:
        for w in hours:
            by_start.setdefault(w.start, []).append(w)

    def avg(values: list[float | None]) -> float | None:
        v = [x for x in values if x is not None]
        return sum(v) / len(v) if v else None

    out = []
    for start, ws in sorted(by_start.items()):
        split = [w for w in ws if w.dni is not None and w.dhi is not None]
        x = sum(w.wind * math.sin(math.radians(w.wind_dir)) for w in ws if w.wind is not None and w.wind_dir is not None)
        y = sum(w.wind * math.cos(math.radians(w.wind_dir)) for w in ws if w.wind is not None and w.wind_dir is not None)
        out.append(WeatherHour(
            start=start, ghi=avg([w.ghi for w in ws]),
            # the irradiance split only when every model has it, otherwise GHI and split would disagree
            dni=avg([w.dni for w in split]) if len(split) == len(ws) else None,
            dhi=avg([w.dhi for w in split]) if len(split) == len(ws) else None,
            temp=avg([w.temp for w in ws]), cloud=avg([w.cloud for w in ws]), sun=avg([w.sun for w in ws]),
            wind=avg([w.wind for w in ws]), wind_dir=math.degrees(math.atan2(x, y)) % 360 if x or y else None))
    return out


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
