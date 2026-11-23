# WP826, firmware 1.0.1.71

Captured from a third WP826 before its firmware was upgraded, to compare against the 1.0.3.35 set in the parent folder and the 1.0.1.87 set next to this one.

| | |
|---|---|
| Model | Grandstream WP826 |
| Firmware | prog 1.0.1.71 |
| Captured | 2026-11-22, over HTTPS |
| Accounts | `login_admin.json` as admin, `login_user.json` as user; everything else as admin |
| SIP server | UniFi Talk (accounts 1 and 2 registered) |

Only captured responses are here. Sanitized the same way as the parent folder (see ../README.md), including this handset's own MAC and address.

Differences from 1.0.3.35 seen while capturing:

- **A dead session gets an ordinary `config_get` answer.** Every key comes back with its alias and an empty value, not the `session-expired` body 1.0.3.35 sends. `config_get_kicked.json` is that answer, captured right after a second admin login ended the session; a made-up `sid` gets the same. Everything else still says the session is gone: `api-get_line_status` answers 401, `config_update` answers `session-expired`, and `api-get_phone_status` answers with the body `unauthorized` (`phone_status_unauthorized.json`).
- **`$battery` is empty in event URLs.** `$level` is filled in (e.g. `82%`). Not visible in these files; seen by pointing the battery-low URL at a listener.
- `model_define.json`: the real file has only `model`, `defines` and `pvalue_json_checksum`, as on 1.0.1.87.
- `line_status_idle.json`: three lines instead of four, as on 1.0.1.87.
- `api-get_phone_status` answers `unavailable` while no account is registered (not captured), `available` once one is.
- Settings that later firmware added come back empty, like any unknown key. Every key the integration polls is present.

Same as 1.0.3.35: one web session per account (a second login ends the first), the challenge-response login and the Referer check, unknown keys answered with an empty value and alias, battery status admin-only, event URL slots writable by `user`, and an event URL limit of exactly 256 characters.
