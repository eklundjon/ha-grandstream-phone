"""Tests for setup, the coordinator and diagnostics.

The fake handset is modeled on a WP826, firmware 1.0.3.35 (fixtures/README.md).
"""

from __future__ import annotations

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.grandstream_phone.const import (
    DOMAIN,
    KEY_DND,
    KEY_LCD_BRIGHTNESS,
)
from custom_components.grandstream_phone.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import UNIQUE_ID
from .fake_phone import SOURCE_FIRMWARE, SOURCE_MODEL, FakePhone, load_fixture


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _idle(coordinator) -> None:
    """Pretend the last successful request was long ago (outside the takeover window)."""
    coordinator.client._last_ok -= 3600


def _reauth_flows(hass: HomeAssistant) -> list:
    return [
        f for f in hass.config_entries.flow.async_progress()
        if f["context"]["source"] == SOURCE_REAUTH
    ]


# ---- setup ----------------------------------------------------------------- #


async def test_setup_as_user(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    data = entry.runtime_data.data
    assert data.values[KEY_LCD_BRIGHTNESS] == "60"
    assert data.values[KEY_DND] == "0"
    assert data.phone_status == "available"
    assert [line["state"] for line in data.line_status] == ["idle"] * 4
    assert data.wifi["signal"] == 4
    # Battery is admin-only; a user session doesn't ask (and doesn't pay a
    # re-login for the 401).
    assert data.battery is None
    assert not phone.calls_to("/cgi-bin/api-get_battery_status")
    assert phone.logins == 1


async def test_setup_as_admin_reads_battery(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    phone.role = "admin"
    await _setup(hass, entry)
    assert entry.runtime_data.data.battery == load_fixture("battery_status")["battery"]


async def test_device_registered(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert (DOMAIN, UNIQUE_ID) in device.identifiers
    assert device.manufacturer == "Grandstream"
    assert device.model == SOURCE_MODEL
    assert device.sw_version == SOURCE_FIRMWARE
    assert (dr.CONNECTION_NETWORK_MAC, UNIQUE_ID) in device.connections
    assert device.configuration_url == "https://192.168.1.50"


async def test_bad_password_at_setup_starts_reauth(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    phone.password = "changed on the handset"
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(_reauth_flows(hass)) == 1
    assert len(phone.calls_to("/cgi-bin/dologin")) == 1


async def test_locked_account_at_setup_starts_reauth(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    phone.login_result = "login_locked"
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(_reauth_flows(hass)) == 1


async def test_unreachable_at_setup_retries(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    phone.transport_errors = [aiohttp.ClientError("a"), aiohttp.ClientError("b")]
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_logs_out(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert phone.calls_to("/cgi-bin/dologout")
    assert phone.sid is None


# ---- polling --------------------------------------------------------------- #


async def test_poll_failure_keeps_entry_loaded(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    phone.transport_errors = [aiohttp.ClientError("a"), aiohttp.ClientError("b")]
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    assert entry.state is ConfigEntryState.LOADED

    await coordinator.async_refresh()
    assert coordinator.last_update_success is True


async def test_password_changed_while_running_starts_reauth(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    _idle(coordinator)
    phone.expire_session()
    phone.password = "changed on the handset"
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success is False
    assert len(_reauth_flows(hass)) == 1
    # One login attempt, not a retry loop toward the lockout.
    assert len(phone.calls_to("/cgi-bin/dologin")) == 2


async def test_session_expiry_is_invisible(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    # A session that ends after sitting idle (not one taken by another login).
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    _idle(coordinator)
    phone.expire_session()
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert phone.logins == 2


# ---- writing --------------------------------------------------------------- #


async def test_set_values_publishes_immediately(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    await coordinator.async_set_values({KEY_LCD_BRIGHTNESS: "10"})
    assert phone.values[KEY_LCD_BRIGHTNESS] == "10"
    assert coordinator.data.values[KEY_LCD_BRIGHTNESS] == "10"


async def test_ignored_write_raises(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    phone.ignored_writes = {KEY_LCD_BRIGHTNESS}
    with pytest.raises(HomeAssistantError) as err:
        await entry.runtime_data.async_set_values({KEY_LCD_BRIGHTNESS: "10"})
    assert err.value.translation_key == "write_rejected"
    assert entry.runtime_data.data.values[KEY_LCD_BRIGHTNESS] == "60"


async def test_write_when_unreachable_raises(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    phone.transport_errors = [aiohttp.ClientError("a"), aiohttp.ClientError("b")]
    with pytest.raises(HomeAssistantError) as err:
        await entry.runtime_data.async_set_values({KEY_LCD_BRIGHTNESS: "10"})
    assert err.value.translation_key == "cannot_connect"


async def test_write_with_rejected_password_starts_reauth(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    phone.expire_session()
    phone.password = "changed on the handset"
    with pytest.raises(HomeAssistantError) as err:
        await entry.runtime_data.async_set_values({KEY_LCD_BRIGHTNESS: "10"})
    assert err.value.translation_key == "auth_failed"
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1


# ---- diagnostics ----------------------------------------------------------- #


async def test_diagnostics_redacts_household_details(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    phone.role = "admin"
    await _setup(hass, entry)
    # A call in progress, so caller details are present to redact.
    original = phone._answer
    phone._answer = lambda m, p, kw: (  # type: ignore[method-assign]
        (200, load_fixture("line_status_connected"))
        if p.endswith("line_status") and phone._authed(kw)
        else original(m, p, kw)
    )
    await entry.runtime_data.async_refresh()

    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    for secret in ("192.168.1.50", "correct horse", "+15555550100", "example-ssid", "00:0B:82", "00:0b:82"):
        assert secret not in text, secret
    # What a device-support report needs survives.
    assert diag["login"]["firmware"] == SOURCE_FIRMWARE
    assert diag["login"]["role"] == "admin"
    assert diag["entry"]["data"]["model"] == SOURCE_MODEL
    assert diag["data"]["values"][KEY_LCD_BRIGHTNESS] == "60"
    assert diag["data"]["line_status"][0]["state"] == "connected"
    assert diag["data"]["battery"]["capacity"] == load_fixture("battery_status")["battery"]["capacity"]


# ---- another login takes the session ----------------------------------------- #


async def test_takeover_backs_off_and_keeps_entities(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry, freezer
) -> None:
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    logins = phone.logins
    # Someone logs in as the same account moments after our last poll.
    phone.expire_session()
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.backoff_until is not None
    assert coordinator.data.values[KEY_LCD_BRIGHTNESS] == "60"
    assert phone.logins == logins  # didn't take the session back

    # During the back-off, polls leave the handset alone.
    calls = len(phone.calls)
    freezer.tick(240)
    await coordinator.async_refresh()
    assert len(phone.calls) == calls

    # After it, log in again and carry on.
    freezer.tick(120)
    await coordinator.async_refresh()
    assert coordinator.backoff_until is None
    assert phone.logins == logins + 1
    assert coordinator.last_update_success is True


async def test_write_during_backoff_logs_in(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    phone.expire_session()
    await coordinator.async_refresh()
    assert coordinator.backoff_until is not None

    await coordinator.async_set_values({KEY_LCD_BRIGHTNESS: "20"})
    assert phone.values[KEY_LCD_BRIGHTNESS] == "20"
    assert coordinator.backoff_until is None


async def test_takeover_before_any_data_is_a_failed_update(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    coordinator.data = None
    phone.expire_session()
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
