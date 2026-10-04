"""A scripted stand-in for a handset, behind a minimal aiohttp-like session.

Its responses come from fixtures/ (see fixtures/README.md), captured from a
Grandstream WP826 on firmware 1.0.3.35. Behavior verified only on that
model and firmware is noted where the fake reproduces it.

It answers the requests GrandstreamClient makes with the captured fixtures and
reproduces the handset behaviors the client has to cope with: the Referer check
on form POSTs, session expiry (401 on api-* endpoints, an error body on
config_get), role-restricted endpoints, and writes the handset ignores.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    """A sanitized handset response captured from a WP826 (fw 1.0.3.35)."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


# The hardware the fixtures were captured from.
SOURCE_MODEL = "WP826"
SOURCE_FIRMWARE = "1.0.3.35"

HOST = "192.168.1.50"
USERNAME = "user"
PASSWORD = "correct horse"
NONCE = load_fixture("access")["body"]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class Call:
    method: str
    path: str
    kwargs: dict[str, Any]

    @property
    def form(self) -> Any:
        return self.kwargs.get("data")

    @property
    def headers(self) -> dict[str, str]:
        return self.kwargs.get("headers", {})


class _Resp:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> _Resp:
        # Yield like real I/O, so concurrent requests actually interleave.
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(None, (), status=self.status)  # type: ignore[arg-type]

    async def json(self, content_type: str | None = None) -> Any:
        if isinstance(self._payload, str):
            return json.loads(self._payload)
        return self._payload


@dataclass
class FakePhone:
    """Handset state plus knobs for the failure modes under test."""

    role: str = "user"
    password: str = PASSWORD
    values: dict[str, str] = field(
        default_factory=lambda: {
            c["pvalue"]: c["value"] for c in load_fixture("config_get")["configs"]
        }
    )
    # Keys whose writes the handset acknowledges but ignores.
    ignored_writes: set[str] = field(default_factory=set)
    # Paths only an admin session may use (401 otherwise).
    admin_only: set[str] = field(default_factory=lambda: {"/cgi-bin/api-get_battery_status"})
    # Exceptions to raise, in order, before answering any request.
    transport_errors: list[Exception] = field(default_factory=list)
    login_result: str = "success"  # or a fixture name like "login_wrong"
    # Override what the handset reports, e.g. an untested model or another handset.
    model: str = SOURCE_MODEL
    mac: str | None = None
    calls: list[Call] = field(default_factory=list)
    sid: str | None = None
    logins: int = 0

    def expire_session(self) -> None:
        self.sid = None

    # ---- aiohttp.ClientSession surface ---------------------------------- #

    def request(self, method: str, url: str, **kwargs: Any) -> _Resp:
        parts = urlsplit(url)
        path = parts.path
        self.calls.append(Call(method, path, kwargs))
        if self.transport_errors:
            raise self.transport_errors.pop(0)
        # Form POSTs need a same-origin Referer (bare HTML 403 otherwise).
        referer = kwargs.get("headers", {}).get("Referer", "")
        if method == "POST" and referer != f"https://{parts.netloc}/":
            return _Resp(403, "<html><body><h1>Forbidden</h1></body></html>")
        status, payload = self._answer(method, path, kwargs)
        return _Resp(status, payload)

    def get(self, url: str, **kwargs: Any) -> _Resp:
        return self.request("GET", url, **kwargs)

    # ---- handset behavior ------------------------------------------------ #

    def calls_to(self, path: str) -> list[Call]:
        return [c for c in self.calls if c.path == path]

    def _authed(self, kwargs: dict[str, Any]) -> bool:
        cookie = kwargs.get("headers", {}).get("Cookie", "")
        return self.sid is not None and cookie == f"sid={self.sid}"

    def _answer(self, method: str, path: str, kwargs: dict[str, Any]) -> tuple[int, Any]:
        if path == "/json/configs/model.define.js":
            return 200, json.dumps({**load_fixture("model_define"), "model": self.model})

        if path == "/cgi-bin/access":
            assert kwargs["data"] == {"access": sha256(USERNAME)}
            return 200, load_fixture("access")
        if path == "/cgi-bin/dologin":
            return self._dologin(kwargs["data"])
        if path == "/cgi-bin/dologout":
            self.sid = None
            return 200, {"response": "success", "body": "logout"}

        if path == "/cgi-bin/config_get":
            if not self._authed(kwargs):
                return 200, load_fixture("config_get_session_expired")
            keys = kwargs["params"]["pvalues"].split(",")
            return 200, {
                "configs": [
                    {"alias": "", "pvalue": k, "value": self.values[k]}
                    for k in keys
                    if k in self.values
                ]
            }
        if path == "/cgi-bin/config_update":
            if not self._authed(kwargs):
                return 401, "<html><body><h1>Unauthorized</h1></body></html>"
            for k, v in kwargs["json"]["pvalue"].items():
                if k not in self.ignored_writes:
                    self.values[k] = v
            return 200, load_fixture("config_update_ok")

        fixtures = {
            "/cgi-bin/api-get_line_status": "line_status_idle",
            "/cgi-bin/api-get_phone_status": "phone_status_available",
            "/cgi-bin/api-get_battery_status": "battery_status",
            "/cgi-bin/api-wifi_status_get": "wifi_status",
            "/cgi-bin/api-get_accounts": "accounts",
        }
        if path in fixtures:
            if not self._authed(kwargs) or (path in self.admin_only and self.role != "admin"):
                return 401, "<html><body><h1>Unauthorized</h1></body></html>"
            return 200, load_fixture(fixtures[path])
        return 404, "<html><body<h1>Not Found</h1></body></html>"

    def _dologin(self, form: dict[str, str]) -> tuple[int, Any]:
        if self.login_result != "success":
            return 200, load_fixture(self.login_result)
        if form["username"] != USERNAME or form["password"] != sha256(self.password + NONCE):
            return 200, load_fixture("login_wrong")
        self.logins += 1
        self.sid = f"{self.logins:064x}"
        body = dict(load_fixture(f"login_{self.role}")["body"])
        body["sid"] = self.sid
        body["role"] = sha256(self.role + self.sid)
        if self.mac:
            body["mac"] = self.mac
        return 200, {"response": "success", "body": body}
