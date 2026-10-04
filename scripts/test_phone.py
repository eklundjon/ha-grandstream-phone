"""The test handset the scripts talk to, from `.test_phone` in the repo root.

    ADDR=192.168.1.50
    USER_PASSWORD=...
    ADMIN_PASSWORD=...

ADDR is the handset's hostname or IP. The passwords are for its `user` and
`admin` web accounts; the usernames are fixed by the firmware. ADMIN_PASSWORD
is only needed with --admin. Keep the file out of git (.gitignore lists it).

Scripts default to `user`. Note that the handset allows one web session per
account, so logging in as `admin` ends any admin session in a browser.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(ROOT, ".test_phone")


@dataclass(frozen=True)
class TestPhone:
    addr: str
    user_password: str
    admin_password: str | None

    def credentials(self, admin: bool = False) -> tuple[str, str]:
        """(username, password) for the requested account."""
        if not admin:
            return "user", self.user_password
        if not self.admin_password:
            raise SystemExit("ADMIN_PASSWORD isn't set in .test_phone")
        return "admin", self.admin_password


def load(path: str | None = None) -> TestPhone:
    path = path or DEFAULT_PATH
    try:
        with open(path) as f:
            values = dict(
                line.strip().split("=", 1)
                for line in f
                if "=" in line and not line.lstrip().startswith("#")
            )
    except FileNotFoundError:
        raise SystemExit(f"{path} not found; see scripts/test_phone.py for its format") from None
    missing = [k for k in ("ADDR", "USER_PASSWORD") if not values.get(k)]
    if missing:
        raise SystemExit(f"{path} is missing {', '.join(missing)}")
    return TestPhone(values["ADDR"], values["USER_PASSWORD"], values.get("ADMIN_PASSWORD") or None)
