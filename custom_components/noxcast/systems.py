"""The installation as systems (pure Python, no Home Assistant): household -> system -> subsystem.

A system is everything that delivers through one output to the house or to its parent system: its PV arrays,
at most one battery, its regulation. A system holds either arrays and a battery, or subsystems, never both, and
there are at most two levels. Every PV array belongs to a system. Each part names its parent (CONF_SYSTEM,
CONF_PARENT), so the tree is read from the children.

Stored, a system of the top level is one config subentry, and its subsystems, arrays and battery sit inside it
(PARTS), so the list of entries shows what belongs together. The functions here see every part flat, by its id
(flatten); nest gives the stored form back.

The common installations are templates of the same block, not types of their own (docs/concept.md,
installation model). A template only fills in the settings; every setting stays changeable.

Limits cascade: a subsystem's limit and its system's both hold, the tighter wins, and all subsystems together
never deliver more than their system. Behaviour (regulation, "when the battery is full") belongs to the system: its
subsystems are devices one control coordinates, so they follow it and have no choice of their own. A device that
does not follow that control is a system of its own. A subsystem keeps what is its own as a device: its output
limit, phase, battery and where that charges from.

The functions here work on plain data, (type, title, data) per subentry id; the shell applies their result.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .const import (
    CONF_GRID, CONF_BALANCE, CONF_BATTERY_ARRAY, CONF_BATTERY_POWER, CONF_CHARGES_FROM, CONF_COUPLING, CONF_EFFICIENCY, CONF_FEED_IN, CONF_FEED_IN_LIMIT,
    CONF_FEED_IN_MODE, CONF_FIXED_POWER, CONF_LIMIT_PER_PHASE, CONF_MAX_CHARGE, CONF_NAME, CONF_OUTPUT_LIMIT, CONF_PARENT,
    CONF_PHASE, CONF_POWER_SENSORS, CONF_REGULATION, CONF_SENSOR_LIMITS, CONF_SOC_SENSORS, CONF_SYSTEM,
    CONF_TEMPLATE, CONF_WHEN_FULL, CONF_OWN_FEED_IN, CONF_FEED_IN_PCT, CONF_ORIENTATIONS, CONF_PEAK_POWER, SUBENTRY_ARRAY, SUBENTRY_BATTERY, SUBENTRY_SYSTEM,
)

type Subs = Mapping[str, tuple[str, str, Mapping[str, Any]]]     # subentry id -> (type, title, data)

# what a template fills in; a setting not named stays empty (no limit); a subsystem takes no behaviour from it
TEMPLATES: dict[str, dict[str, Any]] = {
    "inverter": {CONF_REGULATION: "all"},
    "pv_battery": {CONF_CHARGES_FROM: "own_pv", CONF_REGULATION: "load"},
    "ac_battery": {CONF_CHARGES_FROM: "house", CONF_REGULATION: "load"},
    "coupled": {CONF_BALANCE: True, CONF_REGULATION: "load"},
}
# set on the system only; its subsystems follow it (behaviour). The feed-in deviation is optional: without one the
# system follows the household's feed-in rule (an outer rule holds inside unless a deviation is set)
BEHAVIOUR = (CONF_REGULATION, CONF_FIXED_POWER, CONF_OWN_FEED_IN)
GROUP = "group"          # the former template "only group them": independent systems, dissolved by normalize
AC_EFFICIENCY_PCT = 90                               # battery.DEFAULT_EFFICIENCY of the former AC coupling


@dataclass(frozen=True)
class Changes:
    """What the shell applies: new system subentries (id, title, data), new data of existing subentries, and
    systems removed (left empty)."""

    add: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    update: dict[str, dict[str, Any]] = field(default_factory=dict)
    remove: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.add or self.update or self.remove)


def apply(subs: Subs, changes: Changes) -> dict[str, tuple[str, str, Mapping[str, Any]]]:
    """The parts with the changes made: new systems added, changed data replaced, removed ones gone."""
    out = dict(subs)
    for sid, title, data in changes.add:
        out[sid] = (SUBENTRY_SYSTEM, title, data)
    for sid, data in changes.update.items():
        out[sid] = (out[sid][0], out[sid][1], data)
    for sid in changes.remove:
        out.pop(sid, None)
    return out


PARTS = "parts"          # in a system subentry: its subsystems, arrays and battery, id -> {type, title, data}
INSTALLATION = (SUBENTRY_SYSTEM, SUBENTRY_ARRAY, SUBENTRY_BATTERY)


def flatten(stored: Subs) -> dict[str, tuple[str, str, Mapping[str, Any]]]:
    """Every part by its own id, as the rest of Noxcast sees it. Subentries without parts (consumers, and arrays
    or batteries stored on their own before 1.6) stay as they are."""
    out: dict[str, tuple[str, str, Mapping[str, Any]]] = {}
    for sid, (kind, title, data) in stored.items():
        parts = data.get(PARTS) if kind == SUBENTRY_SYSTEM else None
        out[sid] = (kind, title, {k: v for k, v in data.items() if k != PARTS} if parts is not None else data)
        for pid, p in (parts or {}).items():
            out[pid] = (p["type"], p["title"], p["data"])
    return out


def home(subs: Subs, part: str) -> str | None:
    """The system of the top level a part belongs to, whose subentry stores it; None for a part outside the
    installation or without a system."""
    kind, _, data = subs[part]
    systems = _of(subs, SUBENTRY_SYSTEM)
    sid = part if kind == SUBENTRY_SYSTEM else data.get(CONF_SYSTEM) if kind in INSTALLATION else None
    if sid not in systems:
        return None
    parent = systems[sid][1].get(CONF_PARENT)
    if parent in systems and systems[parent][1].get(CONF_PARENT) in systems:
        return None              # three levels: normalize drops the parent at the next setup
    return parent if parent in systems else sid


def nest(subs: Subs) -> dict[str, tuple[str, str, Mapping[str, Any]]]:
    """The stored form of flat parts: per system of the top level one subentry with its parts inside, in the
    order of the flat parts. A part without a system and every other subentry stay on their own."""
    out: dict[str, tuple[str, str, Mapping[str, Any]]] = {}
    inside: dict[str, dict[str, Any]] = {}
    for pid, (kind, title, data) in subs.items():
        top = home(subs, pid) if kind in INSTALLATION else None
        if top is None or top == pid:
            out[pid] = (kind, title, data)
        else:
            inside.setdefault(top, {})[pid] = {"type": kind, "title": title, "data": dict(data)}
    for top, parts in inside.items():
        kind, title, data = out[top]
        out[top] = (kind, title, {**data, PARTS: parts})
    return out


def template_data(template: str, **values: Any) -> dict[str, Any]:
    return {CONF_TEMPLATE: template, **TEMPLATES.get(template, {}),
            **{k: v for k, v in values.items() if v is not None}}


def _of(subs: Subs, kind: str) -> dict[str, tuple[str, Mapping[str, Any]]]:
    return {sid: (title, data) for sid, (t, title, data) in subs.items() if t == kind}


def _without(data: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k not in keys}


def _template_of(subs: Subs, sid: str) -> str:
    """The template that fits what a system holds: with a battery, PV and battery in one device when it has PV
    arrays as well, else a battery at the house; else an inverter."""
    arrays = any(d.get(CONF_SYSTEM) == sid for _, d in _of(subs, SUBENTRY_ARRAY).values())
    battery = any(d.get(CONF_SYSTEM) == sid for _, d in _of(subs, SUBENTRY_BATTERY).values())
    return ("pv_battery" if arrays else "ac_battery") if battery else "inverter"


def normalize(subs: Subs, new_id: Callable[[], str]) -> Changes:
    """Every array and battery belongs to an existing system; a parent that is gone, or is itself a subsystem,
    is dropped. A new array without a system (or whose system was deleted) gets a system of its own.

    Behaviour belongs to the system: a subsystem's own regulation or "when full" is dropped, and lifted to its
    system where that has none and its subsystems agreed. A former group ("only group them") held independent
    systems: they stand on their own with the behaviour they had, and the empty group goes. A group that shared
    a limit or balanced its batteries coordinated them: it becomes coupled.
    """
    data_of = {sid: dict(data) for sid, (_, data) in _of(subs, SUBENTRY_SYSTEM).items()}
    owned = {d.get(CONF_SYSTEM) for kind in (SUBENTRY_ARRAY, SUBENTRY_BATTERY) for _, d in _of(subs, kind).values()}
    changes = Changes()

    def put(sid: str, data: dict[str, Any]) -> None:
        data_of[sid] = changes.update[sid] = data

    for sid, data in sorted(data_of.items()):
        parent = data.get(CONF_PARENT)
        if parent and (parent not in data_of or data_of[parent].get(CONF_PARENT)):
            put(sid, _without(data, CONF_PARENT))
    for gid, group in sorted(data_of.items()):
        if group.get(CONF_TEMPLATE) != GROUP:
            continue
        if group.get(CONF_OUTPUT_LIMIT) or group.get(CONF_BALANCE):
            put(gid, {**group, CONF_TEMPLATE: "coupled"})
            continue
        for c, d in sorted(data_of.items()):
            if d.get(CONF_PARENT) == gid:
                put(c, {**{k: group[k] for k in BEHAVIOUR if group.get(k) is not None},
                        **_without(d, CONF_PARENT, CONF_PHASE, CONF_LIMIT_PER_PHASE)})
        if gid in owned or group.get(CONF_PARENT):
            put(gid, {**group, CONF_TEMPLATE: _template_of(subs, gid)})
        else:
            changes.remove.append(gid)
            changes.update.pop(gid, None)
            del data_of[gid]
    for pid, pdata in sorted(data_of.items()):
        kids = sorted(c for c, d in data_of.items() if d.get(CONF_PARENT) == pid)
        if not kids:
            continue
        lifted = dict(pdata)
        # the regulation goes up with its fixed power, as one setting; the feed-in deviation on its own, and only
        # where every subsystem has it: an empty one follows the household, which is a choice as well
        for keys in ((CONF_REGULATION, CONF_FIXED_POWER), (CONF_OWN_FEED_IN,)):
            values = []
            for c in kids:
                v = tuple(data_of[c].get(k) for k in keys)
                if (v[0] is not None or keys[0] == CONF_OWN_FEED_IN) and v not in values:
                    values.append(v)
            if values == [(None,)]:
                continue
            if lifted.get(keys[0]) is None and len(values) == 1:
                lifted.update({k: x for k, x in zip(keys, values[0])
                               if x is not None and (k != CONF_FIXED_POWER or values[0][0] == "fixed")})
        if lifted != pdata:
            put(pid, lifted)
        for c in kids:
            if any(k in data_of[c] for k in BEHAVIOUR):
                put(c, _without(data_of[c], *BEHAVIOUR))
    systems = data_of
    # a battery left without system has no PV of its own known: at the house is the case that cuts no PV
    for kind, template in ((SUBENTRY_ARRAY, "inverter"), (SUBENTRY_BATTERY, "ac_battery")):
        for sid, (title, data) in sorted(_of(subs, kind).items()):
            if data.get(CONF_SYSTEM) in systems:
                continue
            new = new_id()
            changes.add.append((new, title, template_data(template, **{CONF_NAME: title})))
            changes.update[sid] = {**data, CONF_SYSTEM: new}
    return changes


def feed_in_rules(household: Mapping[str, Any], subs: Subs) -> Changes:
    """1.7: "when the battery is full" becomes a deviation from the household's feed-in rule where the two
    disagreed, so every system computes as before.

    - Delivering everything when full under zero export: the system feeds the rest in anyway.
    - Covering only the load when full while the household may feed in: the system never feeds in.
    - Full power without that setting under zero export: such an inverter cannot hold the export at zero, it fed
      in what was left before too.
    A subsystem's old setting (stored before 1.6) becomes its deviation the same way, with its system's regulation;
    normalize then lifts what all subsystems agree on to their system, as before. Runs before that normalize.
    """
    mode = household.get(CONF_FEED_IN_MODE, "none")
    changes = Changes()
    systems = _of(subs, SUBENTRY_SYSTEM)
    for sid, (title, d) in sorted(systems.items()):
        parent = systems.get(d.get(CONF_PARENT) or "", ("", {}))[1]
        regulation = d.get(CONF_REGULATION) or parent.get(CONF_REGULATION)
        if CONF_WHEN_FULL not in d and (d.get(CONF_PARENT) or not (regulation == "all" and mode == "none")):
            continue
        new = _without(d, CONF_WHEN_FULL)
        if not d.get(CONF_OWN_FEED_IN):
            full = d.get(CONF_WHEN_FULL) or ("all" if regulation == "all" else None)
            if full == "all" and mode == "none":
                new[CONF_OWN_FEED_IN] = "export"
            elif full == "load" and mode != "none":
                new[CONF_OWN_FEED_IN] = "never"
        if new != dict(d):
            changes.update[sid] = new
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
        made = template_data(template, **{CONF_NAME: title, CONF_OUTPUT_LIMIT: d.get(CONF_OUTPUT_LIMIT), CONF_PARENT: parent})
        # under the balancing system the behaviour is that system's (normalize)
        changes.add.append((system, title, _without(made, *BEHAVIOUR) if parent else made))
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


def part_owners(systems: Mapping[str, System], battery: str | None = None) -> dict[str, str]:
    """Systems that can take a PV array or a battery: those without subsystems; for a battery, those without one
    (battery: its own id, kept where it is)."""
    return {sid: s.name for sid, s in sorted(systems.items(), key=lambda kv: kv[1].name)
            if not s.children and (battery is None or s.battery in (None, battery))}


def parent_owners(systems: Mapping[str, System], own: str | None) -> dict[str, str]:
    """Systems that can take a system as their subsystem: top level, no arrays or battery of their own (either,
    never both). None for a system that has subsystems itself (two levels at most)."""
    if own in systems and systems[own].children:
        return {}
    return {sid: s.name for sid, s in sorted(systems.items(), key=lambda kv: kv[1].name)
            if sid != own and s.parent is None and not s.arrays and not s.battery}


def owners(subs: Subs, part: str) -> tuple[str, dict[str, str]] | None:
    """Where a part can be moved, for the Installation tab: (where it is now, {system id: name}). A system can
    also stand on its own, the key "". None for an unknown part."""
    if part not in subs:
        return None
    kind, _, data = subs[part]
    systems = systems_of(subs)
    if kind == SUBENTRY_SYSTEM:
        return (systems[part].parent or ""), {"": "", **parent_owners(systems, part)}
    if kind == SUBENTRY_ARRAY:
        return data.get(CONF_SYSTEM) or "", part_owners(systems)
    if kind == SUBENTRY_BATTERY:
        return data.get(CONF_SYSTEM) or "", part_owners(systems, part)
    return None


def moved(subs: Subs, part: str, to: str) -> dict[str, Any] | None:
    """The data of a part moved to another system (a system: to another parent, "" for none), or None where the
    move is not possible."""
    options = owners(subs, part)
    if options is None or to not in options[1]:
        return None
    kind, _, data = subs[part]
    key = CONF_PARENT if kind == SUBENTRY_SYSTEM else CONF_SYSTEM
    if kind != SUBENTRY_SYSTEM:
        return {**_without(data, key), **({key: to} if to else {})}
    if to:
        # a subsystem follows its new system: what it did on its own goes
        return {**_without(data, key, *BEHAVIOUR), key: to}
    # on its own it keeps behaving as its system made it; the phase is a setting of a subsystem only (the form
    # drops it the same way when the parent goes)
    before = systems_of(subs)[part].parent
    inherited = {k: subs[before][2][k] for k in BEHAVIOUR if before and subs[before][2].get(k) is not None}
    return {**inherited, **_without(data, key, CONF_PHASE)}


def behaviour(systems: Mapping[str, System], sid: str, key: str) -> Any:
    """A behaviour setting of a system; a subsystem follows its system's."""
    s = systems[sid]
    return systems[s.parent].get(key) if s.parent is not None else s.get(key)


def feed_in(household: Mapping[str, Any], systems: Mapping[str, System], sid: str) -> tuple[str, bool]:
    """What a system may feed in: free, limited, percent or none, and whether that is its own deviation (or its
    system's) rather than the household's rule."""
    own = behaviour(systems, sid, CONF_OWN_FEED_IN)
    if own:
        return ("free" if own == "export" else "none"), True
    return household.get(CONF_FEED_IN_MODE, "none"), False


def exports(household: Mapping[str, Any], systems: Mapping[str, System]) -> bool:
    """Whether anything of the household can go into the grid: its rule allows it, or a system feeds in anyway."""
    return any(feed_in(household, systems, sid)[0] != "none" for sid in systems) or (
        not systems and household.get(CONF_FEED_IN_MODE, "none") != "none")


def _rule_value(household: Mapping[str, Any], subs: Subs) -> Any:
    """The household's feed-in rule for the Installation tab: free / none, the limit in W, or {"pct": n, "w": W}."""
    mode = household.get(CONF_FEED_IN_MODE, "none")
    if mode == "limited":
        return household.get(CONF_FEED_IN_LIMIT)
    if mode == "percent":
        w = feed_in_limit_w(household, subs)
        return {"pct": household.get(CONF_FEED_IN_PCT), **({"w": round(w)} if w else {})}
    return mode


def peak_w(subs: Subs) -> float:
    """The household's installed PV peak power in W: every array with its further orientations."""
    total = 0.0
    for _, d in _of(subs, SUBENTRY_ARRAY).values():
        total += float(d.get(CONF_PEAK_POWER) or 0.0)
        total += sum(float(o[CONF_PEAK_POWER]) for o in (d.get(k) or {} for k in CONF_ORIENTATIONS)
                     if o.get(CONF_PEAK_POWER) is not None)
    return total


def feed_in_limit_w(household: Mapping[str, Any], subs: Subs) -> float | None:
    """The household's feed-in limit in W: the limit itself, or its share of the installed peak power (the grid
    operator's 60 % rule counts the modules' peak power). None for free and zero export, or where it is not set."""
    mode = household.get(CONF_FEED_IN_MODE, "none")
    if mode == "limited" and household.get(CONF_FEED_IN_LIMIT):
        return float(household[CONF_FEED_IN_LIMIT])
    if mode == "percent" and household.get(CONF_FEED_IN_PCT):
        w = float(household[CONF_FEED_IN_PCT]) / 100 * peak_w(subs)
        return w if w > 0 else None
    return None


def feed_in_rule(household: Mapping[str, Any], systems: Mapping[str, System],
                 subs: Subs) -> tuple[float | None, tuple[str, ...], tuple[str, ...]]:
    """What the battery forecast may feed in (battery.feed_in): the household's limit in W (None = free, 0 = zero
    export), and the arrays of systems whose own deviation always and never feeds in. A limit that is not set
    (percent without peak power) counts as free: nothing says what is held back."""
    mode = household.get(CONF_FEED_IN_MODE, "none")
    limit = 0.0 if mode == "none" else feed_in_limit_w(household, subs) if mode in ("limited", "percent") else None
    own = {sid: feed_in(household, systems, sid) for sid in systems}
    free = tuple(sorted(a for sid, (m, dev) in own.items() if dev and m == "free" for a in systems[sid].arrays))
    never = tuple(sorted(a for sid, (m, dev) in own.items() if dev and m == "none" for a in systems[sid].arrays))
    return limit, free, never


def export_limited_arrays(household: Mapping[str, Any], systems: Mapping[str, System]) -> tuple[str, ...]:
    """The arrays of systems that follow the household's feed-in limit, without a deviation of their own: an hour
    in which the household's export reached the limit may have held their PV back."""
    return tuple(sorted(a for sid, s in systems.items()
                        if feed_in(household, systems, sid)[0] in ("limited", "percent") for a in s.arrays))


def charges_from_house(systems: Mapping[str, System], sid: str | None) -> bool:
    return sid in systems and systems[sid].get(CONF_CHARGES_FROM) == "house"


def charge_curtails(systems: Mapping[str, System], sid: str) -> bool:
    """A battery charging at its maximum power curtails PV only where the rest cannot go out: its system follows
    the house load or an output limit holds above it (its own or its system's). The household's zero export does
    not count on its own: what holds the export at zero is the system's regulation, and one that delivers
    everything lets the rest go out."""
    s = systems[sid]
    above = [s, *([systems[s.parent]] if s.parent is not None else [])]
    return behaviour(systems, sid, CONF_REGULATION) == "load" or any(x.get(CONF_OUTPUT_LIMIT) for x in above)


def coordinated(systems: Mapping[str, System], sid: str) -> bool:
    """Whether a battery takes what the system holds back: one in the system, in its subsystems, or in a sibling
    under the same control."""
    s = systems[sid]
    below = [s, *(systems[c] for c in s.children)]
    return any(x.battery for x in below) or (
        s.parent is not None and any(systems[c].battery for c in systems[s.parent].children))


def held_back_arrays(systems: Mapping[str, System]) -> tuple[str, ...]:
    """The arrays of systems that follow the house load without a battery to take the rest: they deliver only what
    the house draws, so an hour in which the household stopped importing may have held PV back."""
    return tuple(sorted(a for sid, s in systems.items() if not s.children
                        and behaviour(systems, sid, CONF_REGULATION) == "load" and not coordinated(systems, sid)
                        for a in s.arrays))


def curtailed_arrays(systems: Mapping[str, System]) -> dict[str, tuple[str, ...]]:
    """System id (with a battery) -> the arrays its battery may curtail when full (chain of ceilings).

    Its own arrays: full, it takes nothing more. Under a parent: the arrays of every subsystem that follows the house
    load together with it, and with a shared output limit those of subsystems without a battery, which the limit may
    hold back. Nothing outside its system: a battery raises another device's ceiling only through a control that
    knows both. A battery at the house grid that is no part of the PV's system, even with zero export, takes only
    what is fed in anyway; the inverter that holds the export at zero does not know it is there, so it curtails the
    same with the battery or without.
    """
    out: dict[str, tuple[str, ...]] = {}
    for s in sorted(systems.values(), key=lambda s: s.id):
        if s.battery is None:
            continue
        found = list(s.arrays)
        if s.parent is not None:
            parent = systems[s.parent]
            for c in (systems[c] for c in parent.children):
                if behaviour(systems, c.id, CONF_REGULATION) == "load" and behaviour(systems, s.id, CONF_REGULATION) == "load" \
                        or (c.battery is None and parent.get(CONF_OUTPUT_LIMIT)):
                    found += c.arrays
        out[s.id] = tuple(dict.fromkeys(found))
    return out


# How far Noxcast computes each setting (shown in the Installation tab): exact, simplified (with how) or not yet.
EXACT, SIMPLIFIED, NOT_YET = "exact", "simplified", "not_yet"


def effects(household: Mapping[str, Any], systems: Mapping[str, System], subs: Subs) -> list[dict[str, Any]]:
    """Per node of the tree the settings that are set and how far they count. Keys are stable for the frontend."""
    arrays = _of(subs, SUBENTRY_ARRAY)
    out: list[dict[str, Any]] = []

    def add(node: str, setting: str, status: str, value: Any = None, inherited: bool = False) -> None:
        out.append({"node": node, "setting": setting, "status": status, "value": value,
                    **({"inherited": True} if inherited else {})})

    mode = household.get(CONF_FEED_IN_MODE, "none")
    curtailed = curtailed_arrays(systems)
    batteries = {bid: d for bid, (_, d) in _of(subs, SUBENTRY_BATTERY).items()}
    # free and none are what the battery forecast and the day card have always known; a limit counts in learning,
    # known from the grid sensor: hours with the export at it count as "at least"; and in the battery forecast,
    # which feeds in the surplus without room up to it (split by the systems' shares of the PV)
    limited = mode in ("limited", "percent")
    grid = bool(household.get(CONF_GRID))
    limit_w = feed_in_limit_w(household, subs) if limited else None
    # it counts only where an array follows it; every system deviating, nothing does
    counts = bool(grid and limit_w and export_limited_arrays(household, systems))
    splits = bool(limit_w and _of(subs, SUBENTRY_BATTERY) and export_limited_arrays(household, systems))
    add("house", "feed_in", (SIMPLIFIED if counts or splits else NOT_YET) if limited else EXACT,
        _rule_value(household, subs))
    if limited and limit_w and export_limited_arrays(household, systems):
        add("house", "limit_learning", SIMPLIFIED if grid else NOT_YET, round(limit_w) if grid else None)
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
        below = [s, *(systems[c] for c in s.children)]
        limit = s.get(CONF_OUTPUT_LIMIT)
        if limit:
            add(sid, "output_limit", _limit_status(s, below), limit)
        if s.get(CONF_LIMIT_PER_PHASE):
            add(sid, "limit_per_phase", NOT_YET, True)
        if s.get(CONF_PHASE):
            add(sid, "phase", NOT_YET, s.get(CONF_PHASE))
        # "delivers everything" and "deliver everything when full" are what Noxcast has always assumed
        # a subsystem shows its system's behaviour, marked as that (it has none of its own)
        sub = s.parent is not None
        regulation = behaviour(systems, sid, CONF_REGULATION)
        if regulation:
            # following the load: learning leaves out the hours with a full battery it coordinates with; without
            # one, hours in which the household stopped importing count as "at least" (needs the grid sensor)
            follows = regulation == "load" and (coordinated(systems, sid) or bool(household.get(CONF_GRID)))
            add(sid, "regulation", EXACT if regulation == "all" else SIMPLIFIED if follows else NOT_YET,
                regulation, sub)
        if regulation == "fixed" and behaviour(systems, sid, CONF_FIXED_POWER):
            add(sid, "fixed_power", NOT_YET, behaviour(systems, sid, CONF_FIXED_POWER), sub)
        # the feed-in rule it follows: the household's, or its own deviation (a subsystem: its system's).
        # Feeding in what is left is what Noxcast has always assumed; holding it at zero counts through the
        # battery whose full hours learning leaves out; a limit through the hours at it (house row limit_learning)
        effective, own = feed_in(household, systems, sid)
        if s.battery or s.children or own or mode != "free":
            batteries_below = any(x.battery for x in below) or (
                sub and any(systems[c].battery for c in systems[s.parent].children))
            status = EXACT if effective == "free" else SIMPLIFIED if (
                effective == "none" and batteries_below
                # the limit counts for its PV in learning, and for its battery in the forecast of what is fed in
                or effective in ("limited", "percent") and (counts and any(x.arrays for x in below)
                                                            or splits)
            ) else NOT_YET
            if own:
                add(sid, "feed_in_rule", status, behaviour(systems, sid, CONF_OWN_FEED_IN), sub)
            else:
                out.append({"node": sid, "setting": "feed_in_rule", "status": status, "value": _rule_value(household, subs),
                            "inherited": "house"})
        if s.arrays and regulation == "load" and not coordinated(systems, sid):
            # what it held back for a low load: hours with the household importing little count as "at least"
            grid = bool(household.get(CONF_GRID))
            add(sid, "held_learning", SIMPLIFIED if grid else NOT_YET, True if grid else None)
        if s.battery and s.get(CONF_CHARGES_FROM):
            add(sid, "charges_from", SIMPLIFIED, s.get(CONF_CHARGES_FROM))
        if curtailed.get(sid):
            # every hour with the battery full is left out, also one in which the output stayed below its ceiling
            add(sid, "full_learning", SIMPLIFIED, len(curtailed[sid]))
            if charge_curtails(systems, sid):
                # charging at its maximum it takes no more either: a lower bound, known from its power sensor and
                # maximum
                d = batteries.get(s.battery or "", {})
                known = bool(d.get(CONF_BATTERY_POWER) and d.get(CONF_MAX_CHARGE))
                add(sid, "charge_learning", SIMPLIFIED if known else NOT_YET, d.get(CONF_MAX_CHARGE) if known else None)
        if s.children:
            # today every battery is simulated in one storage: exact when they balance
            add(sid, "balance", EXACT if s.get(CONF_BALANCE) else NOT_YET, bool(s.get(CONF_BALANCE)))
    return out


def _limit_status(s: System, below: list[System]) -> str:
    """An output limit with a battery below it counts in the battery forecast (its own exactly; one shared by
    subsystems as the cap of their batteries together). Without a battery it is a fixed limit: learning counts the
    hours at it as lower bounds, and it caps the PV forecast, the sum of its PV arrays where it holds several.
    Per phase it waits for the phases."""
    if s.get(CONF_LIMIT_PER_PHASE) and not s.battery:
        return NOT_YET
    if any(x.battery for x in below):
        return EXACT if s.battery else SIMPLIFIED
    return EXACT


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
            "regulation": behaviour(systems, sid, CONF_REGULATION), "fixed_power_w": behaviour(systems, sid, CONF_FIXED_POWER),
            "own_feed_in": behaviour(systems, sid, CONF_OWN_FEED_IN), "charges_from": s.get(CONF_CHARGES_FROM),
            "balance": bool(s.get(CONF_BALANCE)) if s.children else None,
            "arrays": [{"id": a, "name": arrays[a][0], "sensors": len(arrays[a][1].get(CONF_POWER_SENSORS) or []),
                        "sensor_limits": sorted(set((arrays[a][1].get(CONF_SENSOR_LIMITS) or {}).values()))}
                       for a in s.arrays],
            "battery": {"id": s.battery, "name": batteries[s.battery][0]} if s.battery else None,
            "children": [node(c) for c in s.children],
        }

    top = sorted((sid for sid, s in systems.items() if s.parent is None), key=lambda sid: systems[sid].name)
    moves = {}
    for part in subs:
        if (o := owners(subs, part)) is not None:
            moves[part] = {"now": o[0], "to": [[sid, name] for sid, name in o[1].items()]}
    return {
        "feed_in": household.get(CONF_FEED_IN_MODE, "none"), "feed_in_limit_w": household.get(CONF_FEED_IN_LIMIT),
        "feed_in_limit_pct": household.get(CONF_FEED_IN_PCT),
        "old_soc": len(household.get(CONF_SOC_SENSORS) or []),
        "systems": [node(sid) for sid in top],
        "effects": effects(household, systems, subs),
        # per array, battery and system: where it sits and where it can go ("" = a system on its own)
        "moves": moves,
    }
