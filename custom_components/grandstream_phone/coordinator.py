"""Polling coordinator: one per handset."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    ROLE_ADMIN,
    AccountLocked,
    CannotConnect,
    GrandstreamClient,
    GrandstreamError,
    InvalidAuth,
    LoginInfo,
    WriteRejected,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, KEY_DND, POLLED_KEYS
from .push import BATTERY_SLOTS, EVENT_SLOTS

_LOGGER = logging.getLogger(__name__)

type GrandstreamConfigEntry = ConfigEntry[GrandstreamCoordinator]

# Handset event -> the Call event entity's event type.
CALL_EVENT_TYPES = {
    "incoming": "incoming",
    "answered": "answered",
    "outgoing": "outgoing",
    "missed": "missed",
    "terminated": "ended",
}


def signal_call(entry_id: str) -> str:
    """Dispatcher signal for call events of one entry."""
    return f"{DOMAIN}_{entry_id}_call"


def async_get_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """The HTTP session handset clients use.

    Home Assistant's shared session. The client turns certificate checks off
    per request (each handset has a certificate from Grandstream's private CA)
    and sends its own session cookie, so nothing else is needed.
    """
    return async_get_clientsession(hass)


@dataclass(frozen=True)
class GrandstreamData:
    """One poll's worth of handset state."""

    values: dict[str, str] = field(default_factory=dict)
    line_status: list[dict[str, Any]] = field(default_factory=list)
    phone_status: str | None = None
    wifi: dict[str, Any] | None = None
    # None when the session's role can't read it (the `user` account).
    battery: dict[str, Any] | None = None


@dataclass(frozen=True)
class PushEvent:
    """The last event the handset pushed."""

    event: str
    received: datetime
    params: dict[str, str]


@dataclass
class PushStatus:
    """Whether the handset's event URLs point here, and what came in last."""

    # off: not set up (yet), active: our URLs are on the handset,
    # disabled: device disabled, slots released, slots_in_use: some slots point
    # elsewhere, no_url: Home Assistant has no internal URL, error: the handset
    # couldn't be updated.
    state: str = "off"
    claimed: list[str] = field(default_factory=list)
    foreign: list[str] = field(default_factory=list)
    last_event: PushEvent | None = None


@dataclass(frozen=True)
class BatteryEvent:
    """The last battery threshold crossing the handset pushed."""

    low: bool
    level: int | None
    received: datetime


def apply_push(data: GrandstreamData, event: str, params: Mapping[str, str]) -> GrandstreamData:
    """The handset's state right after an event, as best it can be inferred.

    Applied at once so entities don't wait for a fetch: Home Assistant spaces
    requested refreshes 10 s apart, longer than it takes to answer a call. The
    fetch that follows replaces this guess with what the handset reports.
    `busy` is ignored on purpose: it arrives just before `incoming` and would
    flash "in use" before "ringing".
    """
    if event in ("dnd_on", "dnd_off"):
        return replace(data, values={**data.values, KEY_DND: "1" if event == "dnd_on" else "0"})

    number = params.get("r", "")
    lines = [dict(line) for line in data.line_status]
    phone = data.phone_status

    def first(state: str | None = None, *, busy: bool = False) -> dict[str, Any] | None:
        for line in lines:
            current = line.get("state")
            if (busy and current != "idle") or (not busy and current == state):
                return line
        return None

    if event == "incoming":
        line = first("idle")
        if line is not None:
            line.update(state="ringing", remotenumber=number, remotename="")
        if phone == "available":
            phone = "ringing"
    elif event in ("answered", "established", "outgoing"):
        line = first("ringing") or first("idle")
        if line is not None:
            line.update(state="connected", remotenumber=number or line.get("remotenumber", ""))
        phone = "busy"
    elif event in ("terminated", "missed", "rejected"):
        matching = next(
            (ln for ln in lines if ln.get("state") != "idle" and number and ln.get("remotenumber") == number),
            None,
        )
        line = matching or first(busy=True)
        if line is not None:
            line.update(state="idle", remotenumber="", remotename="")
        if all(ln.get("state") == "idle" for ln in lines):
            phone = "available"
    elif event == "idle":
        for line in lines:
            line.update(state="idle", remotenumber="", remotename="")
        phone = "available"
    else:
        return data
    return replace(data, line_status=lines, phone_status=phone)


class GrandstreamCoordinator(DataUpdateCoordinator[GrandstreamData]):
    """Polls one handset and writes settings to it."""

    config_entry: GrandstreamConfigEntry

    def __init__(self, hass: HomeAssistant, entry: GrandstreamConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.data[CONF_HOST]}",
            config_entry=entry,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = GrandstreamClient(
            async_get_session(hass),
            entry.data[CONF_HOST],
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
        )
        self.push = PushStatus()
        self.battery_event: BatteryEvent | None = None
        # (number, name) of the call in progress, for naming its later events.
        self._caller: tuple[str, str] | None = None

    @property
    def login_info(self) -> LoginInfo | None:
        return self.client.login_info

    @property
    def is_admin(self) -> bool:
        info = self.client.login_info
        return info is not None and info.role == ROLE_ADMIN

    async def _async_setup(self) -> None:
        """Log in once before the first poll, so a bad password fails setup."""
        try:
            await self.client.async_login()
        except (InvalidAuth, AccountLocked) as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GrandstreamError as err:
            raise UpdateFailed(f"Cannot reach the handset: {err}") from err

    async def _async_update_data(self) -> GrandstreamData:
        try:
            values = await self.client.async_get_values(POLLED_KEYS)
            line_status = await self.client.async_get_line_status()
            phone_status = await self.client.async_get_phone_status()
            wifi = await self.client.async_get_wifi_status()
            # Admin-only. Asking as `user` costs a fresh login on every poll
            # before the handset's 401 is believed, so don't ask.
            battery = await self.client.async_get_battery_status() if self.is_admin else None
        except (InvalidAuth, AccountLocked) as err:
            # Stop polling: retrying a rejected password walks the handset
            # into its lockout. The user re-enters credentials via reauth.
            raise ConfigEntryAuthFailed(str(err)) from err
        except GrandstreamError as err:
            raise UpdateFailed(f"Handset update failed: {err}") from err
        return GrandstreamData(
            values=values,
            line_status=line_status,
            phone_status=phone_status,
            wifi=wifi,
            battery=battery,
        )

    async def async_set_values(self, values: Mapping[str, str]) -> None:
        """Write settings and publish them without waiting for the next poll."""
        try:
            await self.client.async_set_values(values)
        except WriteRejected as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_rejected",
                translation_placeholders={"keys": ", ".join(err.keys)},
            ) from err
        except (InvalidAuth, AccountLocked) as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except CannotConnect as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        if self.data is not None:
            merged = {**self.data.values, **{str(k): str(v) for k, v in values.items()}}
            self.async_set_updated_data(replace(self.data, values=merged))

    @callback
    def async_handle_push(self, event: str, params: Mapping[str, str]) -> None:
        """An event the handset pushed to our webhook."""
        _LOGGER.debug("Push from %s: %s", self.client.host, event)
        now = dt_util.utcnow()
        self.push.last_event = PushEvent(event, now, dict(params))

        if event in BATTERY_SLOTS.values():
            self.battery_event = BatteryEvent(event == "battery_low", _int(params.get("b")), now)
            self.async_update_listeners()
            return

        if event in CALL_EVENT_TYPES:
            async_dispatcher_send(
                self.hass,
                signal_call(self.config_entry.entry_id),
                CALL_EVENT_TYPES[event],
                self._call_attributes(event, params),
            )
        if self.data is not None:
            updated = apply_push(self.data, event, params)
            if updated is not self.data:
                self.async_set_updated_data(updated)
        if event in EVENT_SLOTS.values():
            # Confirm with what the handset reports, rather than wait for the poll.
            self.hass.async_create_task(self.async_request_refresh())
        self.async_update_listeners()

    def _call_attributes(self, event: str, params: Mapping[str, str]) -> dict[str, str]:
        """Caller details for a call event.

        On 1.0.1.87 the hang-up event reports the number where the name should
        be, so the name comes from the event that started the call.
        """
        number, name = params.get("r", ""), params.get("n", "")
        if event in ("incoming", "outgoing"):
            self._caller = (number, name)
        elif self._caller is not None and self._caller[0] == number and name in ("", number):
            name = self._caller[1]
        if event in ("missed", "terminated"):
            self._caller = None
        return {"number": number, "name": name}

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        await self.client.async_logout()


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
