"""The swap assistant of the Installation tab: a system's device is replaced by a new one (docs/concept.md, System
swap). The form lists every entity the system's parts name, each with the entity of the new device Noxcast
suggests, the swap date and per PV array whether a new device converts its power. Saving freezes the arrays' hours
up to the swap (swap.freeze) and then replaces the entities in the stored parts, so that both happen together or
not at all; the entry reloads as after a dialog. Undo turns it back while the old statistics still exist."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

import probatio as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from . import editor, store
from . import swap as sw
from . import systems as sy
from .const import CONF_GRID, CONF_HOUSE_LOAD, CONF_POWER_SENSORS, SUBENTRY_ARRAY
from .learning import Segment
from .sources import async_hourly_statistics

WHEN = "when"
NEW_STATS_DAYS = 90                      # how far back the first hour of the new sensors is looked for
HOUSEHOLD = (CONF_HOUSE_LOAD, CONF_GRID)  # household entities an old device may have measured

TEXTS = {
    "de": {
        WHEN: "Getauscht am",
        "when_help": "Leer lassen: der Tag, an dem die neuen PV-Sensoren ihre ersten Werte haben. Der Tag des Tauschs "
                     "zählt für keines der Geräte.",
        "row": "{part} · {field}",
        "row_help": "Bisher {old}. Gleich lassen, wenn sich dieser Sensor nicht ändert.",
        "house": "Haushalt",
        "device": "{part}: neues Gerät wandelt die Leistung",
        "device_help": "Ein: neuer Wechselrichter oder neue MPPTs, Noxcast lernt den Wirkungsgrad neu. Aus: nur der "
                       "Akku oder ein Zähler ändert sich.",
        "errors": {
            "changed": "Die Anlage wurde inzwischen anderswo geändert. Bitte den Tausch neu öffnen.",
            "nothing_changed": "Kein Sensor ist anders als bisher: es gibt nichts zu tauschen.",
            "entity_not_found": "Diese Entität gibt es in Home Assistant nicht.",
            "no_new_statistics": "Die neuen PV-Sensoren haben noch keine Werte. Trage das Datum ein oder warte, bis "
                                 "das neue Gerät eine Stunde gelaufen ist.",
            "in_future": "Das Datum liegt in der Zukunft.",
            "invalid_value": "Das ist kein gültiges Datum.",
            "before_last_swap": "Das Datum liegt vor dem letzten Tausch dieses Systems.",
            "old_statistics_missing": "Von den bisherigen PV-Sensoren gibt es keine Statistiken mehr. Was gelernt "
                                      "wurde, lässt sich nicht mehr übernehmen.",
            "frozen_unreadable": "Die gespeicherten Stunden früherer Tausche ließen sich nicht lesen. Sieh ins Log, "
                                 "bevor du tauschst.",
            "not_found": "Dieses System gibt es nicht mehr.",
        },
    },
    "en": {
        WHEN: "Swapped on",
        "when_help": "Leave empty: the day the new PV sensors have their first values. The day of the swap counts "
                     "for neither device.",
        "row": "{part} · {field}",
        "row_help": "So far {old}. Keep it if this sensor does not change.",
        "house": "Household",
        "device": "{part}: a new device converts the power",
        "device_help": "On: a new inverter or new MPPTs, Noxcast learns the efficiency anew. Off: only the battery or "
                       "a meter changes.",
        "errors": {
            "changed": "The installation was changed elsewhere meanwhile. Please open the swap again.",
            "nothing_changed": "No sensor differs from before: there is nothing to swap.",
            "entity_not_found": "Home Assistant has no such entity.",
            "no_new_statistics": "The new PV sensors have no values yet. Enter the date, or wait until the new "
                                 "device has run for an hour.",
            "in_future": "The date is in the future.",
            "invalid_value": "This is not a valid date.",
            "before_last_swap": "The date is before this system's last swap.",
            "old_statistics_missing": "The previous PV sensors have no statistics any more. What was learned can no "
                                      "longer be carried over.",
            "frozen_unreadable": "The stored hours of earlier swaps could not be read. Check the log before swapping.",
            "not_found": "This system no longer exists.",
        },
    },
}


def _texts(lang: str) -> dict[str, Any]:
    return TEXTS.get(lang[:2], TEXTS["en"])


def error_text(code: str, lang: str) -> str:
    return _texts(lang)["errors"].get(code, code)


def _parts(flat: dict[str, Any], system: str) -> list[str]:
    """The system's parts that a device swap touches: itself, its subsystems, their PV arrays and batteries."""
    systems = sy.systems_of(flat)
    if system not in systems:
        raise editor.EditError("not_found")
    out: list[str] = []
    for sid in (system, *systems[system].children):
        s = systems[sid]
        out += [sid, *s.arrays, *([s.battery] if s.battery else [])]
    return out


def _arrays(flat: dict[str, Any], system: str) -> list[str]:
    return [p for p in _parts(flat, system) if flat[p][0] == SUBENTRY_ARRAY]


def _rows(entry: ConfigEntry, system: str) -> list[tuple[str | None, str, str]]:
    """(part id or None for the household, field, entity) for every entity the swap may replace, in form order."""
    flat = dict(store.flat(entry))
    rows: list[tuple[str | None, str, str]] = []
    seen: set[str] = set()
    for part in _parts(flat, system):
        for field, value in flat[part][2].items():
            for entity in sw.entities_in(value):
                if entity not in seen:
                    seen.add(entity)
                    rows.append((part, field, entity))
    rows += [(None, f, entry.data[f]) for f in HOUSEHOLD if entry.data.get(f) and entry.data[f] not in seen]
    return rows


def revision(entry: ConfigEntry, system: str) -> str:
    """A fingerprint of what the swap changes: the system's parts and the household."""
    flat = dict(store.flat(entry))
    stored = {p: flat[p][2] for p in _parts(flat, system)} | {"house": dict(entry.data)}
    return hashlib.sha1(json.dumps(stored, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _candidate(hass: HomeAssistant, entity_id: str) -> sw.Candidate:
    state = hass.states.get(entity_id)
    attrs = state.attributes if state else {}
    entry = er.async_get(hass).async_get(entity_id)
    return sw.Candidate(entity_id, entry.device_id if entry else None, attrs.get("unit_of_measurement"),
                        attrs.get("device_class"))


def _suggestions(hass: HomeAssistant, entry: ConfigEntry, rows: list[tuple[str | None, str, str]]) -> dict[str, str]:
    """The entities of a new device for the system's old ones (swap.suggest); the household's only where the system's
    device measured them, which the shared Home Assistant device shows."""
    used = {e for _, _, e in rows} | set(sw.entities_in(dict(entry.data)))
    used |= {e for _, _, d in store.flat(entry).values() for e in sw.entities_in(d)}
    domains = {e.split(".")[0] for _, _, e in rows}
    candidates = [_candidate(hass, s.entity_id) for s in hass.states.async_all(domains) if s.entity_id not in used]
    old = {e: _candidate(hass, e) for _, _, e in rows}
    devices = {old[e].device for part, _, e in rows if part is not None and old[e].device}
    wanted = [old[e] for part, _, e in rows if part is not None or (old[e].device and old[e].device in devices)]
    return {k: v for k, v in sw.suggest(wanted, candidates).items() if v}


async def async_form(hass: HomeAssistant, entry: ConfigEntry, system: str, lang: str) -> dict[str, Any]:
    """The swap form for ha-form: schema, starting values, texts and the revision it was built on."""
    t = _texts(lang)
    flat = dict(store.flat(entry))
    rows = _rows(entry, system)
    suggested = _suggestions(hass, entry, rows)
    labels: dict[str, dict[str, str]] = {}

    async def field_label(part: str | None, field: str) -> str:
        if part is None:
            kind = editor.HOUSE
        else:
            kind = flat[part][0]
        if kind not in labels:
            labels[kind] = (await editor._texts(hass, kind, lang))["labels"]
        return labels[kind].get(field, field)

    schema: dict[Any, Any] = {vol.Optional(WHEN): selector.DateSelector()}
    texts = {"labels": {WHEN: t[WHEN]}, "helpers": {WHEN: t["when_help"]}, "sections": {},
             "errors": dict(t["errors"])}
    for i, (part, field, entity) in enumerate(rows):
        key = f"e{i}"
        schema[vol.Required(key, description={"suggested_value": suggested.get(entity, entity)})] = (
            selector.EntitySelector(selector.EntitySelectorConfig(domain=entity.split(".")[0])))
        name = flat[part][1] if part else t["house"]
        texts["labels"][key] = t["row"].format(part=name, field=await field_label(part, field))
        texts["helpers"][key] = t["row_help"].format(old=entity)
    for array in _arrays(flat, system):
        key = f"d_{array}"
        schema[vol.Optional(key, default=True)] = selector.BooleanSelector()
        texts["labels"][key] = t["device"].format(part=flat[array][1])
        texts["helpers"][key] = t["device_help"]
    form = vol.Schema(schema)
    return {"system": system, "name": flat[system][1],
            "schema": vol.to_field_list(form, custom_serializer=cv.custom_serializer),
            "data": editor._initial(form), "revision": revision(entry, system), **texts}


async def _first_hour(hass: HomeAssistant, sensors: set[str]) -> float | None:
    """The first hour with a value of any of the sensors, within NEW_STATS_DAYS."""
    if not sensors:
        return None
    stats = await async_hourly_statistics(hass, sorted(sensors), dt_util.utcnow() - timedelta(days=NEW_STATS_DAYS),
                                          "mean")
    starts = [t for series in stats.values() for t, v in series.items() if v is not None]
    return min(starts) if starts else None


async def async_save(hass: HomeAssistant, entry: ConfigEntry, system: str, user_input: dict[str, Any],
                     rev: str | None) -> dict[str, str]:
    """Freeze the system's arrays up to the swap and put the new entities in place; the problems instead. Nothing is
    changed unless both can be done."""
    coordinator = entry.runtime_data
    flat = dict(store.flat(entry))
    if system not in sy.systems_of(flat):
        return {"base": "not_found"}
    if rev != revision(entry, system):
        return {"base": "changed"}
    rows = _rows(entry, system)
    mapping: dict[str, str] = {}
    errors: dict[str, str] = {}
    registry = er.async_get(hass)
    for i, (_, _, old) in enumerate(rows):
        new = user_input.get(f"e{i}") or old
        if new == old:
            continue
        if hass.states.get(new) is None and registry.async_get(new) is None:
            errors[f"e{i}"] = "entity_not_found"
        mapping[old] = new
    if errors:
        return errors
    if not mapping:
        return {"base": "nothing_changed"}
    arrays = _arrays(flat, system)
    new_pv = {mapping.get(s, s) for a in arrays for s in flat[a][2].get(CONF_POWER_SENSORS) or ()}
    if user_input.get(WHEN):
        day = dt_util.parse_date(str(user_input[WHEN])[:10])
        if day is None:
            return {WHEN: "invalid_value"}
    else:
        first = await _first_hour(hass, new_pv - {s for a in arrays for s in flat[a][2].get(CONF_POWER_SENSORS) or ()})
        if first is None:
            return {WHEN: "no_new_statistics"}
        day = dt_util.as_local(dt_util.utc_from_timestamp(first)).date()
    until = dt_util.start_of_local_day(day).timestamp()
    resume = dt_util.start_of_local_day(day + timedelta(days=1)).timestamp()
    if until > dt_util.utcnow().timestamp():
        return {WHEN: "in_future"}
    starts = {a: max((float(f["resume"]) for f in coordinator.frozen.get(a, ())), default=None) for a in arrays}
    if any(s is not None and until < s for s in starts.values()):
        return {WHEN: "before_last_swap"}
    if not coordinator.frozen_writable:
        return {"base": "frozen_unreadable"}
    history, _ = await coordinator.async_pv_history_now()
    old_pv = [s for a in arrays for s in coordinator.arrays[a].sensors]
    if old_pv and not any(t < until for s in old_pv for t in history.power.get(s, {})):
        return {"base": "old_statistics_missing"}
    frozen = {}
    for a in arrays:
        arr = coordinator.arrays[a]
        segment = Segment(until, resume, arr.sensors, arr.limits, arr.caps, bool(user_input.get(f"d_{a}", True)))
        frozen[a] = {**sw.freeze(coordinator.site, a, history, segment, starts[a]), "system": system,
                     "mapping": mapping, "swapped": day.isoformat()}
    await coordinator.async_add_frozen(frozen)
    _replace(hass, entry, system, mapping)
    return {}


def _replace(hass: HomeAssistant, entry: ConfigEntry, system: str, mapping: dict[str, str]) -> None:
    """The entities replaced in the system's parts and the household; the entry reloads."""
    flat = dict(store.flat(entry))
    parts = set(_parts(flat, system))
    household = {k: sw.replaced(v, mapping) if k in HOUSEHOLD else v for k, v in entry.data.items()}
    if household != dict(entry.data):
        hass.config_entries.async_update_entry(entry, data=household)
    store.async_store(hass, entry, {p: (k, t, sw.replaced(dict(d), mapping) if p in parts else d)
                                    for p, (k, t, d) in flat.items()})


def swaps(entry: ConfigEntry) -> list[dict[str, Any]]:
    """The swaps of an entry for the Installation tab, newest first: system, date, its arrays and whether it is the
    newest of its system (only that one can be undone)."""
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None:
        return []
    names = {p: t for p, (_, t, _) in store.flat(entry).items()}
    found: dict[tuple[str, float], dict[str, Any]] = {}
    for array, entries in coordinator.frozen.items():
        for f in entries:
            key = (f.get("system", ""), float(f["until"]))
            item = found.setdefault(key, {"system": key[0], "until": key[1], "swapped": f.get("swapped"),
                                          "arrays": [], "devices": []})
            item["arrays"].append(names.get(array, array))
            if f.get("device", True):
                item["devices"].append(names.get(array, array))
    out = sorted(found.values(), key=lambda s: -s["until"])
    newest: set[str] = set()
    for s in out:
        s["undo"] = s["system"] not in newest
        newest.add(s["system"])
    return out


async def async_undo(hass: HomeAssistant, entry: ConfigEntry, system: str) -> dict[str, str]:
    """Turn the system's newest swap back: the old entities in place again and its frozen hours dropped, while the
    old statistics still exist (afterwards the frozen copy is the only one and stays)."""
    coordinator = entry.runtime_data
    mine = [(a, f) for a, entries in coordinator.frozen.items() for f in entries if f.get("system") == system]
    if not mine:
        return {"base": "not_found"}
    until = max(float(f["until"]) for _, f in mine)
    last = [(a, f) for a, f in mine if float(f["until"]) == until]
    old_pv = sorted({s for _, f in last for s in f["sensors"]})
    stats = await async_hourly_statistics(hass, old_pv, dt_util.utc_from_timestamp(until) - timedelta(days=3), "mean",
                                          end=dt_util.utc_from_timestamp(until))
    if not any(v is not None for series in stats.values() for v in series.values()):
        return {"base": "old_statistics_missing"}
    if not coordinator.frozen_writable:
        return {"base": "frozen_unreadable"}
    reverse = {new: old for old, new in last[0][1].get("mapping", {}).items()}
    await coordinator.async_drop_frozen({a: until for a, _ in last})
    _replace(hass, entry, system, reverse)
    return {}
