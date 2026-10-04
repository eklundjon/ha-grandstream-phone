"""Polling coordinator: one per handset."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

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
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, POLLED_KEYS

_LOGGER = logging.getLogger(__name__)

type GrandstreamConfigEntry = ConfigEntry[GrandstreamCoordinator]


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

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        await self.client.async_logout()
