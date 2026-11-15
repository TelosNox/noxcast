"""What lies between the modules and the house: the installation as configured (pure Python, no Home Assistant).

Power flows from the inputs (PV power sensors) through the systems (systems.py) to the house, and every level can
hold a limit. The chain of ceilings (docs/concept.md, Installation model) decides what each one means:

- A limit is fixed when no battery sits below it: the PV can never exceed it, so it caps the forecast and the
  hours at it are lower bounds in learning. That holds for an input (MPPT, micro-inverter) and for the output limit
  of a system without a battery, alone or shared by its subsystems.
- A battery's full state is a state, no cap: the hours it may have cut are left out of learning instead. Which
  batteries count for an array follows from where they sit (stores_of).
- A battery charging at its maximum power takes no more either, where the rest cannot go out
  (systems.charge_curtails): a ceiling of output plus charge power. The hour delivered at least what was measured,
  so it is a lower bound in learning like a fixed limit (charge_hours). Leaving it out instead would select on the
  measured PV itself, dropping the bright hours and bending the learned efficiency down (#248, E1). The charge power
  is read from means, never from a period's maximum: a power sensor, above all one computed from several others,
  shows spikes of a second far above what the battery can take, and one of them would turn the whole hour into a
  lower bound. Where the 5-minute means are kept (fine.py) they decide; older hours have only their hourly mean,
  which says less, so an hour in between is left out (charge_hours).
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
class Store:
    """A battery whose full state may curtail PV: its state-of-charge sensor and from what % it counts as full;
    charger: set where charging at its maximum power curtails PV too and the sensor and maximum are known."""

    sensor: str
    full_pct: float
    charger: Charger | None = None


@dataclass(frozen=True)
class Cap:
    """A fixed limit on the summed output of arrays: a system with an output limit and no battery below it."""

    system: str
    arrays: tuple[str, ...]
    limit_w: float


@dataclass(frozen=True)
class House:
    soc_sensors: tuple[str, ...] = ()    # batteries whose full state may curtail every array (the old setting)
    soc_full_pct: float = 95.0


@dataclass(frozen=True)
class Installation:
    house: House = field(default_factory=House)
    inputs: Mapping[str, Input] = field(default_factory=dict)                # by sensor
    stores: Mapping[str, tuple[Store, ...]] = field(default_factory=dict)    # array -> batteries of its systems
    caps: tuple[Cap, ...] = ()
    # array -> grid power (W, export negative) below which its system may have held PV back: HELD_IMPORT_W for
    # one that follows the load without a battery, limit_threshold under the household's feed-in limit
    held: Mapping[str, float] = field(default_factory=dict)

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
        """The batteries whose full state may curtail the array: the old household setting and its systems'."""
        old = tuple(Store(s, self.house.soc_full_pct) for s in self.house.soc_sensors)
        return tuple(dict.fromkeys(old + tuple(self.stores.get(array, ()))))

    def soc_sensors(self) -> list[str]:
        """Every state-of-charge sensor that may curtail some array."""
        return sorted({s.sensor for a in {i.array for i in self.inputs.values()} for s in self.stores_of(a)})

    def chargers(self) -> list[Charger]:
        """Every battery power sensor whose maximum may curtail some array."""
        return sorted({st.charger for group in self.stores.values() for st in group if st.charger},
                      key=lambda c: c.sensor)

    def full_hours(self, array: str, soc: Mapping[str, Mapping[float, float | None]]) -> set[float]:
        """Period starts in which a battery that may curtail the array was full; soc: sensor -> highest % per
        period."""
        return {t for s in self.stores_of(array) for t, v in soc.get(s.sensor, {}).items()
                if v is not None and v >= s.full_pct}

    def charge_hours(self, array: str, hourly: Mapping[str, Mapping[float, float]],
                     fine: Mapping[str, Mapping[float, float]]) -> tuple[set[float], set[float]]:
        """(hour starts, unsure hour starts) in which a battery that may curtail the array charged at its maximum
        power: the array delivered at least what was measured in the first, the second are left out of learning.

        hourly, fine: power sensor -> mean charge power per hour, per 5-minute period, W (charge_power). An hour
        with kept 5-minute means counts when one of them is at the maximum (Charger.at_max); one without is judged
        by its hourly mean (HOUR_AT_MAX, HOUR_UNSURE).
        """
        at_max: set[float] = set()
        unsure: set[float] = set()
        for s in self.stores_of(array):
            if s.charger is None:
                continue
            c = s.charger
            periods = fine.get(c.sensor, {})
            known = {t - t % HOUR for t in periods}
            at_max |= {t - t % HOUR for t, w in periods.items() if c.at_max(w)}
            for t, w in hourly.get(c.sensor, {}).items():
                if t in known:
                    continue
                if w >= HOUR_AT_MAX * c.max_w:
                    at_max.add(t)
                elif w >= HOUR_UNSURE * c.max_w:
                    unsure.add(t)
        return at_max, unsure - at_max

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


def charge_power(chargers: Iterable[Charger], values: Mapping[str, Mapping[float, float | None]],
                 factors: Mapping[str, float]) -> dict[str, dict[float, float]]:
    """Power sensor -> mean charge power per period in W (charging positive, also for an inverted sensor), from the
    sensors' period means; factors: the sensor's unit to W (none for values already in W)."""
    out: dict[str, dict[float, float]] = {}
    for c in chargers:
        f = factors.get(c.sensor, 1.0) * (-1.0 if c.invert else 1.0)
        out[c.sensor] = {t: v * f for t, v in values.get(c.sensor, {}).items() if v is not None}
    return out


def _stores(systems: Mapping[str, sy.System], batteries: Mapping[str, Mapping[str, Any]], feed_in_mode: str,
            soc_full: float, max_soc: Mapping[str, float]) -> dict[str, tuple[Store, ...]]:
    """Array -> the batteries that may curtail it (systems.curtailed_arrays).

    A battery counts as full from the household's "full" level, or FULL_TOLERANCE below its upper limit if that is
    lower; max_soc: the current upper limit per battery where it comes from an entity (its history is not kept).
    """
    out: dict[str, list[Store]] = {}
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
            out.setdefault(a, []).append(store)
    return {a: tuple(dict.fromkeys(s)) for a, s in out.items()}


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


def build(arrays: Mapping[str, Iterable[str]], limits: Mapping[str, float] | None = None,
          soc_sensors: Iterable[str] = (), soc_full_pct: float = 95.0,
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
    return Installation(House(tuple(soc_sensors), float(soc_full_pct)), inputs,
                        _stores(systems, batteries or {}, feed_in_mode, float(soc_full_pct), max_soc or {}),
                        _caps(systems, arrays), _held(systems, export_limit))


def _held(systems: Mapping[str, sy.System], export_limit: tuple[Iterable[str], float] | None
          ) -> dict[str, float]:
    """Array -> the grid threshold of Installation.held; where both apply the higher, which marks more hours."""
    out = {a: HELD_IMPORT_W for a in sy.held_back_arrays(systems)}
    if export_limit and export_limit[1] > 0:
        below = limit_threshold(float(export_limit[1]))
        for a in export_limit[0]:
            out[a] = max(out.get(a, below), below)
    return out
