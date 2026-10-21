"""Battery simulation: expected PV against the load forecast, hour by hour, until tomorrow's charge start.

Pure functions, no Home Assistant imports. All batteries are simulated as one storage: capacities
add up, limits and efficiencies are weighted by capacity, power limits add up.

Energy flow per hour (Wh, for the part of the hour from now on):
  surplus = PV - load. A surplus charges the battery up to the upper limit and the charge power;
  what does not fit is "lost" (fed in or curtailed, depending on the system). A deficit
  discharges down to the lower limit plus reserve and the discharge power; the rest comes from
  the grid. Losses: the round-trip efficiency is split evenly into charging and discharging.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

H = 3600.0
COUPLING_DC, COUPLING_AC = "dc", "ac"
DEFAULT_EFFICIENCY = {COUPLING_DC: 0.95, COUPLING_AC: 0.90}   # round trip


@dataclass(frozen=True)
class Battery:
    capacity_wh: float
    soc: float | None                 # 0..1, None = unknown
    max_soc: float = 1.0              # 0..1
    min_soc: float = 0.0              # 0..1, including the reserve
    efficiency: float = 0.95          # round trip
    max_charge_w: float | None = None
    max_discharge_w: float | None = None


def combine(batteries: list[Battery]) -> Battery | None:
    """All batteries as one storage; batteries without a known state of charge are left out."""
    known = [b for b in batteries if b.capacity_wh > 0 and b.soc is not None]
    cap = sum(b.capacity_wh for b in known)
    if not cap:
        return None

    def weighted(attr: str) -> float:
        return sum(getattr(b, attr) * b.capacity_wh for b in known) / cap

    def power(attr: str) -> float | None:
        values = [getattr(b, attr) for b in known]
        return None if any(v is None for v in values) else sum(values)

    return Battery(cap, weighted("soc"), weighted("max_soc"), weighted("min_soc"), weighted("efficiency"),
                   power("max_charge_w"), power("max_discharge_w"))


@dataclass
class Simulation:
    points: list[tuple[float, float]] = field(default_factory=list)   # (time, state of charge 0..1)
    full_at: float | None = None          # first time the upper limit is reached
    empty_at: float | None = None         # first time the lower limit is reached with load left over
    lost_from: float | None = None        # first hour with surplus that does not fit
    lost_wh: float = 0.0                  # surplus that does not fit (fed in or curtailed)
    charged_wh: float = 0.0               # surplus taken by the battery (before losses)
    grid_wh: float = 0.0                  # load not covered by PV or battery
    surplus_wh: float = 0.0               # PV above load
    max_soc: float = 0.0
    max_soc_at: float | None = None
    hours: dict[float, dict] = field(default_factory=dict)              # per hour: surplus, battery, lost, grid, soc

    def soc_at(self, ts: float) -> float | None:
        """State of charge at a time, linear between the simulated points."""
        if not self.points:
            return None
        if ts <= self.points[0][0]:
            return self.points[0][1]
        for (a, fa), (b, fb) in zip(self.points, self.points[1:]):
            if a <= ts <= b:
                return fa + (fb - fa) * (ts - a) / (b - a or 1)
        return self.points[-1][1]


def simulate(battery: Battery, pv: dict[float, float], load: dict[float, float], now: float, end: float) -> Simulation:
    """Simulate from now until end. pv and load: Wh per hour start (mean W of the hour)."""
    sim = Simulation()
    cap = battery.capacity_wh
    e = (battery.soc if battery.soc is not None else battery.min_soc) * cap
    e_max, e_min = battery.max_soc * cap, battery.min_soc * cap
    eta = math.sqrt(max(0.01, min(1.0, battery.efficiency)))
    sim.points.append((now, e / cap))
    sim.max_soc, sim.max_soc_at = e / cap, now
    t = now - now % H
    while t < end:
        start = max(t, now)
        share = (min(t + H, end) - start) / H
        if share <= 0:
            t += H
            continue
        net = (pv.get(t, 0.0) - load.get(t, 0.0)) * share
        hour = {"surplus": round(max(net, 0.0), 1), "battery": 0.0, "lost": 0.0, "grid": 0.0}
        if net > 0:
            sim.surplus_wh += net
            room = max(e_max - e, 0.0) / eta                       # surplus the battery can still take
            limit = battery.max_charge_w * share if battery.max_charge_w else math.inf
            take = min(net, room, limit)
            e += take * eta
            lost = net - take
            if take < net and room <= limit and sim.full_at is None:
                sim.full_at = start + (take / net) * (min(t + H, end) - start)
            if lost > 0.5 and sim.lost_from is None:
                sim.lost_from = start
            sim.charged_wh += take
            sim.lost_wh += lost
            hour.update(battery=round(take, 1), lost=round(lost, 1))
        elif net < 0:
            need = -net
            avail = max(e - e_min, 0.0) * eta                      # load the battery can still cover
            limit = battery.max_discharge_w * share if battery.max_discharge_w else math.inf
            give = min(need, avail, limit)
            e -= give / eta
            if give < need and avail <= limit and sim.empty_at is None:
                sim.empty_at = start + (give / need) * (min(t + H, end) - start)
            sim.grid_wh += need - give
            hour.update(battery=round(-give, 1), grid=round(need - give, 1))
        point = min(t + H, end)
        sim.points.append((point, e / cap))
        if e / cap > sim.max_soc + 1e-9:
            sim.max_soc, sim.max_soc_at = e / cap, point
        hour["soc"] = round(e / cap, 4)
        sim.hours[t] = hour
        t += H
    if sim.full_at is not None and sim.max_soc_at is not None and sim.full_at < sim.max_soc_at:
        sim.max_soc_at = sim.full_at
    return sim


def charge_start(pv: dict[float, float], load: dict[float, float], start: float, end: float) -> float | None:
    """First hour in [start, end) with PV above the load: from here the battery charges again."""
    for t in sorted(pv):
        if start <= t < end and pv[t] > load.get(t, math.inf):
            return t
    return None
