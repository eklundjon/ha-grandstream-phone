"""Binary sensor entities: ringing, in use, SIP registration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import KEY_ACCOUNT_REGISTERED
from .coordinator import GrandstreamConfigEntry, GrandstreamData
from .entity import GrandstreamEntity
from .sensor import line_state

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class GrandstreamBinarySensorDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[GrandstreamData], bool | None]
    available_fn: Callable[[GrandstreamData], bool] = lambda _: True


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
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GrandstreamConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        GrandstreamBinarySensor(coordinator, description) for description in BINARY_SENSORS
    )


class GrandstreamBinarySensor(GrandstreamEntity, BinarySensorEntity):
    """An on/off state derived from one poll."""

    entity_description: GrandstreamBinarySensorDescription

    @property
    def available(self) -> bool:
        return super().available and self.entity_description.available_fn(self.coordinator.data)

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.coordinator.data)
