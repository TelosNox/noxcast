"""Repairs issues exist exactly while their condition holds."""
from __future__ import annotations

from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.noxcast import issues
from custom_components.noxcast.const import DOMAIN


def _entry(arrays=None, learned=None, ok=True, soc=None):
    c = SimpleNamespace(arrays=arrays or {}, learned=learned or {}, last_update_success=ok, house_load=None,
                        consumers={}, batteries={})
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

    entry = _entry(arrays={"r": ROOF}, learned={"r": SimpleNamespace(hints=[])})       # all fixed
    issues.async_update_issues(hass, entry)
    assert _ids(hass) == set()


async def test_unload_clears_the_issues(hass: HomeAssistant) -> None:
    issues.async_update_issues(hass, _entry())
    issues.async_clear_issues(hass)
    assert _ids(hass) == set()
