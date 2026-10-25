"""Weather condition of an hour or a day from the averaged Open-Meteo values (pure Python).

The condition follows from the same numbers the PV forecast uses (cloud cover, precipitation,
temperature, wind), so the weather shown and the expected yield cannot disagree. Thunderstorms and
fog cannot be averaged; they come from the weather code (see sources._mean).
"""
from __future__ import annotations

from collections.abc import Callable

from .model import WeatherHour

THUNDER_CODES = range(95, 100)        # WMO: thunderstorm, with or without hail
FOG_CODES = (45, 48)
RAIN_MM = 0.1                         # mm in the hour from which it counts as wet
POURING_MM = 4.0
SNOW_C = 0.0                          # air temperature at or below which precipitation is snow
SLEET_C = 2.0                         # up to here a mix of rain and snow
WINDY_MS = 10.8                       # Beaufort 6
SUNNY_CLOUD, CLOUDY_CLOUD = 20.0, 70.0
DAY_RAIN_MM, DAY_POURING_MM = 1.0, 20.0


def _wet(precip: float, temp: float | None, pouring: float) -> str:
    if temp is not None and temp <= SNOW_C:
        return "snowy"
    if temp is not None and temp <= SLEET_C:
        return "snowy-rainy"
    return "pouring" if precip >= pouring else "rainy"


def _sky(cloud: float | None, wind: float | None, night: bool) -> str:
    if cloud is None:
        return "cloudy"
    if wind is not None and wind >= WINDY_MS:
        return "windy-variant" if cloud >= CLOUDY_CLOUD else "windy"
    if cloud < SUNNY_CLOUD:
        return "clear-night" if night else "sunny"
    return "partlycloudy" if cloud < CLOUDY_CLOUD else "cloudy"


def hour_condition(w: WeatherHour, night: bool) -> str:
    """Home Assistant weather condition of one hour."""
    precip = w.precip or 0.0
    if w.code in THUNDER_CODES:
        return "lightning-rainy" if precip >= RAIN_MM else "lightning"
    if precip >= RAIN_MM:
        return _wet(precip, w.temp, POURING_MM)
    if w.code in FOG_CODES:
        return "fog"
    return _sky(w.cloud, w.wind, night)


def day_summary(hours: list[WeatherHour], is_day: Callable[[float], bool]) -> dict:
    """Condition and figures of a day; the sky is judged on the daylight hours."""
    temps = [w.temp for w in hours if w.temp is not None]
    precip = sum(w.precip or 0.0 for w in hours)
    light = [w for w in hours if is_day(w.start)] or hours
    clouds = [w.cloud for w in light if w.cloud is not None]
    winds = [w.wind for w in hours if w.wind is not None]
    wet = [w for w in hours if (w.precip or 0.0) >= RAIN_MM]
    if any(w.code in THUNDER_CODES for w in hours):
        condition = "lightning-rainy" if precip >= DAY_RAIN_MM else "lightning"
    elif precip >= DAY_RAIN_MM:
        wet_temps = [w.temp for w in wet if w.temp is not None]
        condition = _wet(precip, sum(wet_temps) / len(wet_temps) if wet_temps else None, DAY_POURING_MM)
    elif sum(w.code in FOG_CODES for w in light) * 2 >= len(light):
        condition = "fog"
    else:
        condition = _sky(sum(clouds) / len(clouds) if clouds else None, max(winds) if winds else None, False)
    return {"condition": condition, "temp_max": max(temps) if temps else None, "temp_min": min(temps) if temps else None,
            "precip": round(precip, 1), "cloud": round(sum(clouds) / len(clouds)) if clouds else None,
            "wind": max(winds) if winds else None}
