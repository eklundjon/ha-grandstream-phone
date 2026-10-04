"""Home Assistant side of pushed events: the webhook and the handset's event URLs.

Our URLs go on the handset when the entry is set up with its device enabled.
They come off again when the entry or the device is disabled, or the entry is
removed, so a handset can move between Home Assistant instances. A plain
unload (a restart or reload) leaves them in place: clearing them would cost a
flash write on the handset each time, and miss events during the restart.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_WEBHOOK_ID
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import GrandstreamClient, GrandstreamError
from .const import DOMAIN
from .coordinator import GrandstreamConfigEntry, async_get_session
from .push import EVENT_SLOTS, UrlTooLong, async_claim, async_release, build_event_urls

_LOGGER = logging.getLogger(__name__)


def slots_in_use_issue_id(entry: GrandstreamConfigEntry) -> str:
    return f"slots_in_use_{entry.entry_id}"


def no_url_issue_id(entry: GrandstreamConfigEntry) -> str:
    return f"no_url_{entry.entry_id}"


def ensure_webhook_id(hass: HomeAssistant, entry: GrandstreamConfigEntry) -> str:
    """The entry's webhook ID, created on first use (entries from 0.1.x lack one)."""
    if not entry.data.get(CONF_WEBHOOK_ID):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_WEBHOOK_ID: webhook.async_generate_id()}
        )
    return entry.data[CONF_WEBHOOK_ID]


def device_disabled(hass: HomeAssistant, entry: GrandstreamConfigEntry) -> bool:
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return any(device.disabled_by is not None for device in devices)


# ---- webhook -------------------------------------------------------------------- #


@callback
def async_register_webhook(hass: HomeAssistant, entry: GrandstreamConfigEntry) -> None:
    webhook_id = entry.data[CONF_WEBHOOK_ID]

    async def handle(hass: HomeAssistant, webhook_id: str, request: web.Request) -> None:
        query = request.query
        event = query.get("e", "")
        # Anyone on the LAN who learns the URL could post to it; the MAC check
        # at least keeps one handset's events from landing on another's entry.
        try:
            mac = format_mac(query.get("m", ""))
        except ValueError:
            mac = ""
        if mac != entry.unique_id or event not in EVENT_SLOTS.values():
            _LOGGER.debug("Ignoring webhook call for %s: event %r", entry.title, event)
            return
        entry.runtime_data.async_handle_push(event, query)

    webhook.async_register(
        hass,
        DOMAIN,
        entry.title,
        webhook_id,
        handle,
        local_only=True,
        allowed_methods=("GET",),
    )
    entry.async_on_unload(lambda: webhook.async_unregister(hass, webhook_id))


# ---- claiming and releasing the handset's slots ---------------------------------- #


async def async_claim_slots(
    hass: HomeAssistant, entry: GrandstreamConfigEntry, *, take_over: bool = False
) -> None:
    """Point the handset's event URLs here, where that's allowed. Never raises."""
    coordinator = entry.runtime_data
    webhook_id = entry.data[CONF_WEBHOOK_ID]
    try:
        base_url = get_url(hass, allow_external=False, allow_cloud=False, prefer_external=False)
        urls = build_event_urls(base_url, webhook_id)
    except (NoURLAvailableError, UrlTooLong) as err:
        _LOGGER.warning("No usable local URL for %s's events: %s", entry.title, err)
        coordinator.push.state = "no_url"
        ir.async_create_issue(
            hass,
            DOMAIN,
            no_url_issue_id(entry),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="no_url",
            translation_placeholders={"name": entry.title},
        )
        return
    ir.async_delete_issue(hass, DOMAIN, no_url_issue_id(entry))

    try:
        result = await async_claim(coordinator.client, urls, webhook_id, take_over=take_over)
    except GrandstreamError as err:
        _LOGGER.warning("Could not set %s's event URLs: %s", entry.title, err)
        coordinator.push.state = "error"
        return

    coordinator.push.claimed = result.claimed
    coordinator.push.foreign = sorted(result.foreign)
    if result.foreign:
        coordinator.push.state = "slots_in_use"
        hosts = sorted({urlsplit(v).netloc or v for v in result.foreign.values()})
        ir.async_create_issue(
            hass,
            DOMAIN,
            slots_in_use_issue_id(entry),
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="slots_in_use",
            translation_placeholders={
                "name": entry.title,
                "events": ", ".join(EVENT_SLOTS[slot] for slot in sorted(result.foreign)),
                "hosts": ", ".join(hosts),
            },
            data={"entry_id": entry.entry_id},
        )
    else:
        coordinator.push.state = "active"
        ir.async_delete_issue(hass, DOMAIN, slots_in_use_issue_id(entry))


async def async_release_slots(
    hass: HomeAssistant, entry: GrandstreamConfigEntry, client: GrandstreamClient
) -> None:
    """Clear our event URLs from the handset. Never raises."""
    try:
        cleared = await async_release(client, entry.data[CONF_WEBHOOK_ID])
    except GrandstreamError as err:
        _LOGGER.warning(
            "Could not clear %s's event URLs; they'll fail harmlessly until replaced: %s",
            entry.title,
            err,
        )
        return
    if cleared:
        _LOGGER.info("Cleared %d event URLs from %s", len(cleared), entry.title)
    for issue_id in (slots_in_use_issue_id(entry), no_url_issue_id(entry)):
        ir.async_delete_issue(hass, DOMAIN, issue_id)


async def async_release_removed(hass: HomeAssistant, entry: GrandstreamConfigEntry) -> None:
    """Release after the entry is gone: it's unloaded, so log in just for this."""
    if not entry.data.get(CONF_WEBHOOK_ID):
        return
    client = GrandstreamClient(
        async_get_session(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    try:
        await async_release_slots(hass, entry, client)
    finally:
        await client.async_logout()


@callback
def async_track_device_disable(hass: HomeAssistant, entry: GrandstreamConfigEntry) -> None:
    """Release when the handset's device is disabled; claim again when re-enabled."""

    @callback
    def changed(event: Event) -> None:
        if event.data.get("action") != "update" or "disabled_by" not in event.data.get("changes", {}):
            return
        device = dr.async_get(hass).async_get(event.data["device_id"])
        if device is None or entry.entry_id not in device.config_entries:
            return
        coordinator = entry.runtime_data
        if device.disabled_by is not None:
            coordinator.push.state = "disabled"
            hass.async_create_task(async_release_slots(hass, entry, coordinator.client))
        else:
            hass.async_create_task(async_claim_slots(hass, entry))

    entry.async_on_unload(hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, changed))
