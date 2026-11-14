"""The installation as Home Assistant stores it: one config subentry per system of the top level, its subsystems,
PV arrays and battery inside (systems.nest). Everything else reads the parts flat (parts, flat) and writes them
back through async_store, which also moves the devices and entities of an array to the subentry it now sits in, so
that removing its old subentry removes none of them."""
from __future__ import annotations

from types import MappingProxyType
from typing import Any, NamedTuple

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from . import systems as sy
from .const import DOMAIN, SUBENTRY_ARRAY


class Part(NamedTuple):
    """A part as the subentries were read before 1.6: type, title, data; home is the subentry that stores it."""

    subentry_type: str
    title: str
    data: Any
    home: str


def stored(entry: ConfigEntry) -> dict[str, tuple[str, str, Any]]:
    return {sid: (sub.subentry_type, sub.title, dict(sub.data)) for sid, sub in entry.subentries.items()}


def locations(entry: ConfigEntry) -> dict[str, str]:
    """Part id -> the subentry that stores it now."""
    out = {}
    for sid, sub in entry.subentries.items():
        out[sid] = sid
        for pid in (sub.data.get(sy.PARTS) or {}) if sub.subentry_type in sy.INSTALLATION else ():
            out[pid] = sid
    return out


def flat(entry: ConfigEntry) -> dict[str, tuple[str, str, Any]]:
    """Every part by its own id (systems.flatten)."""
    return sy.flatten(stored(entry))


def parts(entry: ConfigEntry) -> dict[str, Part]:
    where = locations(entry)
    return {pid: Part(kind, title, MappingProxyType(dict(data)), where[pid])
            for pid, (kind, title, data) in flat(entry).items()}


def _devices_of(hass: HomeAssistant, entry: ConfigEntry, array: str) -> list[dr.DeviceEntry]:
    return [d for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
            if (DOMAIN, array) in d.identifiers]


def _move_array(hass: HomeAssistant, entry: ConfigEntry, array: str, to: str) -> None:
    """The array's device and entities to another subentry (Home Assistant removes what a removed subentry
    still holds). The entities first: moving the device removes those of its entities still on the old
    subentry."""
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    for device in _devices_of(hass, entry, array):
        for e in er.async_entries_for_device(ent_reg, device.id, include_disabled_entities=True):
            if e.config_entry_id == entry.entry_id and e.config_subentry_id != to:
                ent_reg.async_update_entity(e.entity_id, config_subentry_id=to)
        dev_reg.async_update_device(device.id, new_config_subentry_id=to)


def async_store(hass: HomeAssistant, entry: ConfigEntry, parts_flat: sy.Subs) -> bool:
    """Write the parts back as nested subentries; True when anything changed. Order: new subentries first, then
    the data, the devices and entities of moved arrays, and last the subentries no longer needed."""
    want = sy.nest(parts_flat)
    where = locations(entry)
    have = entry.subentries
    changed = False
    for sid, (kind, title, data) in want.items():
        if sid not in have:
            hass.config_entries.async_add_subentry(entry, ConfigSubentry(
                data=MappingProxyType(dict(data)), subentry_id=sid, subentry_type=kind, title=title, unique_id=None))
            changed = True
        elif have[sid].title != title or dict(have[sid].data) != dict(data):
            hass.config_entries.async_update_subentry(entry, have[sid], title=title, data=data)
            changed = True
    new_where = {}
    for sid, (_, _, data) in want.items():
        new_where[sid] = sid
        for pid in data.get(sy.PARTS) or {}:
            new_where[pid] = sid
    for pid, (kind, _, _) in parts_flat.items():
        if kind == SUBENTRY_ARRAY and where.get(pid) != new_where.get(pid):
            _move_array(hass, entry, pid, new_where[pid])
    # an array no longer there: its device goes, and its entities with it, as when its subentry was removed
    for pid, sid in where.items():
        if pid not in parts_flat and pid != sid:
            for device in _devices_of(hass, entry, pid):
                dr.async_get(hass).async_remove_device(device.id)
            changed = True
    for sid in [s for s in have if s not in want]:
        hass.config_entries.async_remove_subentry(entry, sid)
        changed = True
    return changed
