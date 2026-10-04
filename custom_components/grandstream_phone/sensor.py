"""Sensor entities: call state, voicemail, battery, Wi-Fi signal."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import KEY_UNREAD_VOICEMAIL
from .coordinator import GrandstreamConfigEntry, GrandstreamCoordinator, GrandstreamData
from .entity import GrandstreamEntity

PARALLEL_UPDATES = 0

# Line states seen on a WP826 (fw 1.0.3.35), most significant first, and the
# option name each is reported as.
LINE_STATES = {"ringing": "ringing", "connected": "connected", "onhold": "on_hold"}


def line_state(data: GrandstreamData) -> str | None:
    """The handset's overall call state, from its per-line states."""
    states = {str(line.get("state")) for line in data.line_status if isinstance(line, dict)}
    for raw, option in LINE_STATES.items():
        if raw in states:
            return option
    if states <= {"idle"}:
        return "idle"
    return None  # only states this integration doesn't know


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, kw_only=True)
class GrandstreamSensorDescription(SensorEntityDescription):
    value_fn: Callable[[GrandstreamData], Any]
    # Whether the handset (or the session's role) provides this at all.
    exists_fn: Callable[[GrandstreamCoordinator], bool] = lambda _: True
    available_fn: Callable[[GrandstreamData], bool] = lambda _: True


SENSORS = (
    GrandstreamSensorDescription(
        key="call_state",
        translation_key="call_state",
        device_class=SensorDeviceClass.ENUM,
        options=["idle", *LINE_STATES.values()],
        value_fn=line_state,
    ),
    # Verified on WP826 with UniFi Talk: rises ~16 s after a message is left,
    # clears while it's being listened to. Account 1 only.
    GrandstreamSensorDescription(
        key="unread_voicemail",
        translation_key="unread_voicemail",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _int(data.values.get(KEY_UNREAD_VOICEMAIL)),
        available_fn=lambda data: KEY_UNREAD_VOICEMAIL in data.values,
    ),
    # The handset only gives battery status to its admin account.
    GrandstreamSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _int((data.battery or {}).get("capacity")),
        exists_fn=lambda coordinator: coordinator.is_admin,
        available_fn=lambda data: data.battery is not None,
    ),
    # The handset's own 0-4 bar scale.
    GrandstreamSensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _int((data.wifi or {}).get("signal")),
        available_fn=lambda data: data.wifi is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GrandstreamConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        GrandstreamSensor(coordinator, description)
        for description in SENSORS
        if description.exists_fn(coordinator)
    )


class GrandstreamSensor(GrandstreamEntity, SensorEntity):
    """A value derived from one poll."""

    entity_description: GrandstreamSensorDescription

    @property
    def available(self) -> bool:
        return super().available and self.entity_description.available_fn(self.coordinator.data)

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)
