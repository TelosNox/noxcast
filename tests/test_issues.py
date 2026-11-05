"""Repairs issues exist exactly while their condition holds."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

import custom_components.noxcast as noxcast
from custom_components.noxcast import issues
from custom_components.noxcast.const import DOMAIN


def _entry(arrays=None, learned=None, ok=True, soc=None, grid=None, batteries=None):
    c = SimpleNamespace(arrays=arrays or {}, learned=learned or {}, last_update_success=ok, house_load=None,
                        consumers={}, batteries=batteries or {}, grid=grid)
    return SimpleNamespace(runtime_data=c, data={"soc_sensors": soc or []}, subentries={})


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
