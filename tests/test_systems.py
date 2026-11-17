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
    assert data == {"feed_in_mode": "free", "soc_sensors": ["sensor.soc"]}       # the old setting stays until 1.8
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


def test_the_old_soc_sensors_go_and_those_without_a_battery_wait_for_one():
    roof = ("array", "Roof", {**ROOF[2], "system": "s"})
    subs = {"s": ("system", "Roof", {"name": "Roof", "template": "pv_battery"}), "roof": roof,
            "b": ("battery", "Home battery", {"system": "s", "soc_sensor": "sensor.soc", "capacity": 5})}
    data = sy.drop_old_soc({"feed_in_mode": "free", "soc_sensors": ["sensor.soc", "sensor.other"]}, subs)
    assert data == {"feed_in_mode": "free", "unentered_soc_sensors": ["sensor.other"]}
    assert sy.unentered_soc(data, subs) == ["sensor.other"]
    # a battery in a system without PV curtails nothing: its sensor still waits
    lone = {**subs, "t": ("system", "Shed", {"name": "Shed", "template": "pv_battery"}),
            "c": ("battery", "Shed battery", {"system": "t", "soc_sensor": "sensor.other", "capacity": 2})}
    assert sy.unentered_soc(data, lone) == ["sensor.other"]
    # entered with the PV it charges: nothing is left to ask for
    later = {**lone, "shed": ("array", "Shed", {"name": "Shed", "power_sensors": ["sensor.shed_pv"], "system": "t"})}
    assert sy.unentered_soc(data, later) == []
    # every sensor has its battery, or there were none: the setting just goes
    assert sy.drop_old_soc({"soc_sensors": ["sensor.soc"], "soc_full_pct": 95}, subs) == {"soc_full_pct": 95}
    assert sy.drop_old_soc({"soc_full_pct": 95}, subs) == {"soc_full_pct": 95}


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


def test_behaviour_is_the_systems_and_limits_both_hold():
    subs = {"p": ("system", "Stream", {"name": "Stream", "template": "coupled", "regulation": "load",
                                       "own_feed_in": "never", "output_limit_w": 800, "balance": True}),
            # a value left on a subsystem (stored before) does not count: the subsystem follows its system
            "u": ("system", "Ultra", {"name": "Ultra", "template": "pv_battery", "parent": "p", "output_limit_w": 800,
                                      "own_feed_in": "export"}),
            "a": ("system", "AC Pro", {"name": "AC Pro", "template": "ac_battery", "parent": "p"}),
            "south": ("array", "South", {"system": "u", "power_sensors": ["sensor.s"]}),
            "bu": ("battery", "Ultra battery", {"system": "u"}), "ba": ("battery", "AC Pro battery", {"system": "a"})}
    systems = sy.systems_of(subs)
    assert systems["p"].children == ("a", "u") and systems["u"].arrays == ("south",) and systems["u"].battery == "bu"
    assert sy.behaviour(systems, "a", "regulation") == "load" and sy.behaviour(systems, "u", "own_feed_in") == "never"
    tree = sy.tree({"feed_in_mode": "limited", "feed_in_limit_w": 7000}, subs)
    stream = tree["systems"][0]
    assert stream["name"] == "Stream" and [c["name"] for c in stream["children"]] == ["AC Pro", "Ultra"]
    assert stream["children"][0]["regulation"] == "load" and stream["children"][1]["arrays"][0]["name"] == "South"
    effects = {(e["node"], e["setting"]): e["status"] for e in tree["effects"]}
    assert effects[("house", "feed_in")] == sy.NOT_YET
    assert effects[("u", "output_limit")] == sy.EXACT         # with its battery: the battery forecast caps it
    # shared by subsystems with batteries: the battery forecast caps them together, PV and learning do not yet
    assert effects[("p", "balance")] == sy.EXACT and effects[("p", "output_limit")] == sy.SIMPLIFIED
    assert effects[("u", "full_learning")] == sy.SIMPLIFIED and effects[("p", "regulation")] == sy.SIMPLIFIED


def test_a_system_stores_what_sits_inside_it():
    """Stored nested (one entry per system of the top level), read flat: the round trip changes nothing."""
    flat = {"p": ("system", "Stream", {"name": "Stream", "template": "coupled"}),
            "u": ("system", "Ultra", {"name": "Ultra", "template": "pv_battery", "parent": "p"}),
            "r": ("system", "Roof", {"name": "Roof", "template": "inverter"}),
            "south": ("array", "South", {"system": "u"}), "roof": ("array", "Roof", {"system": "r"}),
            "bu": ("battery", "Ultra battery", {"system": "u"}),
            "lost": ("array", "Lost", {"system": "gone"}),                   # no system: stays on its own
            "c": ("consumer", "Washer", {"name": "Washer"})}
    nested = sy.nest(flat)
    assert set(nested) == {"p", "r", "lost", "c"}
    assert set(nested["p"][2]["parts"]) == {"u", "south", "bu"} and set(nested["r"][2]["parts"]) == {"roof"}
    assert sy.flatten(nested) == flat
    assert [sy.home(flat, x) for x in ("p", "u", "south", "bu", "roof", "lost", "c")] == ["p", "p", "p", "p", "r", None, None]


def test_parts_move_only_where_the_forms_allow():
    subs = {"p": ("system", "Stream", {"name": "Stream", "template": "coupled"}),
            "u": ("system", "Ultra", {"name": "Ultra", "template": "pv_battery", "parent": "p"}),
            "r": ("system", "Roof", {"name": "Roof", "template": "inverter"}),
            "e": ("system", "Empty", {"name": "Empty", "template": "inverter"}),
            "south": ("array", "South", {"name": "South", "system": "u"}),
            "bu": ("battery", "Ultra battery", {"system": "u"}), "br": ("battery", "Roof battery", {"system": "r"})}
    now, to = sy.owners(subs, "south")
    assert now == "u" and set(to) == {"u", "r", "e"}                     # not the parent: it has subsystems
    assert sy.moved(subs, "south", "r") == {"name": "South", "system": "r"}
    assert sy.moved(subs, "south", "p") is None
    assert set(sy.owners(subs, "bu")[1]) == {"u", "e"}                   # one battery per system
    # a system: under a parent without parts of its own, or on its own ("")
    now, to = sy.owners(subs, "r")
    assert now == "" and set(to) == {"", "p", "e"}
    subs["u"][2]["phase"] = "l1"                                         # a setting of subsystems only
    assert sy.moved(subs, "u", "") == {"name": "Ultra", "template": "pv_battery"}
    assert sy.moved(subs, "r", "p")["parent"] == "p"
    assert set(sy.owners(subs, "p")[1]) == {""}                          # has subsystems: stays on top
    assert sy.owners(subs, "nothing") is None and sy.moved(subs, "u", "r") is None
    assert sy.tree({}, subs)["moves"]["south"] == {"now": "u", "to": [["e", "Empty"], ["r", "Roof"], ["u", "Ultra"]]}


def test_the_flow_diagram_shows_every_part_and_the_limits():
    fl = load("flowchart")
    subs = {"p": ("system", "Stream", {"name": "Stream", "template": "coupled", "output_limit_w": 800, "balance": True}),
            "u": ("system", "Ultra", {"name": "Ultra", "template": "pv_battery", "parent": "p"}),
            "a": ("system", "Pro <&>", {"name": "Pro <&>", "template": "pv_battery", "parent": "p"}),
            "r": ("system", "Roof", {"name": "Roof", "template": "inverter", "output_limit_w": 600}),
            "south": ("array", "South", {"system": "u"}), "roof": ("array", "Roof", {"system": "r"}),
            "bu": ("battery", "Ultra battery", {"system": "u", "soc_sensor": "soc.u"})}
    svg = fl.render(sy.tree({"feed_in_mode": "none"}, subs), "de")
    for node in ("p", "u", "a", "r", "south", "roof", "house"):
        assert f'data-node="{node}"' in svg
    assert "zusammen ≤ 800 W" in svg and "≤ 600 W" in svg and "Nulleinspeisung" in svg
    assert "Pro &lt;&amp;&gt;" in svg and "<&>" not in svg                  # names are escaped
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "Noch keine Systeme" in fl.render(sy.tree({}, {}), "de")


def test_behaviour_moves_to_the_system_and_a_group_dissolves():
    ids = iter(f"n{i}" for i in range(9))
    subs = {"p": ("system", "Stream", {"name": "Stream", "template": "coupled", "output_limit_w": 800}),
            "u": ("system", "Ultra", {"name": "Ultra", "template": "pv_battery", "parent": "p", "regulation": "load",
                                      "own_feed_in": "never"}),
            "a": ("system", "AC Pro", {"name": "AC Pro", "template": "ac_battery", "parent": "p", "regulation": "load",
                                       "own_feed_in": "never"}),
            "g": ("system", "Group", {"name": "Group", "template": "group", "regulation": "all"}),
            "old": ("system", "Old roof", {"name": "Old roof", "template": "inverter", "parent": "g", "phase": "l1"}),
            "plug": ("system", "Plug battery", {"name": "Plug battery", "template": "ac_battery", "parent": "g",
                                                "regulation": "load"}),
            "roof": ("array", "Roof", {"system": "old"}), "bp": ("battery", "Plug", {"system": "plug"})}
    changes = sy.normalize(subs, lambda: next(ids))
    after = sy.apply(subs, changes)
    # the subsystems agreed: the system takes their value, and they keep none
    assert after["p"][2]["regulation"] == "load" and after["p"][2]["own_feed_in"] == "never"
    assert not any(k in after[c][2] for c in ("u", "a") for k in sy.BEHAVIOUR)
    # the group held independent systems: they stand on their own, with what they did, and the group goes
    assert "g" not in after and changes.remove == ["g"]
    assert after["old"][2] == {"name": "Old roof", "template": "inverter", "regulation": "all"}
    assert after["plug"][2]["regulation"] == "load" and "parent" not in after["plug"][2]
    assert not sy.normalize(after, lambda: next(ids))                     # nothing left to do
    # subsystems that disagreed: the system keeps its own value or none, they keep none
    split = {**subs, "a": ("system", "AC Pro", {"name": "AC Pro", "template": "ac_battery", "parent": "p",
                                                "regulation": "all", "own_feed_in": "never"})}
    after = sy.apply(split, sy.normalize(split, lambda: next(ids)))
    assert "regulation" not in after["p"][2] and after["p"][2]["own_feed_in"] == "never"
    # a group sharing a limit coordinated its devices
    shared = {"g": ("system", "Group", {"name": "Group", "template": "group", "output_limit_w": 800})}
    assert sy.apply(shared, sy.normalize(shared, lambda: next(ids)))["g"][2]["template"] == "coupled"


def test_a_subsystem_moved_out_keeps_behaving_as_its_system_made_it():
    subs = {"p": ("system", "Stream", {"name": "Stream", "template": "coupled", "regulation": "load", "own_feed_in": "never"}),
            "u": ("system", "Ultra", {"name": "Ultra", "template": "pv_battery", "parent": "p", "phase": "l2"}),
            "r": ("system", "Roof", {"name": "Roof", "template": "inverter", "regulation": "all"})}
    assert sy.moved(subs, "u", "") == {"name": "Ultra", "template": "pv_battery", "regulation": "load", "own_feed_in": "never"}
    assert sy.moved(subs, "r", "p") == {"name": "Roof", "template": "inverter", "parent": "p"}


def test_a_fixed_power_goes_up_with_its_regulation():
    ids = iter(f"m{i}" for i in range(9))

    def lifted(*kids):
        subs = {"p": ("system", "P", {"name": "P", "template": "coupled"}),
                **{f"k{i}": ("system", f"K{i}", {"name": f"K{i}", "template": "inverter", "parent": "p", **k})
                   for i, k in enumerate(kids)}}
        return sy.apply(subs, sy.normalize(subs, lambda: next(ids)))["p"][2]
    assert lifted({"regulation": "fixed", "fixed_power_w": 300}, {"regulation": "fixed", "fixed_power_w": 300.0}) == {
        "name": "P", "template": "coupled", "regulation": "fixed", "fixed_power_w": 300}
    # different powers: no regulation of the system is made up
    assert "regulation" not in lifted({"regulation": "fixed", "fixed_power_w": 300}, {"regulation": "fixed", "fixed_power_w": 500})
    # a stray power without "fixed" is not taken along
    assert lifted({"regulation": "load", "fixed_power_w": 300}) == {"name": "P", "template": "coupled", "regulation": "load"}


def test_a_group_holding_parts_keeps_a_template_that_fits():
    subs = {"g": ("system", "G", {"name": "G", "template": "group"}),
            "a": ("array", "A", {"system": "g"}), "b": ("battery", "B", {"system": "g"})}
    after = sy.apply(subs, sy.normalize(subs, lambda: "x"))
    assert after["g"][2]["template"] == "pv_battery"
    tree = sy.tree({}, {**after, "s": ("system", "S", {"name": "S", "template": "pv_battery", "parent": "c"}),
                        "c": ("system", "C", {"name": "C", "template": "coupled", "regulation": "fixed",
                                              "fixed_power_w": 400})})
    rows = {(e["node"], e["setting"]): e for e in tree["effects"]}
    assert rows[("s", "regulation")]["inherited"] and rows[("s", "regulation")]["value"] == "fixed"
    assert rows[("s", "fixed_power")]["value"] == 400 and "inherited" not in rows[("c", "regulation")]


def test_when_full_becomes_a_deviation_where_it_disagreed_with_the_household():
    subs = {"s": ("system", "Stream", {"name": "Stream", "template": "coupled", "regulation": "load", "when_full": "all"}),
            "u": ("system", "Ultra", {"name": "Ultra", "template": "pv_battery", "parent": "s", "when_full": "load"}),
            "b": ("system", "Balcony", {"name": "Balcony", "template": "pv_battery", "regulation": "load",
                                        "when_full": "load"}),
            "r": ("system", "Roof", {"name": "Roof", "template": "inverter", "regulation": "all"})}
    zero = sy.apply(subs, sy.feed_in_rules({"feed_in_mode": "none"}, subs))
    # delivering everything when full under zero export: it feeds the rest in anyway; full power the same
    assert zero["s"][2]["own_feed_in"] == "export" and zero["r"][2]["own_feed_in"] == "export"
    # covering the load when full is what zero export means: it follows; a subsystem follows its system
    assert "own_feed_in" not in zero["b"][2] and "own_feed_in" not in zero["u"][2]
    assert not any("when_full" in d for _, _, d in zero.values())
    free = sy.apply(subs, sy.feed_in_rules({"feed_in_mode": "free"}, subs))
    assert "own_feed_in" not in free["s"][2] and free["b"][2]["own_feed_in"] == "never" and "own_feed_in" not in free["r"][2]
    assert not sy.feed_in_rules({"feed_in_mode": "none"}, zero)                   # nothing left to do
    # stored before 1.6 on subsystems: becomes theirs, and normalize lifts what they agree on
    ids = iter(f"w{i}" for i in range(9))
    old = {"p": ("system", "Stream", {"name": "Stream", "template": "coupled", "regulation": "load"}),
           "u": ("system", "Ultra", {"name": "Ultra", "template": "pv_battery", "parent": "p", "when_full": "load"}),
           "a": ("system", "AC Pro", {"name": "AC Pro", "template": "ac_battery", "parent": "p", "when_full": "load"})}
    moved = sy.apply(old, sy.feed_in_rules({"feed_in_mode": "free"}, old))
    after = sy.apply(moved, sy.normalize(moved, lambda: next(ids)))
    assert after["p"][2]["own_feed_in"] == "never" and "own_feed_in" not in after["u"][2]
    # one following the household and one deviating: the system takes neither
    mixed = {**old, "a": ("system", "AC Pro", {"name": "AC Pro", "template": "ac_battery", "parent": "p",
                                               "when_full": "all"})}
    moved = sy.apply(mixed, sy.feed_in_rules({"feed_in_mode": "free"}, mixed))
    assert "own_feed_in" not in sy.apply(moved, sy.normalize(moved, lambda: next(ids)))["p"][2]


def test_a_system_follows_the_household_rule_unless_it_deviates():
    subs = {"r": ("system", "Roof", {"name": "Roof", "template": "inverter", "regulation": "all", "own_feed_in": "export"}),
            "s": ("system", "Stream", {"name": "Stream", "template": "coupled", "regulation": "load"}),
            "u": ("system", "Ultra", {"name": "Ultra", "template": "pv_battery", "parent": "s"}),
            "bu": ("battery", "Ultra battery", {"system": "u"})}
    systems = sy.systems_of(subs)
    house = {"feed_in_mode": "none"}
    assert sy.feed_in(house, systems, "r") == ("free", True) and sy.feed_in(house, systems, "u") == ("none", False)
    assert sy.exports(house, systems) and not sy.exports(house, {k: v for k, v in systems.items() if k != "r"})
    rows = {(e["node"], e["setting"]): e for e in sy.effects(house, systems, subs)}
    assert rows[("r", "feed_in_rule")]["value"] == "export" and rows[("r", "feed_in_rule")]["status"] == sy.EXACT
    # following zero export: counts through the battery whose full hours learning leaves out
    assert rows[("u", "feed_in_rule")] == {"node": "u", "setting": "feed_in_rule", "status": sy.SIMPLIFIED,
                                           "value": "none", "inherited": "house"}
    pct = {(e["node"], e["setting"]): e for e in sy.effects({"feed_in_mode": "percent", "feed_in_limit_pct": 60},
                                                            systems, subs)}
    assert pct[("house", "feed_in")]["value"] == {"pct": 60} and pct[("s", "feed_in_rule")]["status"] == sy.NOT_YET
    assert ("house", "limit_learning") not in pct          # no peak power yet: no limit in W
    # with peak power and the grid sensor: the share in W, and the hours at it count as "at least"
    peaks = {**subs, "south": ("array", "South", {"system": "u", "peak_power": 6000,
                                                  "orientation_2": {"tilt": 30, "azimuth": 90, "peak_power": 4000}})}
    house = {"feed_in_mode": "percent", "feed_in_limit_pct": 60, "grid_power": "sensor.grid"}
    assert sy.peak_w(peaks) == 10000 and sy.feed_in_limit_w(house, peaks) == 6000
    assert sy.feed_in_limit_w({"feed_in_mode": "limited", "feed_in_limit_w": 800}, peaks) == 800
    assert sy.feed_in_limit_w({"feed_in_mode": "free", "feed_in_limit_w": 800}, peaks) is None
    rows = {(e["node"], e["setting"]): e for e in sy.effects(house, sy.systems_of(peaks), peaks)}
    assert rows[("house", "feed_in")]["value"] == {"pct": 60, "w": 6000}
    assert rows[("house", "feed_in")]["status"] == rows[("house", "limit_learning")]["status"] == sy.SIMPLIFIED
    assert rows[("house", "limit_learning")]["value"] == 6000 and rows[("u", "feed_in_rule")]["status"] == sy.SIMPLIFIED
    # the roof feeds in anyway: its own deviation, not under the limit
    assert sy.export_limited_arrays(house, sy.systems_of({**peaks, "roof": ("array", "Roof", {"system": "r"})})) \
        == ("south",)


def test_feed_in_rule_for_the_battery_forecast():
    subs = {"r": ("system", "Roof", {"name": "Roof", "template": "inverter", "own_feed_in": "export"}),
            "s": ("system", "Stream", {"name": "Stream", "template": "coupled", "own_feed_in": "never"}),
            "u": ("system", "Ultra", {"name": "Ultra", "template": "pv_battery", "parent": "s"}),
            "h": ("system", "House", {"name": "House", "template": "inverter"}),
            "roof": ("array", "Roof", {"system": "r", "peak_power": 5000}),
            "ultra": ("array", "Ultra", {"system": "u", "peak_power": 1000}),
            "garage": ("array", "Garage", {"system": "h", "peak_power": 4000})}
    systems = sy.systems_of(subs)
    assert sy.feed_in_rule({"feed_in_mode": "none"}, systems, subs) == (0.0, ("roof",), ("ultra",))
    assert sy.feed_in_rule({"feed_in_mode": "free"}, systems, subs)[0] is None
    assert sy.feed_in_rule({"feed_in_mode": "limited", "feed_in_limit_w": 800}, systems, subs)[0] == 800
    assert sy.feed_in_rule({"feed_in_mode": "percent", "feed_in_limit_pct": 60}, systems, subs)[0] == 6000
    assert sy.feed_in_rule({"feed_in_mode": "percent"}, systems, subs)[0] is None   # not set: nothing held back
    # with a battery the limit counts in its forecast, without the grid sensor too; not for a system deviating
    house = {"feed_in_mode": "limited", "feed_in_limit_w": 800}
    status = {(e["node"], e["setting"]): e["status"] for e in sy.effects(house, systems, subs)}
    assert status[("house", "feed_in")] == sy.NOT_YET
    with_battery = {**subs, "bh": ("battery", "House battery", {"system": "h"})}
    status = {(e["node"], e["setting"]): e["status"]
              for e in sy.effects(house, sy.systems_of(with_battery), with_battery)}
    assert status[("house", "feed_in")] == status[("h", "feed_in_rule")] == sy.SIMPLIFIED
