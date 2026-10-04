"""The handset's event URLs ("Action URLs"): building, claiming and releasing them.

The handset has one URL setting ("slot") per event and requests that URL, with
its variables filled in, when the event happens. This integration points slots
at a Home Assistant webhook. Observed on a WP826 (fw 1.0.3.35): plain GET, no
authentication, sent within about a second, and a value over roughly 256
characters is silently dropped.

Slots are shared with anything else the owner configured, including another
Home Assistant instance, so this module only ever writes a slot that's empty or
already points at this entry's webhook, and only ever clears its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .api import GrandstreamClient

# Slot (P-number) -> event name sent as `e=`. Verified on WP826 fw 1.0.3.35.
CALL_SLOTS = {
    "8310": "incoming",
    "8311": "outgoing",
    "8312": "missed",
    "22171": "answered",
    "8313": "established",
    "8314": "terminated",
    "8315": "rejected",
}
STATE_SLOTS = {
    "8328": "busy",
    "8329": "idle",
    "8316": "dnd_on",
    "8317": "dnd_off",
}
BATTERY_SLOTS = {
    "22568": "battery_low",
    "22570": "battery_ok",
}
EVENT_SLOTS = {**CALL_SLOTS, **STATE_SLOTS, **BATTERY_SLOTS}

# Handset variables to request with each kind of event. Short keys keep URLs
# well under the length limit. `$mac` comes with every event and is checked
# against the entry.
_CALL_QUERY = "m=$mac&r=$remote&n=$display_remote"
_STATE_QUERY = "m=$mac"
_BATTERY_QUERY = "m=$mac&b=$battery"

# Longest value the handset keeps (256 accepted, 300 dropped on WP826).
MAX_URL_LENGTH = 256


class UrlTooLong(Exception):
    """An event URL wouldn't fit in the handset's setting."""


def webhook_path(webhook_id: str) -> str:
    return f"/api/webhook/{webhook_id}"


def build_event_urls(base_url: str, webhook_id: str) -> dict[str, str]:
    """Slot -> the URL the handset should request for that event."""
    base = base_url.rstrip("/") + webhook_path(webhook_id)
    urls = {}
    for slot, event in EVENT_SLOTS.items():
        if slot in CALL_SLOTS:
            query = _CALL_QUERY
        elif slot in BATTERY_SLOTS:
            query = _BATTERY_QUERY
        else:
            query = _STATE_QUERY
        url = f"{base}?e={event}&{query}"
        if len(url) > MAX_URL_LENGTH:
            raise UrlTooLong(f"{len(url)} characters: {base_url}")
        urls[slot] = url
    return urls


def is_ours(value: str, webhook_id: str) -> bool:
    return webhook_path(webhook_id) in value


@dataclass
class ClaimResult:
    """What a claim did, slot by slot."""

    claimed: list[str] = field(default_factory=list)
    # Slots pointing somewhere else, left alone: slot -> current value.
    foreign: dict[str, str] = field(default_factory=dict)
    # Slots the handset doesn't report (another model or firmware).
    missing: list[str] = field(default_factory=list)


async def async_claim(
    client: GrandstreamClient,
    urls: Mapping[str, str],
    webhook_id: str,
    *,
    take_over: bool = False,
) -> ClaimResult:
    """Point empty (or our own) slots at our webhook.

    With ``take_over``, also replace slots that point somewhere else; only the
    owner's explicit choice in a Repairs flow sets it.
    """
    current = await client.async_get_values(urls)
    result = ClaimResult()
    writes: dict[str, str] = {}
    for slot, url in urls.items():
        if slot not in current:
            result.missing.append(slot)
            continue
        value = current[slot]
        if value and not is_ours(value, webhook_id) and not take_over:
            result.foreign[slot] = value
            continue
        result.claimed.append(slot)
        if value != url:
            writes[slot] = url
    if writes:
        await client.async_set_values(writes)
    return result


async def async_release(client: GrandstreamClient, webhook_id: str) -> list[str]:
    """Clear every slot that points at our webhook. Returns the slots cleared."""
    current = await client.async_get_values(EVENT_SLOTS)
    ours = [slot for slot, value in current.items() if value and is_ours(value, webhook_id)]
    if ours:
        await client.async_set_values(dict.fromkeys(ours, ""))
    return ours
