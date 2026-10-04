"""Event entity: calls, as the handset pushes them."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import CALL_EVENT_TYPES, GrandstreamConfigEntry, signal_call
from .entity import GrandstreamEntity

PARALLEL_UPDATES = 0

CALL = EventEntityDescription(
    key="call",
    translation_key="call",
    event_types=sorted(set(CALL_EVENT_TYPES.values())),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GrandstreamConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([GrandstreamCallEvent(entry.runtime_data, CALL)])


class GrandstreamCallEvent(GrandstreamEntity, EventEntity):
    """Fires on incoming, answered, outgoing, missed and ended calls.

    Event data carries the other party's `number` and `name`. Only pushed
    events produce these; polling alone never does.
    """

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_call(self.coordinator.config_entry.entry_id),
                self._handle_call,
            )
        )

    @callback
    def _handle_call(self, event_type: str, attributes: dict[str, Any]) -> None:
        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()
