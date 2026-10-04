"""Exercise the integration's API client against a real handset.

Usage:
  .venv-test/bin/python scripts/client_smoke.py [--admin] [--host HOST]

Reads model info, logs in, reads settings and status, then writes LCD
brightness one step down and restores it. Nothing it changes is left changed.
The handset's address and passwords come from .test_phone in the repo root
(see scripts/test_phone.py); it logs in as `user` unless --admin is given.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import aiohttp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from test_phone import load as load_test_phone  # noqa: E402

from custom_components.grandstream_phone.api import (  # noqa: E402
    GrandstreamClient,
    PermissionDenied,
    async_get_model_info,
)


async def main(host: str, username: str, password: str) -> None:
    async with aiohttp.ClientSession() as session:
        model = await async_get_model_info(session, host)
        print("model:", model["model"])

        client = GrandstreamClient(session, host, username, password)
        info = await client.async_login()
        print(f"login: role={info.role} firmware={info.firmware}")
        try:
            values = await client.async_get_values(["334", "1413", ":dnd", ":unread_vm_1", "sp_vol"])
            print("values:", values)
            print("phone:", await client.async_get_phone_status())
            print("lines:", [line["state"] for line in await client.async_get_line_status()])
            print("wifi signal:", (await client.async_get_wifi_status())["signal"])
            try:
                print("battery:", (await client.async_get_battery_status())["capacity"])
            except PermissionDenied:
                print("battery: not available to this role")

            original = values["334"]
            trial = "50" if original != "50" else "40"
            await client.async_set_values({"334": trial})
            print(f"write: 334 {original} -> {trial} applied")
            await client.async_set_values({"334": original})
            print(f"write: 334 restored to {original}")
        finally:
            await client.async_logout()
            print("logged out")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(usage=__doc__)
    parser.add_argument("--admin", action="store_true", help="log in as admin instead of user")
    parser.add_argument("--host", help="override ADDR from .test_phone")
    opts = parser.parse_args()
    test_phone = load_test_phone()
    asyncio.run(main(opts.host or test_phone.addr, *test_phone.credentials(admin=opts.admin)))
