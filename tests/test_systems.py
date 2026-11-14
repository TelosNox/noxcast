"""The installation as systems: migration of the former settings, every part in a system, the tree."""
import itertools

from conftest import load

sy = load("systems")


def _ids():
    counter = itertools.count(1)
    return lambda: f"new{next(counter)}"


def _apply(subs: dict, changes) -> dict:
    out = dict(subs)
    for sid, title, data in changes.add:
        out[sid] = ("system", title, data)
    for sid, data in changes.update.items():
        out[sid] = (out[sid][0], out[sid][1], data)
    return out


ROOF = ("array", "Roof", {"name": "Roof", "power_sensors": ["sensor.roof"]})
BALCONY = ("array", "Balcony", {"name": "Balcony", "power_sensors": ["sensor.b1", "sensor.b2"],
                                "sensor_limits": {"sensor.b1": 800.0, "sensor.b2": 800.0}})


def test_migration_keeps_what_was_computed():
    subs = {"roof": ROOF, "balcony": BALCONY,
            "dc": ("battery", "Balcony battery", {"soc_sensor": "sensor.soc", "capacity": 2, "coupling": "dc",
                                                  "array": "balcony", "output_limit_w": 1600}),
            "ac": ("battery", "Home battery", {"soc_sensor": "sensor.soc2", "capacity": 5, "coupling": "ac",
                                               "output_limit_w": 800})}
    data, changes = sy.migrate({"feed_in": True, "soc_sensors": ["sensor.soc"]}, subs, _ids(), "Batteries")
    assert data == {"feed_in_mode": "free", "soc_sensors": ["sensor.soc"]}       # the old setting stays
    after = _apply(subs, changes)
    systems = sy.systems_of(after)
    # two batteries were one storage: now subsystems of a system that balances them
    parent = next(s for s in systems.values() if s.children)
    assert parent.name == "Batteries" and parent.get("balance") is True and parent.get("template") == "coupled"
    dc = systems[after["dc"][2]["system"]]
    assert dc.get("template") == "pv_battery" and dc.get("output_limit_w") == 1600 and dc.arrays == ("balcony",)
    assert dc.get("charges_from") == "own_pv" and dc.parent == parent.id
    ac = systems[after["ac"][2]["system"]]
    assert ac.get("template") == "ac_battery" and ac.get("output_limit_w") == 800 and ac.arrays == ()
    assert ac.get("charges_from") == "house"
    # the roof gets its own inverter; sensor limits stay on the array (one sensor per micro-inverter)
    roof = systems[after["roof"][2]["system"]]
    assert roof.get("template") == "inverter" and roof.get("output_limit_w") is None and roof.parent is None
    assert after["balcony"][2]["sensor_limits"] == {"sensor.b1": 800.0, "sensor.b2": 800.0}
    # the batteries keep their data, without what moved to the system
    assert after["dc"][2] == {"soc_sensor": "sensor.soc", "capacity": 2, "system": dc.id}
    assert after["ac"][2] == {"soc_sensor": "sensor.soc2", "capacity": 5, "system": ac.id}
    assert not sy.normalize(after, _ids())                 # nothing left without a system
    assert sy.battery_without_pv(after) == []


def test_migration_of_a_lone_dc_battery_asks_for_its_arrays():
    subs = {"roof": ROOF, "dc": ("battery", "Battery", {"soc_sensor": "sensor.soc", "capacity": 2, "coupling": "dc"})}
    data, changes = sy.migrate({}, subs, _ids(), "Batteries")
    assert data == {"feed_in_mode": "none"}               # surplus was not fed in
    after = _apply(subs, changes)
    assert not any(s.children for s in sy.systems_of(after).values())   # one battery: no parent
    assert sy.battery_without_pv(after) == ["dc"]


def test_ac_battery_with_its_array_keeps_its_efficiency():
    """An array behind the battery's inverter means it charges before the output; the AC default stays a number."""
    subs = {"roof": ROOF, "b": ("battery", "B", {"capacity": 2, "coupling": "ac", "array": "roof", "output_limit_w": 800})}
    _, changes = sy.migrate({}, subs, _ids(), "Batteries")
    after = _apply(subs, changes)
    system = sy.systems_of(after)[after["b"][2]["system"]]
    assert system.get("template") == "pv_battery" and system.arrays == ("roof",)
    assert after["b"][2]["efficiency"] == sy.AC_EFFICIENCY_PCT


def test_normalize_gives_every_part_a_system():
    subs = {"roof": ROOF, "b": ("battery", "Home", {"capacity": 2}),
            "s": ("system", "Stream", {"name": "Stream", "template": "coupled"}),
            "sub": ("system", "Ultra", {"name": "Ultra", "parent": "gone"})}
    changes = sy.normalize(subs, _ids())
    after = _apply(subs, changes)
    systems = sy.systems_of(after)
    assert systems[after["roof"][2]["system"]].get("template") == "inverter"
    assert systems[after["b"][2]["system"]].get("charges_from") == "house"     # cuts no PV
    assert "parent" not in after["sub"][2]                                     # its parent is gone
    assert not sy.normalize(after, _ids())


def test_behaviour_is_inherited_and_limits_both_hold():
    subs = {"p": ("system", "Stream", {"name": "Stream", "template": "coupled", "regulation": "load",
                                       "when_full": "load", "output_limit_w": 800, "balance": True}),
            "u": ("system", "Ultra", {"name": "Ultra", "template": "pv_battery", "parent": "p", "output_limit_w": 800,
                                      "when_full": "all"}),
            "a": ("system", "AC Pro", {"name": "AC Pro", "template": "ac_battery", "parent": "p"}),
            "south": ("array", "South", {"system": "u", "power_sensors": ["sensor.s"]}),
            "bu": ("battery", "Ultra battery", {"system": "u"}), "ba": ("battery", "AC Pro battery", {"system": "a"})}
    systems = sy.systems_of(subs)
    assert systems["p"].children == ("a", "u") and systems["u"].arrays == ("south",) and systems["u"].battery == "bu"
    assert sy.behaviour(systems, "a", "regulation") == "load" and sy.behaviour(systems, "u", "when_full") == "all"
    tree = sy.tree({"feed_in_mode": "limited", "feed_in_limit_w": 7000}, subs)
    stream = tree["systems"][0]
    assert stream["name"] == "Stream" and [c["name"] for c in stream["children"]] == ["AC Pro", "Ultra"]
    assert stream["children"][0]["regulation"] == "load" and stream["children"][1]["arrays"][0]["name"] == "South"
    effects = {(e["node"], e["setting"]): e["status"] for e in tree["effects"]}
    assert effects[("house", "feed_in")] == sy.NOT_YET
    assert effects[("u", "output_limit")] == sy.EXACT         # with its battery: the battery forecast caps it
    assert effects[("p", "balance")] == sy.EXACT and effects[("p", "output_limit")] == sy.NOT_YET
