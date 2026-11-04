"""Binary sensors: per flexible consumer whether starting it now is worth it."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import plan as pl
from .const import ATTR_SCHEMA_VERSION, DOMAIN, START_SHARE_DEFAULT
from .coordinator import NoxcastConfigEntry
from .sensor import NoxcastEntity, _iso


async def async_setup_entry(hass: HomeAssistant, entry: NoxcastConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    if not coordinator.house_load:
        return
    device = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Noxcast", manufacturer="Noxcast",
                        entry_type=DeviceEntryType.SERVICE)
    async_add_entities([StartNowSensor(coordinator, device, sid, c.name)
                        for sid, c in coordinator.consumers.items() if c.timing == "flexible"])


class StartNowSensor(NoxcastEntity, BinarySensorEntity):
    """On while starting the consumer now covers at least its threshold from surplus and is about as good as
    the best window still to come today."""

    _attr_icon = "mdi:play-circle-outline"
    _attr_translation_key = "flex_start_now"

    def __init__(self, coordinator, device: DeviceInfo, sid: str, name: str) -> None:
        super().__init__(coordinator, f"{sid}_start_now", device)
        self._sid = sid
        self._attr_translation_placeholders = {"name": name}

    def _p(self) -> dict | None:
        data = self.coordinator.data
        return data.plan.get(self._sid) if data is not None else None

    @property
    def _threshold(self) -> float:
        share = self.coordinator.consumers[self._sid].start_share
        return START_SHARE_DEFAULT if share is None else share

    @property
    def available(self) -> bool:
        return self._p() is not None

    @property
    def is_on(self) -> bool | None:
        p = self._p()
        return pl.start_now(p["now"], p["today"], self._threshold / 100) if p else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        p = self._p()
        if not p:
            return None
        best = p["today"]
        return {"schema_version": ATTR_SCHEMA_VERSION, "pv_share_now": round(p["now"].pv_share * 100),
                "threshold": self._threshold, "best_today_start": _iso(best.start) if best else None,
                "best_today_pv_share": round(best.pv_share * 100) if best else None}
