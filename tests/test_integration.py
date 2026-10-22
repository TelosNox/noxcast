"""Home Assistant level tests: config flow, array subentry, learning and sensors."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util

from custom_components.noxcast import model, solar
from custom_components.noxcast.const import DOMAIN

pytestmark = pytest.mark.usefixtures("recorder_mock", "enable_custom_integrations")

ARRAY = {"name": "Roof", "power_sensors": ["sensor.pv1", "sensor.pv2"], "tilt": 30, "azimuth": 180, "peak_power": 1000}


def _clear_hours(site: solar.Site, start: float, hours: int) -> list[model.WeatherHour]:
    out = []
    for h in range(hours):
        t = start + h * 3600
        ghi = 0.0
        for i in range(model.SUBSTEPS):
            sun = solar.sun_position(site, t + (i + 0.5) * 600)
            ghi += solar.clearsky_simplified_solis(sun.elevation, site.pressure, solar.extra_radiation(t))[0] / model.SUBSTEPS
        out.append(model.WeatherHour(t, ghi))
    return out


def _statistics(site: solar.Site, days: int):
    geo = model.ArrayGeometry(30, 180, 1000)
    start = dt_util.start_of_local_day().timestamp() - days * 86400
    series = {}
    for h in range(days * 24):
        t = start + h * 3600
        beam, diff, *_ = model.clearsky_hour(site, geo, t)
        series[t] = 0.5 * geo.peak_w * 0.92 * (beam + diff) / 1000      # two sensors, half each
    return {"sensor.pv1": series, "sensor.pv2": dict(series)}


async def test_setup_learn_and_forecast(hass: HomeAssistant, hass_ws_client) -> None:
    site = solar.Site(hass.config.latitude, hass.config.longitude, float(hass.config.elevation or 0))
    weather = _clear_hours(site, dt_util.start_of_local_day().timestamp(), 72)

    async def fake_stats(_hass, ids, _start, kind, period="hour"):
        return _statistics(site, 30) if kind == "mean" and period == "hour" else {}

    for sensor in ARRAY["power_sensors"]:
        hass.states.async_set(sensor, "0", {"unit_of_measurement": "W", "state_class": "measurement"})

    async def fake_factors(_hass, ids):
        return {i: 1.0 for i in ids}

    with patch("custom_components.noxcast.config_flow.async_weather_entity", return_value=weather), \
         patch("custom_components.noxcast.coordinator.async_weather_entity", return_value=weather), \
         patch("custom_components.noxcast.coordinator.async_power_factors", side_effect=fake_factors), \
         patch("custom_components.noxcast.coordinator.async_hourly_statistics", side_effect=fake_stats), \
         patch("custom_components.noxcast.coordinator.async_archive_temperature", return_value={}):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"weather_source": "entity", "weather_entity": "weather.home", "soc_full_pct": 95})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        entry = result["result"]
        await hass.async_block_till_done()

        sub = await hass.config_entries.subentries.async_init((entry.entry_id, "array"),
                                                              context={"source": config_entries.SOURCE_USER})
        assert sub["type"] is FlowResultType.FORM
        sub = await hass.config_entries.subentries.async_configure(sub["flow_id"], ARRAY)
        assert sub["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()

        coordinator = hass.config_entries.async_get_entry(entry.entry_id).runtime_data
        await coordinator.async_learn()
        await hass.async_block_till_done()

    learned = next(iter(coordinator.learned.values()))
    assert abs(learned.efficiency - 0.92) < 0.03
    tomorrow = (dt_util.now().date() + timedelta(days=1)).isoformat()
    assert len(coordinator.forecast_archive[tomorrow]["hours"]) >= 23        # day-ahead forecast archived
    image = next(s for s in hass.states.async_all("image") if s.entity_id.endswith("shading_map"))
    assert image.state not in ("unknown", "unavailable")

    states = {s.entity_id: s for s in hass.states.async_all("sensor")}
    today = [s for e, s in states.items() if e.endswith("forecast_today")]
    assert today and all(s.state not in ("unknown", "unavailable") for s in today)
    expected = [s for e, s in states.items() if e.endswith("expected_today")]
    assert len(expected) == 2 and all(s.state not in ("unknown", "unavailable") for s in expected)
    assert all("fixed_at" in s.attributes for s in today)
    tomorrow = [s for e, s in states.items() if e.endswith("forecast_tomorrow")]
    assert len(tomorrow) == 2                                # total + array
    assert all(float(s.state) > 1 for s in tomorrow)
    assert all(s.attributes["schema_version"] == 1 and len(s.attributes["hourly"]) == 24 for s in tomorrow)

    client = await hass_ws_client(hass)                      # data for the sidebar panel
    await client.send_json_auto_id({"type": "noxcast/learned"})
    msg = await client.receive_json()
    assert msg["success"]
    arr = msg["result"]["arrays"][0]
    assert arr["name"] == "Roof" and arr["learned"]["efficiency"] > 0.8 and "cells" in arr["learned"]
    await client.send_json_auto_id({"type": "noxcast/accuracy"})
    msg = await client.receive_json()
    assert msg["success"] and msg["result"]["entries"][0]["arrays"][0]["name"] == "Roof"
    await client.send_json_auto_id({"type": "noxcast/load"})
    msg = await client.receive_json()
    assert msg["success"] and msg["result"]["entries"] == []          # no house load configured
    await client.send_json_auto_id({"type": "noxcast/diagnostics"})
    msg = await client.receive_json()
    assert msg["success"] and msg["result"]["entries"][0]["sensors"][0]["role"] == "pv"
    await client.send_json_auto_id({"type": "noxcast/card_config"})
    msg = await client.receive_json()
    assert msg["success"] and msg["result"]["pv"]["groups"][0]["name"] == "Roof"
    assert msg["result"]["pv"]["groups"][0]["entities"] and msg["result"]["batteries"] == []
    for offset in (0, 1, -1):                                 # the day view: today, tomorrow, a past day
        await client.send_json_auto_id({"type": "noxcast/day", "offset": offset})
        msg = await client.receive_json()
        assert msg["success"]
        day = msg["result"]["entries"][0]
        assert day["arrays"][0]["name"] == "Roof" and isinstance(day["history"], list)
        assert day["flows"] is None                           # neither house load nor batteries configured
        if offset >= 0:
            steps = day["arrays"][0]["steps"]
            assert steps and all(abs(s["clear"] + s["clouds"] + s["shade"] + s["reflection"] + s["direction"]
                                     + s["heat"] - s["result"]) < 1 for s in steps.values())


async def test_array_validation_rejects_energy_sensor(hass: HomeAssistant) -> None:
    weather = [model.WeatherHour(dt_util.utcnow().timestamp(), 100.0)]
    hass.states.async_set("sensor.pv_kwh", "1", {"unit_of_measurement": "kWh", "state_class": "total_increasing"})
    with patch("custom_components.noxcast.config_flow.async_weather_entity", return_value=weather), \
         patch("custom_components.noxcast.coordinator.async_weather_entity", return_value=weather):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"weather_source": "entity", "weather_entity": "weather.home", "soc_full_pct": 95})
        entry = result["result"]
        sub = await hass.config_entries.subentries.async_init((entry.entry_id, "array"),
                                                              context={"source": config_entries.SOURCE_USER})
        sub = await hass.config_entries.subentries.async_configure(
            sub["flow_id"], {**ARRAY, "power_sensors": ["sensor.pv_kwh"]})
    assert sub["type"] is FlowResultType.FORM
    assert sub["errors"] == {"power_sensors": "sensor_unit"}


async def test_options_test_archive_on_save(hass: HomeAssistant) -> None:
    weather = [model.WeatherHour(dt_util.utcnow().timestamp(), 100.0)]
    with patch("custom_components.noxcast.config_flow.async_weather_entity", return_value=weather), \
         patch("custom_components.noxcast.coordinator.async_weather_entity", return_value=weather):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"weather_source": "entity", "weather_entity": "weather.home", "soc_full_pct": 95})
        entry = result["result"]
        await hass.async_block_till_done()

        flow = await hass.config_entries.options.async_init(entry.entry_id)
        assert flow["type"] is FlowResultType.FORM
        options = {"archive_temperature": True, "archive_url": "https://archive.invalid/v1/archive",
                   "satellite": True, "satellite_url": "https://satellite.invalid/v1/archive",
                   "satellite_model": "satellite_radiation_seamless"}
        with patch("custom_components.noxcast.config_flow.async_archive_temperature", side_effect=OSError), \
             patch("custom_components.noxcast.config_flow.async_archive_irradiance", return_value={1.0: 500.0}):
            bad = await hass.config_entries.options.async_configure(flow["flow_id"], options)
        assert bad["errors"] == {"archive_url": "archive_failed"}
        with patch("custom_components.noxcast.config_flow.async_archive_temperature", return_value={1.0: 12.0}), \
             patch("custom_components.noxcast.config_flow.async_archive_irradiance", return_value={1.0: 500.0}), \
             patch("custom_components.noxcast.coordinator.async_archive_temperature", return_value={}), \
             patch("custom_components.noxcast.coordinator.async_archive_irradiance", return_value={}):
            done = await hass.config_entries.options.async_configure(flow["flow_id"], options)
            await hass.async_block_till_done()
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert hass.config_entries.async_get_entry(entry.entry_id).options["satellite"] is True
