"""Open-Meteo: irradiance plus the details kept for the archive (cloud cover, sunshine, wind), and the model mean."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.noxcast import sources

T = "2026-10-24T{:02d}:00:00+00:00"
HOURLY = {"shortwave_radiation": [0, 300, 400], "direct_normal_irradiance": [0, 500, 600], "diffuse_radiation": [0, 100, 100],
          "temperature_2m": [10, 12, 14], "cloud_cover": [0, 50, 100], "sunshine_duration": [0, 1800, 3600],
          "wind_speed_10m": [2, 4, 6], "wind_direction_10m": [90, 90, 90]}


def test_bearing_and_wind_mean():
    assert sources._bearing(370) == 10.0 and sources._bearing("?") is None
    mid = sources._mid_wind({0.0: (2.0, 350.0), 3600.0: (4.0, 10.0)})
    speed, direction = mid[0.0]
    assert speed == 3.0 and (direction < 5 or direction > 355)    # vector mean across north, not 180°
    assert 3600.0 not in mid


async def test_open_meteo_details(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(sources.OPEN_METEO_URL, json={"hourly": {"time": [T.format(h)[:16] for h in range(3)], **HOURLY}})
    mean, per_model = await sources.async_open_meteo(hass, 49.0, 8.4, models=["icon_seamless"])
    hours = {dt_util.utc_from_timestamp(w.start).hour: w for w in mean}
    w = hours[1]                                     # 02:00 holds the mean of 01:00-02:00
    assert w.ghi == 400 and w.sun == 1.0 and w.cloud == 75 and w.wind == 5 and w.wind_dir == pytest.approx(90)
    assert hours[0].sun == 0.5 and hours[0].cloud == 25
    assert list(per_model) == ["icon_seamless"]


async def test_open_meteo_model_mean(hass: HomeAssistant, aioclient_mock) -> None:
    other = {**HOURLY, "shortwave_radiation": [0, 100, 200], "direct_normal_irradiance": [0, 100, None],
             "wind_direction_10m": [270, 270, 270]}
    aioclient_mock.get(sources.OPEN_METEO_URL, json={"hourly": {
        "time": [T.format(h)[:16] for h in range(3)], **{f"{k}_icon_seamless": v for k, v in HOURLY.items()},
        **{f"{k}_ecmwf_ifs": v for k, v in other.items()}}})
    mean, per_model = await sources.async_open_meteo(hass, 49.0, 8.4, models=["icon_seamless", "ecmwf_ifs"])
    hours = {dt_util.utc_from_timestamp(w.start).hour: w for w in mean}
    assert set(per_model) == {"icon_seamless", "ecmwf_ifs"}
    assert hours[0].ghi == 200 and hours[0].dni == 300 and hours[0].dhi == 100
    assert hours[1].ghi == 300 and hours[1].dni is None      # one model without a split: GHI only
    assert hours[1].wind == 5                                # mean speed; opposite directions leave no direction
    assert hours[1].temp == 13.0

