"""Exercise the integration's API client against a real handset.

Usage:
  .venv-test/bin/python scripts/client_smoke.py HOST [CREDFILE]

Reads model info, logs in, reads settings and status, then writes LCD
brightness one step down and restores it. CREDFILE holds USERNAME= and
PASSWORD= lines (default: .credential-user in the repo root). Nothing it
changes is left changed.
"""

from __future__ import annotations

import asyncio
import os
import sys

import aiohttp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from custom_components.grandstream_phone.api import (  # noqa: E402
    GrandstreamClient,
    PermissionDenied,
    async_get_model_info,
)


def load_credentials(path: str) -> tuple[str, str]:
    creds = dict(line.strip().split("=", 1) for line in open(path) if "=" in line)
    return creds["USERNAME"], creds["PASSWORD"]


async def main(host: str, cred_path: str) -> None:
    async with aiohttp.ClientSession() as session:
        model = await async_get_model_info(session, host)
        print("model:", model["model"])

        client = GrandstreamClient(session, host, *load_credentials(cred_path))
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
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, ".credential-user")))
