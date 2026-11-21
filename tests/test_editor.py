"""Editing the installation in the panel (editor.py): the same forms, checks and stored data as the dialogs."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.noxcast import model
from custom_components.noxcast.const import DOMAIN

from test_integration import ARRAY, parts_of

pytestmark = pytest.mark.usefixtures("recorder_mock", "enable_custom_integrations")
POWER = {"unit_of_measurement": "W", "state_class": "measurement"}


@pytest.fixture
async def setup(hass: HomeAssistant, hass_ws_client):
    """An installation of one roof inverter with one array; returns (entry, ask), ask sends one command."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    for s in ("sensor.pv1", "sensor.pv2", "sensor.pv3"):
        hass.states.async_set(s, "100", POWER)
    hass.states.async_set("sensor.soc", "50", {"unit_of_measurement": "%"})
    weather = [model.WeatherHour(dt_util.utcnow().timestamp(), 100.0)]
    entry = MockConfigEntry(domain=DOMAIN, version=1, minor_version=3, unique_id=DOMAIN, title="Noxcast",
                            data={"soc_full_pct": 95},
                            subentries_data=[config_entries.ConfigSubentryData(
                                data=ARRAY, subentry_type="array", title="Roof", unique_id=None, subentry_id="roof")])
    entry.add_to_hass(hass)
    patcher = patch("custom_components.noxcast.coordinator.async_open_meteo", return_value=(weather, {}))
    patcher.start()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)

    async def ask(type_: str, **fields):
        await client.send_json_auto_id({"type": f"noxcast/{type_}", "entry_id": entry.entry_id, **fields})
        msg = await client.receive_json()
        await hass.async_block_till_done()
        return msg

    yield entry, ask
    patcher.stop()


def _names(schema: list[dict]) -> set[str]:
    out = set()
    for f in schema:
        out.add(f["name"])
        out |= {f"{f['name']}.{x['name']}" for x in f.get("schema", [])}
    return out


async def test_an_array_is_edited_with_the_dialogs_form_and_checks(hass: HomeAssistant, setup) -> None:
    entry, ask = setup
    msg = await ask("part_form", part="roof", language="de")
    assert msg["success"]
    form = msg["result"]
    assert form["kind"] == "array" and form["revision"]
    assert {"name", "power_sensors", "tilt", "azimuth", "peak_power", "orientation_2.tilt", "limits.sensor_limit_w",
            "advanced.valid_since"} <= _names(form["schema"])
    assert form["data"]["peak_power"] == 1000 and form["data"]["power_sensors"] == ["sensor.pv1", "sensor.pv2"]
    # the texts of the dialog, in the language asked for
    assert form["labels"]["power_sensors"] == "Leistungssensoren" and form["sections"]["orientation_2"]
    assert form["errors"]["sensor_missing"]

    changed = {**form["data"], "peak_power": 2000}
    msg = await ask("part_preview", part="roof", input=changed, language="de")
    assert msg["success"] and msg["result"]["errors"] == {} and msg["result"]["node"] == "roof"
    assert "30° · S 180° · 2 kWp" in msg["result"]["diagram"]
    assert parts_of(hass, entry, "array")["roof"]["peak_power"] == 1000           # a preview stores nothing
    # the same problem as in the dialog
    msg = await ask("part_preview", part="roof", input={**changed, "power_sensors": ["sensor.gone"]})
    assert msg["result"]["errors"] == {"power_sensors": "sensor_missing"}
    msg = await ask("part_save", part="roof", input={**changed, "power_sensors": ["sensor.gone"]},
                    revision=form["revision"])
    assert msg["result"] == {"v": msg["result"]["v"], "errors": {"power_sensors": "sensor_missing"}}

    msg = await ask("part_save", part="roof", input=changed, revision=form["revision"])
    assert msg["success"] and "entries" in msg["result"]
    assert parts_of(hass, entry, "array")["roof"]["peak_power"] == 2000
    assert entry.state is config_entries.ConfigEntryState.LOADED
    # the form was opened before this change: saving it again would overwrite it
    msg = await ask("part_save", part="roof", input={**changed, "peak_power": 3000}, revision=form["revision"])
    assert msg["result"]["errors"] == {"base": "changed"}
    assert parts_of(hass, entry, "array")["roof"]["peak_power"] == 2000


async def test_a_system_form_follows_its_template(hass: HomeAssistant, setup) -> None:
    entry, ask = setup
    system = parts_of(hass, entry, "array")["roof"]["system"]
    form = (await ask("part_form", part=system))["result"]
    assert form["kind"] == "system" and form["data"]["template"] == "inverter"
    assert "charges_from" not in _names(form["schema"])
    # another template: the form is built anew with its settings
    form = (await ask("part_form", part=system, draft={"name": "Roof", "template": "pv_battery"}))["result"]
    assert "charges_from" in _names(form["schema"])
    # filled in by the template, as in the dialog
    assert form["data"]["name"] == "Roof" and form["data"]["charges_from"] == "own_pv" and form["data"]["regulation"] == "load"
    msg = await ask("part_save", part=system, revision=form["revision"],
                    input={"name": "Roof", "template": "inverter", "output_limit_w": 600, "regulation": "fixed"})
    assert msg["result"]["errors"] == {"base": "fixed_power_needed"}
    msg = await ask("part_save", part=system, revision=form["revision"],
                    input={"name": "Roof", "template": "inverter", "output_limit_w": 600, "regulation": "all"})
    assert msg["success"] and parts_of(hass, entry, "system")[system]["output_limit_w"] == 600


async def test_a_new_part_goes_into_its_system(hass: HomeAssistant, setup) -> None:
    entry, ask = setup
    system = parts_of(hass, entry, "array")["roof"]["system"]
    form = (await ask("part_form", kind="battery", to=system))["result"]
    assert form["data"]["system"] == system and form["revision"] is None
    battery = {**form["data"], "name": "Cellar", "soc_sensor": "sensor.soc", "capacity": 5}
    msg = await ask("part_preview", kind="battery", to=system, input=battery)
    assert msg["result"]["node"] == "draft" and 'data-node="draft"' in msg["result"]["diagram"]
    msg = await ask("part_save", kind="battery", to=system, input=battery)
    assert msg["success"]
    stored = [d for d in parts_of(hass, entry, "battery").values() if d["name"] == "Cellar"]
    assert len(stored) == 1 and stored[0]["system"] == system and stored[0]["capacity"] == 5
    # an array cannot go into a system that is not there
    msg = await ask("part_form", kind="array", to="nowhere")
    assert not msg["success"] and msg["error"]["code"] == "not_possible"


async def test_the_household_is_edited_like_its_dialog(hass: HomeAssistant, setup) -> None:
    entry, ask = setup
    form = (await ask("part_form", part="house", language="en"))["result"]
    assert form["kind"] == "house" and "feed_in_mode" in _names(form["schema"])
    assert form["labels"]["feed_in_mode"] == "Feed-in rule"
    msg = await ask("part_save", part="house", revision=form["revision"],
                    input={**form["data"], "feed_in_mode": "limited"})
    assert msg["result"]["errors"] == {"base": "feed_in_limit_needed"}
    msg = await ask("part_save", part="house", revision=form["revision"],
                    input={**form["data"], "feed_in_mode": "limited", "feed_in_limit": {"feed_in_limit_w": 800}})
    assert msg["success"] and entry.data["feed_in_mode"] == "limited" and entry.data["feed_in_limit_w"] == 800
