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
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
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
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GrandstreamConfigEntry
) -> dict[str, Any]:
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
            },
            "data": asdict(data) if data else None,
        },
        TO_REDACT,
    )
