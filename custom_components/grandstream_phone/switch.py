"""Switch entities: do not disturb."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import KEY_DND
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GrandstreamConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        GrandstreamSwitch(coordinator, description, description.value_key)
        for description in SWITCHES
    )


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
