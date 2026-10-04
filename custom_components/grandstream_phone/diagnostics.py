"""Diagnostics download.

Aimed at bug and device-support reports, which get pasted into public issues:
keep what helps add support for a model (model, firmware, role, raw values and
status shapes) and redact what identifies the household (credentials, address,
MACs, Wi-Fi names, callers).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant

from .coordinator import GrandstreamConfigEntry

# async_redact_data matches these keys at any depth.
TO_REDACT = {
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
    "unique_id",
    "mac",
    "remotename",
    "remotenumber",
    "ssid",
    "bssid",
    CONF_WEBHOOK_ID,
    # MAC and caller details in the last pushed event.
    "m",
    "r",
    "n",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GrandstreamConfigEntry
) -> dict[str, Any]:
    if entry.state is not ConfigEntryState.LOADED:
        # Disabled or failed to set up: there's no session or data to show.
        return async_redact_data({"entry": entry.as_dict()}, TO_REDACT)
    coordinator = entry.runtime_data
    info = coordinator.login_info
    data = coordinator.data
    return async_redact_data(
        {
            "entry": entry.as_dict(),
            "login": asdict(info) if info else None,
            "coordinator": {
                "last_update_success": coordinator.last_update_success,
                "update_interval": str(coordinator.update_interval),
                "backoff_until": coordinator.backoff_until,
            },
            "data": asdict(data) if data else None,
            "push": asdict(coordinator.push),
            "battery_event": asdict(coordinator.battery_event) if coordinator.battery_event else None,
        },
        TO_REDACT,
    )
