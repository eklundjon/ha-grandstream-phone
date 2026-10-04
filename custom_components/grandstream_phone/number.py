"""Number entities: LCD brightness and ring volume."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import KEY_LCD_BRIGHTNESS, KEY_RING_VOLUME
from .coordinator import GrandstreamConfigEntry
from .entity import GrandstreamValueEntity

# Writes go to one small device; one at a time.
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class GrandstreamNumberDescription(NumberEntityDescription):
    value_key: str


NUMBERS = (
    # The handset's own UI offers 10-100 in steps of 10. Verified on WP826:
    # a change applies at once, without a popup, and without waking a dark
    # screen.
    GrandstreamNumberDescription(
        key="lcd_brightness",
        translation_key="lcd_brightness",
        value_key=KEY_LCD_BRIGHTNESS,
        native_min_value=10,
        native_max_value=100,
        native_step=10,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
    GrandstreamNumberDescription(
        key="ring_volume",
        translation_key="ring_volume",
        value_key=KEY_RING_VOLUME,
        native_min_value=0,
        native_max_value=10,
        native_step=1,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GrandstreamConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        GrandstreamNumber(coordinator, description, description.value_key)
        for description in NUMBERS
    )


class GrandstreamNumber(GrandstreamValueEntity, NumberEntity):
    """A numeric handset setting."""

    entity_description: GrandstreamNumberDescription

    @property
    def native_value(self) -> float | None:
        try:
            return float(self.raw_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self._async_write(str(int(round(value))))
