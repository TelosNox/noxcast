"""Shading map and today's shade timeline per PV array as image entities (SVG)."""
from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import NoxcastConfigEntry, NoxcastCoordinator
from .model import reachable_cells
from .shade import day_profile, render_day
from .visual import render_shading_map


async def async_setup_entry(hass: HomeAssistant, entry: NoxcastConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    for sub_id, arr in coordinator.arrays.items():
        device = DeviceInfo(identifiers={(DOMAIN, sub_id)}, name=f"Noxcast {arr.name}", manufacturer="Noxcast",
                            entry_type=DeviceEntryType.SERVICE)
        async_add_entities([ShadingMapImage(hass, coordinator, sub_id, device),
                            ShadeTodayImage(hass, coordinator, sub_id, device)], config_subentry_id=sub_id)


class ShadingMapImage(CoordinatorEntity[NoxcastCoordinator], ImageEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "shading_map"
    _attr_content_type = "image/svg+xml"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass: HomeAssistant, coordinator: NoxcastCoordinator, sub_id: str, device: DeviceInfo) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{sub_id}_shading_map"
        self._attr_device_info = device
        self._sub_id = sub_id
        self._reachable: set[tuple[int, int]] | None = None
        self._key: tuple | None = None
        self._svg: bytes | None = None
        self._attr_image_last_updated = dt_util.utcnow()

    @property
    def available(self) -> bool:
        return True

    def _state_key(self) -> tuple:
        learned = self.coordinator.learned.get(self._sub_id)
        return (learned.learned_at if learned else None, dt_util.now().date())

    @callback
    def _handle_coordinator_update(self) -> None:
        # a new image URL (token) only when learning ran or the day changed
        if self._state_key() != self._key:
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        key = self._state_key()
        if self._svg is None or key != self._key:
            arr = self.coordinator.arrays[self._sub_id]
            if self._reachable is None:
                self._reachable = await self.hass.async_add_executor_job(
                    reachable_cells, self.coordinator.site, arr.geometry)
            svg = await self.hass.async_add_executor_job(
                render_shading_map, self.coordinator.site, self.coordinator.learned.get(self._sub_id),
                self._reachable, key[1], arr.name, self.hass.config.language, arr.geometry)
            self._svg, self._key = svg.encode(), key
        return self._svg


class ShadeTodayImage(CoordinatorEntity[NoxcastCoordinator], ImageEntity):
    """Today's sun path through the shading map as timeline; re-rendered on every update (current time marker)."""

    _attr_has_entity_name = True
    _attr_translation_key = "shade_today"
    _attr_content_type = "image/svg+xml"

    def __init__(self, hass: HomeAssistant, coordinator: NoxcastCoordinator, sub_id: str, device: DeviceInfo) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{sub_id}_shade_today"
        self._attr_device_info = device
        self._sub_id = sub_id
        self._key: tuple | None = None
        self._svg: bytes | None = None
        self._attr_image_last_updated = dt_util.utcnow()

    @property
    def available(self) -> bool:
        return True

    def _state_key(self) -> tuple:
        learned = self.coordinator.learned.get(self._sub_id)
        data = self.coordinator.data
        return (learned.learned_at if learned else None, data.updated if data else None)

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._state_key() != self._key:
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        key = self._state_key()
        if self._svg is None or key != self._key:
            arr = self.coordinator.arrays[self._sub_id]
            steps = await self.hass.async_add_executor_job(
                day_profile, self.coordinator.site, arr.geometry, self.coordinator.learned.get(self._sub_id),
                dt_util.start_of_local_day().timestamp())
            svg = render_day(steps, arr.name, lambda ts: dt_util.as_local(dt_util.utc_from_timestamp(ts)),
                             dt_util.utcnow().timestamp(), self.hass.config.language)
            self._svg, self._key = svg.encode(), key
        return self._svg
