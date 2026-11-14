"""The installation as systems (pure Python, no Home Assistant): household -> system -> subsystem.

A system is everything that delivers through one output to the house or to its parent system: its PV arrays,
at most one battery, its regulation. A system holds either arrays and a battery, or subsystems, never both, and
there are at most two levels. Every PV array belongs to a system. Arrays, batteries and systems are config
subentries; each names its parent (CONF_SYSTEM, CONF_PARENT), so the tree is read from the children.

The common installations are templates of the same block, not types of their own (docs/concept.md,
installation model). A template only fills in the settings; every setting stays changeable.

Limits cascade: a subsystem's limit and its system's both hold, the tighter wins, and all subsystems together
never deliver more than their system. Behaviour (regulation, "when the battery is full") is set on the system and
holds for its subsystems unless one sets its own.

The functions here work on plain data, (type, title, data) per subentry id; the shell applies their result.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .const import (
    CONF_BALANCE, CONF_BATTERY_ARRAY, CONF_CHARGES_FROM, CONF_COUPLING, CONF_EFFICIENCY, CONF_FEED_IN, CONF_FEED_IN_LIMIT,
    CONF_FEED_IN_MODE, CONF_FIXED_POWER, CONF_LIMIT_PER_PHASE, CONF_NAME, CONF_OUTPUT_LIMIT, CONF_PARENT,
    CONF_PHASE, CONF_POWER_SENSORS, CONF_REGULATION, CONF_SENSOR_LIMITS, CONF_SOC_SENSORS, CONF_SYSTEM,
    CONF_TEMPLATE, CONF_WHEN_FULL, SUBENTRY_ARRAY, SUBENTRY_BATTERY, SUBENTRY_SYSTEM,
)

type Subs = Mapping[str, tuple[str, str, Mapping[str, Any]]]     # subentry id -> (type, title, data)

# what a template fills in; a setting not named stays empty (no limit, inherited behaviour)
TEMPLATES: dict[str, dict[str, Any]] = {
    "inverter": {CONF_REGULATION: "all"},
    "pv_battery": {CONF_CHARGES_FROM: "own_pv", CONF_REGULATION: "load", CONF_WHEN_FULL: "all"},
    "ac_battery": {CONF_CHARGES_FROM: "house", CONF_REGULATION: "load"},
    "coupled": {CONF_BALANCE: True, CONF_REGULATION: "load"},
    "group": {},
}
BEHAVIOUR = (CONF_REGULATION, CONF_WHEN_FULL)        # inherited from the parent when a subsystem leaves them empty
AC_EFFICIENCY_PCT = 90                               # battery.DEFAULT_EFFICIENCY of the former AC coupling


@dataclass(frozen=True)
class Changes:
    """What the shell applies: new system subentries (id, title, data) and new data of existing subentries."""

    add: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    update: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.add or self.update)


def template_data(template: str, **values: Any) -> dict[str, Any]:
    return {CONF_TEMPLATE: template, **TEMPLATES.get(template, {}),
            **{k: v for k, v in values.items() if v is not None}}


def _of(subs: Subs, kind: str) -> dict[str, tuple[str, Mapping[str, Any]]]:
    return {sid: (title, data) for sid, (t, title, data) in subs.items() if t == kind}


def normalize(subs: Subs, new_id: Callable[[], str]) -> Changes:
    """Every array and battery belongs to an existing system; a parent that is gone, or is itself a subsystem,
    is dropped. A new array without a system (or whose system was deleted) gets a system of its own."""
    systems = _of(subs, SUBENTRY_SYSTEM)
    changes = Changes()
    for sid, (_, data) in sorted(systems.items()):
        parent = data.get(CONF_PARENT)
        if parent and (parent not in systems or systems[parent][1].get(CONF_PARENT)):
            changes.update[sid] = {k: v for k, v in data.items() if k != CONF_PARENT}
    # a battery left without system has no PV of its own known: at the house is the case that cuts no PV
    for kind, template in ((SUBENTRY_ARRAY, "inverter"), (SUBENTRY_BATTERY, "ac_battery")):
        for sid, (title, data) in sorted(_of(subs, kind).items()):
            if data.get(CONF_SYSTEM) in systems:
                continue
            new = new_id()
            changes.add.append((new, title, template_data(template, **{CONF_NAME: title})))
            changes.update[sid] = {**data, CONF_SYSTEM: new}
    return changes


def migrate(data: Mapping[str, Any], subs: Subs, new_id: Callable[[], str], batteries_title: str
            ) -> tuple[dict[str, Any], Changes]:
    """The configuration before 1.5 as systems, computing as before.

    - "Surplus is fed in" yes / no becomes the household's feed-in free / none.
    - A battery with the array behind its inverter becomes a system "PV with battery" with that array and the
      output limit; a DC battery without array one without PV (a repair asks for its arrays); an AC battery a
      system "battery at the house".
    - Two or more batteries are simulated as one storage today, so they become subsystems of a system that
      balances them.
    - Every other array gets a system "inverter without battery" of its own. Sensor limits stay on the array:
      with one sensor per micro-inverter, a system limit would be wrong.
    - The global state-of-charge sensors of the main entry stay as they are (the old setting).
    """
    out = {k: v for k, v in data.items() if k != CONF_FEED_IN}
    out.setdefault(CONF_FEED_IN_MODE, "free" if data.get(CONF_FEED_IN) else "none")
    changes = Changes()
    arrays = _of(subs, SUBENTRY_ARRAY)
    batteries = sorted(_of(subs, SUBENTRY_BATTERY).items())
    parent = new_id() if len(batteries) > 1 else None
    if parent:
        changes.add.append((parent, batteries_title, template_data("coupled", **{CONF_NAME: batteries_title})))
    taken: set[str] = set()
    for sid, (title, d) in batteries:
        system = new_id()
        array = d.get(CONF_BATTERY_ARRAY)
        array = array if array in arrays and array not in taken else None
        ac = d.get(CONF_COUPLING) == "ac"
        # an array behind the battery's inverter means the battery charges before the output, whatever the
        # coupling said; the coupling's default efficiency is kept as a number then
        template = "pv_battery" if array or not ac else "ac_battery"
        changes.add.append((system, title, template_data(
            template, **{CONF_NAME: title, CONF_OUTPUT_LIMIT: d.get(CONF_OUTPUT_LIMIT), CONF_PARENT: parent})))
        new = {k: v for k, v in d.items() if k not in (CONF_BATTERY_ARRAY, CONF_OUTPUT_LIMIT, CONF_COUPLING)}
        if ac and template == "pv_battery" and not d.get(CONF_EFFICIENCY):
            new[CONF_EFFICIENCY] = AC_EFFICIENCY_PCT
        changes.update[sid] = {**new, CONF_SYSTEM: system}
        if array:
            taken.add(array)
            changes.update[array] = {**arrays[array][1], CONF_SYSTEM: system}
    for sid, (title, d) in sorted(arrays.items()):
        if sid not in taken:
            system = new_id()
            changes.add.append((system, title, template_data("inverter", **{CONF_NAME: title})))
            changes.update[sid] = {**d, CONF_SYSTEM: system}
    return out, changes


def battery_without_pv(subs: Subs) -> list[str]:
    """Batteries charging from their own PV whose system has no array: the migration cannot know the wiring."""
    systems = _of(subs, SUBENTRY_SYSTEM)
    with_pv = {d.get(CONF_SYSTEM) for _, d in _of(subs, SUBENTRY_ARRAY).values()}
    return sorted(sid for sid, (_, d) in _of(subs, SUBENTRY_BATTERY).items()
                  if (s := systems.get(d.get(CONF_SYSTEM))) is not None
                  and s[1].get(CONF_CHARGES_FROM) == "own_pv" and d.get(CONF_SYSTEM) not in with_pv)


@dataclass(frozen=True)
class System:
    id: str
    name: str
    data: Mapping[str, Any]
    parent: str | None
    arrays: tuple[str, ...]
    battery: str | None
    children: tuple[str, ...]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


def systems_of(subs: Subs) -> dict[str, System]:
    systems = _of(subs, SUBENTRY_SYSTEM)
    arrays = _of(subs, SUBENTRY_ARRAY)
    batteries = _of(subs, SUBENTRY_BATTERY)
    out = {}
    for sid, (title, d) in systems.items():
        out[sid] = System(
            sid, d.get(CONF_NAME) or title, d, d.get(CONF_PARENT) if d.get(CONF_PARENT) in systems else None,
            tuple(sorted((a for a, (_, ad) in arrays.items() if ad.get(CONF_SYSTEM) == sid), key=lambda a: arrays[a][0])),
            next((b for b, (_, bd) in sorted(batteries.items()) if bd.get(CONF_SYSTEM) == sid), None),
            tuple(sorted((c for c, (_, cd) in systems.items() if cd.get(CONF_PARENT) == sid),
                         key=lambda c: systems[c][0])))
    return out


def behaviour(systems: Mapping[str, System], sid: str, key: str) -> Any:
    """A behaviour setting of a system, inherited from its parent when it leaves it empty."""
    s = systems[sid]
    value = s.get(key)
    if value is None and s.parent is not None:
        value = systems[s.parent].get(key)
    return value


def charges_from_house(systems: Mapping[str, System], sid: str | None) -> bool:
    return sid in systems and systems[sid].get(CONF_CHARGES_FROM) == "house"


# How far Noxcast computes each setting (shown in the Installation tab): exact, simplified (with how) or not yet.
EXACT, SIMPLIFIED, NOT_YET = "exact", "simplified", "not_yet"


def effects(household: Mapping[str, Any], systems: Mapping[str, System], subs: Subs) -> list[dict[str, Any]]:
    """Per node of the tree the settings that are set and how far they count. Keys are stable for the frontend."""
    arrays = _of(subs, SUBENTRY_ARRAY)
    out: list[dict[str, Any]] = []

    def add(node: str, setting: str, status: str, value: Any = None) -> None:
        out.append({"node": node, "setting": setting, "status": status, "value": value})

    mode = household.get(CONF_FEED_IN_MODE, "none")
    # free and none are what the battery forecast and the day card have always known; a limit counts later
    add("house", "feed_in", NOT_YET if mode == "limited" else EXACT,
        household.get(CONF_FEED_IN_LIMIT) if mode == "limited" else mode)
    if household.get(CONF_SOC_SENSORS):
        add("house", "old_soc", EXACT, len(household[CONF_SOC_SENSORS]))
    # every battery is simulated in one storage: exact only where they all balance in one system
    with_battery = [s for s in systems.values() if s.battery]
    if len(with_battery) > 1:
        parents = {s.parent for s in with_battery}
        one = len(parents) == 1 and None not in parents and systems[next(iter(parents))].get(CONF_BALANCE)
        add("house", "one_storage", EXACT if one else SIMPLIFIED, len(with_battery))
    for aid, (_, d) in sorted(arrays.items()):
        if d.get(CONF_SENSOR_LIMITS):
            add(aid, "sensor_limit", EXACT, sorted(set(d[CONF_SENSOR_LIMITS].values())))
    for sid, s in sorted(systems.items()):
        limit = s.get(CONF_OUTPUT_LIMIT)
        if limit:
            # with a battery: the battery forecast caps PV and battery together (since 0.3); without one the cap
            # of the PV forecast and learning follow in a later step
            add(sid, "output_limit", EXACT if s.battery else NOT_YET, limit)
        if s.get(CONF_LIMIT_PER_PHASE):
            add(sid, "limit_per_phase", NOT_YET, True)
        if s.get(CONF_PHASE):
            add(sid, "phase", NOT_YET, s.get(CONF_PHASE))
        # "delivers everything" and "deliver everything when full" are what Noxcast has always assumed
        regulation = s.get(CONF_REGULATION)
        if regulation:
            add(sid, "regulation", EXACT if regulation == "all" else SIMPLIFIED if s.battery else NOT_YET, regulation)
        if s.get(CONF_FIXED_POWER):
            add(sid, "fixed_power", NOT_YET, s.get(CONF_FIXED_POWER))
        if s.get(CONF_WHEN_FULL):
            add(sid, "when_full", EXACT if s.get(CONF_WHEN_FULL) == "all" else NOT_YET, s.get(CONF_WHEN_FULL))
        if s.battery and s.get(CONF_CHARGES_FROM):
            add(sid, "charges_from", SIMPLIFIED, s.get(CONF_CHARGES_FROM))
        if s.children:
            # today every battery is simulated in one storage: exact when they balance
            add(sid, "balance", EXACT if s.get(CONF_BALANCE) else NOT_YET, bool(s.get(CONF_BALANCE)))
    return out


def tree(household: Mapping[str, Any], subs: Subs) -> dict[str, Any]:
    """The installation for the Installation tab: household -> systems -> subsystems -> arrays and battery."""
    systems = systems_of(subs)
    arrays = _of(subs, SUBENTRY_ARRAY)
    batteries = _of(subs, SUBENTRY_BATTERY)

    def node(sid: str) -> dict[str, Any]:
        s = systems[sid]
        return {
            "id": sid, "name": s.name, "template": s.get(CONF_TEMPLATE),
            "output_limit_w": s.get(CONF_OUTPUT_LIMIT), "limit_per_phase": bool(s.get(CONF_LIMIT_PER_PHASE)),
            "phase": s.get(CONF_PHASE),
            "regulation": behaviour(systems, sid, CONF_REGULATION), "fixed_power_w": s.get(CONF_FIXED_POWER),
            "when_full": behaviour(systems, sid, CONF_WHEN_FULL), "charges_from": s.get(CONF_CHARGES_FROM),
            "balance": bool(s.get(CONF_BALANCE)) if s.children else None,
            "arrays": [{"id": a, "name": arrays[a][0], "sensors": len(arrays[a][1].get(CONF_POWER_SENSORS) or []),
                        "sensor_limits": sorted(set((arrays[a][1].get(CONF_SENSOR_LIMITS) or {}).values()))}
                       for a in s.arrays],
            "battery": {"id": s.battery, "name": batteries[s.battery][0]} if s.battery else None,
            "children": [node(c) for c in s.children],
        }

    top = sorted((sid for sid, s in systems.items() if s.parent is None), key=lambda sid: systems[sid].name)
    return {
        "feed_in": household.get(CONF_FEED_IN_MODE, "none"), "feed_in_limit_w": household.get(CONF_FEED_IN_LIMIT),
        "old_soc": len(household.get(CONF_SOC_SENSORS) or []),
        "systems": [node(sid) for sid in top],
        "effects": effects(household, systems, subs),
    }
