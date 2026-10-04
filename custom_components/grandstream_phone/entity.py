"""Base entity: device info, naming and availability for one handset."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GrandstreamCoordinator


class GrandstreamEntity(CoordinatorEntity[GrandstreamCoordinator]):
    """An entity of one handset.

    Unique IDs combine the handset's MAC with the description's key, a
    descriptive name like ``lcd_brightness`` rather than a P-number, so a model
    that stores the same setting under another P-number keeps the same entity.
    """

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: GrandstreamCoordinator, description: EntityDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        device_id = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{device_id}_{description.key}"
        # setup registers the device in full; this just links to it.
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_id)})


class GrandstreamValueEntity(GrandstreamEntity):
    """An entity backed by one handset setting or runtime value.

    Unavailable when the handset doesn't report the setting, which is how an
    untested model with a different settings layout shows up.
    """

    _value_key: str

    def __init__(
        self,
        coordinator: GrandstreamCoordinator,
        description: EntityDescription,
        value_key: str,
    ) -> None:
        super().__init__(coordinator, description)
        self._value_key = value_key

    @property
    def available(self) -> bool:
        return super().available and self._value_key in self.coordinator.data.values

    @property
    def raw_value(self) -> str | None:
        return self.coordinator.data.values.get(self._value_key)

    async def _async_write(self, value: str) -> None:
        await self.coordinator.async_set_values({self._value_key: value})
