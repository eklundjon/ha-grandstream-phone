# WP826, firmware 1.0.1.87

Captured from a second WP826 before its firmware was upgraded, to compare against the 1.0.3.35 set in the parent folder.

| | |
|---|---|
| Model | Grandstream WP826 |
| Firmware | prog 1.0.1.87 (boot 1.0.1.10, core 1.0.1.52, locale 1.0.1.74) |
| Captured | 2026-10-04, over HTTPS |
| Accounts | `login_admin.json` as admin, `login_user.json` as user; `battery_status.json` as admin; everything else as user or admin (same answer) |
| SIP server | UniFi Talk |

Only captured responses are here. Sanitized the same way as the parent folder (see ../README.md), including this handset's own MAC and address.

Differences from 1.0.3.35 seen while capturing:

- `model_define.json`: the real file has only `model`, `defines` and `pvalue_json_checksum` (no vendor fields or `oem_id`).
- `line_status_idle.json`: three lines instead of four.
- A second login on the same account leaves the first session alive. On 1.0.3.35 it ends it (one session per account).
- Settings 1.0.3.35 added aren't reported (they come back empty, like any unknown key), e.g. the star-key silent mode (P22662). Every key the integration polls is present.

Same as 1.0.3.35: the challenge-response login and the Referer check, unknown keys answered with an empty value and alias, battery status admin-only, admin-only writes silently ignored for `user`, event URL slots writable by `user`, and an event URL limit of exactly 256 characters.
