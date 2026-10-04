"""Select entities: backlight timeout and keypad backlight."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import KEY_BACKLIGHT_TIMEOUT, KEY_KEYPAD_BACKLIGHT
from .coordinator import GrandstreamConfigEntry
from .entity import GrandstreamValueEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class GrandstreamSelectDescription(SelectEntityDescription):
    value_key: str
    # Option name -> the handset's value, in display order.
    values: dict[str, str]


SELECTS = (
    # The handset's own choices (seconds; 0 = never). Verified on WP826: the
    # screen goes dark this long after the last activity, and setting it
    # while the screen is dark doesn't wake it.
    GrandstreamSelectDescription(
        key="backlight_timeout",
        translation_key="backlight_timeout",
        value_key=KEY_BACKLIGHT_TIMEOUT,
        values={
            "never": "0",
            "15_s": "15",
            "30_s": "30",
            "1_min": "60",
            "2_min": "120",
            "5_min": "300",
            "10_min": "600",
            "20_min": "1200",
            "30_min": "1800",
        },
        entity_category=EntityCategory.CONFIG,
    ),
    GrandstreamSelectDescription(
        key="keypad_backlight",
        translation_key="keypad_backlight",
        value_key=KEY_KEYPAD_BACKLIGHT,
        values={"off": "0", "on": "1", "auto": "2"},
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
        GrandstreamSelect(coordinator, description, description.value_key)
        for description in SELECTS
    )


class GrandstreamSelect(GrandstreamValueEntity, SelectEntity):
    """A handset setting with a fixed set of choices."""

    entity_description: GrandstreamSelectDescription

    @property
    def options(self) -> list[str]:
        return list(self.entity_description.values)

    @property
    def current_option(self) -> str | None:
        # A value outside the known choices (another model or firmware) reads
        # as unknown rather than as the wrong option.
        raw = self.raw_value
        return next(
            (name for name, value in self.entity_description.values.items() if value == raw),
            None,
        )

    async def async_select_option(self, option: str) -> None:
        await self._async_write(self.entity_description.values[option])
