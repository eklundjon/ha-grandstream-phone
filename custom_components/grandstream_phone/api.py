"""HTTPS client for the Grandstream WP8x6 handset web API.

Everything that talks to a handset lives here. The protocol was worked out from
the handset's own web UI (firmware 1.0.3.35) and verified against a WP826:

* Login is challenge-response. ``POST /cgi-bin/access`` with
  ``access=sha256(username)`` returns a nonce; ``POST /cgi-bin/dologin`` with
  the plain username and ``sha256(password + nonce)`` returns a session id
  (``sid``). The handset locks the account after five failed logins, so a
  rejected password is never retried here.
* Form POSTs are refused (bare HTML 403) without a same-origin ``Referer``.
* Only the ``sid`` cookie is needed on later requests. It is sent explicitly,
  so the client works on Home Assistant's shared session.
* Settings are read with ``GET /cgi-bin/config_get?pvalues=...`` and written
  with ``PUT /cgi-bin/config_update``. Keys are P-numbers without the ``P``
  (``"334"``), runtime values with a leading colon (``":dnd"``), or a few text
  names (``"sp_vol"``).
* The handset silently ignores some writes (settings the account's role may not
  change, over-length values) while still answering "success", so every write
  is read back.
* An expired session shows up as HTTP 401 on ``api-*`` endpoints but as a
  ``{"status": "session-expired"}`` body on ``config_get``.
* With the screen dark, Wi-Fi power save can stall the TLS handshake, so
  transport errors are retried once.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Generous: a handset in Wi-Fi power save can take several seconds to answer.
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=20)

ROLE_ADMIN = "admin"
ROLE_USER = "user"

_WRONG_PASSWORD = re.compile(r"^wrong(\d*)$")


class GrandstreamError(Exception):
    """Base class for handset API errors."""


class CannotConnect(GrandstreamError):
    """The handset could not be reached or gave an unusable answer."""


class InvalidAuth(GrandstreamError):
    """The handset rejected the credentials."""

    def __init__(self, reason: str, attempts_left: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.attempts_left = attempts_left


class AccountLocked(GrandstreamError):
    """Too many failed logins; the handset refuses logins for a while."""

    def __init__(self, minutes: int | None = None) -> None:
        super().__init__(f"account locked for {minutes} minutes" if minutes else "account locked")
        self.minutes = minutes


class PermissionDenied(GrandstreamError):
    """A fresh session was still refused: the account's role lacks access."""


class WriteRejected(GrandstreamError):
    """The handset accepted a write but the values did not change."""

    def __init__(self, keys: Iterable[str]) -> None:
        self.keys = sorted(keys)
        super().__init__(f"handset did not apply: {', '.join(self.keys)}")


class _SessionExpired(Exception):
    """Internal: the request needs a fresh login."""


@dataclass(frozen=True)
class LoginInfo:
    """What the handset reports about itself and the session at login."""

    mac: str
    firmware: str
    role: str | None
    default_password: bool


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _role_from(role_hash: str, sid: str) -> str | None:
    """The handset reports the role only as sha256(role + sid)."""
    for role in (ROLE_ADMIN, ROLE_USER):
        if _sha256(role + sid) == role_hash:
            return role
    return None


async def async_get_model_info(
    session: aiohttp.ClientSession,
    host: str,
    timeout: aiohttp.ClientTimeout = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Fetch the handset's model definition. No login needed.

    Returns the parsed ``/json/configs/model.define.js`` document, which carries
    ``model``, ``vendor_name`` and a ``defines`` map of feature flags.
    """
    url = f"https://{host}/json/configs/model.define.js"
    for attempt in range(2):
        try:
            async with session.get(url, ssl=False, timeout=timeout) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            if attempt == 0:
                _LOGGER.debug("Model probe to %s failed, retrying once: %s", host, err)
                continue
            raise CannotConnect(f"model probe failed: {err}") from err
        if not isinstance(data, dict) or "model" not in data:
            raise CannotConnect("unexpected model definition")
        return data
    raise AssertionError("unreachable")


class GrandstreamClient:
    """One authenticated session with one handset."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        timeout: aiohttp.ClientTimeout = DEFAULT_TIMEOUT,
    ) -> None:
        self._session = session
        self._host = host
        self._base = f"https://{host}"
        self._username = username
        self._password = password
        self._timeout = timeout
        self._sid: str | None = None
        self._login_info: LoginInfo | None = None
        self._login_lock = asyncio.Lock()

    @property
    def host(self) -> str:
        return self._host

    @property
    def login_info(self) -> LoginInfo | None:
        """Set after the first successful login."""
        return self._login_info

    # ---- session ---------------------------------------------------------- #

    async def async_login(self) -> LoginInfo:
        """Log in and start a new session.

        Raises ``InvalidAuth`` or ``AccountLocked`` without retrying: each failed
        attempt counts toward the handset's lockout.
        """
        async with self._login_lock:
            return await self._login()

    async def _login(self) -> LoginInfo:
        self._sid = None
        challenge = await self._request(
            "POST", "/cgi-bin/access", form={"access": _sha256(self._username)}
        )
        if not isinstance(challenge, dict):
            raise CannotConnect(f"unexpected login challenge: {challenge!r}")
        nonce = challenge.get("body")
        if challenge.get("response") != "success" or not isinstance(nonce, str):
            raise CannotConnect(f"unexpected login challenge: {challenge!r}")

        result = await self._request(
            "POST",
            "/cgi-bin/dologin",
            form={
                "username": self._username,
                "password": _sha256(self._password + nonce),
            },
            retry=False,
        )
        body = result.get("body") if isinstance(result, dict) else None
        if not isinstance(result, dict) or result.get("response") != "success" or not isinstance(body, dict):
            raise _login_error(result)

        sid = str(body.get("sid") or "")
        if not sid:
            raise CannotConnect("login succeeded without a session id")
        self._sid = sid
        self._login_info = LoginInfo(
            mac=str(body.get("mac") or "").upper(),
            firmware=str(body.get("ver") or ""),
            role=_role_from(str(body.get("role") or ""), sid),
            default_password=bool(body.get("defaultAuth")),
        )
        return self._login_info

    async def async_logout(self) -> None:
        """End the session. Best effort: a dead session expires on its own."""
        if self._sid is None:
            return
        try:
            await self._request("POST", "/cgi-bin/dologout", form={}, retry=False)
        except GrandstreamError as err:
            _LOGGER.debug("Logout from %s failed: %s", self._host, err)
        finally:
            self._sid = None

    # ---- settings --------------------------------------------------------- #

    async def async_get_values(self, keys: Iterable[str]) -> dict[str, str]:
        """Read settings and runtime values. Keys the handset doesn't know are left out.

        The handset answers every key it's asked for: one it doesn't know comes
        back with an empty value and an empty alias. A real setting that happens
        to be empty (an unset event URL, say) still has its alias, and a runtime
        value (``:dnd``) has no alias but isn't empty, so both survive.
        """
        keys = list(keys)
        payload = await self._authed(
            "GET", "/cgi-bin/config_get", params={"pvalues": ",".join(keys)}
        )
        configs = payload.get("configs") if isinstance(payload, dict) else None
        if not isinstance(configs, list):
            raise CannotConnect(f"unexpected config_get answer: {payload!r}")
        return {
            str(item["pvalue"]): str(item.get("value", ""))
            for item in configs
            if isinstance(item, dict)
            and "pvalue" in item
            and (item.get("value") or item.get("alias"))
        }

    async def async_set_values(self, values: Mapping[str, str]) -> None:
        """Write settings, then read them back.

        Raises ``WriteRejected`` when the handset answered "success" but left
        any value unchanged.
        """
        values = {str(k): str(v) for k, v in values.items()}
        payload = await self._authed(
            "PUT",
            "/cgi-bin/config_update",
            json_body={"alias": {}, "pvalue": values},
        )
        if not isinstance(payload, dict) or payload.get("response") != "success":
            raise CannotConnect(f"config_update failed: {payload!r}")
        current = await self.async_get_values(values)
        rejected = [k for k, v in values.items() if current.get(k) != v]
        if rejected:
            raise WriteRejected(rejected)

    # ---- status ----------------------------------------------------------- #

    async def async_get_line_status(self) -> list[dict[str, Any]]:
        """Per-line call state: ``idle``, ``ringing``, ``connected``, ``onhold``."""
        payload = await self._authed(
            "POST", "/cgi-bin/api-get_line_status", form={"line": "-1"}, form_sid=True
        )
        return _success_body(payload, list)

    async def async_get_phone_status(self) -> str:
        """Overall state: ``available``, ``ringing`` or ``busy``."""
        payload = await self._authed(
            "POST", "/cgi-bin/api-get_phone_status", form={}, form_sid=True
        )
        return _success_body(payload, str)

    async def async_get_battery_status(self) -> dict[str, Any]:
        """Battery level, charge status and health. Admin role only."""
        payload = await self._authed("GET", "/cgi-bin/api-get_battery_status")
        if not isinstance(payload, dict) or payload.get("response") != "success":
            raise CannotConnect(f"unexpected battery answer: {payload!r}")
        battery = payload.get("battery")
        if not isinstance(battery, dict):
            raise CannotConnect(f"unexpected battery answer: {payload!r}")
        return battery

    async def async_get_wifi_status(self) -> dict[str, Any]:
        """Wi-Fi connection: signal (0-4), SSID, BSSID, channel."""
        payload = await self._authed("GET", "/cgi-bin/api-wifi_status_get")
        if not isinstance(payload, dict) or payload.get("response") != "success":
            raise CannotConnect(f"unexpected Wi-Fi answer: {payload!r}")
        status = payload.get("status")
        if not isinstance(status, dict):
            raise CannotConnect(f"unexpected Wi-Fi answer: {payload!r}")
        return status

    async def async_get_accounts(self) -> list[dict[str, Any]]:
        """SIP accounts with their registration state."""
        payload = await self._authed("GET", "/cgi-bin/api-get_accounts")
        return _success_body(payload, list)

    # ---- transport -------------------------------------------------------- #

    async def _authed(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
        json_body: Any = None,
        form_sid: bool = False,
    ) -> Any:
        """A request that needs a session. Logs in, or in again, as needed."""
        for attempt in range(2):
            if self._sid is None or attempt == 1:
                await self._relogin()
            sid = self._sid
            assert sid is not None
            if form_sid:
                form = {**(form or {}), "sid": sid}
            try:
                return await self._request(
                    method, path, params=params, form=form, json_body=json_body
                )
            except _SessionExpired:
                if attempt == 1:
                    raise PermissionDenied(f"{path} refused after a fresh login") from None
                _LOGGER.debug("Session with %s expired, logging in again", self._host)
        raise AssertionError("unreachable")

    async def _relogin(self) -> None:
        sid_before = self._sid
        async with self._login_lock:
            # Another task may have logged in while this one waited.
            if self._sid is not None and self._sid != sid_before:
                return
            await self._login()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
        json_body: Any = None,
        retry: bool = True,
    ) -> Any:
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self._base}/",
        }
        if self._sid:
            headers["Cookie"] = f"sid={self._sid}"
        kwargs: dict[str, Any] = {"headers": headers, "ssl": False, "timeout": self._timeout}
        if params is not None:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body
        elif form:
            kwargs["data"] = form
        elif form is not None:
            # An empty body still needs Content-Length: the handset answers 411
            # to a POST without one.
            kwargs["data"] = b""
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        url = f"{self._base}{path}"
        for attempt in range(2 if retry else 1):
            try:
                async with self._session.request(method, url, **kwargs) as resp:
                    if resp.status == 401:
                        raise _SessionExpired
                    if resp.status >= 400:
                        raise CannotConnect(f"{method} {path}: HTTP {resp.status}")
                    payload = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                if retry and attempt == 0:
                    _LOGGER.debug("%s %s to %s failed, retrying once: %s", method, path, self._host, err)
                    continue
                raise CannotConnect(f"{method} {path}: {err}") from err
            if _is_session_expired(payload):
                raise _SessionExpired
            return payload
        raise AssertionError("unreachable")


def _is_session_expired(payload: Any) -> bool:
    if not isinstance(payload, dict) or payload.get("response") != "error":
        return False
    body = payload.get("body")
    return isinstance(body, dict) and body.get("status") == "session-expired"


def _success_body(payload: Any, kind: type) -> Any:
    if not isinstance(payload, dict) or payload.get("response") != "success":
        raise CannotConnect(f"unexpected answer: {payload!r}")
    body = payload.get("body")
    if not isinstance(body, kind):
        raise CannotConnect(f"unexpected answer: {payload!r}")
    return body


def _login_error(result: Any) -> GrandstreamError:
    body = result.get("body") if isinstance(result, dict) else None
    if body == "locked":
        minutes = result.get("lockTime")
        return AccountLocked(int(minutes) if str(minutes).isdigit() else None)
    if isinstance(body, str):
        match = _WRONG_PASSWORD.match(body)
        if match:
            left = match.group(1)
            return InvalidAuth("wrong password", int(left) if left else None)
        if body == "user is not allow":
            return InvalidAuth("web access is disabled for this user")
    return CannotConnect(f"login failed: {result!r}")
