"""Switch entities: do not disturb, Wi-Fi power save."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import KEY_DND, KEY_WIFI_POWER_SAVE
from .coordinator import GrandstreamConfigEntry
from .entity import GrandstreamValueEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class GrandstreamSwitchDescription(SwitchEntityDescription):
    value_key: str


SWITCHES = (
    # The handset's runtime DND state. Verified on WP826: remote changes show
    # on the handset (without waking the screen), and DND toggled on the
    # handset shows up here on the next poll.
    GrandstreamSwitchDescription(
        key="do_not_disturb",
        translation_key="do_not_disturb",
        value_key=KEY_DND,
    ),
)


# The handset's Wi-Fi power saving trades reachability for battery: with it on
# and the screen dark, the handset answers slowly or not at all for seconds at a
# time (on WP826, ping replies went from about 11 % to most of them with it
# off). Off by default so automations can decide when reachability matters.
# Verified on WP826 fw 1.0.3.35: applies live, writable by `user`, no Wi-Fi drop
# seen.
POWER_SAVE = SwitchEntityDescription(
    key="wifi_power_save",
    translation_key="wifi_power_save",
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,
)
POWER_SAVE_DISABLED = "3"
POWER_SAVE_MODES = ("0", "4")  # generic PSM, U-APSD


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GrandstreamConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = [
        GrandstreamSwitch(coordinator, description, description.value_key)
        for description in SWITCHES
    ]
    entities.append(GrandstreamPowerSaveSwitch(coordinator, POWER_SAVE, KEY_WIFI_POWER_SAVE))
    async_add_entities(entities)


class GrandstreamSwitch(GrandstreamValueEntity, SwitchEntity):
    """An on/off handset setting."""

    entity_description: GrandstreamSwitchDescription

    @property
    def is_on(self) -> bool | None:
        raw = self.raw_value
        return None if raw not in ("0", "1") else raw == "1"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write("1")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write("0")


class GrandstreamPowerSaveSwitch(GrandstreamValueEntity, SwitchEntity):
    """Wi-Fi power save on (generic or U-APSD) or off.

    Turning it on restores the mode last seen, so a handset set to U-APSD isn't
    moved to generic power save. If it's never been seen on, generic it is.
    """

    _last_mode = POWER_SAVE_MODES[0]

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.raw_value in POWER_SAVE_MODES:
            self._last_mode = self.raw_value
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.raw_value in POWER_SAVE_MODES:
            self._last_mode = self.raw_value

    @property
    def is_on(self) -> bool | None:
        raw = self.raw_value
        if raw in POWER_SAVE_MODES:
            return True
        return False if raw == POWER_SAVE_DISABLED else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(self._last_mode)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(POWER_SAVE_DISABLED)
