"""Battery simulation: expected PV against the load forecast, hour by hour, until tomorrow's charge start.

Pure functions, no Home Assistant imports. All batteries are simulated as one storage: capacities
add up, limits and efficiencies are weighted by capacity, power limits add up.

Energy flow per hour (Wh, for the part of the hour from now on):
  surplus = PV - load. A surplus charges the battery up to the upper limit and the charge power;
  what does not fit is "lost" (fed in or curtailed, depending on the system). A deficit
  discharges down to the lower limit plus reserve and the discharge power; the rest comes from
  the grid. Losses: the round-trip efficiency is split evenly into charging and discharging.

Output cap (balcony systems): a battery can sit with one PV array behind an inverter whose output is
capped (800 W, 1600 W). PV and battery together deliver at most that much. PV above the cap cannot
reach the house: it can only charge the battery, the rest is curtailed. And the battery delivers at
most the cap minus what its PV delivers in that hour. Without an array the cap limits the battery alone.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from .const import Series

H = 3600.0
COUPLING_DC, COUPLING_AC = "dc", "ac"
DEFAULT_EFFICIENCY = {COUPLING_DC: 0.95, COUPLING_AC: 0.90}   # round trip
LIMIT_TOLERANCE = 0.02          # within 2 percentage points of a limit counts as reached (as in the review)


@dataclass(frozen=True)
class Battery:
    capacity_wh: float
    soc: float | None                 # 0..1, None = unknown
    max_soc: float = 1.0              # 0..1
    min_soc: float = 0.0              # 0..1, including the reserve
    efficiency: float = 0.95          # round trip
    max_charge_w: float | None = None
    max_discharge_w: float | None = None
    output_w: float | None = None     # cap of the inverter's output, for PV of `array` and battery together
    array: str | None = None          # the PV array (subentry id) behind the same inverter


@dataclass(frozen=True)
class Limits:
    """Per hour start (Wh of the full hour): PV that cannot reach the house, and what the batteries may deliver."""
    capped: dict[float, float] = field(default_factory=dict)       # PV above the output caps: battery or curtailed
    discharge: dict[float, float] = field(default_factory=dict)    # discharge limit of all batteries; empty = none


def limits(batteries: list[Battery], pv_arrays: dict[str, Series], hours: list[float]) -> Limits:
    """The output caps per hour, for the batteries with a known state of charge (those combine uses).

    pv_arrays: Wh per hour start of each array. A battery without output cap keeps its discharge power
    (or none); with a cap its PV first goes to the house up to the cap, the battery gets what is left.
    """
    known = [b for b in batteries if b.capacity_wh > 0 and b.soc is not None]
    if not any(b.output_w for b in known):
        return Limits()
    capped: dict[float, float] = {}
    discharge: dict[float, float] = {}
    for t in hours:
        over, give = 0.0, 0.0
        for b in known:
            own = b.max_discharge_w if b.max_discharge_w else math.inf
            if b.output_w:
                pv = pv_arrays.get(b.array, {}).get(t, 0.0) if b.array else 0.0
                over += max(pv - b.output_w, 0.0)
                own = min(own, max(b.output_w - pv, 0.0))
            give += own
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


def simulate(battery: Battery, pv: Series, load: Series, now: float, end: float,
             caps: Limits | None = None, held_wh: float = 0.0) -> Simulation:
    """Simulate from now until end. pv and load: Wh per hour start (mean W of the hour).

    caps: the output caps (see limits); pv is all PV, the capped part goes to the battery only.
    held_wh: room below the upper limit the batteries will not use (see held_back).
    """
    caps = caps or Limits()
    sim = Simulation()
    cap = battery.capacity_wh
    e = (battery.soc if battery.soc is not None else battery.min_soc) * cap
    e_max, e_min = battery.max_soc * cap, battery.min_soc * cap
    e_max = max(e_max - held_wh, 0.0)
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
        capped = caps.capped.get(t, 0.0) * share                   # PV above an output cap: battery only
        net = (pv.get(t, 0.0) - load.get(t, 0.0)) * share - capped
        surplus = max(net, 0.0) + capped
        hour = {"surplus": round(surplus, 1), "battery": 0.0, "lost": 0.0, "grid": 0.0}
        if surplus > 0:
            sim.surplus_wh += surplus
            room = max(e_max - e, 0.0) / eta                       # surplus the battery can still take
            limit = battery.max_charge_w * share if battery.max_charge_w else math.inf
            take = min(surplus, room, limit)
            e += take * eta
            lost = surplus - take
            if take < surplus and room <= limit and sim.full_at is None:
                sim.full_at = start + (take / surplus) * (min(t + H, end) - start)
            if lost > 0.5 and sim.lost_from is None:
                sim.lost_from = start
            sim.charged_wh += take
            sim.lost_wh += lost
            hour.update(battery=round(take, 1), lost=round(lost, 1))
        if net < 0:                                                # capped PV may charge while the house draws
            need = -net
            avail = max(e - e_min, 0.0) * eta                      # load the battery can still cover
            power = caps.discharge.get(t, battery.max_discharge_w) if caps.discharge else battery.max_discharge_w
            limit = power * share if power is not None else math.inf
            give = min(need, avail, limit)
            e -= give / eta
            if give < need and avail <= limit:
                sim.empties.append(start + (give / need) * (min(t + H, end) - start))
                if sim.empty_at is None:
                    sim.empty_at = sim.empties[-1]
            sim.grid_wh += need - give
            hour.update(battery=round(hour["battery"] - give, 1), grid=round(need - give, 1))
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


def day_simulation(batteries: list[Battery], pv: Series, load: Series | None,
                   band_hours: dict[float, tuple[float, float]] | None, now: float, day_end: float,
                   pv_arrays: dict[str, Series] | None = None) -> dict | None:
    """All batteries as one storage, simulated with expected PV against the load forecast until tomorrow's charge start.

    {"sim", "charge_start", "storage", "day_end"}, plus with a PV band "band": the surplus lost today and
    the time full with PV at P10 and at P90, and the surplus lost per hour with a full battery at P90. None without batteries or load forecast.
    pv_arrays: PV per array, for batteries with an output cap behind an array.
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
    hours = sorted(t for t in set(pv) | set(load) if t + H > now)
    caps = limits(batteries, pv_arrays, hours)
    # a level near the limit only says "it stops here" while it has surplus to take, not after a discharge
    hour = now - now % H
    held = held_back(batteries) if pv.get(hour, 0.0) > load.get(hour, math.inf) else 0.0
    sim = simulate(storage, pv, load, now, start, caps, held)
    out = {"sim": sim, "charge_start": start, "storage": storage, "day_end": day_end, "limits": caps, "held_wh": held}
    if band_hours:
        # the same simulation with PV at P10 and at P90: how much surplus is lost, and when full, in a weak and a strong case
        sims = []
        for i in (0, 1):
            variant = {**pv, **{t: lh[i] for t, lh in band_hours.items()}}
            # each array scaled like the total, so the caps see the weak and the strong case too
            scale = {t: variant[t] / pv[t] for t in band_hours if pv.get(t)}
            arrays = {a: {t: v * scale.get(t, 1.0) for t, v in s.items()} for a, s in pv_arrays.items()}
            sims.append(simulate(storage, variant, load, now, start, limits(batteries, arrays, hours), held))
        out["band"] = {"lost_today": [round(sum(h["lost"] for t, h in s.hours.items() if t < day_end) / 1000, 3)
                                      for s in sims],
                       "full_at": [s.full_at for s in sims],
                       "lost_p90": lost_when_full(sims[1], storage.max_soc)}   # per hour, in the strong case
    return out


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
