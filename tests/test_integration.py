"""Home Assistant level tests: config flow, array subentry, learning and sensors."""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util

from custom_components.noxcast import model, solar
from custom_components.noxcast.const import DOMAIN, WS_SCHEMA_VERSION

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
    await client.send_json_auto_id({"type": "noxcast/today"})    # the day card: the day the data was computed for
    msg = await client.receive_json()
    assert msg["success"] and msg["result"]["day_start"] == dt_util.start_of_local_day().timestamp()
    assert msg["result"]["arrays"][0]["tomorrow_wh"] > 0
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


def _canon(o):
    """Everything the coordinator computed as plain JSON, floats exact (repr), for a fingerprint."""
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {"__": type(o).__name__, **{f.name: _canon(getattr(o, f.name)) for f in dataclasses.fields(o)}}
    if isinstance(o, dict):
        return {k if isinstance(k, str) else repr(k): _canon(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_canon(v) for v in o]
    if isinstance(o, (set, frozenset)):
        return sorted(_canon(v) for v in o)
    if isinstance(o, float):
        return repr(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if o is None or isinstance(o, (str, int, bool)):
        return o
    if hasattr(o, "__dict__"):
        return {"__": type(o).__name__, **{k: _canon(v) for k, v in vars(o).items()}}
    return repr(o)


def _fingerprint(c) -> str:
    state = _canon({
        "learned": c.learned, "band": c.band, "load_profile": c.load_profile, "floor_profile": c.floor_profile,
        "consumer_profiles": c.consumer_profiles, "consumer_models": c.consumer_models, "run_profiles": c.run_profiles,
        "load_events": c.load_events, "load_backtest": c.load_backtest, "battery_log": c.battery_log,
        "store": c._store_data(), "archive": c._archive_data(), "fine": c.fine, "data": c.data,
        "runs": {sid: c.run_profile(sid) for sid, cc in c.consumers.items() if cc.timing == "flexible"},
    })
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:16]


# Fingerprints of the pinned scenario below, one per step. They change only when a number changes:
# a refactoring must leave them as they are; a deliberate model change updates them.
PINNED = ['63ab873ca37523af', '80726995bf099c98', '5dc8607464a603a4', 'fedca41903a68936', '98f9e1716b5afcd9',
          'f2c51ee26fa14305']
# Fingerprints of every websocket answer of the panel and the day card in the same scenario (midday and the
# next morning), serialized as sent, key order included: the payloads the frontend reads must not change by accident.
WS_PINNED = {
    "midday": {"learned": "01cacdd618a28b71", "today": "19c9522eb037afa6", "accuracy": "81c58fe13ca81e1c",
               "load": "a7700b7f3ce0a9c6", "diagnostics": "2792537e49f6ca12", "battery": "4a924f136495a769",
               "card_config": "9576395acefde089", "day": "a81cbef60c41e7c3"},
    "morning": {"learned": "e2ad061042929404", "today": "82eda12d4b88078a", "accuracy": "792f93e859a36ca0",
                "load": "ed6dea904ab7ff60", "diagnostics": "8b61b68cdfcfbce0", "battery": "e0dc625e527929a8",
                "card_config": "9576395acefde089", "day": "11f58e9a21f84166"},
}
WS_COMMANDS = ("learned", "today", "accuracy", "load", "diagnostics", "battery", "card_config")


async def _ws_prints(client) -> dict:
    out = {}
    for cmd in WS_COMMANDS:
        await client.send_json_auto_id({"type": f"{DOMAIN}/{cmd}"})
        msg = await client.receive_json()
        assert msg["success"], (cmd, msg)
        assert msg["result"]["v"] == WS_SCHEMA_VERSION, cmd
        out[cmd] = hashlib.sha256(json.dumps(msg["result"]).encode()).hexdigest()[:16]
    days = []
    for offset in range(-31, 3):
        await client.send_json_auto_id({"type": f"{DOMAIN}/day", "offset": offset})
        msg = await client.receive_json()
        assert msg["success"], (offset, msg)
        assert msg["result"]["v"] == WS_SCHEMA_VERSION, offset
        days.append(msg["result"])
    out["day"] = hashlib.sha256(json.dumps(days).encode()).hexdigest()[:16]
    return out


async def test_learning_and_forecast_are_pinned(hass: HomeAssistant, hass_storage, freezer, hass_ws_client) -> None:
    """Learning (PV, band, load, consumers, events), hourly updates (intraday, battery, plan) and the nightly
    run on synthetic data, compared bit for bit with pinned fingerprints."""
    import math
    import random

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.noxcast import archive
    from custom_components.noxcast.coordinator import NoxcastCoordinator

    tz = dt_util.get_default_time_zone()

    def local(y, m, d, h, mi):
        return datetime(y, m, d, h, mi, tzinfo=tz).astimezone(dt_util.UTC)

    times = [local(2026, 6, 10, 5, 30), local(2026, 6, 10, 12, 5), local(2026, 6, 10, 13, 40),
             local(2026, 6, 11, 1, 30), local(2026, 6, 11, 9, 10)]
    client = await hass_ws_client(hass)               # before the clock moves back: the access token stays valid
    freezer.move_to(times[0])
    rng = random.Random(7)
    site = solar.Site(hass.config.latitude, hass.config.longitude, float(hass.config.elevation or 0))
    t_end = local(2026, 6, 12, 0, 0).timestamp()
    t0 = t_end - 75 * 86400
    hours = [t0 + i * 3600 for i in range(int((t_end - t0) // 3600))]
    kind = {d: rng.choices(["clear", "mixed", "overcast"], [0.4, 0.35, 0.25])[0] for d in range(80)}
    day = {t: int((t - t0) // 86400) for t in hours}
    cloud = {t: 1.0 if kind[day[t]] == "clear" else rng.uniform(0.4, 0.95) if kind[day[t]] == "mixed"
             else rng.uniform(0.15, 0.35) for t in hours}
    loc = {t: datetime.fromtimestamp(t, tz) for t in hours}
    temp = {t: 14 + 6 * math.sin(2 * math.pi * (loc[t].hour - 9) / 24) - 0.1 * (75 - day[t]) for t in hours}
    sky = {t: model.clearsky_weather(site, t).ghi for t in hours}
    ghi = {t: sky[t] * cloud[t] for t in hours}
    geos = {"S": model.ArrayGeometry(30, 180, 3000, 35.0),
            "EW": model.ArrayGeometry(20, 90, 2400, model.HEATING_K, (model.Plane(20, 270, 1200),))}
    pv = {n: model.forecast(site, g, model.LearnedArray(efficiency=0.9), [model.WeatherHour(t, ghi[t], temp=temp[t])
                                                                           for t in hours]) for n, g in geos.items()}
    stats = {("sensor.pv1", "mean"): {t: v * 0.55 for t, v in pv["S"].items()},
             ("sensor.pv2", "mean"): {t: v * 0.45 for t, v in pv["S"].items()},
             ("sensor.pv3", "mean"): pv["EW"]}
    for sid in ("sensor.pv1", "sensor.pv2", "sensor.pv3"):
        mean = stats[(sid, "mean")]
        stats[(sid, "min")] = {t: v * (0.95 if kind[day[t]] == "clear" else 0.5) for t, v in mean.items()}
        stats[(sid, "max")] = {t: v * (1.05 if kind[day[t]] == "clear" else 1.4) for t, v in mean.items()}
    stats[("sensor.soc", "max")] = {t: min(100.0, 20 + (22 if kind[day[t]] == "clear" else 8) * max(0, loc[t].hour - 8))
                                    for t in hours}
    washer = {t: ((1900 if loc[t].hour == 10 else 450) if day[t] % 4 == 1 and loc[t].hour in (10, 11) else 0.0)
              for t in hours}
    dishwasher = {t: (1.1 if loc[t].hour == 20 else 0.35 if loc[t].hour == 21 else 0.0) if day[t] % 3 else 0.0
                  for t in hours}                                                  # kWh per hour
    heat_pump = {t: max(0.0, 16 - temp[t]) * 70 for t in hours}
    tv = {t: 125.0 if 19 <= loc[t].hour <= 22 and day[t] % 5 else 0.0 for t in hours}
    base = {t: 230 + (380 if loc[t].hour == 7 else 0) + (520 if 18 <= loc[t].hour <= 21 else 0) + rng.uniform(-40, 60)
            for t in hours}
    stats[("sensor.house", "mean")] = {t: base[t] + washer[t] + dishwasher[t] * 1000 + heat_pump[t] + tv[t]
                                       for t in hours}
    stats[("sensor.house", "min")] = {t: base[t] * 0.8 + heat_pump[t] * 0.9 for t in hours}
    stats[("sensor.washer", "mean")], stats[("sensor.dw", "change")] = washer, dishwasher
    stats[("sensor.hp", "mean")], stats[("sensor.tv", "mean")] = heat_pump, tv
    fine = {}
    for (sid, kind_), series in stats.items():
        if kind_ in ("mean", "change"):
            fine[(sid, kind_)] = {t + i * 300: v / 12 if kind_ == "change" else v * (1 + 0.04 * math.sin(i))
                                  for t, v in series.items() if t >= t_end - 14 * 86400 for i in range(12)}

    async def fake_stats(_hass, ids, start, kind, period="hour", end=None):
        a, b = start.timestamp(), end.timestamp() if end else math.inf
        now = dt_util.utcnow().timestamp()
        src, step = (stats, 4200) if period == "hour" else (fine, 300)        # hours are compiled a bit later
        return {sid: {t: v for t, v in src[(sid, kind)].items() if a <= t < b and t + step <= now}
                for sid in ids if (sid, kind) in src}

    async def fake_factors(_hass, ids, table=None):
        return {i: 1000.0 if i == "sensor.dw" and table else 1.0 for i in ids}

    async def fake_archive(series, *args):
        return {t: v for t, v in series.items() if args[-3].timestamp() <= t < args[-2].timestamp()}

    weather, alt = [], {"icon_seamless": [], "ecmwf_ifs": []}
    for i in range(9 * 24):
        t = local(2026, 6, 9, 0, 0).timestamp() + i * 3600
        c, g = 0.3 + 0.35 * (1 + math.sin(i / 7)), model.clearsky_weather(site, t).ghi
        tt = 18 + 6 * math.sin(2 * math.pi * ((i % 24) - 9) / 24)
        details = {"cloud": 100 * (1 - c), "sun": c, "wind": 3.0 + i % 5, "wind_dir": float(i * 37 % 360)}
        weather.append(model.WeatherHour(t, g * c, temp=tt, **details))
        alt["icon_seamless"].append(model.WeatherHour(t, g * c * 0.9, temp=tt - 1, **details))
        alt["ecmwf_ifs"].append(model.WeatherHour(t, g * min(1.0, c * 1.1), temp=tt + 1, **details))

    # fixed ids: they are keys of everything learned and stored, so random ones would change the fingerprints
    subentries = [config_entries.ConfigSubentryData(data=data, subentry_type=kind_, title=data.get("name", kind_),
                                                    unique_id=None, subentry_id=sid) for sid, (kind_, data) in zip((
        "S", "EW", "washer", "dryer", "dishwasher", "heat_pump", "tv", "battery", "battery2"), (
        ("array", {"name": "South", "power_sensors": ["sensor.pv1", "sensor.pv2"], "tilt": 30, "azimuth": 180,
                   "peak_power": 3000, "heating": 35, "temp_coeff": -0.35, "valid_since": "2026-04-10"}),
        ("array", {"name": "EastWest", "power_sensors": ["sensor.pv3"], "tilt": 20, "azimuth": 90, "peak_power": 2400,
                   "orientation_2": {"tilt": 20, "azimuth": 270, "peak_power": 1200}}),
        ("consumer", {"name": "Washer", "power_sensor": "sensor.washer", "timing": "flexible"}),
        ("consumer", {"name": "Dryer", "power_sensor": "sensor.dryer", "timing": "flexible", "run_power": 1500,
                      "run_minutes": 90}),
        ("consumer", {"name": "Dishwasher", "energy_sensor": "sensor.dw", "timing": "fixed", "kind": "program",
                      "remaining_time": "sensor.dw_remaining", "elapsed_time": "sensor.dw_elapsed"}),
        ("consumer", {"name": "Heat pump", "power_sensor": "sensor.hp", "timing": "fixed", "kind": "regulated"}),
        ("consumer", {"name": "TV", "power_sensor": "sensor.tv", "timing": "fixed", "kind": "session"}),
        ("battery", {"soc_sensor": "sensor.soc", "capacity": 5, "max_soc": 100, "min_soc": 10, "reserve": 5,
                     "coupling": "dc", "max_charge_w": 2000, "max_discharge_w": 1500}),
        ("battery", {"soc_sensor": "sensor.soc2", "capacity": 2, "max_soc_entity": "sensor.max_soc", "min_soc": 20,
                     "coupling": "ac", "efficiency": 88})))]
    entry = MockConfigEntry(domain=DOMAIN, version=1, minor_version=3, unique_id=DOMAIN, title="Noxcast",
                            entry_id="pinned", data={"soc_sensors": ["sensor.soc"], "soc_full_pct": 95,
                                                     "house_load": "sensor.house"},
                            options={"archive_temperature": True, "satellite": True}, subentries_data=subentries)
    entry.add_to_hass(hass)
    assert set(entry.subentries) >= {"S", "EW"}
    past = {}                                       # day-ahead forecasts of the last weeks, for the band
    for back in range(1, 30):
        d = date(2026, 6, 10) - timedelta(days=back)
        a = datetime(d.year, d.month, d.day, tzinfo=tz).timestamp()
        f = rng.uniform(0.6, 1.4)
        archive.snapshot(past, d, "2026-01-01T00:00:00+00:00",
                         {a + i * 3600: (min(sky[a + i * 3600], ghi[a + i * 3600] * f), temp[a + i * 3600]) for i in range(24)},
                         {n: {a + i * 3600: pv[n][a + i * 3600] * f for i in range(24)} for n in ("S", "EW")})
    hass_storage[f"{DOMAIN}.{entry.entry_id}.archive"] = {"version": 1, "minor_version": 1, "key": "a",
                                                           "data": archive.to_store(past)}
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {"version": 1, "minor_version": 1, "key": "m", "data": {
        "exclusions": [{"start": t_end - 30 * 86400, "end": t_end - 29 * 86400, "note": "x"}]}}
    for eid, value, unit in (("sensor.soc", "55", "%"), ("sensor.soc2", "80", "%"), ("sensor.max_soc", "90", "%"),
                             ("sensor.washer", "0", "W"), ("sensor.hp", "0.8", "kW"), ("sensor.dw_remaining", "45", "min"),
                             ("sensor.dw", "0.6", "kWh"), ("sensor.dw_elapsed", "30", "min"), ("sensor.tv", "130", "W"),
                             ("sensor.dryer", "0", "W")):
        hass.states.async_set(eid, value, {"unit_of_measurement": unit})

    prints, ws_prints = [], {}
    with patch("custom_components.noxcast.coordinator.async_open_meteo", return_value=(weather, alt)), \
         patch("custom_components.noxcast.coordinator.async_power_factors", side_effect=fake_factors), \
         patch("custom_components.noxcast.coordinator.async_hourly_statistics", side_effect=fake_stats), \
         patch("custom_components.noxcast.coordinator.async_archive_temperature",
               side_effect=functools.partial(fake_archive, temp)), \
         patch("custom_components.noxcast.coordinator.async_archive_irradiance",
               side_effect=functools.partial(fake_archive, ghi)), \
         patch.object(NoxcastCoordinator, "_async_started"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        c = entry.runtime_data
        prints.append(_fingerprint(c))
        assert await c.async_learn()
        await c.async_refresh()
        prints.append(_fingerprint(c))
        for when in times[1:3]:
            freezer.move_to(when)
            await c.async_refresh()
            prints.append(_fingerprint(c))
        ws_prints["midday"] = await _ws_prints(client)
        freezer.move_to(times[3])
        await c._async_nightly(times[3])
        await c.async_refresh()
        prints.append(_fingerprint(c))
        freezer.move_to(times[4])
        await c.async_refresh()
        prints.append(_fingerprint(c))
        ws_prints["morning"] = await _ws_prints(client)
        await hass.async_block_till_done()
    data = c.data
    assert c.band and data.load and data.battery and data.plan
    assert c.load_events is not None and all(c.consumer_models.values())
    assert prints == PINNED, prints
    assert ws_prints == WS_PINNED, ws_prints


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
