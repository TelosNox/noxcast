"""The notification blueprints: valid for Home Assistant, and their conditions decide as described."""
from __future__ import annotations

import pathlib
from datetime import timedelta

import pytest
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import load_yaml

DIR = pathlib.Path(__file__).parents[1] / "blueprints" / "automation" / "noxcast"
FILES = sorted(DIR.glob("*.yaml"))
INPUTS = {"full_at": "sensor.full", "lost": "sensor.lost", "empty_at": "sensor.empty", "start_now": "binary_sensor.go",
          "notify_device": "abc"}


def _automation(path: pathlib.Path) -> dict:
    bp = Blueprint(load_yaml(path), expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA)
    inputs = {k: v for k, v in INPUTS.items() if k in bp.inputs}
    return BlueprintInputs(bp, {"use_blueprint": {"path": path.name, "input": inputs}}).async_substitute()


def test_three_blueprints_with_their_source():
    assert [p.name for p in FILES] == ["battery_empty_tonight.yaml", "battery_full_early.yaml", "start_now.yaml"]
    for p in FILES:
        bp = load_yaml(p)["blueprint"]
        assert bp["source_url"].endswith(f"/blueprints/automation/noxcast/{p.name}")


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_blueprint_is_valid(path):
    config = _automation(path)
    assert config["triggers"] and config["actions"][0]["device_id"] == "abc"


async def _condition(hass: HomeAssistant, path: pathlib.Path, this: dict | None = None) -> bool:
    config = _automation(path)
    variables = {"this": this or {"attributes": {"last_triggered": None}}}
    for k, v in config["variables"].items():          # in order, as the automation renders them
        variables[k] = Template(v, hass).async_render(variables) if isinstance(v, str) and "{" in v else v
    return Template(config["conditions"][0]["value_template"], hass).async_render(variables)


async def test_battery_full_early(hass: HomeAssistant):
    path = DIR / "battery_full_early.yaml"
    today = dt_util.now().replace(hour=10, minute=30, second=0, microsecond=0)
    hass.states.async_set("sensor.full", today.isoformat())
    hass.states.async_set("sensor.lost", "2.4")
    assert await _condition(hass, path) is True
    hass.states.async_set("sensor.lost", "0.5")                     # below 1 kWh lost
    assert await _condition(hass, path) is False
    hass.states.async_set("sensor.lost", "2.4")
    hass.states.async_set("sensor.full", today.replace(hour=15).isoformat())   # full only after 13:00
    assert await _condition(hass, path) is False
    hass.states.async_set("sensor.full", "unknown")                 # not full today
    assert await _condition(hass, path) is False


async def test_battery_empty_tonight(hass: HomeAssistant):
    path = DIR / "battery_empty_tonight.yaml"
    hass.states.async_set("sensor.empty", (dt_util.now() + timedelta(hours=5)).isoformat())
    assert await _condition(hass, path) is True
    hass.states.async_set("sensor.empty", "unknown")                # lasts until the next charge
    assert await _condition(hass, path) is False


async def test_start_now_once_a_day(hass: HomeAssistant):
    path = DIR / "start_now.yaml"
    hass.states.async_set("binary_sensor.go", "on", {"pv_share_now": 85})
    assert await _condition(hass, path) is True
    earlier = {"attributes": {"last_triggered": dt_util.now().isoformat()}}
    assert await _condition(hass, path, earlier) is False
    earlier = {"attributes": {"last_triggered": dt_util.utcnow()}}      # as the automation's state holds it
    assert await _condition(hass, path, earlier) is False
    yesterday = {"attributes": {"last_triggered": (dt_util.now() - timedelta(days=1)).isoformat()}}
    assert await _condition(hass, path, yesterday) is True
