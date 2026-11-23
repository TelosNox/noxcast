"""System swap: the hours of a replaced device, frozen so that learning keeps them (docs/concept.md, System swap).

Learning is recomputed every night from Home Assistant's statistics, so what an array learned lives only as long as
the old device's sensors stay readable. A swap writes the array's hours before it into a store of its own: the power
of the sensors the swap replaces, and how the battery, grid and output filters classified each hour, which needs
sensors that are gone afterwards. merge puts them back into the history, so that learning sees one history.

Pure functions, no Home Assistant imports; the coordinator loads and saves the store.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone

from .learning import PvHistory, Segment
from .model import HOUR
from .solar import Site, sun_position

FROZEN_VERSION = 1
KINDS = ("full", "charge", "held", "unsure", "output")    # the hour sets of PvHistory a segment keeps
STATS = ("mean", "min", "max")


def _day(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).date().isoformat()


def _day_start(day: str) -> float:
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()


def _sun_up(site: Site, t: float) -> bool:
    """Whether the sun is above the horizon at some time of the hour: sky view, facade and the hours with the sun
    behind the plane need the edges of the day too."""
    return any(sun_position(site, t + f * HOUR).elevation > 0 for f in (0.0, 0.5, 1.0))


def _to_days(series: Mapping[float, float]) -> dict[str, list]:
    """{hour start: W} as lists of 24 per UTC day, None where there is no value."""
    out: dict[str, list] = {}
    for t, v in series.items():
        out.setdefault(_day(t), [None] * 24)[int((t % 86400) // HOUR)] = round(v, 1)
    return out


def _from_days(days: Mapping[str, list]) -> dict[float, float]:
    out = {}
    for day, values in days.items():
        start = _day_start(day)
        out.update({start + i * HOUR: float(v) for i, v in enumerate(values) if v is not None})
    return out


def freeze(site: Site, sid: str, history: PvHistory, segment: Segment, start: float | None = None) -> dict:
    """The array's hours from start (the previous swap's resume) to segment.until, as the store keeps them: the power
    of the segment's sensors in W per hour with the sun up, and the hour sets of the filters.

    history is the one learning saw before the swap, with the installation as it was. Every sensor of the segment is
    frozen, also one that stays: its statistics may be deleted later, and then the frozen copy is the only one."""
    def inside(t: float) -> bool:
        return t < segment.until and (start is None or t >= start)

    power = {}
    for sensor in segment.sensors:
        factor = history.factors.get(sensor, 1.0)
        mean = history.power.get(sensor, {})
        hours = {t for t, v in mean.items() if v is not None and t % HOUR == 0 and inside(t) and _sun_up(site, t)}
        power[sensor] = {
            kind: _to_days({t: v * factor for t in hours if (v := stats.get(sensor, {}).get(t)) is not None})
            for kind, stats in zip(STATS, (history.power, history.power_min, history.power_max))}
    return {
        "until": segment.until, "resume": segment.resume, "start": start,
        "sensors": list(segment.sensors), "limits": [[s, w] for s, w in segment.limits],
        "caps": [[list(sensors), w] for sensors, w in segment.caps], "device": segment.device,
        "power": power,
        "hours": {kind: sorted(int(t) for t in getattr(history, kind).get(sid, ()) if inside(t)) for kind in KINDS},
    }


def segment_of(frozen: Mapping) -> Segment:
    """The segment a stored entry describes."""
    return Segment(float(frozen["until"]), float(frozen["resume"]), tuple(frozen["sensors"]),
                   tuple((s, float(w)) for s, w in frozen.get("limits") or ()),
                   tuple((tuple(sensors), float(w)) for sensors, w in frozen.get("caps") or ()),
                   bool(frozen.get("device", True)))


def segments(stored: Iterable[Mapping]) -> tuple[Segment, ...]:
    """An array's stored segments as learning sees them, oldest first."""
    return tuple(sorted((segment_of(f) for f in stored), key=lambda s: s.until))


def sensors(store: Mapping[str, list]) -> set[str]:
    """Every sensor a frozen segment holds: the 5-minute archive keeps them (fine.prune)."""
    return {s for entries in store.values() for f in entries for s in f["sensors"]}


def merge(history: PvHistory, store: Mapping[str, list]) -> PvHistory:
    """The history with the frozen hours of every array put back: before a swap the frozen power and hour sets hold,
    after it the live ones. Power goes back into the sensors' units, so that history.factors still apply."""
    if not any(store.values()):
        return history
    stats = {kind: {s: dict(v) for s, v in series.items()}
             for kind, series in zip(STATS, (history.power, history.power_min, history.power_max))}
    sets = {kind: dict(getattr(history, kind)) for kind in KINDS}
    for sid, entries in store.items():
        if not entries:
            continue
        last = max(float(f["until"]) for f in entries)
        for kind in KINDS:
            live = {t for t in sets[kind].get(sid, ()) if t >= last}
            frozen = {float(t) for f in entries for t in f["hours"].get(kind, ())}
            if live or frozen:
                sets[kind][sid] = frozenset(live | frozen)
        for f in entries:
            until, start = float(f["until"]), f.get("start")
            for sensor, kinds in f["power"].items():
                factor = history.factors.get(sensor, 1.0) or 1.0
                for kind in STATS:
                    series = stats[kind].setdefault(sensor, {})
                    # the frozen copy holds before the swap; a sensor that stays runs on live after it
                    for t in [t for t in series if t < until and (start is None or t >= start)]:
                        del series[t]
                    series.update({t: v / factor for t, v in _from_days(kinds.get(kind, {})).items()})
    return PvHistory(stats["mean"], stats["min"], stats["max"], history.factors, history.air_temp,
                     history.satellite_ghi, sets["full"], sets["charge"], sets["held"], sets["unsure"],
                     history.snowfall, output=sets["output"])


def prune(store: dict[str, list], oldest: float) -> None:
    """Drop what is older than the learning window: hours before oldest, and segments that end before it."""
    for sid in list(store):
        kept = []
        for f in store[sid]:
            if float(f["until"]) <= oldest:
                continue
            first = _day(oldest)
            for kinds in f["power"].values():
                for kind in STATS:
                    days = kinds.get(kind, {})
                    for day in [d for d in days if d < first]:
                        del days[day]
            f["hours"] = {kind: [t for t in hours if t >= oldest] for kind, hours in f["hours"].items()}
            kept.append(f)
        store[sid] = kept
