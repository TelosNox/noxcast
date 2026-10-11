"""Forecast and diagnostic sensors for the total and for each PV array."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import ATTR_SCHEMA_VERSION, DOMAIN
from .coordinator import NoxcastConfigEntry, NoxcastCoordinator


def _day_bounds(offset: int) -> tuple[float, float]:
    start = dt_util.start_of_local_day() + timedelta(days=offset)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


MIN_DAY_HOURS = 20          # a day total needs (almost) all hours; partial days would look like bad weather


def day_energy(hours: dict[float, float], offset: int) -> float | None:
    a, b = _day_bounds(offset)
    day = [wh for t, wh in hours.items() if a <= t < b]
    return round(sum(day) / 1000, 3) if len(day) >= MIN_DAY_HOURS else None


def remaining_today(hours: dict[float, float]) -> float:
    now = dt_util.utcnow().timestamp()
    _, end = _day_bounds(0)
    total = 0.0
    for t, wh in hours.items():
        if t + 3600 <= now or t >= end:
            continue
        total += wh * min(1.0, (t + 3600 - now) / 3600) if t < now else wh
    return round(total / 1000, 3)


def hourly_attribute(hours: dict[float, float], offset: int) -> dict[str, float]:
    a, b = _day_bounds(offset)
    return {dt_util.as_local(dt_util.utc_from_timestamp(t)).isoformat(): round(wh / 1000, 3)
            for t, wh in hours.items() if a <= t < b}


@dataclass(frozen=True, kw_only=True)
class ForecastDescription(SensorEntityDescription):
    value: Callable[[dict[float, float]], float | None]
    day_offset: int | None = None


FORECASTS = (
    ForecastDescription(key="energy_today", translation_key="energy_today", value=lambda h: day_energy(h, 0), day_offset=0),
    ForecastDescription(key="energy_tomorrow", translation_key="energy_tomorrow", value=lambda h: day_energy(h, 1), day_offset=1),
    ForecastDescription(key="energy_day_after", translation_key="energy_day_after", value=lambda h: day_energy(h, 2), day_offset=2),
    ForecastDescription(key="energy_remaining_today", translation_key="energy_remaining_today", value=remaining_today),
)


async def async_setup_entry(hass: HomeAssistant, entry: NoxcastConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    total_device = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Noxcast", manufacturer="Noxcast",
                              entry_type=DeviceEntryType.SERVICE)
    async_add_entities([ForecastSensor(coordinator, d, None, total_device) for d in FORECASTS]
                       + [LastLearningSensor(coordinator, total_device)])

    for sub_id, arr in coordinator.arrays.items():
        device = DeviceInfo(identifiers={(DOMAIN, sub_id)}, name=f"Noxcast {arr.name}", manufacturer="Noxcast",
                            entry_type=DeviceEntryType.SERVICE)
        async_add_entities(
            [ForecastSensor(coordinator, d, sub_id, device) for d in FORECASTS]
            + [LearnedSensor(coordinator, sub_id, device, "efficiency", lambda la: round(la.efficiency * 100, 1)),
               LearnedSensor(coordinator, sub_id, device, "shading_coverage", lambda la: round(la.coverage * 100, 1))],
            config_subentry_id=sub_id)


class NoxcastEntity(CoordinatorEntity[NoxcastCoordinator]):
    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({"hourly"})      # keep the recorder database small

    def __init__(self, coordinator: NoxcastCoordinator, unique_id: str, device: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._attr_device_info = device


class ForecastSensor(NoxcastEntity, SensorEntity):
    """Forecast energy for a day; hourly values as attribute with a versioned schema."""

    entity_description: ForecastDescription
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, description: ForecastDescription, sub_id: str | None, device: DeviceInfo) -> None:
        owner = sub_id or coordinator.config_entry.entry_id
        super().__init__(coordinator, f"{owner}_{description.key}", device)
        self.entity_description = description
        self._sub_id = sub_id

    @property
    def available(self) -> bool:
        # a single failed weather update must not blank the forecast
        return self.coordinator.forecast_available

    def _hours(self) -> dict[float, float]:
        data = self.coordinator.data
        if data is None:
            return {}
        return data.total if self._sub_id is None else data.arrays.get(self._sub_id, {})

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value(self._hours())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.day_offset is None:
            return None
        return {"schema_version": ATTR_SCHEMA_VERSION,
                "hourly": hourly_attribute(self._hours(), self.entity_description.day_offset)}


class LearnedSensor(NoxcastEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE

    @property
    def available(self) -> bool:
        return True                                       # learned state does not depend on the weather source

    def __init__(self, coordinator, sub_id: str, device: DeviceInfo, key: str, value: Callable) -> None:
        super().__init__(coordinator, f"{sub_id}_{key}", device)
        self._attr_translation_key = key
        self._sub_id = sub_id
        self._value = value

    @property
    def native_value(self) -> float | None:
        learned = self.coordinator.learned.get(self._sub_id)
        return self._value(learned) if learned else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        learned = self.coordinator.learned.get(self._sub_id)
        if not learned:
            return None
        first = dt_util.utc_from_timestamp(learned.first_sample).isoformat() if learned.first_sample else None
        return {"samples": learned.samples, "first_sample": first, "shading_cells": len(learned.cells)}


class LastLearningSensor(NoxcastEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "last_learning"

    def __init__(self, coordinator, device: DeviceInfo) -> None:
        super().__init__(coordinator, f"{coordinator.config_entry.entry_id}_last_learning", device)

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_learning

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_error": self.coordinator.last_learning_error,
                "excluded_periods": len(self.coordinator.exclusions)}
