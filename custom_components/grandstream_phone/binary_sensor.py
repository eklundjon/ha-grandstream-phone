"""Binary sensor entities: ringing, in use, SIP registration, battery low, on charger."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import KEY_ACCOUNT_REGISTERED
from .coordinator import GrandstreamConfigEntry, GrandstreamCoordinator, GrandstreamData
from .entity import GrandstreamEntity
from .sensor import line_state

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class GrandstreamBinarySensorDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[GrandstreamData], bool | None]
    available_fn: Callable[[GrandstreamData], bool] = lambda _: True
    # Whether the handset (or the session's role) provides this at all.
    exists_fn: Callable[[GrandstreamCoordinator], bool] = lambda _: True


BINARY_SENSORS = (
    # Observed on WP826: phone status goes available -> ringing -> busy, and a
    # line goes ringing -> connected (-> onhold) -> idle. Either source counts.
    GrandstreamBinarySensorDescription(
        key="ringing",
        translation_key="ringing",
        is_on_fn=lambda data: data.phone_status == "ringing" or line_state(data) == "ringing",
    ),
    GrandstreamBinarySensorDescription(
        key="in_use",
        translation_key="in_use",
        is_on_fn=lambda data: (
            data.phone_status == "busy" or line_state(data) in ("connected", "on_hold")
        ),
    ),
    GrandstreamBinarySensorDescription(
        key="sip_registered",
        translation_key="sip_registered",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda data: data.values.get(KEY_ACCOUNT_REGISTERED) == "1",
        available_fn=lambda data: KEY_ACCOUNT_REGISTERED in data.values,
    ),
    # From the admin-only battery status. Seen on WP826: Discharging out of the
    # cradle, Charging in it, Full once charged in it.
    GrandstreamBinarySensorDescription(
        key="on_charger",
        translation_key="on_charger",
        device_class=BinarySensorDeviceClass.PLUG,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda data: (data.battery or {}).get("status") in ("Charging", "Full"),
        available_fn=lambda data: data.battery is not None,
        exists_fn=lambda coordinator: coordinator.is_admin,
    ),
)

BATTERY_LOW = BinarySensorEntityDescription(
    key="battery_low",
    translation_key="battery_low",
    device_class=BinarySensorDeviceClass.BATTERY,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GrandstreamConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        GrandstreamBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
        if description.exists_fn(coordinator)
    ]
    entities.append(GrandstreamBatteryLow(coordinator, BATTERY_LOW))
    async_add_entities(entities)


class GrandstreamBinarySensor(GrandstreamEntity, BinarySensorEntity):
    """An on/off state derived from one poll."""

    entity_description: GrandstreamBinarySensorDescription

    @property
    def available(self) -> bool:
        return super().available and self.entity_description.available_fn(self.coordinator.data)

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.coordinator.data)


class GrandstreamBatteryLow(GrandstreamEntity, RestoreEntity, BinarySensorEntity):
    """On below the handset's low-battery threshold, off above its sufficient one.

    Fed by the handset's battery events, which only come at threshold crossings
    (20 % down, 60 % up, by default), so it works for `user` sessions too. It's
    unknown until the first crossing, and keeps its last state through restarts
    because the next crossing may be days away. The level from that event is
    an attribute; it's not refreshed in between.
    """

    _restored_on: bool | None = None
    _restored_attributes: dict[str, Any] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            if last.state in (STATE_ON, STATE_OFF):
                self._restored_on = last.state == STATE_ON
            self._restored_attributes = {
                k: last.attributes[k] for k in ("level", "reported") if k in last.attributes
            }

    @property
    def is_on(self) -> bool | None:
        event = self.coordinator.battery_event
        return event.low if event is not None else self._restored_on

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        event = self.coordinator.battery_event
        if event is None:
            return self._restored_attributes or None
        return {"level": event.level, "reported": event.received.isoformat()}
