"""Tests for the handset API client.

The handset behaviors exercised here were observed on a Grandstream WP826,
firmware 1.0.3.35 (see fixtures/README.md). Other models and firmware may
differ; add their fixtures in a subfolder rather than changing these.
"""

from __future__ import annotations

import asyncio

import aiohttp
import pytest

from custom_components.grandstream_phone.api import (
    ROLE_ADMIN,
    ROLE_USER,
    AccountLocked,
    CannotConnect,
    GrandstreamClient,
    InvalidAuth,
    PermissionDenied,
    WriteRejected,
    async_get_model_info,
)

from .fake_phone import (
    HOST,
    NONCE,
    PASSWORD,
    SOURCE_FIRMWARE,
    SOURCE_MODEL,
    USERNAME,
    FakePhone,
    load_fixture,
    sha256,
)


def _client(phone: FakePhone, password: str = PASSWORD) -> GrandstreamClient:
    return GrandstreamClient(phone, HOST, USERNAME, password)  # type: ignore[arg-type]


# ---- model probe ---------------------------------------------------------- #


async def test_model_info_needs_no_login() -> None:
    phone = FakePhone()
    info = await async_get_model_info(phone, HOST)  # type: ignore[arg-type]
    assert info["model"] == SOURCE_MODEL
    assert phone.logins == 0


async def test_model_info_unreachable() -> None:
    phone = FakePhone(transport_errors=[aiohttp.ClientError("x"), TimeoutError()])
    with pytest.raises(CannotConnect):
        await async_get_model_info(phone, HOST)  # type: ignore[arg-type]


# ---- login ---------------------------------------------------------------- #


@pytest.mark.parametrize("role", [ROLE_USER, ROLE_ADMIN])
async def test_login_reports_handset_and_role(role: str) -> None:
    phone = FakePhone(role=role)
    info = await _client(phone).async_login()
    assert info.role == role
    assert info.firmware == SOURCE_FIRMWARE
    assert info.mac == "00:0B:82:12:34:56"
    assert info.default_password is False


async def test_login_is_challenge_response_with_referer() -> None:
    phone = FakePhone()
    await _client(phone).async_login()
    login = phone.calls_to("/cgi-bin/dologin")[0]
    # The password never travels in clear: sha256(password + nonce).
    assert login.form == {"username": USERNAME, "password": sha256(PASSWORD + NONCE)}
    assert login.headers["Referer"] == f"https://{HOST}/"


async def test_wrong_password_is_not_retried() -> None:
    phone = FakePhone()
    with pytest.raises(InvalidAuth) as err:
        await _client(phone, password="nope").async_login()
    assert err.value.attempts_left == 4
    # Each failed login counts toward the handset's lockout.
    assert len(phone.calls_to("/cgi-bin/dologin")) == 1


async def test_locked_account() -> None:
    phone = FakePhone(login_result="login_locked")
    with pytest.raises(AccountLocked) as err:
        await _client(phone).async_login()
    assert err.value.minutes == 5


async def test_user_web_access_disabled() -> None:
    phone = FakePhone()
    phone._dologin = lambda form: (200, {"response": "error", "body": "user is not allow"})  # type: ignore[method-assign]
    with pytest.raises(InvalidAuth, match="disabled"):
        await _client(phone).async_login()


async def test_dologin_transport_error_is_not_retried() -> None:
    phone = FakePhone()
    client = _client(phone)
    # The access call succeeds, then dologin's connection fails.
    real_request = phone.request

    def flaky(method, url, **kwargs):
        if url.endswith("/cgi-bin/dologin"):
            phone.calls.append(None)  # type: ignore[arg-type]
            raise aiohttp.ClientError("reset")
        return real_request(method, url, **kwargs)

    phone.request = flaky  # type: ignore[method-assign]
    with pytest.raises(CannotConnect):
        await client.async_login()
    assert phone.calls.count(None) == 1


async def test_forbidden_without_referer_is_cannot_connect() -> None:
    # A Referer naming another host is refused like a missing one.
    client = GrandstreamClient(FakePhone(), "10.0.0.9", USERNAME, PASSWORD)  # type: ignore[arg-type]
    with pytest.raises(CannotConnect, match="403"):
        await client.async_login()


# ---- reading and writing settings ----------------------------------------- #


async def test_get_values_logs_in_and_sends_sid_cookie() -> None:
    phone = FakePhone()
    values = await _client(phone).async_get_values(["334", ":dnd", "sp_vol", "99999"])
    assert values == {"334": "60", ":dnd": "0", "sp_vol": "8"}
    get = phone.calls_to("/cgi-bin/config_get")[0]
    assert get.kwargs["params"] == {"pvalues": "334,:dnd,sp_vol,99999"}
    assert get.headers["Cookie"] == f"sid={phone.sid}"


async def test_expired_session_on_config_get_logs_in_again() -> None:
    phone = FakePhone()
    client = _client(phone)
    await client.async_get_values(["334"])
    phone.expire_session()
    assert await client.async_get_values(["334"]) == {"334": "60"}
    assert phone.logins == 2


async def test_set_values_writes_and_reads_back() -> None:
    phone = FakePhone()
    await _client(phone).async_set_values({"334": 10, ":dnd": "1"})
    put = phone.calls_to("/cgi-bin/config_update")[0]
    assert put.kwargs["json"] == {"alias": {}, "pvalue": {"334": "10", ":dnd": "1"}}
    assert phone.values["334"] == "10"
    # Read back after the write.
    assert phone.calls[-1].path == "/cgi-bin/config_get"


async def test_ignored_write_raises_write_rejected() -> None:
    # Observed for an admin-only setting written as `user`, and for an event
    # URL over the length limit: "success", but the value doesn't change.
    phone = FakePhone(ignored_writes={"32051"})
    phone.values["32051"] = "1"
    with pytest.raises(WriteRejected) as err:
        await _client(phone).async_set_values({"334": "20", "32051": "0"})
    assert err.value.keys == ["32051"]
    assert phone.values["334"] == "20"


async def test_expired_session_on_write_logs_in_again() -> None:
    phone = FakePhone()
    client = _client(phone)
    await client.async_login()
    phone.expire_session()
    await client.async_set_values({"334": "30"})
    assert phone.values["334"] == "30"
    assert phone.logins == 2


# ---- status endpoints ----------------------------------------------------- #


async def test_line_and_phone_status_send_sid_in_form() -> None:
    phone = FakePhone()
    client = _client(phone)
    lines = await client.async_get_line_status()
    assert [line["state"] for line in lines] == ["idle"] * 4
    assert await client.async_get_phone_status() == "available"
    for path in ("/cgi-bin/api-get_line_status", "/cgi-bin/api-get_phone_status"):
        assert phone.calls_to(path)[0].form["sid"] == phone.sid


async def test_wifi_and_accounts() -> None:
    client = _client(FakePhone())
    assert (await client.async_get_wifi_status())["signal"] == 4
    accounts = await client.async_get_accounts()
    assert accounts[0]["reg"] == 1


async def test_battery_as_admin() -> None:
    battery = await _client(FakePhone(role=ROLE_ADMIN)).async_get_battery_status()
    assert battery == load_fixture("battery_status")["battery"]


async def test_battery_as_user_is_permission_denied() -> None:
    # On the WP826 the battery endpoint answers 401 to a `user` session even
    # right after logging in.
    phone = FakePhone(role=ROLE_USER)
    with pytest.raises(PermissionDenied):
        await _client(phone).async_get_battery_status()
    assert phone.logins == 2


async def test_unexpected_status_body_is_cannot_connect() -> None:
    phone = FakePhone()
    client = _client(phone)
    await client.async_login()
    original = phone._answer
    phone._answer = lambda m, p, kw: (200, {"response": "error"}) if p.endswith("phone_status") else original(m, p, kw)  # type: ignore[method-assign]
    with pytest.raises(CannotConnect):
        await client.async_get_phone_status()


# ---- transport ------------------------------------------------------------ #


async def test_one_transport_error_is_retried() -> None:
    # A handset in Wi-Fi power save can time out the TLS handshake once.
    phone = FakePhone()
    client = _client(phone)
    await client.async_login()
    phone.transport_errors = [TimeoutError()]
    assert await client.async_get_values(["334"]) == {"334": "60"}


async def test_two_transport_errors_are_cannot_connect() -> None:
    phone = FakePhone()
    client = _client(phone)
    await client.async_login()
    phone.transport_errors = [aiohttp.ClientError("a"), aiohttp.ClientError("b")]
    with pytest.raises(CannotConnect):
        await client.async_get_values(["334"])


async def test_concurrent_requests_share_one_login() -> None:
    phone = FakePhone()
    client = _client(phone)
    await asyncio.gather(
        client.async_get_values(["334"]),
        client.async_get_line_status(),
        client.async_get_phone_status(),
    )
    assert phone.logins == 1


# ---- logout --------------------------------------------------------------- #


async def test_logout_sends_a_body() -> None:
    # The handset answers 411 to a POST without Content-Length.
    phone = FakePhone()
    client = _client(phone)
    await client.async_login()
    await client.async_logout()
    logout = phone.calls_to("/cgi-bin/dologout")[0]
    assert logout.form == b""
    assert phone.sid is None


async def test_logout_without_session_is_a_no_op() -> None:
    phone = FakePhone()
    await _client(phone).async_logout()
    assert phone.calls == []


async def test_logout_failure_is_swallowed() -> None:
    phone = FakePhone()
    client = _client(phone)
    await client.async_login()
    phone.transport_errors = [aiohttp.ClientError("gone")]
    await client.async_logout()
    assert client._sid is None
