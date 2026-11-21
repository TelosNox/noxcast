"""Editing the installation in the panel's Installation tab: the same forms, checks and stored data as the
configuration dialogs. The forms go to the panel as Home Assistant form schemas with their texts, so that the panel
shows them with ha-form beside the diagram; every change is drawn before it is saved, and saving writes the parts
the way the dialogs do (store.async_store), so both ways stay interchangeable."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any

import probatio as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.translation import async_get_translations

from . import config_flow as cf
from . import flowchart, store
from . import systems as sy
from .const import (
    CONF_NAME, CONF_PARENT, CONF_SYSTEM, CONF_TEMPLATE, CONF_UNENTERED_SOC, DOMAIN, SUBENTRY_ARRAY,
    SUBENTRY_BATTERY, SUBENTRY_SYSTEM, TEMPLATE_OPTIONS,
)

HOUSE = "house"
DRAFT = "draft"                       # the id a new part has in the diagram until it is saved
KINDS = (SUBENTRY_SYSTEM, SUBENTRY_ARRAY, SUBENTRY_BATTERY, HOUSE)
# where the texts of each form are: (translation category, flow, steps)
STEPS = {SUBENTRY_SYSTEM: ("config_subentries", "system", ("basics", "settings")),
         SUBENTRY_ARRAY: ("config_subentries", "system", ("array",)),
         SUBENTRY_BATTERY: ("config_subentries", "system", ("battery",)),
         HOUSE: ("config", None, ("reconfigure",))}


class EditError(Exception):
    """A request the installation cannot take: unknown part, or a place it cannot go."""


def revision(entry: ConfigEntry, part: str) -> str:
    """A short fingerprint of what is stored for a part now, so that saving notices a change made elsewhere
    meanwhile (a dialog, a repair, another browser) instead of overwriting it."""
    stored = dict(entry.data) if part == HOUSE else (store.flat(entry).get(part) or [None, None, None])[2]
    return hashlib.sha1(json.dumps(stored, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _kind(entry: ConfigEntry, part: str | None, kind: str | None) -> str:
    if part == HOUSE:
        return HOUSE
    if part:
        found = store.flat(entry).get(part)
        if found is None:
            raise EditError("not_found")
        return found[0]
    if kind not in (SUBENTRY_SYSTEM, SUBENTRY_ARRAY, SUBENTRY_BATTERY):
        raise EditError("not_found")
    return kind


def _system_form(entry: ConfigEntry, part: str | None, to: str | None,
                 draft: dict[str, Any] | None) -> vol.Schema:
    """What a system is and its settings in one form. A changed template fills in its settings anew: the panel then
    sends only name, template and parent as the draft."""
    flat = dict(store.flat(entry))
    systems = sy.systems_of(flat)
    old = dict(flat[part][2]) if part else {}
    given = cf._flatten(draft, cf.SYSTEM_SECTIONS) if draft else {}
    template = given.get(CONF_TEMPLATE) or old.get(CONF_TEMPLATE) or TEMPLATE_OPTIONS[0]
    parents = sy.parent_owners(systems, part) if part else {}
    parent = (given.get(CONF_PARENT) if CONF_PARENT in given else old.get(CONF_PARENT)) if part else to
    sub = bool(parent)
    values = {**cf._system_defaults(systems, flat, part, template, sub),
              **{k: v for k, v in given.items() if v is not None}}
    if part and parent:
        values[CONF_PARENT] = parent
    group, battery = cf._system_kinds(systems, part, template)
    basics = cf._system_basics(values, parents)
    settings = cf._system_settings(values, battery=battery, group=group, sub=sub)
    return vol.Schema({**basics.schema, **settings.schema})


def _schema(entry: ConfigEntry, kind: str, part: str | None, to: str | None,
            draft: dict[str, Any] | None) -> vol.Schema:
    flat = dict(store.flat(entry))
    systems = sy.systems_of(flat)
    if kind == HOUSE:
        return cf._main_schema(cf._flatten(draft, cf.MAIN_SECTIONS) if draft else dict(entry.data))
    if kind == SUBENTRY_SYSTEM:
        return _system_form(entry, part, to, draft)
    old = dict(flat[part][2]) if part else {CONF_SYSTEM: to}
    if kind == SUBENTRY_ARRAY:
        return cf._array_schema(draft or old, sy.part_owners(systems))
    return cf._battery_schema(cf._battery_data(draft) if draft else old, sy.part_owners(systems, part or ""))


def _initial(schema: vol.Schema) -> dict[str, Any]:
    """The values a form starts with, as the dialogs fill them in: a suggested value, else the default."""
    out: dict[str, Any] = {}
    for key, value in schema.schema.items():
        name = str(key)
        if isinstance(value, cf.section):
            inner = _initial(value.schema)
            if inner:
                out[name] = inner
            continue
        suggested = (getattr(key, "description", None) or {}).get("suggested_value")
        if suggested is not None:
            out[name] = suggested
        elif not isinstance(key.default, vol.Undefined):
            default = key.default()
            if default is not None:
                out[name] = default
    return out


async def _texts(hass: HomeAssistant, kind: str, lang: str) -> dict[str, dict[str, str]]:
    """Labels, helper texts, section names and error messages of a form, from the dialogs' translations: fields of
    the form itself win over equally named ones inside a section."""
    category, flow, steps = STEPS[kind]
    strings = await async_get_translations(hass, lang, category, [DOMAIN])
    base = f"component.{DOMAIN}.{category}." + (f"{flow}." if flow else "")
    out: dict[str, dict[str, str]] = {"labels": {}, "helpers": {}, "sections": {}, "errors": {}}
    for key, text in strings.items():
        if not key.startswith(base):
            continue
        rest = key[len(base):].split(".")
        if rest[0] == "error" and len(rest) == 2:
            out["errors"][rest[1]] = text
        if rest[0] != "step" or rest[1] not in steps:
            continue
        rest = rest[2:]
        if rest[0] in ("data", "data_description") and len(rest) == 2:
            out["labels" if rest[0] == "data" else "helpers"][rest[1]] = text
        elif rest[0] == "sections" and len(rest) == 3 and rest[2] == "name":
            out["sections"][rest[1]] = text
        elif rest[0] == "sections" and len(rest) == 4 and rest[2] in ("data", "data_description"):
            out["labels" if rest[2] == "data" else "helpers"].setdefault(rest[3], text)
    return out


async def async_form(hass: HomeAssistant, entry: ConfigEntry, part: str | None, kind: str | None = None,
                     to: str | None = None, draft: dict[str, Any] | None = None, lang: str = "en") -> dict[str, Any]:
    """The form of a part (part = its id, HOUSE for the household) or of a new one (kind, and to = the system it goes
    into, for a new subsystem its system): schema for ha-form, starting values, texts and the stored revision."""
    kind = _kind(entry, part, kind)
    if part is None and kind != SUBENTRY_SYSTEM and to not in sy.part_owners(sy.systems_of(store.flat(entry))):
        raise EditError("not_possible")
    schema = _schema(entry, kind, part, to, draft)
    return {"kind": kind, "part": part, "to": to,
            "schema": vol.to_field_list(schema, custom_serializer=cv.custom_serializer),
            "data": _initial(schema),
            "revision": revision(entry, part) if part else None, **await _texts(hass, kind, lang)}


async def _check(hass: HomeAssistant, entry: ConfigEntry, kind: str, part: str | None, to: str | None,
                 user_input: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """The stored data of the part from its form, and the problems the dialogs would show for the same input."""
    flat = dict(store.flat(entry))
    systems = sy.systems_of(flat)
    if kind == HOUSE:
        data = cf._flatten(user_input, cf.MAIN_SECTIONS)
        errors = await cf._validate_main(SimpleNamespace(hass=hass), data)
        kept = {k: entry.data[k] for k in (CONF_UNENTERED_SOC,) if k in entry.data}
        return {**data, **kept}, errors
    if kind == SUBENTRY_SYSTEM:
        given = dict(user_input)
        basics = {k: given.pop(k) for k in (CONF_NAME, CONF_TEMPLATE, CONF_PARENT) if k in given}
        if part:
            if basics.get(CONF_PARENT) and basics[CONF_PARENT] not in sy.parent_owners(systems, part):
                return None, {CONF_PARENT: "parent_not_possible"}
        else:
            basics.pop(CONF_PARENT, None)
            if to:
                basics[CONF_PARENT] = to
        if not basics.get(CONF_PARENT):
            basics.pop(CONF_PARENT, None)
        return cf._system_data(basics, given, bool(basics.get(CONF_PARENT)))
    if kind == SUBENTRY_ARRAY:
        errors = cf._validate_array(hass, user_input)
        data = cf._array_data(user_input)
        data.setdefault(CONF_SYSTEM, to or (flat[part][2].get(CONF_SYSTEM) if part else None))
        if not errors and data.get(CONF_SYSTEM) not in sy.part_owners(systems):
            errors = {CONF_SYSTEM: "system_has_subsystems"}
        return data, errors
    choices = sy.part_owners(systems, part or "")
    errors = cf._battery_errors(hass, user_input, choices)
    data = cf._battery_data(user_input)
    data.setdefault(CONF_SYSTEM, to or (flat[part][2].get(CONF_SYSTEM) if part else None))
    return data, errors


def _with(entry: ConfigEntry, kind: str, part: str | None, data: dict[str, Any] | None) -> tuple[dict, Any]:
    """(household, parts) as they would be with the part's new data."""
    if kind == HOUSE:
        return data or dict(entry.data), store.flat(entry)
    flat = dict(store.flat(entry))
    if data is not None:
        flat[part or DRAFT] = (kind, data.get(CONF_NAME) or "…", data)
    return dict(entry.data), flat


async def async_preview(hass: HomeAssistant, entry: ConfigEntry, kind: str | None, part: str | None,
                        to: str | None, user_input: dict[str, Any], lang: str) -> dict[str, Any]:
    """The diagram as it would be after saving this form, and the form's problems; stores nothing."""
    kind = _kind(entry, part, kind)
    data, errors = await _check(hass, entry, kind, part, to, user_input)
    if data is None:                 # a form that does not make a part yet: draw what it has
        data = {k: v for k, v in cf._flatten(user_input, cf.SYSTEM_SECTIONS).items() if v not in (None, "", False)}
        if not part and to:
            data[CONF_PARENT] = to
    household, flat = _with(entry, kind, part, data)
    tree = sy.tree(household, flat)
    return {"diagram": flowchart.render(tree, lang), "errors": errors, "node": part or DRAFT}


async def async_save(hass: HomeAssistant, entry: ConfigEntry, kind: str | None, part: str | None, to: str | None,
                     user_input: dict[str, Any], rev: str | None) -> dict[str, str]:
    """Store the part as the dialogs do; the problems instead when there are any, or "changed" when what is stored
    changed since the form was opened. The entry reloads as after a dialog."""
    kind = _kind(entry, part, kind)
    if part and rev != revision(entry, part):
        return {"base": "changed"}
    data, errors = await _check(hass, entry, kind, part, to, user_input)
    if errors or data is None:
        return errors
    if kind == HOUSE:
        hass.config_entries.async_update_entry(entry, data=data)
        return {}
    flat = dict(store.flat(entry))
    flat[part or cf.ulid_now()] = (kind, data[CONF_NAME], data)
    store.async_store(hass, entry, flat)
    return {}
