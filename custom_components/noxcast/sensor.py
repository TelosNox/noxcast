"""Forecast and diagnostic sensors for the total and for each PV array."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import archive as fc_archive
from .const import ATTR_SCHEMA_VERSION, COMPARE_SOURCE, DOMAIN, ERROR_DAYS
from .coordinator import NoxcastConfigEntry, NoxcastCoordinator
from .shade import STEP_MIN, day_profile, shade_periods, shaded_minutes, unknown_periods


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
    series: str = "forecast"            # forecast (latest), morning (fixed in the morning), expected (actual + corrected)
    day_offset: int | None = None       # day of the hourly attribute


FORECASTS = (
    ForecastDescription(key="energy_today", translation_key="energy_today", value=lambda h: day_energy(h, 0),
                        series="morning", day_offset=0),
    ForecastDescription(key="energy_expected_today", translation_key="energy_expected_today",
                        value=lambda h: day_energy(h, 0), series="expected", day_offset=0),
    ForecastDescription(key="energy_tomorrow", translation_key="energy_tomorrow", value=lambda h: day_energy(h, 1), day_offset=1),
    ForecastDescription(key="energy_day_after", translation_key="energy_day_after", value=lambda h: day_energy(h, 2), day_offset=2),
    ForecastDescription(key="energy_remaining_today", translation_key="energy_remaining_today", value=remaining_today,
                        series="expected", day_offset=0),
)


async def async_setup_entry(hass: HomeAssistant, entry: NoxcastConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    total_device = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Noxcast", manufacturer="Noxcast",
                              entry_type=DeviceEntryType.SERVICE)
    async_add_entities([ForecastSensor(coordinator, d, None, total_device) for d in FORECASTS]
                       + [LastLearningSensor(coordinator, total_device), ForecastErrorSensor(coordinator, total_device)])

    for sub_id, arr in coordinator.arrays.items():
        device = DeviceInfo(identifiers={(DOMAIN, sub_id)}, name=f"Noxcast {arr.name}", manufacturer="Noxcast",
                            entry_type=DeviceEntryType.SERVICE)
        async_add_entities(
            [ForecastSensor(coordinator, d, sub_id, device) for d in FORECASTS]
            + [LearnedSensor(coordinator, sub_id, device, "efficiency", lambda la: round(la.efficiency * 100, 1)),
               LearnedSensor(coordinator, sub_id, device, "shading_coverage", lambda la: round(la.coverage * 100, 1)),
               LearnedSensor(coordinator, sub_id, device, "temp_coeff", lambda la: round(la.temp_coeff * 100, 3), "%/K"),
               LearnedSensor(coordinator, sub_id, device, "agreement", lambda la: la.agreement, None),
               ShadeTodaySensor(coordinator, sub_id, device)],
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
        return getattr(data, self.entity_description.series).get(self._sub_id, {})

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value(self._hours())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.day_offset is None:
            return None
        attrs = {"schema_version": ATTR_SCHEMA_VERSION,
                 "hourly": hourly_attribute(self._hours(), self.entity_description.day_offset)}
        data = self.coordinator.data
        if self.entity_description.series == "morning" and data is not None:
            attrs["fixed_at"] = data.morning_fixed.isoformat() if data.morning_fixed else None
        if self.entity_description.series == "expected" and data is not None and self._sub_id is not None:
            attrs["correction"] = data.correction.get(self._sub_id)
        return attrs


class LearnedSensor(NoxcastEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE

    @property
    def available(self) -> bool:
        return True                                       # learned state does not depend on the weather source

    def __init__(self, coordinator, sub_id: str, device: DeviceInfo, key: str, value: Callable,
                 unit: str | None = PERCENTAGE) -> None:
        super().__init__(coordinator, f"{sub_id}_{key}", device)
        self._attr_native_unit_of_measurement = unit
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
        return {"samples": learned.samples, "first_sample": first, "shading_cells": len(learned.cells),
                "temperature_learned": learned.temp_learned,
                "temperature_source": learned.temp_source, "clear_filter": learned.clear_filter,
                "direction_factor": [min(learned.direction.values()), max(learned.direction.values())]
                if learned.direction else None,
                "sky_view": learned.sky_view,
                "reflection_max": max(learned.reflection.values()) if learned.reflection else None}


class ShadeTodaySensor(NoxcastEntity, SensorEntity):
    """Shaded minutes on today's sun path; shaded and not yet learned periods as attributes."""

    _attr_translation_key = "shade_today"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_icon = "mdi:weather-sunny-off"

    def __init__(self, coordinator, sub_id: str, device: DeviceInfo) -> None:
        super().__init__(coordinator, f"{sub_id}_shade_today", device)
        self._sub_id = sub_id
        self._key: tuple | None = None
        self._steps: list = []

    @property
    def available(self) -> bool:
        return True

    def _profile(self) -> list:
        learned = self.coordinator.learned.get(self._sub_id)
        day_start = dt_util.start_of_local_day().timestamp()
        key = (learned.learned_at if learned else None, day_start)
        if key != self._key:
            arr = self.coordinator.arrays[self._sub_id]
            self._steps = day_profile(self.coordinator.site, arr.geometry, learned, day_start)
            self._key = key
        return self._steps

    @property
    def native_value(self) -> int:
        return shaded_minutes(self._profile())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        def iso(ts: float) -> str:
            return dt_util.as_local(dt_util.utc_from_timestamp(ts)).isoformat()
        steps = self._profile()
        return {"schema_version": ATTR_SCHEMA_VERSION, "step_minutes": STEP_MIN,
                "periods": [{"start": iso(p["start"]), "end": iso(p["end"]),
                             "min_transmission": p["min_transmission"]} for p in shade_periods(steps)],
                "not_learned": [{"start": iso(p["start"]), "end": iso(p["end"])} for p in unknown_periods(steps)]}


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
                "archive_errors": self.coordinator.archive_errors,
                "excluded_periods": len(self.coordinator.exclusions)}


class ForecastErrorSensor(NoxcastEntity, SensorEntity):
    """Day-ahead daily energy error over the last days, from the forecast archive."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1
    _attr_translation_key = "forecast_error"

    def __init__(self, coordinator, device: DeviceInfo) -> None:
        super().__init__(coordinator, f"{coordinator.config_entry.entry_id}_forecast_error", device)

    @property
    def available(self) -> bool:
        return True

    def _since(self):
        return dt_util.now().date() - timedelta(days=ERROR_DAYS)

    @property
    def native_value(self) -> float | None:
        error, _ = fc_archive.daily_error(self.coordinator.forecast_archive, self._since())
        return None if error is None else round(error * 100, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        archive = self.coordinator.forecast_archive
        _, days = fc_archive.daily_error(archive, self._since())
        per_array = {}
        for sub_id, arr in self.coordinator.arrays.items():
            error, _ = fc_archive.daily_error(archive, self._since(), sub_id)
            per_array[arr.name] = None if error is None else round(error * 100, 1)
        sources = {}
        for name in (COMPARE_SOURCE, fc_archive.MIX):            # same days as the comparison source
            error, n = fc_archive.daily_error(archive, self._since(), source=name)
            if n:
                own = {k: v for k, v in fc_archive.day_totals(archive).items()
                       if k in fc_archive.day_totals(archive, source=name) and k >= self._since().isoformat()}
                own_actual = sum(a for _, a in own.values())
                sources[name] = {"error": round(error * 100, 1) if error is not None else None, "days": n,
                                 "primary_error": round(sum(abs(f - a) for f, a in own.values()) / own_actual * 100, 1)
                                 if own_actual > 0 else None}
        return {"days": days, "archived_days": len(archive), "per_array": per_array, "comparison": sources}
