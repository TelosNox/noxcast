"""Battery simulation: expected PV against the load forecast, hour by hour, until tomorrow's charge start.

Pure functions, no Home Assistant imports. The batteries of one system are simulated as one storage: capacities
add up, limits and efficiencies are weighted by capacity, power limits add up. Independent systems (each top-level
system with its own inverter) are simulated each on its own and together against the house load (simulate_units):
each charges from its own PV, the load is shared evenly by their batteries, a system with a fixed power delivers it
whatever the load. Batteries without a system, or all of one system, stay one storage (simulate).

Energy flow per hour (Wh, for the part of the hour from now on):
  surplus = PV - load. A surplus charges the battery up to the upper limit and the charge power;
  what does not fit is "lost" for the battery: fed in or curtailed (see Feed-in below). A deficit
  discharges down to the lower limit plus reserve and the discharge power; the rest comes from
  the grid. Losses: the round-trip efficiency is split evenly into charging and discharging.

Output cap (balcony systems): a battery can sit with PV arrays behind an inverter whose output is
capped (800 W, 1600 W), the arrays of its system. PV and battery together deliver at most that much. PV above the cap cannot
reach the house: it can only charge the battery, the rest is curtailed. And the battery delivers at
most the cap minus what its PV delivers in that hour. Without an array the cap limits the battery alone.

Shared cap (coupled systems, e.g. two devices behind one 800 W limit): the subsystems of a system with an output
limit deliver together at most that much, their batteries and their PV, also the PV of subsystems without a
battery. What their PV delivers above it can only charge the batteries; what is left for the batteries to deliver
is the shared cap minus that PV.

Feed-in: of the surplus that does not fit, PV above an output cap is curtailed (it cannot reach the house, so not
the grid either). The rest goes into the grid where the feed-in rule allows it: the household's rule (free, a limit
in W, zero export), or a system's own deviation (always feeds in, never feeds in). One storage has no surplus per
system, so the rest is split by each system's share of the hour's PV. What systems that feed in anyway export counts
against the household's limit, since the inverters that hold it measure at the grid connection; under zero export
they cannot take it back.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from statistics import median
from datetime import date, timedelta

from .const import Series

H = 3600.0
COUPLING_DC, COUPLING_AC = "dc", "ac"
DEFAULT_EFFICIENCY = {COUPLING_DC: 0.95, COUPLING_AC: 0.90}   # round trip
LIMIT_TOLERANCE = 0.02          # within 2 percentage points of a limit counts as reached (as in the review)
AHEAD_HOURS = 48                # the state of charge outlook for tariff and charge planners
QUARTER = 900.0                 # the simulation's step: the PV forecast comes in quarter hours, and so does a full
                                # battery or the morning's first surplus


@dataclass(frozen=True)
class Battery:
    capacity_wh: float
    soc: float | None                 # 0..1, None = unknown
    max_soc: float = 1.0              # 0..1
    min_soc: float = 0.0              # 0..1, including the reserve
    efficiency: float = 0.95          # round trip
    max_charge_w: float | None = None
    max_discharge_w: float | None = None
    output_w: float | None = None     # cap of the system's output, for PV of `arrays` and battery together
    arrays: tuple[str, ...] = ()      # the PV arrays (subentry ids) of the same system
    # the output limit shared with the other subsystems of its system: (system id, W, the arrays of the
    # subsystems without a battery); the arrays of the batteries' own systems count through `arrays`
    shared: tuple[str, float, tuple[str, ...]] | None = None
    # the independent system it belongs to (its top-level system): batteries of one system are one storage,
    # those of different systems are simulated each on their own (simulate_units); None = all one storage
    unit: str | None = None
    fixed_w: float | None = None      # the system delivers this power regardless of the load (regulation fixed)
    from_house: bool = False          # charges from the household's surplus (a battery at the house grid)


@dataclass(frozen=True)
class Limits:
    """Per hour start (Wh of the full hour): PV that cannot reach the house, and what the batteries may deliver."""
    capped: dict[float, float] = field(default_factory=dict)       # PV above the output caps: battery or curtailed
    discharge: dict[float, float] = field(default_factory=dict)    # discharge limit of all batteries; empty = none


@dataclass(frozen=True)
class FeedIn:
    """What of the surplus that does not fit may go into the grid (see Feed-in above).

    limit_w: the household's feed-in limit, None = free, 0 = zero export. free / never: per hour start, the share of
    the PV from systems that feed in regardless of the household's rule and from those that never feed in; the rest
    follows the household's rule. The default feeds everything in.
    """
    limit_w: float | None = None
    free: dict[float, float] = field(default_factory=dict)
    never: dict[float, float] = field(default_factory=dict)

    def fed_in(self, t: float, wh: float, share: float) -> float:
        """Wh of `wh` surplus (without capped PV) fed in, in an hour of which `share` is simulated."""
        free, never = self.free.get(t, 0.0), self.never.get(t, 0.0)
        rule = max(1.0 - free - never, 0.0) * wh
        room = math.inf if self.limit_w is None else self.limit_w * share
        return free * wh + min(rule, max(room - free * wh, 0.0))


def feed_in(limit_w: float | None, free: tuple[str, ...], never: tuple[str, ...],
            pv_arrays: dict[str, Series], hours: list[float],
            ceilings: list[tuple[tuple[str, ...], float]] = ()) -> FeedIn:
    """The feed-in rule with each hour's PV shares of the systems that deviate from it (arrays by subentry id).

    ceilings: output limits (arrays, W); the shares count the PV that gets past them, since what is above is
    curtailed before it could be fed in (see output_ceilings).
    """
    shares: tuple[dict[float, float], dict[float, float]] = ({}, {})
    if free or never:
        for t in hours:
            pv = {a: s.get(t, 0.0) for a, s in pv_arrays.items()}
            for arrays, w in ceilings:
                over = sum(pv.get(a, 0.0) for a in arrays)
                if over > w:
                    for a in arrays:
                        if a in pv:
                            pv[a] *= w / over
            total = sum(pv.values())
            if total <= 0:
                continue
            for out, arrays in zip(shares, (free, never)):
                if part := sum(pv.get(a, 0.0) for a in arrays):
                    out[t] = part / total
    return FeedIn(limit_w, *shares)


def output_ceilings(batteries: list[Battery]) -> list[tuple[tuple[str, ...], float]]:
    """The output limits over PV arrays known from the batteries: each one's own, then each shared one (arrays of
    its subsystems with and without a battery)."""
    out = [(b.arrays, b.output_w) for b in batteries if b.output_w and b.arrays]
    groups: dict[str, tuple[float, set[str]]] = {}
    for b in batteries:
        if b.shared:
            limit, arrays = groups.setdefault(b.shared[0], (b.shared[1], set(b.shared[2])))
            arrays.update(b.arrays)
    out += [(tuple(sorted(arrays)), limit) for limit, arrays in groups.values() if arrays]
    return out


def limits(batteries: list[Battery], pv_arrays: dict[str, Series], hours: list[float]) -> Limits:
    """The output caps per hour, for the batteries with a known state of charge (those combine uses).

    pv_arrays: Wh per hour start of each array. A battery without output cap keeps its discharge power
    (or none); with a cap its PV first goes to the house up to the cap, the battery gets what is left.
    """
    known = [b for b in batteries if b.capacity_wh > 0 and b.soc is not None]
    if not any(b.output_w or b.shared for b in known):
        return Limits()
    capped: dict[float, float] = {}
    discharge: dict[float, float] = {}
    groups = {b.shared[0]: b.shared for b in known if b.shared}
    for t in hours:
        over, give = 0.0, 0.0
        # per shared cap: the PV its subsystems deliver and what their batteries could give
        delivered = {g: sum(pv_arrays.get(a, {}).get(t, 0.0) for a in shared[2]) for g, shared in groups.items()}
        gives = dict.fromkeys(groups, 0.0)
        for b in known:
            own = b.max_discharge_w if b.max_discharge_w else math.inf
            pv = sum(pv_arrays.get(a, {}).get(t, 0.0) for a in b.arrays)
            if b.output_w:
                over += max(pv - b.output_w, 0.0)
                own = min(own, max(b.output_w - pv, 0.0))
                pv = min(pv, b.output_w)
            if b.shared:
                delivered[b.shared[0]] += pv
                gives[b.shared[0]] += own
            else:
                give += own
        for g, (_, limit, _) in groups.items():
            over += max(delivered[g] - limit, 0.0)
            give += min(gives[g], max(limit - delivered[g], 0.0))
        if over > 0:
            capped[t] = over
        if give < math.inf:
            discharge[t] = give
    return Limits(capped, discharge)


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
    empties: list[float] = field(default_factory=list)                  # every hour's time at the lower limit with load left
    lost_from: float | None = None        # first hour with surplus that does not fit
    lost_wh: float = 0.0                  # surplus that does not fit (fed in or curtailed)
    fed_in_wh: float = 0.0                # of that, fed into the grid
    curtailed_wh: float = 0.0             # of that, curtailed: above the feed-in limit, zero export or an output cap
    charged_wh: float = 0.0               # surplus taken by the battery (before losses)
    grid_wh: float = 0.0                  # load not covered by PV or battery
    surplus_wh: float = 0.0               # PV above load
    max_soc: float = 0.0
    max_soc_at: float | None = None
    hours: dict[float, dict] = field(default_factory=dict)              # per hour: surplus, battery, lost, grid, soc, fed_in, curtailed

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


def simulate(battery: Battery, pv: Series, load: Series, now: float, end: float,
             caps: Limits | None = None, held_wh: float = 0.0, grid: FeedIn | None = None,
             step: float = H) -> Simulation:
    """Simulate from now until end. pv and load: Wh per hour start (mean W of the hour).

    caps: the output caps (see limits); pv is all PV, the capped part goes to the battery only.
    held_wh: room below the upper limit the batteries will not use (see held_back).
    grid: what of the surplus that does not fit is fed in (see FeedIn); None feeds everything in.
    step: the length of a period; pv, load and caps.capped are Wh per period start, while caps.discharge and the
    feed-in shares stay per hour start (in_steps prepares them). sim.hours is then per period.
    """
    caps = caps or Limits()
    grid = grid or FeedIn()
    sim = Simulation()
    cap = battery.capacity_wh
    e = (battery.soc if battery.soc is not None else battery.min_soc) * cap
    e_max, e_min = battery.max_soc * cap, battery.min_soc * cap
    e_max = max(e_max - held_wh, 0.0)
    eta = math.sqrt(max(0.01, min(1.0, battery.efficiency)))
    sim.points.append((now, e / cap))
    sim.max_soc, sim.max_soc_at = e / cap, now
    t = now - now % step
    while t < end:
        start = max(t, now)
        share = (min(t + step, end) - start) / step
        per = share * step / H                                     # of an hour, for powers in W
        if share <= 0:
            t += step
            continue
        capped = caps.capped.get(t, 0.0) * share                   # PV above an output cap: battery only
        net = (pv.get(t, 0.0) - load.get(t, 0.0)) * share - capped
        surplus = max(net, 0.0) + capped
        hour = {"surplus": round(surplus, 1), "battery": 0.0, "lost": 0.0, "grid": 0.0, "fed_in": 0.0, "curtailed": 0.0}
        if surplus > 0:
            sim.surplus_wh += surplus
            room = max(e_max - e, 0.0) / eta                       # surplus the battery can still take
            limit = battery.max_charge_w * per if battery.max_charge_w else math.inf
            take = min(surplus, room, limit)
            e += take * eta
            lost = surplus - take
            if take < surplus and room <= limit and sim.full_at is None:
                sim.full_at = start + (take / surplus) * (min(t + step, end) - start)
            if lost > 0.5 and sim.lost_from is None:
                sim.lost_from = start
            # the battery takes capped PV first, since nothing else can; what it leaves of it is curtailed
            fed = grid.fed_in(t - t % H, max(lost - max(capped - take, 0.0), 0.0), per)
            sim.charged_wh += take
            sim.lost_wh += lost
            sim.fed_in_wh += fed
            sim.curtailed_wh += lost - fed
            hour.update(battery=round(take, 1), lost=round(lost, 1), fed_in=round(fed, 1), curtailed=round(lost - fed, 1))
        if net < 0:                                                # capped PV may charge while the house draws
            need = -net
            avail = max(e - e_min, 0.0) * eta                      # load the battery can still cover
            power = caps.discharge.get(t - t % H, battery.max_discharge_w) if caps.discharge else battery.max_discharge_w
            limit = power * per if power is not None else math.inf
            give = min(need, avail, limit)
            e -= give / eta
            if give < need and avail <= limit:
                sim.empties.append(start + (give / need) * (min(t + step, end) - start))
                if sim.empty_at is None:
                    sim.empty_at = sim.empties[-1]
            sim.grid_wh += need - give
            hour.update(battery=round(hour["battery"] - give, 1), grid=round(need - give, 1))
        point = min(t + step, end)
        sim.points.append((point, e / cap))
        if e / cap > sim.max_soc + 1e-9:
            sim.max_soc, sim.max_soc_at = e / cap, point
        hour["soc"] = round(e / cap, 4)
        sim.hours[t] = hour
        t += step
    if sim.full_at is not None and sim.max_soc_at is not None and sim.full_at < sim.max_soc_at:
        sim.max_soc_at = sim.full_at
    return sim


def without_storage(pv: Series, load: Series, now: float, end: float, grid: FeedIn | None = None) -> Simulation:
    """A household without battery, counted as simulate counts it: per hour the PV above the load is surplus, all of
    it without room ("lost"), fed in as far as the feed-in rule allows and curtailed beyond; the rest of the load
    comes from the grid. No state of charge."""
    grid = grid or FeedIn()
    sim = Simulation()
    t = now - now % H
    while t < end:
        share = (min(t + H, end) - max(t, now)) / H
        if share > 0:
            net = (pv.get(t, 0.0) - load.get(t, 0.0)) * share
            surplus = max(net, 0.0)
            fed = grid.fed_in(t, surplus, share) if surplus > 0 else 0.0
            if surplus > 0.5 and sim.lost_from is None:
                sim.lost_from = max(t, now)
            sim.surplus_wh += surplus
            sim.lost_wh += surplus
            sim.fed_in_wh += fed
            sim.curtailed_wh += surplus - fed
            sim.grid_wh += max(-net, 0.0)
            sim.hours[t] = {"surplus": round(surplus, 1), "battery": 0.0, "lost": round(surplus, 1),
                            "grid": round(max(-net, 0.0), 1), "fed_in": round(fed, 1), "curtailed": round(surplus - fed, 1)}
        t += H
    return sim


def charge_start(pv: Series, load: Series, start: float, end: float) -> float | None:
    """First hour in [start, end) with PV above the load: from here the battery charges again."""
    for t in sorted(pv):
        if start <= t < end and pv[t] > load.get(t, math.inf):
            return t
    return None


def held_back(batteries: list[Battery]) -> float:
    """Room (Wh) below the upper limit of the batteries that stand within the tolerance of it.

    Many batteries stop charging a little below their set limit. While there is surplus, one that already
    stands within the tolerance counts as full: else the last percent of room would hide the surplus that
    is already lost. Per battery, so a full one does not fill up a neighbour that still charges.
    """
    return sum(max(b.max_soc - b.soc, 0.0) * b.capacity_wh for b in batteries
               if b.capacity_wh > 0 and b.soc is not None and b.soc >= b.max_soc - LIMIT_TOLERANCE)


def lost_when_full(sim: Simulation, max_soc: float) -> dict[float, float]:
    """Surplus lost per hour (Wh) in the hours that end with the battery at its upper limit.

    The simulation also loses surplus above the charge power of a battery that is far from full; that is not
    what a "full, surplus lost" warning means, so those hours are left out.
    """
    return {t: h["lost"] for t, h in sim.hours.items() if h["lost"] > 0 and h.get("soc", 0.0) >= max_soc - LIMIT_TOLERANCE}


@dataclass(frozen=True)
class Unit:
    """An independent system with its batteries as one storage (simulate_units)."""
    key: str
    storage: Battery
    pv: Series                        # Wh per hour start of its own arrays
    caps: Limits
    held_wh: float = 0.0
    fixed_w: float | None = None
    from_house: bool = False


def units_of(batteries: list[Battery], pv_arrays: dict[str, Series], hours: list[float],
             charging: bool) -> list[Unit]:
    """The independent systems among the batteries with a known state of charge, each as one storage.

    charging: the current hour has surplus, so a level near the limit counts as full (held_back)."""
    known = [b for b in batteries if b.capacity_wh > 0 and b.soc is not None]
    groups: dict[str, list[Battery]] = {}
    for b in known:
        groups.setdefault(b.unit or "", []).append(b)
    out = []
    for key, members in sorted(groups.items()):
        storage = combine(members)
        if storage is None:
            continue
        # the arrays of subsystems without a battery behind a shared cap belong to the system too
        arrays = tuple(dict.fromkeys([a for b in members for a in b.arrays]
                                     + [a for b in members if b.shared for a in b.shared[2]]))
        pv = {t: sum(pv_arrays.get(a, {}).get(t, 0.0) for a in arrays) for t in hours} if arrays else {}
        out.append(Unit(key, storage, pv, limits(members, pv_arrays, hours), held_back(members) if charging else 0.0,
                        next((b.fixed_w for b in members if b.fixed_w), None), all(b.from_house for b in members)))
    return out


def _fill(need: float, caps: list[float]) -> list[float]:
    """need split evenly over the parts, none above its cap; what a part cannot take goes to the others."""
    out = [0.0] * len(caps)
    open_ = [i for i, c in enumerate(caps) if c > 0]
    while need > 1e-9 and open_:
        each = need / len(open_)
        full = [i for i in open_ if caps[i] - out[i] <= each]
        if not full:
            for i in open_:
                out[i] += each
            return out
        for i in full:
            need -= caps[i] - out[i]
            out[i] = caps[i]
        open_ = [i for i in open_ if i not in full]
    return out


def simulate_units(units: list[Unit], pv: Series, load: Series, now: float, end: float,
                   grid: FeedIn | None = None, step: float = H) -> tuple[dict[str, Simulation], Simulation]:
    """Independent systems simulated together, hour by hour, against one house load.

    The house load is covered by PV first: the PV of systems without a battery, then each system's own PV (in
    proportion to it). What is left is shared evenly by the systems' batteries, each up to what it can deliver in
    the hour; what one cannot give, the others take over. A system with a fixed power delivers that power whatever
    the load. Each system charges from its own PV what it does not deliver; a battery at the house grid charges
    from what goes out of the house. What then still goes out follows the feed-in rule (FeedIn).

    Returns each system's simulation and the whole. A system's "lost" is the surplus its own battery cannot take;
    the whole's is what leaves the house (also the PV of systems without a battery, and what a battery at the house
    grid did not take), as the single storage counts it. The whole's level is weighted by capacity; full, empty and
    loss come from the earliest system, so an early full one is not hidden behind the average.
    step: the length of a period, as in simulate (the units' pv and caps.capped per period).
    """
    grid = grid or FeedIn()
    n = len(units)
    sims = {u.key: Simulation() for u in units}
    whole = Simulation()
    caps_wh = [u.storage.capacity_wh for u in units]
    e = [(u.storage.soc if u.storage.soc is not None else u.storage.min_soc) * c for u, c in zip(units, caps_wh)]
    e_max = [max(u.storage.max_soc * c - u.held_wh, 0.0) for u, c in zip(units, caps_wh)]
    e_min = [u.storage.min_soc * c for u, c in zip(units, caps_wh)]
    eta = [math.sqrt(max(0.01, min(1.0, u.storage.efficiency))) for u in units]
    follow = [i for i, u in enumerate(units) if not u.fixed_w]
    takers = [i for i, u in enumerate(units) if u.from_house]
    for u, x, c in zip(units, e, caps_wh):
        sims[u.key].points.append((now, x / c))
        sims[u.key].max_soc, sims[u.key].max_soc_at = x / c, now
    t = now - now % step
    while t < end:
        start = max(t, now)
        share = (min(t + step, end) - start) / step
        per = share * step / H                                          # of an hour, for powers in W
        if share <= 0:
            t += step
            continue
        stop = min(t + step, end)
        own = [u.pv.get(t, 0.0) * share for u in units]
        capped = [min(u.caps.capped.get(t, 0.0) * share, o) for u, o in zip(units, own)]   # battery only
        deliverable = [o - c for o, c in zip(own, capped)]
        free = max(pv.get(t, 0.0) * share - sum(own), 0.0)              # PV of systems without a battery
        power, avail = [], []
        for i, u in enumerate(units):
            w = u.caps.discharge.get(t - t % H, u.storage.max_discharge_w) if u.caps.discharge else u.storage.max_discharge_w
            power.append(w * per if w is not None else math.inf)
            avail.append(max(e[i] - e_min[i], 0.0) * eta[i])
        give_max = [min(a, p) for a, p in zip(avail, power)]
        need = load.get(t, 0.0) * share - free
        out = max(-need, 0.0)                                           # leaves the house before the batteries
        need = max(need, 0.0)
        from_pv, give = [0.0] * n, [0.0] * n
        # a fixed power goes out whatever the house draws: PV first, then the battery
        short = [False] * n
        for i, u in enumerate(units):
            if u.fixed_w:
                target = u.fixed_w * per
                from_pv[i] = min(deliverable[i], target)
                give[i] = min(target - from_pv[i], give_max[i])
                short[i] = from_pv[i] + give[i] < target - 0.5
        fixed_out = sum(from_pv) + sum(give)
        out += max(fixed_out - need, 0.0)
        need = max(need - fixed_out, 0.0)
        pv_left = sum(deliverable[i] for i in follow)
        if pv_left > 0 and need > 0:
            part = min(need / pv_left, 1.0)
            for i in follow:
                from_pv[i] = deliverable[i] * part
            need -= pv_left * part
        for i, g in zip(follow, _fill(need, [give_max[i] for i in follow])):
            give[i] = g
        grid_wh = max(need - sum(give[i] for i in follow), 0.0)
        hours, lost, gone = [], [0.0] * n, [0.0] * n
        for i, u in enumerate(units):
            sim = sims[u.key]
            surplus = deliverable[i] - from_pv[i] + capped[i]
            hour = {"surplus": round(surplus, 1), "battery": 0.0, "lost": 0.0, "grid": 0.0, "fed_in": 0.0,
                    "curtailed": 0.0}
            took = 0.0
            if surplus > 0:
                sim.surplus_wh += surplus
                took = _charge(sim, e, e_max, eta, i, surplus, u.storage.max_charge_w, per, start, stop)
                lost[i] = surplus - took
            # capped PV the battery left is curtailed; the rest of its surplus goes out to the house, except behind a
            # fixed power, which holds the output like a limit: what its battery cannot take is curtailed too
            gone[i] = 0.0 if u.fixed_w else max(lost[i] - max(capped[i] - took, 0.0), 0.0)
            if give[i] > 0:
                e[i] -= give[i] / eta[i]
            hour["battery"] = round(took - give[i], 1)
            # empty: the battery gave all it had left, below its power, with load (or fixed power) left over
            if avail[i] <= power[i] and give[i] >= avail[i] - 1e-9 and (short[i] or (i in follow and grid_wh > 0.5)):
                sim.empties.append(stop)
                if sim.empty_at is None:
                    sim.empty_at = stop
            hours.append(hour)
        out += sum(gone)
        to_house = 0.0                                                  # taken by batteries at the house grid
        left = [0.0] * n                                                # what a full one at the house grid lets go
        if takers and out > 0:
            limit = [units[i].storage.max_charge_w * per if units[i].storage.max_charge_w else math.inf for i in takers]
            rooms = [min(max(e_max[i] - e[i], 0.0) / eta[i], c) for i, c in zip(takers, limit)]
            for i, w in zip(takers, _fill(out, rooms)):
                if w > 0:
                    e[i] += w * eta[i]
                    sims[units[i].key].charged_wh += w
                    hours[i]["battery"] = round(hours[i]["battery"] + w, 1)
                    to_house += w
            out -= to_house
            # full: its room, not its charge power, ended what it took while surplus still leaves the house
            full = [i for k, i in enumerate(takers) if rooms[k] < limit[k] and e[i] >= e_max[i] - 1e-6]
            for i in full:
                sim = sims[units[i].key]
                if sim.full_at is None:
                    sim.full_at = start if rooms[takers.index(i)] <= 1e-9 else stop
                if out > 0.5:
                    left[i] = out / len(full)
        fed = grid.fed_in(t - t % H, out, per) if out > 0 else 0.0
        lost_sum = sum(lost)
        sent = [g + x for g, x in zip(gone, left)]
        for i, u in enumerate(units):
            sim, hour = sims[u.key], hours[i]
            lost_i = lost[i] + left[i]
            if lost_i > 0:
                # the house's feed-in shared by what each system sent out, never more than it lost
                f = min(fed * sent[i] / (sum(sent) or 1.0), lost_i) if sent[i] else 0.0
                sim.lost_wh += lost_i
                sim.fed_in_wh += f
                sim.curtailed_wh += lost_i - f
                hour.update(lost=round(lost_i, 1), fed_in=round(f, 1), curtailed=round(lost_i - f, 1))
                if lost_i > 0.5 and sim.lost_from is None:
                    sim.lost_from = start
            # the grid supplies what no battery covers, booked evenly where the systems shared it
            if i in follow and grid_wh > 0:
                g = grid_wh / len(follow)
                sim.grid_wh += g
                hour["grid"] = round(g, 1)
            sim.points.append((stop, e[i] / caps_wh[i]))
            if e[i] / caps_wh[i] > sim.max_soc + 1e-9:
                sim.max_soc, sim.max_soc_at = e[i] / caps_wh[i], stop
            hour["soc"] = round(e[i] / caps_wh[i], 4)
            sim.hours[t] = hour
        # the whole as the house sees it: what leaves it, and what is curtailed before (capped PV without room)
        curtailed_capped = lost_sum - sum(gone)
        leaves = out + curtailed_capped
        surplus = sum(max(deliverable[i] - from_pv[i], 0.0) + capped[i] for i in range(n)) + max(free - load.get(t, 0.0) * share, 0.0)
        whole.surplus_wh += surplus
        whole.charged_wh += sum(max(h["battery"], 0.0) for h in hours)
        whole.lost_wh += leaves
        whole.fed_in_wh += fed
        whole.curtailed_wh += leaves - fed
        whole.grid_wh += grid_wh
        if leaves > 0.5 and whole.lost_from is None:
            whole.lost_from = start
        whole.hours[t] = {"surplus": round(surplus, 1), "battery": round(sum(h["battery"] for h in hours), 1),
                          "lost": round(leaves, 1), "grid": round(grid_wh, 1), "fed_in": round(fed, 1),
                          "curtailed": round(leaves - fed, 1),
                          "soc": round(sum(x for x in e) / sum(caps_wh), 4)}
        t += step
    for sim in sims.values():
        if sim.full_at is not None and sim.max_soc_at is not None and sim.full_at < sim.max_soc_at:
            sim.max_soc_at = sim.full_at
    return sims, _whole(units, sims, whole)


def _charge(sim: Simulation, e: list[float], e_max: list[float], eta: list[float], i: int, surplus: float,
            max_charge_w: float | None, share: float, start: float, stop: float) -> float:
    """Charge battery i from its own surplus; the part taken (Wh before losses)."""
    room = max(e_max[i] - e[i], 0.0) / eta[i]
    limit = max_charge_w * share if max_charge_w else math.inf
    take = min(surplus, room, limit)
    e[i] += take * eta[i]
    sim.charged_wh += take
    if take < surplus and room <= limit and sim.full_at is None:
        sim.full_at = start + (take / surplus) * (stop - start)
    return take


def _whole(units: list[Unit], sims: dict[str, Simulation], whole: Simulation) -> Simulation:
    """The whole (energies and hours from simulate_units): the level weighted by capacity, events from the
    earliest system."""
    cap = sum(u.storage.capacity_wh for u in units)
    parts = [(sims[u.key], u.storage.capacity_wh) for u in units]
    for k in range(min(len(s.points) for s, _ in parts)):
        whole.points.append((parts[0][0].points[k][0], sum(s.points[k][1] * c for s, c in parts) / cap))
    for attr in ("full_at", "empty_at"):
        found = [getattr(s, attr) for s, _ in parts if getattr(s, attr) is not None]
        setattr(whole, attr, min(found) if found else None)
    whole.empties = sorted(t for s, _ in parts for t in s.empties)
    if whole.points:
        best = max(whole.points, key=lambda p: p[1])
        whole.max_soc, whole.max_soc_at = best[1], best[0]
    return whole


Shape = dict[float, tuple[float, ...]]   # per hour start: the share of each quarter in the hour's PV (adds up to 1)


def shape_of(quarters: Series) -> Shape:
    """The quarter shares of each hour from a series in quarter hours (Wh per quarter start)."""
    hours: dict[float, list[float]] = {}
    for t, wh in quarters.items():
        hours.setdefault(t - t % H, [0.0] * 4)[int(t % H // QUARTER)] += max(wh, 0.0)
    return {t: tuple(q / sum(qs) for q in qs) for t, qs in hours.items() if sum(qs) > 0}


EVEN = (0.25, 0.25, 0.25, 0.25)


def in_quarters(hourly: Series, shape: Shape | None = None) -> Series:
    """Wh per hour start -> Wh per quarter start: by the hour's PV shape where one is given, else evenly (the load,
    whose course within the hour the hourly profiles do not know)."""
    out: Series = {}
    for t, wh in hourly.items():
        for k, f in enumerate((shape or {}).get(t, EVEN)):
            out[t + k * QUARTER] = wh * f
    return out


def _quarter_caps(caps: Limits | None, shape: Shape | None) -> Limits | None:
    """Capped PV follows the PV within the hour; the discharge limits are powers per hour and stay."""
    return replace(caps, capped=in_quarters(caps.capped, shape)) if caps is not None and caps.capped else caps


def _quarter_unit(u: Unit, shape: Shape | None) -> Unit:
    return replace(u, pv=in_quarters(u.pv, shape), caps=_quarter_caps(u.caps, shape))


def _by_hour(sim: Simulation) -> Simulation:
    """sim.hours per quarter -> per hour: energies add up, the level is the one at the end of the hour."""
    hours: dict[float, dict] = {}
    for t, q in sorted(sim.hours.items()):
        h = hours.setdefault(t - t % H, dict.fromkeys(q, 0.0))
        for k, v in q.items():
            h[k] = v if k == "soc" else round(h[k] + v, 1)
    sim.hours = hours
    return sim


def simulate_quarters(battery: Battery, pv: Series, load: Series, now: float, end: float, shape: Shape | None,
                      caps: Limits | None = None, held_wh: float = 0.0, grid: FeedIn | None = None) -> Simulation:
    """simulate in quarter hours from hourly pv and load, the PV shaped within each hour; hours as in simulate."""
    return _by_hour(simulate(battery, in_quarters(pv, shape), in_quarters(load), now, end, _quarter_caps(caps, shape),
                             held_wh, grid, QUARTER))


def simulate_units_quarters(units: list[Unit], pv: Series, load: Series, now: float, end: float, shape: Shape | None,
                            grid: FeedIn | None = None) -> tuple[dict[str, Simulation], Simulation]:
    """simulate_units in quarter hours, as simulate_quarters. Every system's PV gets the shape of the whole."""
    per, whole = simulate_units([_quarter_unit(u, shape) for u in units], in_quarters(pv, shape), in_quarters(load),
                                now, end, grid, QUARTER)
    return {k: _by_hour(v) for k, v in per.items()}, _by_hour(whole)


def day_simulation(batteries: list[Battery], pv: Series, load: Series | None,
                   band_hours: dict[float, tuple[float, float]] | None, now: float, day_end: float,
                   pv_arrays: dict[str, Series] | None = None,
                   rule: tuple[float | None, tuple[str, ...], tuple[str, ...]] | None = None,
                   ahead: tuple[Series, float] | None = None, shape: Shape | None = None) -> dict | None:
    """All batteries as one storage, simulated with expected PV against the load forecast until tomorrow's charge start.

    {"sim", "charge_start", "storage", "day_end"}, plus with a PV band "band": the surplus lost today and
    the time full with PV at P10 and at P90, and the surplus lost per hour with a full battery at P90. None without batteries or load forecast.
    pv_arrays: PV per array, for batteries with an output cap behind an array.
    rule: the feed-in rule (limit in W or None for free, arrays of systems that always and that never feed in);
    None feeds everything in.
    ahead: (load, end) for the outlook "ahead": the same simulation for the next AHEAD_HOURS, past the charge start,
    but not past `end`, where the PV forecast ends.
    shape: how the PV of each hour spreads over its quarters (shape_of the quarter-hour forecast). The simulation runs
    in quarter hours: a battery that gets full at 11:40 is full then, and the morning's first surplus starts within
    the hour. Results stay per hour, the levels (points) per quarter.
    """
    if not batteries or load is None:
        return None
    storage = combine(batteries)
    if storage is None:
        return None
    tomorrow_end = day_end + 86400
    start = charge_start(pv, load, day_end, tomorrow_end) or tomorrow_end
    pv_arrays = pv_arrays or {}
    # caps for every hour ahead, also past the charge start: the consumer plan for tomorrow and the card use them
    hours = sorted(t for t in set(pv) | set(load) | set(ahead[0] if ahead else ()) if t + H > now)
    caps = limits(batteries, pv_arrays, hours)
    # a level near the limit only says "it stops here" while it has surplus to take, not after a discharge
    hour = now - now % H
    charging = pv.get(hour, 0.0) > load.get(hour, math.inf)
    held = held_back(batteries) if charging else 0.0
    grid = feed_in(*rule, pv_arrays, hours, output_ceilings(batteries)) if rule else FeedIn()
    # independent systems are simulated each on its own; one system (or all batteries without one) stays one storage
    several = len({b.unit for b in batteries if b.capacity_wh > 0 and b.soc is not None}) > 1

    def run(pv_: Series, arrays: dict[str, Series], load_: Series, until: float,
            caps_: Limits) -> tuple[Simulation, dict[str, Simulation] | None, dict[float, float]]:
        """The simulation, each system's (None for one storage) and the surplus lost per hour with a full battery."""
        if not several:
            one = simulate_quarters(storage, pv_, load_, now, until, shape, caps_, held, grid)
            return one, None, lost_when_full(one, storage.max_soc)
        units = units_of(batteries, arrays, hours, charging)
        per, whole = simulate_units_quarters(units, pv_, load_, now, until, shape, grid)
        # full per system: one full system loses its surplus even while the average level is below its limit; what
        # is lost then is what leaves the house, since a battery at the house grid may still take a full one's surplus
        full = {t for u in units for t in lost_when_full(per[u.key], u.storage.max_soc)}
        lost = {t: h["lost"] for t, h in whole.hours.items() if t in full and h["lost"] > 0}
        return whole, per, lost

    sim, systems, lost_full = run(pv, pv_arrays, load, start, caps)
    out = {"sim": sim, "charge_start": start, "storage": storage, "day_end": day_end, "limits": caps, "held_wh": held,
           "feed_in": grid, "lost_full": lost_full, "shape": shape}
    if systems is not None:
        out["systems"] = systems
        out["units"] = units_of(batteries, pv_arrays, hours, charging)     # for what-if runs (views.battery_entry)
    if ahead and (until := min(now + AHEAD_HOURS * H, ahead[1])) > now:
        out["ahead"] = run(pv, pv_arrays, ahead[0], until, caps)[0]
    if band_hours:
        # the same simulation with PV at P10 and at P90: how much surplus is lost, and when full, in a weak and a strong case
        sims = []
        for i in (0, 1):
            variant = {**pv, **{t: lh[i] for t, lh in band_hours.items()}}
            # each array scaled like the total, so the caps see the weak and the strong case too
            scale = {t: variant[t] / pv[t] for t in band_hours if pv.get(t)}
            arrays = {a: {t: v * scale.get(t, 1.0) for t, v in s.items()} for a, s in pv_arrays.items()}
            # every array scaled alike keeps the systems' shares of the PV, so the same feed-in split holds
            sims.append(run(variant, arrays, load, start, limits(batteries, arrays, hours)))
        out["band"] = {"lost_today": [round(sum(h["lost"] for t, h in s.hours.items() if t < day_end) / 1000, 3)
                                      for s, _, _ in sims],
                       "full_at": [s.full_at for s, _, _ in sims],
                       "lost_p90": sims[1][2]}   # per hour, in the strong case
    return out


def replay(battery: dict, pv: Series, load: Series, now: float, until: float) -> Simulation:
    """The simulation of day_simulation (its result `battery`) again with another load, e.g. with a planned run."""
    shape = battery.get("shape")
    if battery.get("units"):
        return simulate_units_quarters(battery["units"], pv, load, now, until, shape, battery.get("feed_in"))[1]
    return simulate_quarters(battery["storage"], pv, load, now, until, shape, battery.get("limits"),
                             battery.get("held_wh", 0.0), battery.get("feed_in"))


def planning_cost(battery: dict, pv: Series, load: Series, now: float):
    """For planning consumers (plan.Cost): extra load (Wh per hour start) -> grid Wh, lost surplus Wh and the energy
    the battery can still deliver at the end. Runs as far as the 48-hour outlook reaches (then load must cover it),
    else until the charge start: a run that empties the battery tonight but is refilled by tomorrow's surplus costs
    nothing, one whose energy is still missing at the end costs that energy."""
    ahead = battery.get("ahead")
    until = ahead.points[-1][0] if ahead is not None and ahead.points else battery["charge_start"]
    storage = battery["storage"]
    deliver = storage.capacity_wh * math.sqrt(max(0.01, min(1.0, storage.efficiency)))

    def cost(extra: Series) -> tuple[float, float, float]:
        sim = replay(battery, pv, {t: load.get(t, 0.0) + extra.get(t, 0.0) for t in load.keys() | extra.keys()},
                     now, until)
        kept = sim.points[-1][1] * deliver if sim.points else 0.0
        return sim.grid_wh, sim.lost_wh, kept
    return cost


CAPACITY_SWING = 0.15          # a run must move the state of charge this far, so rounding of the level hardly counts
CAPACITY_RUNS = 3               # runs per direction before the learned capacity is used
CAPACITY_MIN_W = 50.0           # mean power of an hour that counts as charging or discharging
CAPACITY_DAYS = 120             # recent runs only: a battery ages, and an old capacity should not linger


def capacity_runs(soc: Series, power: Series) -> tuple[list[float], list[float]]:
    """Wh per unit of state of charge in runs of charging and of discharging hours.

    soc: hourly mean state of charge (0..1), power: hourly mean battery power in W (> 0 = charging), both by hour
    start. A run is consecutive hours that all charge (or all discharge) with at least CAPACITY_MIN_W while the
    level moves the same way; its energy runs from the middle of its first hour to the middle of its last (half of
    each, both means belong to the middle of their hour). Runs of at least CAPACITY_SWING count. Charging gives
    capacity / charge efficiency, discharging capacity × discharge efficiency (energy at the power sensor)."""
    charge, discharge = [], []
    run: list[float] = []

    def close() -> None:
        if len(run) < 2:
            return
        swing = soc[run[-1]] - soc[run[0]]
        energy = sum(power[t] for t in run) - (power[run[0]] + power[run[-1]]) / 2
        if abs(swing) >= CAPACITY_SWING and energy * swing > 0:
            (charge if swing > 0 else discharge).append(energy / swing)

    for t in sorted(set(soc) & set(power)):
        p, s = power[t], soc[t]
        sign = 1 if p >= CAPACITY_MIN_W else -1 if p <= -CAPACITY_MIN_W else 0
        prev = run[-1] if run else None
        same = (prev is not None and t - prev == H and sign != 0
                and sign == (1 if power[prev] > 0 else -1) and (s - soc[prev]) * sign >= 0)
        if not same:
            close()
            run = [t] if sign else []
        else:
            run.append(t)
    close()
    return charge, discharge


def learn_capacity(soc: Series, power: Series) -> dict | None:
    """The capacity and round-trip efficiency the battery shows, from its level and its power sensor.

    Charging stores a = capacity / η_charge per unit of level, discharging gives b = capacity × η_discharge. With
    both ways equally efficient, capacity = √(a·b) and the round trip η = b / a, whatever the efficiency is: the
    medians of at least CAPACITY_RUNS runs each way. None with fewer runs, or with a round trip outside 50–102 %,
    where the power sensor measures something else than the battery (an inverter's output, the house)."""
    charge, discharge = capacity_runs(soc, power)
    if len(charge) < CAPACITY_RUNS or len(discharge) < CAPACITY_RUNS:
        return None
    a, b = median(charge), median(discharge)
    if a <= 0 or b <= 0 or not 0.5 <= b / a <= 1.02:
        return None
    return {"capacity_wh": round(math.sqrt(a * b)), "efficiency": round(min(b / a, 1.0), 3),
            "charges": len(charge), "discharges": len(discharge)}


def system_figures(battery: dict, names: dict[str, str]) -> list[dict]:
    """Per independent system (see day_simulation): name, full at, empty at and highest level; empty for one."""
    return [{"name": names.get(key, key), "full": sim.full_at, "empty": sim.empty_at, "max_soc": round(sim.max_soc, 4)}
            for key, sim in sorted((battery.get("systems") or {}).items(), key=lambda kv: names.get(kv[0], kv[0]))]


def soc_days(series: list[tuple[float, list[tuple[float, float, float]]]], day_of) -> list[dict]:
    """Daily lowest and highest state of charge of all batteries as one storage, from hourly statistics.

    series: per battery (capacity_wh, [(hour start, min 0..1, max 0..1)]). Hours missing for one
    battery are left out, so the weighting stays the same. day_of: hour start -> day key.
    """
    cap = sum(c for c, _ in series)
    if not cap:
        return []
    by_hour: dict[float, list[tuple[float, float, float]]] = {}
    for c, rows in series:
        for t, lo, hi in rows:
            by_hour.setdefault(t, []).append((c, lo, hi))
    days: dict[str, dict] = {}
    for t in sorted(by_hour):
        parts = by_hour[t]
        if len(parts) < len(series):
            continue
        lo = sum(c * v for c, v, _ in parts) / cap
        hi = sum(c * v for c, _, v in parts) / cap
        d = days.setdefault(day_of(t), {"date": day_of(t), "min": lo, "max": hi, "hours": 0})
        d["min"], d["max"], d["hours"] = min(d["min"], lo), max(d["max"], hi), d["hours"] + 1
    return [{**d, "min": round(d["min"], 4), "max": round(d["max"], 4)} for d in days.values()]


# ---------------------------------------------------------------- prediction against what happened

PREDICTION_DAYS = 30            # predictions kept


def night_empty(points: list[tuple[float, float]], low: float,
                empties: list[float] | None = None) -> tuple[bool, float | None]:
    """(charged, empty for the night) of a state of charge curve until the next charge start.

    Empty for the night is when the battery reaches its lower limit for the last time and stays there until
    the end of the curve; empty phases before, on a day of changing weather, do not count. A rise at the very
    end that only goes up is the next morning's charge starting before the predicted charge start, not a
    night the battery lasted: it is cut off. Charged is False when the battery never got above the limit
    before: then nothing ran empty. empties: the simulation's times at the limit (see simulate), which place
    the empty time within its hour; without them it is the first sample at the limit (5-minute statistics).
    """
    near = low + LIMIT_TOLERANCE
    at = [i for i, (_, s) in enumerate(points) if s <= near]
    if at and at[-1] < len(points) - 1:
        peak = -math.inf
        for _, s in points[at[-1] + 1:]:
            peak = max(peak, s)
            if s < peak - LIMIT_TOLERANCE:
                break
        else:
            points = points[:at[-1] + 1]                     # only rising after the last time at the limit
    above = [i for i, (_, s) in enumerate(points) if s > near]
    if not above:
        return False, None
    i = above[-1]
    if i == len(points) - 1:
        return True, None                                    # still above the limit at the end: holds the night
    (t1, s1), (t2, s2) = points[i], points[i + 1]
    if empties is None:
        return True, t2
    exact = next((e for e in empties if t1 <= e <= t2), None)
    if exact is not None:
        return True, exact
    if s1 <= s2:
        return True, t2
    return True, t1 + (s1 - near) / (s1 - s2) * (t2 - t1)


def prediction(sim: Simulation, storage: Battery, made: float, day_end: float, until: float) -> dict:
    """What the morning simulation says about the day: when full, when empty for the night, the highest state of charge.

    "empty" is looked for until `until` (the next charge start), since the battery usually runs empty
    in the night; "full" and the highest state of charge belong to the day itself. `night` marks
    predictions whose "empty" is empty for the night; older ones hold the first time empty after the
    morning, which is the morning itself when the battery is empty then.
    """
    today = [s for t, s in sim.points if t < day_end]
    charged, empty = night_empty([(t, s) for t, s in sim.points if t <= until], storage.min_soc, sim.empties)
    return {"made": made, "until": until,
            "full_at": sim.full_at if sim.full_at is not None and sim.full_at < day_end else None,
            "empty_at": empty, "charged": charged, "night": True,
            "max_soc": round(max(today), 4) if today else None,
            "max": storage.max_soc, "min": storage.min_soc}


def remember(log: dict[str, dict], day: date, battery: dict, now: float) -> dict[str, dict] | None:
    """The log with the day's first prediction (see day_simulation) added and old days dropped; None if it has one."""
    key = day.isoformat()
    if key in log:
        return None
    log = {**log, key: prediction(battery["sim"], battery["storage"], now, battery["day_end"], battery["charge_start"])}
    oldest = (day - timedelta(days=PREDICTION_DAYS)).isoformat()
    return {k: v for k, v in log.items() if k >= oldest}


def combined_soc(series: list[tuple[float, Series]]) -> Series:
    """All batteries as one storage: state of charge 0..1 weighted by capacity, where every battery has a value.

    series: per battery (capacity_wh, {time: state of charge 0..1}).
    """
    cap = sum(c for c, _ in series)
    if not cap or not series:
        return {}
    common = set(series[0][1])
    for _, s in series[1:]:
        common &= set(s)
    return {t: sum(c * s[t] for c, s in series) / cap for t in sorted(common)}


def outcome(soc: Series, pred: dict, day_end: float, step: float = 300.0) -> dict | None:
    """What happened to a prediction: first time full and empty in the same windows, highest state of charge.

    soc: combined state of charge per period start (5-minute statistics). `complete` tells whether the
    data already cover the whole window, i.e. whether "not empty" is final.
    """
    made, until = pred["made"], pred["until"]
    day = sorted((t, s) for t, s in soc.items() if made <= t < day_end)
    if not day:
        return None
    full = next((t for t, s in day if s >= pred["max"] - LIMIT_TOLERANCE), None)
    charged, empty = night_empty(sorted((t, s) for t, s in soc.items() if made <= t < until), pred["min"])
    last = max(soc)
    return {"full_at": full, "empty_at": empty, "charged": charged, "max_soc": round(max(s for _, s in day), 4),
            "complete": last + step >= until, "day_complete": last + step >= day_end}


def prediction_review(log: dict[str, dict], soc: Series, day_end_of) -> list[dict]:
    """Logged predictions with their outcome, oldest first; days without data are left out.

    day_end_of: date key -> end of that local day (timestamp).
    """
    out = []
    for key in sorted(log):
        pred = log[key]
        result = outcome(soc, pred, day_end_of(key))
        if result is not None:
            out.append({"date": key, "predicted": pred, "actual": result})
    return out
