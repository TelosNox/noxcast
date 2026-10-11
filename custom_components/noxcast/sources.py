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

from .const import FORECAST_DAYS, IRRADIANCE_KEYS, POWER_UNIT_FACTORS
from .model import WeatherHour

_LOGGER = logging.getLogger(__name__)
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


class NoIrradianceError(HomeAssistantError):
    """The weather entity provides no irradiance in its hourly forecast."""


def _start_ts(value) -> float:
    return value.timestamp() if isinstance(value, datetime) else float(value)


def _irradiance(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return max(v, 0.0) if math.isfinite(v) else None


async def async_hourly_statistics(hass: HomeAssistant, statistic_ids: list[str], start: datetime,
                                  kind: str) -> dict[str, dict[float, float | None]]:
    """Hourly long-term statistics per statistic id: {hour_start_ts: value}."""
    if not statistic_ids:
        return {}
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, None, set(statistic_ids), "hour", None, {kind})
    return {sid: {_start_ts(row["start"]): row.get(kind) for row in rows} for sid, rows in result.items()}


async def async_power_factors(hass: HomeAssistant, statistic_ids: list[str]) -> dict[str, float]:
    """Factor converting each sensor's statistics unit to W (statistics keep the sensor's unit)."""
    meta = await get_instance(hass).async_add_executor_job(
        lambda: get_metadata(hass, statistic_ids=set(statistic_ids)))
    factors = {}
    for sid, (_, data) in meta.items():
        unit = data.get("unit_of_measurement")
        if unit not in POWER_UNIT_FACTORS:
            _LOGGER.warning("%s has unit %r; expected W or kW – values are used as W", sid, unit)
        factors[sid] = POWER_UNIT_FACTORS.get(unit, 1.0)
    return factors


async def async_weather_entity(hass: HomeAssistant, entity_id: str) -> list[WeatherHour]:
    """Hourly global irradiance from a weather entity.

    The value at a forecast time is taken as the mean of the preceding hour (DWD MOSMIX convention).
    """
    response = await hass.services.async_call(
        "weather", "get_forecasts", {"type": "hourly"}, target={"entity_id": entity_id},
        blocking=True, return_response=True)
    items = (response or {}).get(entity_id, {}).get("forecast") or []
    key = next((k for k in IRRADIANCE_KEYS if any(k in item for item in items)), None)
    if key is None:
        raise NoIrradianceError(f"{entity_id} provides no irradiance in its hourly forecast")
    hours = []
    for item in items:
        when = dt_util.parse_datetime(str(item.get("datetime", "")))
        value = _irradiance(item.get(key))
        if when is None or value is None:
            continue
        hours.append(WeatherHour(start=dt_util.as_utc(when).timestamp() - 3600, ghi=value))
    return hours


async def async_open_meteo(hass: HomeAssistant, latitude: float, longitude: float) -> list[WeatherHour]:
    """Hourly GHI/DNI/DHI from Open-Meteo (mean of the preceding hour), including yesterday."""
    params = {
        "latitude": latitude, "longitude": longitude, "timezone": "UTC", "past_days": 1,
        "forecast_days": FORECAST_DAYS,
        "hourly": "shortwave_radiation,direct_normal_irradiance,diffuse_radiation",
    }
    session = async_get_clientsession(hass)
    async with session.get(OPEN_METEO_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        resp.raise_for_status()
        data = (await resp.json())["hourly"]
    hours = []
    for t, ghi, dni, dhi in zip(data["time"], data["shortwave_radiation"],
                                data["direct_normal_irradiance"], data["diffuse_radiation"]):
        when = dt_util.parse_datetime(t if t.endswith("Z") else f"{t}Z")
        g, n, d = _irradiance(ghi), _irradiance(dni), _irradiance(dhi)
        if when is None or g is None:
            continue
        both = n is not None and d is not None
        hours.append(WeatherHour(start=when.timestamp() - 3600, ghi=g, dni=n if both else None, dhi=d if both else None))
    return hours
