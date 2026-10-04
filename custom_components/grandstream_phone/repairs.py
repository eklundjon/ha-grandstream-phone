"""Repairs: taking over event URL slots that point at another destination."""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant

from .event_urls import async_claim_slots


class TakeOverSlotsFlow(ConfirmRepairFlow):
    """Confirm, then point the handset's remaining slots here."""

    def __init__(self, entry_id: str) -> None:
        super().__init__()
        self._entry_id = entry_id

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is not None and entry.state.recoverable and hasattr(entry, "runtime_data"):
                await async_claim_slots(self.hass, entry, take_over=True)
            return self.async_create_entry(data={})
        return await super().async_step_confirm(user_input)


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    return TakeOverSlotsFlow(str((data or {}).get("entry_id", "")))
