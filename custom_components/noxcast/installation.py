"""What lies between the modules and the house: the installation as configured (pure Python, no Home Assistant).

Power flows from the inputs (PV power sensors) through the systems (systems.py) to the house, and every level can
hold a limit. The chain of ceilings (docs/concept.md, Installation model) decides what each one means:

- A limit is fixed when no battery sits below it: the PV can never exceed it, so it caps the forecast and the
  hours at it are lower bounds in learning. That holds for an input (MPPT, micro-inverter) and for the output limit
  of a system without a battery, alone or shared by its subsystems.
- A battery's full state is a state, no cap: the hours it may have cut are left out of learning instead. Which
  batteries count for an array follows from where they sit (Reach): the battery of its own system takes its PV
  first; batteries of systems under the same control take what reaches them over AC. The PV is curtailed only when
  the own battery takes no more and none of the others does either, or when the own system's output limit keeps
  the rest from reaching them (blocked).
- A battery charging at its maximum power takes no more either, where the rest cannot go out
  (systems.charge_curtails): a ceiling of output plus charge power. The hour delivered at least what was measured,
  so it is a lower bound in learning like a fixed limit (battery_hours). Leaving it out instead would select on the
  measured PV itself, dropping the bright hours and bending the learned efficiency down (#248, E1). The charge power
  is read from means, never from a period's maximum: a power sensor, above all one computed from several others,
  shows spikes of a second far above what the battery can take, and one of them would turn the whole hour into a
  lower bound. Where the 5-minute means are kept (fine.py) they decide; older hours have only their hourly mean,
  which says less, so an hour in between is left out (battery_hours). Charging at its maximum holds back none
  of the own PV where the own arrays' fixed limits together stay at or below that maximum: what it charges beyond
  them came from elsewhere.
- An output limit with a battery below it is the other half of that ceiling: the PV leaves the system through the
  output or into the battery, nowhere else. Its output is measured as the PV less what the batteries charge (a
  discharge counts as nothing: the controller lowers it before it cuts PV); a period in which it reached the limit delivered at least what was measured, whatever the
  battery took, so it is a lower bound in learning too (Outlet, output_hours). Overestimating the output only turns
  an exact hour into a lower bound; missing one teaches a cut as shade.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import systems as sy
from .const import (
    CONF_BATTERY_INVERT, CONF_BATTERY_POWER, CONF_LIMIT_PER_PHASE, CONF_MAX_CHARGE, CONF_MAX_SOC, CONF_OUTPUT_LIMIT,
    CONF_SOC_SENSOR,
)

HOUR = 3600.0
FULL_TOLERANCE = 2.0             # percent points below its upper limit from which a battery counts as full
CHARGE_MARGIN = 0.05             # a battery charges at its maximum from 95 % of it (5-minute mean)
# an hour known only by its mean: from 85 % of the maximum the battery stood at it for most of the hour; from 70 % it
# may have for a part of it (a ramp up to the maximum, a plateau broken by clouds), so the hour is left out; below,
# it held nothing back
HOUR_AT_MAX = 0.85
HOUR_UNSURE = 0.70
# a device that follows the house load regulates the grid to about zero: below this import at some time in an hour
# it may have held PV back. Zero-export controllers keep an import of 10-100 W as margin; marking an hour too many
# only turns an exact value into a lower bound, missing one teaches too little PV
HELD_IMPORT_W = 100.0
# an hour in which the household's export came this close to its feed-in limit may have held PV back: a share of
# the limit, at least HELD_IMPORT_W (controllers keep a margin below the limit as below zero)
LIMIT_MARGIN = 0.05
# a system's output counts as at its limit from 95 % of it, as a fixed limit (curtailment.LIMIT_MARGIN). The output is
# measured on the DC side (PV less charge), a few percent above the AC output the limit holds: the margin covers it
OUTPUT_MARGIN = 0.05
FINE_STEP = 300.0                # seconds, the periods of the 5-minute archive (fine.STEP)


def limit_threshold(limit_w: float) -> float:
    """Grid power (W, export negative) below which an hour counts as at the feed-in limit."""
    return -(limit_w - max(HELD_IMPORT_W, LIMIT_MARGIN * limit_w))


@dataclass(frozen=True)
class Input:
    """One PV power sensor, part of an array (the learning unit)."""

    sensor: str
    array: str                           # array subentry id
    limit_w: float | None = None         # fixed limit in W (MPPT, micro-inverter), None = none known


@dataclass(frozen=True)
class Charger:
    """A battery's power sensor (> 0 = charging, unless inverted) and its maximum charge power in W."""

    sensor: str
    invert: bool
    max_w: float

    def at_max(self, charging_w: float | None) -> bool:
        """At its maximum, judged by a 5-minute mean (or a value as short)."""
        return charging_w is not None and charging_w >= (1 - CHARGE_MARGIN) * self.max_w


@dataclass(frozen=True)
class Meter:
    """A battery's power sensor (> 0 = charging, unless inverted), where its maximum is not needed."""

    sensor: str
    invert: bool


@dataclass(frozen=True)
class Store:
    """A battery whose full state may curtail PV: its state-of-charge sensor and from what % it counts as full;
    charger: set where charging at its maximum power curtails PV too and the sensor and maximum are known."""

    sensor: str
    full_pct: float
    charger: Charger | None = None


@dataclass(frozen=True)
class Reach:
    """The batteries that take an array's PV before it is curtailed (chain of ceilings).

    own: the battery of the array's own system, which takes its PV first. others: those of systems under the same
    control, which take what reaches them over AC. own_charge: charging at its maximum can hold back own PV: the
    own arrays' fixed limits together exceed that maximum, or are not all known. overflow: the own system's output
    limit is below what its arrays can give (or that is not known), so a blocked own battery curtails whatever the
    others could still take.
    """

    own: tuple[Store, ...] = ()
    others: tuple[Store, ...] = ()
    own_charge: bool = True
    overflow: bool = False


Hours = set[float]


def blocked(reach: Reach, full: Mapping[Store, Hours], at_max: Mapping[Store, Hours],
            unsure: Mapping[Store, Hours]) -> tuple[Hours, Hours, Hours]:
    """(full, at its maximum, unsure) period starts in which the array's PV may have been curtailed, from each
    battery's own periods; disjoint, the first wins.

    A battery is blocked when full or charging at its maximum. The own battery has to be blocked (an array without
    one sends everything onwards); then, unless the own system's output limit holds the rest back (overflow), every
    other battery too. Such a period counts as full when a full battery took part, which learning leaves out;
    otherwise as at its maximum, a lower bound. Where only an unsure charge period, or another battery whose charge
    power is not known, completes the block, it is unsure.
    """
    def union(stores: Iterable[Store], *of: Mapping[Store, Hours]) -> Hours:
        return set().union(*(m.get(s, set()) for s in stores for m in of))

    def every(stores: tuple[Store, ...], *of: Mapping[Store, Hours]) -> Hours:
        return set.intersection(*(union((s,), *of) for s in stores))

    out_full: Hours = set()
    out_max: Hours = set()
    out_unsure: Hours = set()
    if reach.own or reach.others:
        own_full = union(reach.own, full)
        own_max = union(reach.own, at_max) - own_full if reach.own_charge else set()
        own_unsure = union(reach.own, unsure) - own_full - own_max if reach.own_charge else set()
        if reach.others and not (reach.own and reach.overflow):
            sure = every(reach.others, full, at_max)
            # a battery without a known charge power may have been at its maximum whenever the others were
            # blocked: such a period is left out rather than counted as exact
            anyone = union(reach.own + reach.others, full, at_max, unsure)
            maybe = set.intersection(*(union((s,), full, at_max, unsure) if s.charger else anyone
                                       for s in reach.others))
            if reach.own:
                sure &= own_full | own_max
                maybe &= own_full | own_max | own_unsure
            out_full = sure & (own_full | union(reach.others, full))
            out_max = sure - out_full
            out_unsure = maybe - sure
        else:
            out_full, out_max, out_unsure = own_full, own_max, own_unsure
    return out_full, out_max - out_full, out_unsure - out_full - out_max


@dataclass(frozen=True)
class Cap:
    """A fixed limit on the summed output of arrays: a system with an output limit and no battery below it."""

    system: str
    arrays: tuple[str, ...]
    limit_w: float


@dataclass(frozen=True)
class Outlet:
    """An output limit with batteries below it: what the arrays' PV gives, less what the batteries charge, leaves
    through it. Not fixed, so no forecast cap; a measured state, read period by period (output_hours)."""

    system: str
    arrays: tuple[str, ...]
    limit_w: float
    meters: tuple[Meter, ...]            # the power sensors of every battery below the limit


@dataclass(frozen=True)
class Installation:
    inputs: Mapping[str, Input] = field(default_factory=dict)                # by sensor
    reach: Mapping[str, Reach] = field(default_factory=dict)                 # array -> batteries that take its PV
    caps: tuple[Cap, ...] = ()
    # array -> grid power (W, export negative) below which its system may have held PV back: HELD_IMPORT_W for
    # one that follows the load without a battery, limit_threshold under the household's feed-in limit
    held: Mapping[str, float] = field(default_factory=dict)
    outlets: tuple[Outlet, ...] = ()

    def held_hours(self, array: str, grid_low: Mapping[float, float]) -> set[float]:
        """Period starts in which the array's system may have held PV back: the household's lowest grid import
        (W, export negative) in the period fell below the array's threshold (for a low load, or the export
        reached the feed-in limit)."""
        if array not in self.held:
            return set()
        return {t for t, w in grid_low.items() if w < self.held[array]}

    def inputs_of(self, array: str) -> list[Input]:
        return [i for i in self.inputs.values() if i.array == array]

    def stores_of(self, array: str) -> tuple[Store, ...]:
        """The batteries whose state may curtail the array: those of its systems."""
        r = self.reach.get(array, Reach())
        return tuple(dict.fromkeys(r.own + r.others))

    def blocked(self, array: str, full: Mapping[Store, Hours], at_max: Mapping[Store, Hours],
                unsure: Mapping[Store, Hours] | None = None) -> tuple[Hours, Hours, Hours]:
        """blocked() for the array."""
        return blocked(self.reach.get(array, Reach()), full, at_max, unsure or {})

    def soc_sensors(self) -> list[str]:
        """Every state-of-charge sensor that may curtail some array."""
        return sorted({s.sensor for a in {i.array for i in self.inputs.values()} for s in self.stores_of(a)})

    def chargers(self) -> list[Charger]:
        """Every battery power sensor whose maximum may curtail some array."""
        return sorted({st.charger for r in self.reach.values() for st in r.own + r.others if st.charger},
                      key=lambda c: c.sensor)

    def meters(self) -> list[Meter]:
        """Every battery power sensor learning reads: those whose maximum may curtail some array, and those below
        an output limit (Outlet)."""
        found = {c.sensor: Meter(c.sensor, c.invert) for c in self.chargers()}
        found.update({m.sensor: m for o in self.outlets for m in o.meters})
        return [found[s] for s in sorted(found)]

    def outlet_sensors(self) -> list[str]:
        """The PV sensors of the arrays below an output limit with a battery."""
        return sorted({i.sensor for o in self.outlets for a in o.arrays for i in self.inputs_of(a)})

    def output_hours(self, array: str, peak: Mapping[str, Mapping[float, float]],
                     charge: Mapping[str, Mapping[float, float]], fine_pv: Mapping[str, Mapping[float, float]],
                     fine_charge: Mapping[str, Mapping[float, float]]) -> Hours:
        """Hour starts in which an output limit with a battery below it, over the array, was reached: the PV less
        what the batteries charged came to the limit (OUTPUT_MARGIN), so the array delivered at least what was
        measured.

        peak: PV sensor -> highest power per hour (W); charge: battery power sensor -> mean charge power per hour
        (W, charging positive, charge_power). fine_pv, fine_charge: the same as 5-minute means where they are kept;
        an hour they cover goes by them. Otherwise by the PV's maximum less the charge's mean: the battery charges
        most when the PV peaks, so this overestimates the output, the safe side.
        """
        out: Hours = set()
        for o in self.outlets:
            if array not in o.arrays:
                continue
            sensors = [i.sensor for a in o.arrays for i in self.inputs_of(a)]
            meters = [m.sensor for m in o.meters]
            reached = (1 - OUTPUT_MARGIN) * o.limit_w
            fine = _output(sensors, meters, fine_pv, fine_charge)
            # an hour goes by its 5-minute means only when all of them are there: one without a plane's sun has
            # gaps, and its periods at the limit may sit in them
            count: dict[float, int] = {}
            for t in fine:
                count[t - t % HOUR] = count.get(t - t % HOUR, 0) + 1
            known = {h for h, n in count.items() if n >= HOUR // FINE_STEP}
            fine = {t: w for t, w in fine.items() if t - t % HOUR in known}
            out |= {t - t % HOUR for t, w in fine.items() if w >= reached}
            out |= {t for t, w in _output(sensors, meters, peak, charge).items() if t not in known and w >= reached}
        return out

    def battery_hours(self, array: str, soc: Mapping[str, Mapping[float, float | None]],
                      hourly: Mapping[str, Mapping[float, float]], fine: Mapping[str, Mapping[float, float]]
                      ) -> tuple[Hours, Hours, Hours]:
        """(full, at its maximum, unsure) hour starts in which the array's PV may have been curtailed (blocked):
        learning leaves out the first and the last, and counts the second as delivering at least what was measured.

        soc: sensor -> highest % per hour. hourly, fine: power sensor -> mean charge power per hour, per 5-minute
        period, W (charge_power). An hour with kept 5-minute means counts as at the maximum when one of them is
        (Charger.at_max); one without is judged by its hourly mean (HOUR_AT_MAX, HOUR_UNSURE).
        """
        full: dict[Store, Hours] = {}
        at_max: dict[Store, Hours] = {}
        unsure: dict[Store, Hours] = {}
        for s in self.stores_of(array):
            full[s] = {t for t, v in soc.get(s.sensor, {}).items() if v is not None and v >= s.full_pct}
            if s.charger is not None:
                at_max[s], unsure[s] = _charge_hours(s.charger, hourly, fine)
        return self.blocked(array, full, at_max, unsure)

    def array_limit(self, array: str) -> float | None:
        """The fixed limit of an array's measured sum: the sum of its sensors' limits, if every sensor has one,
        and the output limit of a system that holds only this array, whichever is lower.

        Exact when the sensors reach their limits together (same orientation). With one sensor unlimited the
        sum is unbounded, so there is no cap from the sensors.
        """
        inputs = self.inputs_of(array)
        found = [sum(i.limit_w for i in inputs)] if inputs and all(i.limit_w is not None for i in inputs) else []
        found += [c.limit_w for c in self.caps if c.arrays == (array,)]
        return min(found) if found else None

    def shared_caps(self, array: str) -> list[Cap]:
        """The fixed limits the array shares with others: they mark lower bounds, and cap the sum of the arrays'
        forecasts (curtailment.cap_group)."""
        return [c for c in self.caps if array in c.arrays and len(c.arrays) > 1]

    def forecast_caps(self) -> list[tuple[tuple[str, ...], float]]:
        """The fixed limits shared by several arrays as (arrays, W): they cap the sum of the arrays' forecasts
        (curtailment.cap_group)."""
        return [(c.arrays, c.limit_w) for c in self.caps if len(c.arrays) > 1]

    def limited_arrays(self) -> dict[str, float]:
        """Array id -> fixed limit (W), for arrays that have one."""
        return {a: w for a in {i.array for i in self.inputs.values()} if (w := self.array_limit(a)) is not None}


def _charge_hours(c: Charger, hourly: Mapping[str, Mapping[float, float]],
                  fine: Mapping[str, Mapping[float, float]]) -> tuple[Hours, Hours]:
    """(hour starts at its maximum, unsure hour starts) of one battery (Installation.battery_hours)."""
    periods = fine.get(c.sensor, {})
    known = {t - t % HOUR for t in periods}
    at_max = {t - t % HOUR for t, w in periods.items() if c.at_max(w)}
    unsure: Hours = set()
    for t, w in hourly.get(c.sensor, {}).items():
        if t in known:
            continue
        if w >= HOUR_AT_MAX * c.max_w:
            at_max.add(t)
        elif w >= HOUR_UNSURE * c.max_w:
            unsure.add(t)
    return at_max, unsure - at_max


def _output(sensors: list[str], meters: list[str], pv: Mapping[str, Mapping[float, float]],
            charge: Mapping[str, Mapping[float, float]]) -> dict[float, float]:
    """Period start -> the PV of the sensors less what the batteries charged (W), where every sensor has a value.

    A discharge counts as nothing: below the limit the controller lowers the discharge before it cuts PV, so PV and
    discharge together at the limit say nothing about the PV."""
    if not sensors or any(s not in pv for s in sensors) or any(m not in charge for m in meters):
        return {}
    common = set(pv[sensors[0]]).intersection(*(pv[s] for s in sensors[1:]), *(charge[m] for m in meters))
    return {t: sum(pv[s][t] for s in sensors) - sum(max(charge[m][t], 0.0) for m in meters) for t in common}


def charge_power(chargers: Iterable[Charger | Meter], values: Mapping[str, Mapping[float, float | None]],
                 factors: Mapping[str, float]) -> dict[str, dict[float, float]]:
    """Power sensor -> mean charge power per period in W (charging positive, also for an inverted sensor), from the
    sensors' period means; factors: the sensor's unit to W (none for values already in W)."""
    out: dict[str, dict[float, float]] = {}
    for c in chargers:
        f = factors.get(c.sensor, 1.0) * (-1.0 if c.invert else 1.0)
        out[c.sensor] = {t: v * f for t, v in values.get(c.sensor, {}).items() if v is not None}
    return out


def _reach(systems: Mapping[str, sy.System], batteries: Mapping[str, Mapping[str, Any]], soc_full: float,
           max_soc: Mapping[str, float], ceilings: Mapping[str, float | None]) -> dict[str, Reach]:
    """Array -> the batteries that take its PV (systems.curtailed_arrays); ceilings: array -> the sum of its
    sensors' fixed limits, None where one is not known.

    A battery counts as full from the household's "full" level, or FULL_TOLERANCE below its upper limit if that is
    lower; max_soc: the current upper limit per battery where it comes from an entity (its history is not kept).
    """
    own: dict[str, tuple[Store, str]] = {}
    others: dict[str, list[Store]] = {}
    for sid, arrays in sy.curtailed_arrays(systems).items():
        d = batteries.get(systems[sid].battery or "")
        if not d or not d.get(CONF_SOC_SENSOR):
            continue
        charger = (Charger(d[CONF_BATTERY_POWER], bool(d.get(CONF_BATTERY_INVERT)), float(d[CONF_MAX_CHARGE]))
                   if d.get(CONF_BATTERY_POWER) and d.get(CONF_MAX_CHARGE)
                   and sy.charge_curtails(systems, sid) else None)
        store = Store(d[CONF_SOC_SENSOR], min(soc_full, float(max_soc.get(systems[sid].battery or "",
                                                                            d.get(CONF_MAX_SOC) or 100))
                                              - FULL_TOLERANCE), charger)
        for a in arrays:
            if a in systems[sid].arrays:
                own[a] = (store, sid)
            else:
                others.setdefault(a, []).append(store)
    out: dict[str, Reach] = {}
    for a in set(own) | set(others):
        if a not in own:
            out[a] = Reach((), tuple(dict.fromkeys(others[a])))
            continue
        store, sid = own[a]
        rest = tuple(s for s in dict.fromkeys(others.get(a, ())) if s != store)
        parts = [ceilings.get(x) for x in systems[sid].arrays]
        ceiling = sum(p for p in parts if p is not None) if parts and None not in parts else None
        limit = systems[sid].get(CONF_OUTPUT_LIMIT)
        out[a] = Reach((store,), rest,
                       own_charge=store.charger is None or ceiling is None or ceiling > store.charger.max_w,
                       overflow=bool(limit) and (ceiling is None or ceiling > float(limit)))
    return out


def _caps(systems: Mapping[str, sy.System], arrays: Iterable[str]) -> tuple[Cap, ...]:
    """The output limits that are fixed: no battery in the system or its subsystems. A limit per phase is left
    out: the system's total may exceed it."""
    known = set(arrays)
    out = []
    for s in sorted(systems.values(), key=lambda s: s.id):
        limit = s.get(CONF_OUTPUT_LIMIT)
        if not limit or s.get(CONF_LIMIT_PER_PHASE):
            continue
        tree = [s, *(systems[c] for c in s.children)]
        if any(x.battery for x in tree):
            continue
        members = tuple(a for x in tree for a in x.arrays if a in known)
        if members:
            out.append(Cap(s.id, members, float(limit)))
    return tuple(out)


def _outlets(systems: Mapping[str, sy.System], arrays: Iterable[str],
             batteries: Mapping[str, Mapping[str, Any]]) -> tuple[Outlet, ...]:
    """The output limits with a battery in the system or its subsystems (the others are fixed, _caps), where every
    such battery has a power sensor: without one the output is not known. A limit per phase is left out."""
    known = set(arrays)
    out = []
    for s in sorted(systems.values(), key=lambda s: s.id):
        limit = s.get(CONF_OUTPUT_LIMIT)
        if not limit or s.get(CONF_LIMIT_PER_PHASE):
            continue
        tree = [s, *(systems[c] for c in s.children)]
        found = [batteries.get(x.battery) or {} for x in tree if x.battery]
        if not found or not all(d.get(CONF_BATTERY_POWER) for d in found):
            continue
        members = tuple(a for x in tree for a in x.arrays if a in known)
        if members:
            meters = tuple(Meter(d[CONF_BATTERY_POWER], bool(d.get(CONF_BATTERY_INVERT))) for d in found)
            out.append(Outlet(s.id, members, float(limit), meters))
    return tuple(out)


def build(arrays: Mapping[str, Iterable[str]], limits: Mapping[str, float] | None = None,
          soc_full_pct: float = 95.0,
          systems: Mapping[str, sy.System] | None = None, batteries: Mapping[str, Mapping[str, Any]] | None = None,
          feed_in_mode: str = "free", max_soc: Mapping[str, float] | None = None,
          export_limit: tuple[Iterable[str], float] | None = None) -> Installation:
    """The installation from the configuration: arrays (id -> sensors), limits (sensor -> W), the systems and the
    battery subentries' data; max_soc: battery id -> current upper limit in % where an entity sets it;
    export_limit: the arrays under the household's feed-in limit and the limit in W."""
    limits = limits or {}
    inputs = {}
    for array, sensors in arrays.items():
        for s in sensors:
            w = limits.get(s)
            inputs[s] = Input(s, array, float(w) if w is not None and w > 0 else None)
    systems = systems or {}
    ceilings: dict[str, float | None] = {}
    for array in arrays:
        group = [i.limit_w for i in inputs.values() if i.array == array]
        ceilings[array] = sum(w for w in group if w is not None) if group and None not in group else None
    return Installation(inputs,
                        _reach(systems, batteries or {}, float(soc_full_pct), max_soc or {}, ceilings),
                        _caps(systems, arrays), _held(systems, export_limit),
                        _outlets(systems, arrays, batteries or {}))


def _held(systems: Mapping[str, sy.System], export_limit: tuple[Iterable[str], float] | None
          ) -> dict[str, float]:
    """Array -> the grid threshold of Installation.held; where both apply the higher, which marks more hours."""
    out = {a: HELD_IMPORT_W for a in sy.held_back_arrays(systems)}
    if export_limit and export_limit[1] > 0:
        below = limit_threshold(float(export_limit[1]))
        for a in export_limit[0]:
            out[a] = max(out.get(a, below), below)
    return out
