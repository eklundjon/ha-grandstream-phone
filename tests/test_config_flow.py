"""Tests for the config flow (fake handset modeled on a WP826, fw 1.0.3.35)."""

from __future__ import annotations

import aiohttp
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.grandstream_phone.const import CONF_MODEL, DOMAIN

from .conftest import UNIQUE_ID
from .fake_phone import HOST, PASSWORD, SOURCE_MODEL, USERNAME, FakePhone


async def _start(hass: HomeAssistant, host: str = HOST):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: f"  {host} "}
    )


async def _credentials(hass: HomeAssistant, flow_id: str, password: str = PASSWORD):
    return await hass.config_entries.flow.async_configure(
        flow_id, {CONF_USERNAME: USERNAME, CONF_PASSWORD: password}
    )


# ---- adding a handset ----------------------------------------------------- #


async def test_add_verified_handset(hass: HomeAssistant, phone: FakePhone) -> None:
    result = await _start(hass)
    assert result["step_id"] == "credentials"
    assert result["description_placeholders"]["model"] == SOURCE_MODEL

    result = await _credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Title carries the extension, to tell handsets apart.
    assert result["title"] == f"{SOURCE_MODEL} 10"
    assert result["data"] == {
        CONF_HOST: HOST,
        CONF_USERNAME: USERNAME,
        CONF_PASSWORD: PASSWORD,
        CONF_MODEL: SOURCE_MODEL,
    }
    assert result["result"].unique_id == UNIQUE_ID
    # The flow closes its own session before the new entry logs in.
    paths = [c.path for c in phone.calls]
    first_logout = paths.index("/cgi-bin/dologout")
    logins = [i for i, p in enumerate(paths) if p == "/cgi-bin/dologin"]
    assert logins[0] < first_logout < logins[1]


async def test_unreachable_host(hass: HomeAssistant, phone: FakePhone) -> None:
    phone.transport_errors = [aiohttp.ClientError("a"), aiohttp.ClientError("b")]
    result = await _start(hass)
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_wrong_password_then_right(hass: HomeAssistant, phone: FakePhone) -> None:
    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"], password="nope")
    assert result["errors"] == {"base": "invalid_auth_attempts"}
    assert result["description_placeholders"]["attempts_left"] == "4"

    result = await _credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_locked_account(hass: HomeAssistant, phone: FakePhone) -> None:
    phone.login_result = "login_locked"
    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"])
    assert result["errors"] == {"base": "account_locked"}
    assert result["description_placeholders"]["minutes"] == "5"


async def test_user_web_access_disabled(hass: HomeAssistant, phone: FakePhone) -> None:
    phone._dologin = lambda form: (200, {"response": "error", "body": "user is not allow"})  # type: ignore[method-assign]
    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"])
    assert result["errors"] == {"base": "user_access_disabled"}


async def test_cannot_connect_during_login(hass: HomeAssistant, phone: FakePhone) -> None:
    result = await _start(hass)
    phone.transport_errors = [aiohttp.ClientError("a"), aiohttp.ClientError("b")]
    result = await _credentials(hass, result["flow_id"])
    assert result["errors"] == {"base": "cannot_connect"}


async def test_same_handset_new_address_updates_entry(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    result = await _start(hass, host="192.168.1.77")
    result = await _credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.1.77"


async def test_untested_model_asks_first(hass: HomeAssistant, phone: FakePhone) -> None:
    phone.model = "WP836"
    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"])
    assert result["step_id"] == "unverified_model"
    assert result["description_placeholders"]["verified"] == "WP826"
    assert "device_support" in result["description_placeholders"]["report_url"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "WP836 10"
    assert result["data"][CONF_MODEL] == "WP836"


# ---- reauth ---------------------------------------------------------------- #


async def test_reauth_updates_credentials(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    phone.password = "new password"
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: "new password"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new password"


async def test_reauth_wrong_password_shows_error(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: "nope"}
    )
    assert result["errors"] == {"base": "invalid_auth_attempts"}


async def test_reauth_against_another_handset_aborts(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    phone.mac = "00:0B:82:AA:BB:CC"
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"


# ---- reconfigure ----------------------------------------------------------- #


async def test_reconfigure_new_address(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.88"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "192.168.1.88"


async def test_reconfigure_unreachable(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    phone.transport_errors = [aiohttp.ClientError("a"), aiohttp.ClientError("b")]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.88"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_to_another_handset_aborts(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    phone.mac = "00:0B:82:AA:BB:CC"
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.88"}
    )
    assert result["reason"] == "wrong_device"
    assert entry.data[CONF_HOST] == HOST
