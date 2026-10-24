"""Weather sources: irradiance plus the details kept for the archive (cloud cover, sunshine, wind)."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.util import dt as dt_util

from custom_components.noxcast import sources

T = "2026-10-24T{:02d}:00:00+00:00"


def test_bearing_and_wind_mean():
    assert sources._bearing("SW") == 225.0 and sources._bearing(370) == 10.0 and sources._bearing("?") is None
    mid = sources._mid_wind({0.0: (2.0, 350.0), 3600.0: (4.0, 10.0)})
    speed, direction = mid[0.0]
    assert speed == 3.0 and (direction < 5 or direction > 355)    # vector mean across north, not 180°
    assert 3600.0 not in mid


async def test_weather_entity_details(hass: HomeAssistant) -> None:
    hass.states.async_set("weather.home", "sunny", {"wind_speed_unit": "km/h", "temperature_unit": "°C"})
    items = [{"datetime": T.format(h), "sun_irradiance": 100.0 * h, "temperature": 10.0, "cloud_coverage": 20.0 * h,
              "sun_duration": 900.0 * h, "wind_speed": 36.0, "wind_bearing": "W"} for h in range(1, 5)]

    async def get_forecasts(call):
        return {"weather.home": {"forecast": items}}

    hass.services.async_register("weather", "get_forecasts", get_forecasts, supports_response=SupportsResponse.ONLY)
    hours = {dt_util.utc_from_timestamp(w.start).hour: w for w in await sources.async_weather_entity(hass, "weather.home")}
    w = hours[1]                                     # value at 02:00 is the hour 01:00-02:00
    assert w.ghi == 200.0 and w.sun == 0.5
    assert w.cloud == 30.0 and w.wind == pytest.approx(10.0) and w.wind_dir == pytest.approx(270.0)
    assert hours[0].cloud is None and hours[0].sun == 0.25     # no cloud value at 00:00 to average with


async def test_open_meteo_details(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(sources.OPEN_METEO_URL, json={"hourly": {
        "time": [T.format(h)[:16] for h in range(3)],
        "shortwave_radiation": [0, 300, 400], "direct_normal_irradiance": [0, 500, 600], "diffuse_radiation": [0, 100, 100],
        "temperature_2m": [10, 12, 14], "cloud_cover": [0, 50, 100], "sunshine_duration": [0, 1800, 3600],
        "wind_speed_10m": [2, 4, 6], "wind_direction_10m": [90, 90, 90]}})
    hours = {dt_util.utc_from_timestamp(w.start).hour: w for w in await sources.async_open_meteo(hass, 49.0, 8.4)}
    w = hours[1]                                     # 02:00 holds the mean of 01:00-02:00
    assert w.ghi == 400 and w.sun == 1.0 and w.cloud == 75 and w.wind == 5 and w.wind_dir == pytest.approx(90)
    assert hours[0].sun == 0.5 and hours[0].cloud == 25
