# Test fixtures

Every JSON file here is a handset response captured from real hardware,
unless listed under "Synthesized" below.

| | |
|---|---|
| Model | Grandstream WP826 |
| Firmware | prog 1.0.3.35 (boot 1.0.3.4, core 1.0.3.9, base 1.0.3.35) |
| Captured | 2026-10-04, over HTTPS |
| Accounts | `login_admin.json` as admin, `login_user.json` as user; everything else as admin |
| SIP server | UniFi Talk |

The call-state files (`line_status_ringing`, `_connected`, `_onhold`) come
from live calls placed during the same session.

## Sanitized

Personal and network details were replaced before saving:

- Phone numbers → `+15555550100`
- Handset MAC → `00:0B:82:12:34:56` (Grandstream's OUI, made-up device part)
- SSID → `example-ssid`, BSSID → `00:11:22:33:44:55`
- LAN addresses → `192.168.1.x`
- Session ID → a fixed placeholder; `role` recomputed to match it
  (`sha256(role + sid)`), so role detection still works against it

## Synthesized

These follow response shapes observed on the same handset but were written
by hand, because capturing them would mean failing a login or waiting for a
session to expire:

- `access.json` (the nonce value only; the shape is real)
- `login_wrong.json`, `login_locked.json`
- `config_get_session_expired.json`, `config_update_ok.json`
- `phone_status_ringing.json`, `phone_status_busy.json` (values observed
  live; the files were written from the observed values)

Fixtures from a different model or firmware go in a subfolder named for it
(for example `wp816_1.0.5.x/`), so the source of each stays clear.
