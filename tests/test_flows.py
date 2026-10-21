"""Energy paths of a day: PV, battery and grid into house, battery and grid."""
from conftest import load

flows = load("flows")

H = 3600.0


def test_battery_energy_central_difference():
    soc = {0.0: 20.0, H: 30.0, 2 * H: 50.0, 3 * H: 50.0}
    e = flows.battery_energy(soc, 2000.0)
    assert e == {H: (50 - 20) / 2 / 100 * 2000, 2 * H: (50 - 30) / 2 / 100 * 2000}


def test_paths_add_up_per_source_and_sink():
    pv = {0.0: 0.0, H: 2000.0, 2 * H: 500.0}
    load = {0.0: 400.0, H: 600.0, 2 * H: 800.0}
    battery = {0.0: -300.0, H: 1000.0, 2 * H: 0.0}       # night discharge, midday charge
    f = flows.day_flows(pv, load, battery, 0.0, 3 * H)
    assert f["hours"] == 3
    assert f["pv_home"] == 1100 and f["pv_battery"] == 1000 and f["pv_grid"] == 400
    assert f["battery_home"] == 300 and f["grid_home"] == 100 + 300 and f["grid_battery"] == 0
    assert f["pv_home"] + f["pv_battery"] + f["pv_grid"] == sum(pv.values())
    assert f["pv_home"] + f["battery_home"] + f["grid_home"] == sum(load.values())


def test_without_load_only_battery_and_rest_and_hours_need_all_series():
    f = flows.day_flows({H: 1000.0, 2 * H: 800.0}, None, {H: 300.0}, 0.0, 3 * H)
    assert f["hours"] == 1 and f["pv_battery"] == 300 and f["pv_rest"] == 700 and not f["load"]
    assert flows.day_flows({}, {5 * H: 1.0}, None, 0.0, 3 * H) is None
