# WP826, firmware 1.0.3.40

Captured from a WP826 right after upgrading it from 1.0.3.35, to check whether anything the integration relies on changed against the 1.0.3.35 set in the parent folder. Nothing did.

| | |
|---|---|
| Model | Grandstream WP826 |
| Firmware | prog 1.0.3.40 |
| Captured | 2026-11-23, over HTTPS |
| Accounts | `login_admin.json` as admin, `login_user.json` as user; everything else as admin |
| SIP server | UniFi Talk (accounts 1 and 2 registered) |

Only captured responses are here. Sanitized the same way as the parent folder (see ../README.md), plus the Home Assistant webhook ID in the event URLs in `config_get.json`, which is replaced with a placeholder.

Compared with 1.0.3.35:

- Every response has the same shape. `config_get.json` asks for more keys than the parent's (the event URL slots, `AccountRegistered1`, `:unread_vm_1`), so it's longer, but each entry looks the same.
- **A dead session gets `session-expired`**, as on 1.0.3.35 and unlike 1.0.1.71. `config_get_kicked.json` is that answer, captured right after a second admin login ended the session. It matches the parent folder's hand-written `config_get_session_expired.json` exactly. `api-get_phone_status` on the dead session answers with the body `unauthorized` (`phone_status_unauthorized.json`).
- `:unread_vm_1` is empty, as on a handset that has never had voicemail.

Same as 1.0.3.35: one web session per account, four lines in line status, a battery reply with `health` (admin only), and event URL slots writable by `user` with a limit of exactly 256 characters.

Seen while testing, not visible in these files: the handset posts the outgoing-call event URL twice, a few tens of ms apart, on every outgoing call (1.0.3.35 does the same). When an outgoing call is hung up before it's answered, it sends busy-to-idle with no terminated event. When an incoming call goes unanswered, busy-to-idle arrives just before missed.
