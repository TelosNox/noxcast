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

    models = {"icon_seamless": weather, "ecmwf_ifs": weather}
    with patch("custom_components.noxcast.coordinator.async_open_meteo", return_value=(weather, models)), \
         patch("custom_components.noxcast.coordinator.async_power_factors", side_effect=fake_factors), \
         patch("custom_components.noxcast.coordinator.async_hourly_statistics", side_effect=fake_stats), \
         patch("custom_components.noxcast.coordinator.async_archive_temperature", return_value={}):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"soc_full_pct": 95})
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
    lit = [h for h in coordinator.forecast_archive[tomorrow]["hours"].values() if h["ghi"] > 0]
    assert lit and all(set(h["alt"]) == {"icon_seamless", "ecmwf_ifs"} for h in lit)    # each model on its own
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
    assert arr["planes"] == [{"tilt": 30, "azimuth": 180, "peak_w": 1000}]
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
    assert msg["result"]["export"] is False                  # zero export unless the integration says otherwise
    weather_id = msg["result"]["weather"]                    # the cards show Noxcast's own weather
    assert weather_id and weather_id.startswith("weather.")
    state = hass.states.get(weather_id)
    assert state.state not in ("unknown", "unavailable")
    for kind, n in (("daily", 2), ("hourly", 24)):
        res = await hass.services.async_call("weather", "get_forecasts", {"type": kind}, target={"entity_id": weather_id},
                                             blocking=True, return_response=True)
        assert len(res[weather_id]["forecast"]) >= n
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
    with patch("custom_components.noxcast.coordinator.async_open_meteo", return_value=(weather, {})):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"soc_full_pct": 95})
        entry = result["result"]
        sub = await hass.config_entries.subentries.async_init((entry.entry_id, "array"),
                                                              context={"source": config_entries.SOURCE_USER})
        sub = await hass.config_entries.subentries.async_configure(
            sub["flow_id"], {**ARRAY, "power_sensors": ["sensor.pv_kwh"]})
    assert sub["type"] is FlowResultType.FORM
    assert sub["errors"] == {"power_sensors": "sensor_unit"}


async def test_options_test_archive_on_save(hass: HomeAssistant) -> None:
    weather = [model.WeatherHour(dt_util.utcnow().timestamp(), 100.0)]
    with patch("custom_components.noxcast.coordinator.async_open_meteo", return_value=(weather, {})):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"soc_full_pct": 95})
        entry = result["result"]
        await hass.async_block_till_done()

        flow = await hass.config_entries.options.async_init(entry.entry_id)
        assert flow["type"] is FlowResultType.FORM
        options = {"weather_models": ["icon_seamless", "ecmwf_ifs"], "archive_temperature": True,
                   "archive_url": "https://archive.invalid/v1/archive",
                   "satellite": True, "satellite_url": "https://satellite.invalid/v1/archive",
                   "satellite_model": "satellite_radiation_seamless"}
        both = {"icon_seamless": weather, "ecmwf_ifs": weather}
        with patch("custom_components.noxcast.config_flow.async_open_meteo", return_value=(weather, {"icon_seamless": weather})):
            partial = await hass.config_entries.options.async_configure(flow["flow_id"], options)
        assert partial["errors"] == {"weather_models": "model_partial"}       # one model unknown to Open-Meteo
        with patch("custom_components.noxcast.config_flow.async_open_meteo", return_value=(weather, both)), \
             patch("custom_components.noxcast.config_flow.async_archive_temperature", side_effect=OSError), \
             patch("custom_components.noxcast.config_flow.async_archive_irradiance", return_value={1.0: 500.0}):
            bad = await hass.config_entries.options.async_configure(flow["flow_id"], options)
        assert bad["errors"] == {"archive_url": "archive_failed"}
        with patch("custom_components.noxcast.config_flow.async_open_meteo", return_value=(weather, both)), \
             patch("custom_components.noxcast.config_flow.async_archive_temperature", return_value={1.0: 12.0}), \
             patch("custom_components.noxcast.config_flow.async_archive_irradiance", return_value={1.0: 500.0}), \
             patch("custom_components.noxcast.coordinator.async_archive_temperature", return_value={}), \
             patch("custom_components.noxcast.coordinator.async_archive_irradiance", return_value={}):
            done = await hass.config_entries.options.async_configure(flow["flow_id"], options)
            await hass.async_block_till_done()
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert hass.config_entries.async_get_entry(entry.entry_id).options["satellite"] is True


async def test_change_during_reload_is_not_lost(hass: HomeAssistant) -> None:
    """A subentry saved while a reload builds the coordinator (no update listener yet) triggers one more reload."""
    from types import MappingProxyType

    from custom_components.noxcast.coordinator import NoxcastCoordinator

    weather = [model.WeatherHour(dt_util.utcnow().timestamp(), 100.0)]
    original = NoxcastCoordinator.async_load
    added = []

    async def load_and_edit(self):
        await original(self)
        if not added:                                           # the second edit lands mid-reload
            added.append(True)
            hass.config_entries.async_add_subentry(self.config_entry, config_entries.ConfigSubentry(
                data=MappingProxyType(ARRAY), subentry_type="array", title="Roof", unique_id=None))

    with patch("custom_components.noxcast.coordinator.async_open_meteo", return_value=(weather, {})):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"soc_full_pct": 95})
        entry = result["result"]
        await hass.async_block_till_done()
        with patch.object(NoxcastCoordinator, "async_load", load_and_edit):
            await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
    assert added and len(hass.config_entries.async_get_entry(entry.entry_id).runtime_data.arrays) == 1


async def test_migration_drops_the_weather_source(hass: HomeAssistant) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    weather = [model.WeatherHour(dt_util.utcnow().timestamp(), 100.0)]
    entry = MockConfigEntry(domain=DOMAIN, version=1, minor_version=1, unique_id=DOMAIN, title="Noxcast",
                            data={"weather_source": "entity", "weather_entity": "weather.home", "soc_full_pct": 95},
                            options={"compare_open_meteo": True, "satellite": False})
    entry.add_to_hass(hass)
    with patch("custom_components.noxcast.coordinator.async_open_meteo", return_value=(weather, {})):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.minor_version == 3
    assert dict(entry.data) == {"soc_full_pct": 95}
    assert dict(entry.options) == {"satellite": False}


async def test_stored_weather_stands_in_when_open_meteo_fails(hass: HomeAssistant) -> None:
    site = solar.Site(hass.config.latitude, hass.config.longitude, float(hass.config.elevation or 0))
    weather = _clear_hours(site, dt_util.start_of_local_day().timestamp(), 72)
    with patch("custom_components.noxcast.coordinator.async_open_meteo", return_value=(weather, {})):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"soc_full_pct": 95})
        await hass.async_block_till_done()
    coordinator = result["result"].runtime_data
    fetched = coordinator.data.weather_fetched
    with patch("custom_components.noxcast.coordinator.async_open_meteo", side_effect=OSError):
        await coordinator.async_refresh()
    assert coordinator.last_update_success and coordinator.data.weather_fetched == fetched
    stored = coordinator._store_data()["weather"]
    assert stored and len(stored["mean"]) == len(weather)


async def test_array_with_further_orientations(hass: HomeAssistant) -> None:
    """Extra orientations are accepted, empty ones are not stored, incomplete or doubled ones are rejected."""
    import voluptuous_serialize
    from homeassistant.data_entry_flow import InvalidData
    from homeassistant.helpers import config_validation as cv

    from custom_components.noxcast.model import Plane

    weather = [model.WeatherHour(dt_util.utcnow().timestamp(), 100.0)]
    for sensor in ARRAY["power_sensors"]:
        hass.states.async_set(sensor, "0", {"unit_of_measurement": "W", "state_class": "measurement"})
    with patch("custom_components.noxcast.coordinator.async_open_meteo", return_value=(weather, {})):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"soc_full_pct": 95})
        entry = result["result"]
        await hass.async_block_till_done()

        async def configure(data: dict):
            sub = await hass.config_entries.subentries.async_init((entry.entry_id, "array"),
                                                                  context={"source": config_entries.SOURCE_USER})
            return await hass.config_entries.subentries.async_configure(sub["flow_id"], data)

        form = await hass.config_entries.subentries.async_init((entry.entry_id, "array"),
                                                               context={"source": config_entries.SOURCE_USER})
        fields = voluptuous_serialize.convert(form["data_schema"], custom_serializer=cv.custom_serializer)
        sections = [f for f in fields if f["name"].startswith("orientation_")]
        assert len(sections) == 3 and all(f["type"] == "expandable" and f["expanded"] is False for f in sections)
        hass.config_entries.subentries.async_abort(form["flow_id"])

        bad = await configure({**ARRAY, "orientation_2": {"tilt": 30, "azimuth": 90}})
        assert bad["type"] is FlowResultType.FORM and bad["errors"] == {"base": "orientation_incomplete"}
        bad = await configure({**ARRAY, "orientation_2": {"tilt": 30, "azimuth": 180, "peak_power": 400}})
        assert bad["errors"] == {"base": "orientation_duplicate"}                # same as the main plane
        with pytest.raises(InvalidData):
            await configure({**ARRAY, "orientation_2": {"tilt": 95, "azimuth": 90, "peak_power": 400}})

        sub = await configure({**ARRAY, "orientation_2": {"tilt": 30, "azimuth": 90, "peak_power": 400},
                               "orientation_3": {}, "orientation_4": {"tilt": 30, "azimuth": 270, "peak_power": 500}})
        assert sub["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()

    subentry = next(s for s in hass.config_entries.async_get_entry(entry.entry_id).subentries.values())
    assert "orientation_3" not in subentry.data and subentry.data["orientation_2"]["azimuth"] == 90
    coordinator = hass.config_entries.async_get_entry(entry.entry_id).runtime_data
    geo = coordinator.arrays[subentry.subentry_id].geometry
    assert geo.extra == (Plane(30, 90, 400), Plane(30, 270, 500)) and geo.peak_w == 1900
    assert [p["peak_w"] for p in geo.describe()] == [1000, 400, 500]
    assert coordinator._geometry_key(subentry.subentry_id)[-1] == [[30, 90, 400], [30, 270, 500]]

    with patch("custom_components.noxcast.coordinator.async_open_meteo", return_value=(weather, {})):
        form = await hass.config_entries.subentries.async_init(
            (entry.entry_id, "array"),
            context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": subentry.subentry_id})
        fields = voluptuous_serialize.convert(form["data_schema"], custom_serializer=cv.custom_serializer)
        expanded = {f["name"]: f["expanded"] for f in fields if f.get("type") == "expandable"}
        assert expanded == {"orientation_2": True, "orientation_3": False, "orientation_4": True}
        done = await hass.config_entries.subentries.async_configure(form["flow_id"], {**ARRAY, "orientation_2": {}})
        assert done["type"] is FlowResultType.ABORT
        await hass.async_block_till_done()
    data = hass.config_entries.async_get_entry(entry.entry_id).subentries[subentry.subentry_id].data
    assert "orientation_2" not in data and "orientation_4" not in data          # removed again: one plane


async def test_old_array_entry_loads_unchanged(hass: HomeAssistant) -> None:
    """An array stored before orientations existed keeps its geometry and geometry key (no relearning)."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    weather = [model.WeatherHour(dt_util.utcnow().timestamp(), 100.0)]
    entry = MockConfigEntry(domain=DOMAIN, version=1, minor_version=3, unique_id=DOMAIN, title="Noxcast",
                            data={"soc_full_pct": 95},
                            subentries_data=[config_entries.ConfigSubentryData(
                                data=ARRAY, subentry_type="array", title="Roof", unique_id=None)])
    entry.add_to_hass(hass)
    with patch("custom_components.noxcast.coordinator.async_open_meteo", return_value=(weather, {})):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    coordinator = entry.runtime_data
    sub_id, arr = next(iter(coordinator.arrays.items()))
    assert arr.geometry == model.ArrayGeometry(30.0, 180.0, 1000.0)
    assert coordinator._geometry_key(sub_id) == [ARRAY["power_sensors"], 30.0, 180.0, 1000.0, None, None,
                                                 True, False, model.HEATING_K]
