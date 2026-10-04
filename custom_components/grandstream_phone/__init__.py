"""The Grandstream Phone integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import CONF_MODEL, DOMAIN, MANUFACTURER
from .coordinator import GrandstreamConfigEntry, GrandstreamCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: GrandstreamConfigEntry) -> bool:
    coordinator = GrandstreamCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    # Logs out on unload. Newer Home Assistant also does this for coordinators
    # tied to an entry; a second call is a no-op.
    entry.async_on_unload(coordinator.async_shutdown)

    # Register the handset now, so it shows up with its firmware even before
    # any entity does. The unique_id is the handset's MAC.
    info = coordinator.login_info
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        connections={(dr.CONNECTION_NETWORK_MAC, entry.unique_id)} if entry.unique_id else set(),
        manufacturer=MANUFACTURER,
        model=entry.data.get(CONF_MODEL),
        name=entry.title,
        sw_version=info.firmware if info else None,
        configuration_url=f"https://{entry.data[CONF_HOST]}",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GrandstreamConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
