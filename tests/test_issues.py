"""Repairs issues exist exactly while their condition holds."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

import custom_components.noxcast as noxcast
from custom_components.noxcast import installation, issues, repairs
from custom_components.noxcast.const import DOMAIN


def _entry(arrays=None, learned=None, ok=True, soc=None, grid=None, batteries=None, limits=None, sensor_limits=None):
    arrays = arrays or {}
    c = SimpleNamespace(arrays=arrays, learned=learned or {}, last_update_success=ok, house_load=None,
                        consumers={}, batteries=batteries or {}, grid=grid,
                        installation=installation.build({sid: a.sensors for sid, a in arrays.items()}, sensor_limits),
                        limits=limits or {"detected": {}, "dismissed": {}})
    return SimpleNamespace(runtime_data=c, data={"soc_sensors": soc or []}, subentries={}, entry_id="e1")


def _ids(hass: HomeAssistant) -> set[str]:
    return {i for d, i in ir.async_get(hass).issues if d == DOMAIN}


ROOF = SimpleNamespace(name="Roof", sensors=["sensor.pv_roof"])


async def test_issues_follow_their_conditions(hass: HomeAssistant) -> None:
    entry = _entry()
    issues.async_update_issues(hass, entry)
    assert _ids(hass) == {issues.NO_ARRAYS}

    hass.states.async_set("sensor.pv_roof", "100")
    entry = _entry(arrays={"r": ROOF}, soc=["sensor.gone"], ok=False,
                   learned={"r": SimpleNamespace(hints=["peak"])})
    issues.async_update_issues(hass, entry)
    assert _ids(hass) == {issues.MISSING_SENSORS, issues.WEATHER_UNAVAILABLE, "array_check_r"}
    reg = ir.async_get(hass)
    missing = reg.async_get_issue(DOMAIN, issues.MISSING_SENSORS)
    assert missing.translation_placeholders == {"sensors": "sensor.gone"}           # the existing one is not listed
    check = reg.async_get_issue(DOMAIN, "array_check_r")
    assert check.translation_key == "array_check" and check.translation_placeholders == {"name": "Roof"}

    # the card's grid and battery power sensors count as well
    entry = _entry(arrays={"r": ROOF}, grid="sensor.grid", batteries={"b": {"battery_power": "sensor.bat_w"}})
    issues.async_update_issues(hass, entry)
    missing = ir.async_get(hass).async_get_issue(DOMAIN, issues.MISSING_SENSORS)
    assert missing.translation_placeholders == {"sensors": "sensor.bat_w, sensor.grid"}

    # direction and season can come from shade: no repair for them, as no badge in the panel
    entry = _entry(arrays={"r": ROOF}, learned={"r": SimpleNamespace(hints=["direction", "season"])})
    issues.async_update_issues(hass, entry)
    assert _ids(hass) == set()


async def test_an_ignored_issue_stays_ignored_while_it_holds(hass: HomeAssistant) -> None:
    entry = _entry(arrays={"r": ROOF}, soc=["sensor.gone"], learned={"r": SimpleNamespace(hints=["peak"])})
    issues.async_update_issues(hass, entry)
    reg = ir.async_get(hass)
    for issue_id in (issues.MISSING_SENSORS, "array_check_r"):
        reg.async_ignore(DOMAIN, issue_id, True)
    issues.async_update_issues(hass, entry)                 # the next update, e.g. after a reload
    assert all(reg.async_get_issue(DOMAIN, i).dismissed_version for i in (issues.MISSING_SENSORS, "array_check_r"))
    # while Home Assistant starts, sensors are not checked yet: the existing issue is kept, not deleted
    with patch.object(type(hass), "is_running", new_callable=PropertyMock, return_value=False):
        issues.async_update_issues(hass, entry)
    assert reg.async_get_issue(DOMAIN, issues.MISSING_SENSORS).dismissed_version


async def test_deleting_the_entry_clears_the_issues(hass: HomeAssistant) -> None:
    issues.async_update_issues(hass, _entry())
    assert _ids(hass) == {issues.NO_ARRAYS}
    await noxcast.async_remove_entry(hass, SimpleNamespace())
    assert _ids(hass) == set()


async def test_disabling_the_entry_clears_the_issues_a_reload_keeps_them(hass: HomeAssistant) -> None:
    issues.async_update_issues(hass, _entry())
    with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)):
        assert await noxcast.async_unload_entry(hass, SimpleNamespace(disabled_by=None))
        assert _ids(hass) == {issues.NO_ARRAYS}
        assert await noxcast.async_unload_entry(hass, SimpleNamespace(disabled_by="user"))
    assert _ids(hass) == set()


PLATEAU = {"peak_w": 501.0, "limit_w": 500.0, "flat_days": 14, "days": 60, "window_days": 60}


async def test_a_limit_seen_in_the_measurements_is_suggested_until_answered(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.pv_roof", "100")
    detected = {"detected": {"sensor.pv_roof": PLATEAU}, "dismissed": {}}
    issues.async_update_issues(hass, _entry(arrays={"r": ROOF}, limits=detected))
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "limit_suggestion_r")
    assert issue.is_fixable and issue.translation_key == "limit_suggestion"
    assert issue.data == {"entry_id": "e1", "subentry_id": "r"}
    assert issue.translation_placeholders == {"name": "Roof", "sensors": "sensor.pv_roof", "peak": "501", "limit": "500",
                                              "flat_days": "14", "window_days": "60"}
    # a limit set, or "There is no limit" at about this level: no notice
    issues.async_update_issues(hass, _entry(arrays={"r": ROOF}, limits=detected, sensor_limits={"sensor.pv_roof": 800}))
    assert _ids(hass) == set()
    rejected = {"detected": {"sensor.pv_roof": PLATEAU}, "dismissed": {"sensor.pv_roof": 499.0}}
    issues.async_update_issues(hass, _entry(arrays={"r": ROOF}, limits=rejected))
    assert _ids(hass) == set()


async def test_the_fix_flow_writes_the_limit_or_remembers_the_answer(hass: HomeAssistant) -> None:
    from homeassistant import config_entries
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    data = {"name": "Roof", "power_sensors": ["sensor.a", "sensor.b"], "tilt": 30, "azimuth": 180, "peak_power": 1000}
    entry = MockConfigEntry(domain=DOMAIN, version=1, minor_version=4, unique_id=DOMAIN, title="Noxcast", data={},
                            subentries_data=[config_entries.ConfigSubentryData(
                                data=data, subentry_type="array", title="Roof", unique_id=None, subentry_id="r")])
    entry.add_to_hass(hass)
    rejected: list = []

    async def dismiss(sensors, peak):
        rejected.append((sensors, peak))
    arrays = {"r": SimpleNamespace(name="Roof", sensors=("sensor.a", "sensor.b"))}
    entry.runtime_data = SimpleNamespace(
        arrays=arrays, installation=installation.build({"r": ["sensor.a", "sensor.b"]}),
        limits={"detected": {"sensor.a": PLATEAU, "sensor.b": {**PLATEAU, "peak_w": 497.0}}, "dismissed": {}},
        async_dismiss_limit=dismiss)

    async def flow():
        f = await repairs.async_create_fix_flow(hass, "limit_suggestion_r", {"entry_id": entry.entry_id,
                                                                          "subentry_id": "r"})
        f.hass = hass
        return f

    f = await flow()
    menu = await f.async_step_init()
    assert menu["type"] == "menu" and menu["menu_options"] == ["set_limit", "other_value", "no_limit"]
    assert menu["description_placeholders"]["sensors"] == "sensor.a, sensor.b"
    assert (await f.async_step_set_limit())["type"] == "create_entry"
    assert entry.subentries["r"].data["sensor_limits"] == {"sensor.a": 500.0, "sensor.b": 500.0}
    assert entry.subentries["r"].data["tilt"] == 30                          # the rest of the array is kept

    f = await flow()
    form = await f.async_step_other_value()
    assert form["type"] == "form"
    await f.async_step_other_value({"limit_w": 480})
    assert entry.subentries["r"].data["sensor_limits"] == {"sensor.a": 480.0, "sensor.b": 480.0}

    hass.config_entries.async_update_subentry(entry, entry.subentries["r"], data=data)   # no limit again
    f = await flow()
    assert (await f.async_step_no_limit())["type"] == "create_entry"
    assert rejected == [(["sensor.a", "sensor.b"], 501.0)]
    assert "sensor_limits" not in entry.subentries["r"].data

    # once the array has a limit, an old notice has nothing left to do
    entry.runtime_data.installation = installation.build({"r": ["sensor.a", "sensor.b"]}, {"sensor.a": 500, "sensor.b": 500})
    f = await flow()
    assert (await f.async_step_init())["type"] == "abort"
