"""The swap assistant of the Installation tab (swapflow.py): a system's device replaced by a new one."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.noxcast import learning as ln
from custom_components.noxcast import swapflow

from test_editor import POWER, setup  # noqa: F401 – the fixture
from test_integration import parts_of

pytestmark = pytest.mark.usefixtures("recorder_mock", "enable_custom_integrations")


def _old_history() -> ln.PvHistory:
    """The old sensors' last days, as learning sees them."""
    start = dt_util.start_of_local_day(dt_util.now() - timedelta(days=6)).timestamp()
    power = {s: {start + h * 3600: 300.0 for h in range(5 * 24)} for s in ("sensor.pv1", "sensor.pv2")}
    return ln.PvHistory(power, power, power, {})


async def test_a_device_is_swapped_and_undone(hass: HomeAssistant, setup) -> None:  # noqa: F811
    entry, ask = setup
    for s in ("sensor.x_pv1", "sensor.x_pv2"):
        hass.states.async_set(s, "100", POWER)
    system = parts_of(hass, entry, "array")["roof"]["system"]
    msg = await ask("swap_form", system=system, language="de")
    assert msg["success"]
    form = msg["result"]
    assert form["data"]["e0"] == "sensor.x_pv1" and form["data"]["e1"] == "sensor.x_pv2"      # suggested
    assert form["data"]["d_roof"] is True and form["labels"]["when"] == "Getauscht am"

    # the old statistics are gone: nothing to carry over, nothing changes
    coordinator = entry.runtime_data
    with patch.object(coordinator, "async_pv_history_now",
                      AsyncMock(return_value=(ln.PvHistory({}, {}, {}, {}), []))):
        msg = await ask("swap_save", system=system, input={**form["data"], "when": dt_util.now().date().isoformat()},
                        revision=form["revision"])
    assert msg["result"]["errors"] == {"base": "old_statistics_missing"}
    assert parts_of(hass, entry, "array")["roof"]["power_sensors"] == ["sensor.pv1", "sensor.pv2"]

    day = (dt_util.now() - timedelta(days=1)).date()
    with patch.object(coordinator, "async_pv_history_now", AsyncMock(return_value=(_old_history(), []))):
        msg = await ask("swap_save", system=system, input={**form["data"], "when": day.isoformat()},
                        revision=form["revision"])
    assert msg["success"] and "errors" not in msg["result"]
    await hass.async_block_till_done()
    assert parts_of(hass, entry, "array")["roof"]["power_sensors"] == ["sensor.x_pv1", "sensor.x_pv2"]
    coordinator = entry.runtime_data                                          # reloaded
    [frozen] = coordinator.frozen["roof"]
    assert frozen["sensors"] == ["sensor.pv1", "sensor.pv2"] and frozen["power"]["sensor.pv1"]["mean"]
    assert frozen["until"] == dt_util.start_of_local_day(day).timestamp() and frozen["device"] is True
    assert coordinator.arrays["roof"].segments[0].sensors == ("sensor.pv1", "sensor.pv2")
    swaps = swapflow.swaps(entry)
    assert swaps == [{"system": system, "until": frozen["until"], "swapped": day.isoformat(), "arrays": ["Roof"],
                      "devices": ["Roof"], "undo": True}]

    with patch("custom_components.noxcast.swapflow.async_hourly_statistics", AsyncMock(return_value={})):
        msg = await ask("swap_undo", system=system, language="de")
    assert msg["result"]["errors"] == {"base": "old_statistics_missing"} and msg["result"]["message"]
    with patch("custom_components.noxcast.swapflow.async_hourly_statistics",
               AsyncMock(return_value={"sensor.pv1": {0.0: 300.0}})):
        msg = await ask("swap_undo", system=system)
    assert msg["success"] and "errors" not in msg["result"]
    await hass.async_block_till_done()
    assert parts_of(hass, entry, "array")["roof"]["power_sensors"] == ["sensor.pv1", "sensor.pv2"]
    assert entry.runtime_data.frozen == {"roof": []}
