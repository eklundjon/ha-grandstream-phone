# Grandstream Phone for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2025.4+-blue.svg?logo=homeassistant)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Home Assistant integration for Grandstream Wi-Fi handsets (the WP8x6 line). It talks to each handset's own web API on your network, so there's no cloud and no PBX involvement. You get the handset's backlight, ring volume and do-not-disturb as controls, and its call state, voicemail count and Wi-Fi signal as sensors.

The original use case was dimming handsets at night, but anything an automation can do with a switch or a sensor works.

This is an unofficial integration, not affiliated with or endorsed by Grandstream.

**Before you start:**

- **Turn on the handset's web interface** if it's off: on the handset, Settings → Advanced → Security.
- **Give Home Assistant the handset's `user` account, not `admin`.** In the handset's web interface (System Settings → Security), turn on user web access and set a user password.
  - On firmware 1.0.3.35 the handset allows one web session per account, so if Home Assistant and you both log in as `admin`, each login kicks the other one out. (1.0.1.87 allows several sessions, but logging out of one ends the others, which has much the same effect.) When Home Assistant loses its session like that, it leaves the handset alone for 5 minutes before logging in again, so you get a window in the web interface; entities keep their last values meanwhile and pushed events still arrive. A change made from Home Assistant logs in straight away.
  - `user` can do everything this integration needs except read the battery level, and it can't change the handset's security settings.
- **Home Assistant 2025.4 or later.**
- A DHCP reservation for each handset is a good idea. If a handset's address changes anyway, Home Assistant usually follows it (see [Setup](#setup)).

## Supported handsets

| Model | Firmware | Status | Tested by |
|---|---|---|---|
| WP826 | 1.0.3.35 | Verified 2026-10-04: every entity, setup, re-authentication | [@eklundjon](https://github.com/eklundjon) |
| WP826 | 1.0.1.87 | Verified 2026-10-04 as `user`: every entity, setup, pushed events | [@eklundjon](https://github.com/eklundjon) |

Other WP8x6 handsets will **probably** work. They run the same web interface and API as the WP826, and the WP826's web interface carries settings for features it doesn't have, which suggests one firmware family underneath. Setup lets you add any model that answers like a WP826, with a warning if it isn't in the table above.

Probably *won't* work: Android-based models (e.g. the WP820) and older desk and DECT phones. As far as I know they have a different web API, but none have been tried.

If you try a model or firmware that isn't listed, please [report how it went](https://github.com/eklundjon/ha-grandstream-phone/issues/new?template=device_support.yml), working or not, with a diagnostics download attached. That report is what moves a model into the table.

## What you get

One device per handset, with these entities:

| Entity | Type | Notes |
|---|---|---|
| LCD brightness | number | 10–100 %, steps of 10 |
| Ring volume | number | 0–10 |
| Backlight timeout | select | Never, 15 s … 30 min |
| Keypad backlight | select | Off / On / Auto (ambient light sensor) |
| Missed call backlight while charging | select | Always on / Off after backlight timeout / Dim, always on |
| Do not disturb | switch | |
| Wi-Fi power save | switch | Disabled by default; see below |
| Call state | sensor | Idle / Ringing / Connected / On hold |
| Call | event | Incoming / Answered / Outgoing / Missed / Ended, with the other party's number and name. Needs [pushed events](#pushed-events) |
| Ringing | binary sensor | |
| In use | binary sensor | On during a call, including on hold |
| Unread voicemail | sensor | Account 1 |
| Account 2 / 3 unread voicemail | sensor | Disabled by default unless the account is set up on the handset |
| Wi-Fi signal | sensor | The handset's 0–4 bars (diagnostic) |
| SIP registered | binary sensor | Account 1 (diagnostic) |
| Account 2 / 3 SIP registered | binary sensor | Disabled by default unless the account is set up (diagnostic) |
| Battery low | binary sensor | From the handset's battery events; works with `user` (diagnostic) |
| Last pushed event | sensor | When the handset last pushed an event (diagnostic) |
| Battery | sensor | `admin` account only (diagnostic) |
| On charger | binary sensor | `admin` account only (diagnostic) |

Some behavior worth knowing:

- **Changes apply immediately and quietly.** On the WP826, a brightness, timeout or DND change takes effect at once without a popup, and doesn't wake a dark screen. That's what makes overnight automations practical.
- **Status is polled every 30 seconds, and the handset pushes changes in between.** A call or a DND change on the handset makes Home Assistant fetch the new state right away (see [Pushed events](#pushed-events)). Without pushed events, a short ring can come and go between polls.
- **Every write is checked.** The handset answers "success" even when it ignores a change (e.g. a setting the account isn't allowed to touch), so the integration reads each change back and reports an error if it didn't stick.
- **A missed call can keep a charging handset lit.** While a missed call is on screen and the handset is charging, the missed call backlight setting takes over from the backlight timeout. Set to "Always on", a handset in its cradle stays lit until someone clears the missed call, overnight included. "Off after backlight timeout" lets it go dark as usual.
- **No call control**, on purpose: no dialing, answering or hanging up.
- **Wi-Fi power save trades reachability for battery.** With it on and the screen dark, the handset can take seconds to answer, or miss a poll; with it off, it answers right away and the battery runs down faster. The switch is disabled by default. Enable it if you want automations to decide, e.g. off while the handset is on its charger. Turning it back on restores the power save mode the handset had.

## Pushed events

The handset can request a URL whenever something happens: a call comes in, is answered or ends, DND changes, the battery runs low. Grandstream calls these Action URLs (in the handset's web interface under Maintenance → Outbound Notification). The integration points them at a Home Assistant webhook, so changes show up immediately instead of at the next poll.

- **The handset has to reach Home Assistant** over plain HTTP on your network, at Home Assistant's local URL (Settings → System → Network, or the address Home Assistant detects if that's unset). If there's nothing usable, a Repairs notice says so and the handset is polled only.
- **It only takes empty URLs, or ones it set itself.** A URL that points somewhere else (another Home Assistant instance with the same handset, or something you set up by hand) is left alone, with a Repairs notice naming the events. The notice offers to take them over.
- **It removes its URLs when you disable the device or the integration entry, or delete the entry**, so another instance can claim the handset. A restart or reload leaves them in place.
- **What arrives:**
  - **Calls** fire the Call event entity, with the other party's number and name as the handset reports them (the name falls back to the number for unknown callers). They also update ringing, in use and call state straight away.
  - **DND changes** made on the handset update the switch.
  - **Battery events** drive the battery low sensor. The handset only sends them when the battery crosses its low threshold (20 % by default) going down or its sufficient threshold (60 %) going up, so the sensor is unknown until the first crossing, keeps its state through restarts, and its `level` attribute is only as fresh as the last crossing. For a live percentage, use the `admin` account's battery sensor.
- Note that the webhook ID in each URL is the only thing protecting it, since the handset can't authenticate. The webhook only accepts requests from your local network, and ignores ones that don't carry this handset's MAC address.

## Install

This isn't in the default catalog, so add it by its repository link. The badge opens it in the Marketplace or in HACS, whichever your Home Assistant has:

[![Open this repository in your Home Assistant instance.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=eklundjon&repository=ha-grandstream-phone&category=integration)

**Marketplace (Home Assistant 2026.11 and later)**

The Marketplace is HACS, built into Home Assistant. Go to Settings → **Marketplace** → **Add from link** (upper right), enter `https://github.com/eklundjon/ha-grandstream-phone`, and select **Add**. If it asks for a type, pick Integration. Then install it from its page. If Home Assistant needs a restart before the integration can load, a repair notice says so.

The first time you add a repository by link, the Marketplace asks you to connect GitHub: it shows a code and a link, and you enter the code on GitHub and approve. The connection only reads public information.

**HACS (Home Assistant before 2026.11)**

In HACS: ⋮ → Custom repositories, add `https://github.com/eklundjon/ha-grandstream-phone` as an Integration, click **Download**, and restart Home Assistant. When you update Home Assistant to 2026.11 or later, it moves to the Marketplace along with everything else HACS installed, and keeps getting updates.

**Manual**

Copy `custom_components/grandstream_phone` into your Home Assistant `config/custom_components/` folder and restart Home Assistant.

However you install it, the Grandstream icon only shows up on the integration page after a restart. Clearing the browser cache isn't enough.

## Setup

Home Assistant may find the handset by itself: look under **Discovered** in Settings → Devices & services, and click **Add**. That skips the first step below. Discovery relies on Home Assistant seeing the handset's DHCP requests, which works on most Home Assistant OS installs but not every setup, so don't worry if nothing shows up. Otherwise, Settings → Devices & services → **Add integration** → **Grandstream Phone**.

1. **Host.** The handset's IP address or hostname. Setup reads the model from the handset before asking for credentials.
2. **Credentials.** Username `user` (or `admin`) and its password.
   - Note that the handset locks the account after 5 failed logins. Setup shows how many attempts are left when the handset reports it.
3. **Untested model.** If the model isn't in the table above, setup warns you and asks to continue.

Each handset is named after its model and its first SIP account's user ID, e.g. "WP826 10" for a handset whose extension is 10. That's only there to keep several handsets apart. Rename the device to whatever you like (e.g. "Kitchen phone") and let Home Assistant update the entity IDs to match.

If the password changes, the integration makes one login attempt, stops polling, and asks you to re-authenticate. It doesn't retry on its own, because each retry would count toward the lockout. Re-authentication also lets you switch between `user` and `admin`.

If the handset moves to a new address, Home Assistant updates the entry when it next sees the handset ask for one over DHCP. If it doesn't, use **Reconfigure** on the integration entry.

The polling interval (30 seconds by default, 10–300) is under **Configure** on the integration entry. With [pushed events](#pushed-events) working, polling mostly catches settings changed on the handset itself, so a longer interval costs little.

## What you can do with it

Some ideas, from the obvious to the slightly less so. The first four come as blueprints: click the badge to import one, then create an automation from it under Settings → Automations & scenes → Blueprints. The blueprints live in [blueprints/](blueprints/automation/grandstream_phone/).

### Quiet nights

Dim the screens at night and put them back in the morning. Optionally shorten the backlight timeout overnight, and set the missed call backlight to "Off after backlight timeout" so a missed call doesn't keep a cradled handset lit until morning. The changes don't wake a dark screen. The blueprint also runs when Home Assistant starts, so a restart at 2 a.m. doesn't leave the handsets in day mode.

[![Import the blueprint.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Feklundjon%2Fha-grandstream-phone%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fgrandstream_phone%2Fnight_mode.yaml)

### Pause the music when the phone rings

Pause media players when a handset rings (or when a call is answered, if you'd rather), and resume whatever was playing when the call ends. Players that weren't playing are left alone. Needs [pushed events](#pushed-events).

[![Import the blueprint.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Feklundjon%2Fha-grandstream-phone%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fgrandstream_phone%2Fpause_media_during_calls.yaml)

### Missed call alerts

Send a notification, with the caller's name and number, when a call to a handset goes unanswered. Use any notify action: the companion app, a chat service, a TTS announcement. Needs pushed events.

[![Import the blueprint.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Feklundjon%2Fha-grandstream-phone%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fgrandstream_phone%2Fmissed_call_notification.yaml)

### Do not disturb that follows the house

Turn handset DND on and off with another entity: a bedtime toggle, a schedule, a calendar of meetings, a "baby's asleep" helper. Can be inverted (DND on while the entity is off). DND changes made on the handset stick until that entity next changes.

[![Import the blueprint.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Feklundjon%2Fha-grandstream-phone%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fgrandstream_phone%2Fdnd_follows_entity.yaml)

### Flash a light while it rings

For a noisy workshop, a room out of earshot, or anyone who doesn't hear the ring. This one is short enough to write as an automation:

```yaml
automation:
  - alias: "Flash the garage light while the kitchen phone rings"
    triggers:
      - trigger: state
        entity_id: binary_sensor.kitchen_phone_ringing
        to: "on"
    actions:
      - repeat:
          while:
            - condition: state
              entity_id: binary_sensor.kitchen_phone_ringing
              state: "on"
          sequence:
            - action: light.turn_on
              target:
                entity_id: light.garage
              data:
                flash: short
            - delay: 2
```

With pushed events, the ringing sensor turns on as the call comes in, rather than at the next poll.

### Voicemail waiting

The unread voicemail sensor is a count, so a numeric state trigger (above 0) can light a lamp a particular color, show a badge on a dashboard, or remind someone at a set time that messages are waiting.

### Keeping an eye on the handsets

Most of the diagnostic entities are there for this:

- **The phone line is down.** SIP registered going off usually means the PBX, the ATA or the internet connection is down, not the handset. A notification on that (with a few minutes' `for:`, to ride out brief re-registrations) catches it before someone tries to make a call:

  ```yaml
  automation:
    - alias: "Phone line down"
      triggers:
        - trigger: state
          entity_id: binary_sensor.kitchen_phone_sip_registered
          to: "off"
          for: "00:05:00"
      actions:
        - action: notify.notify
          data:
            message: "The kitchen phone has lost its phone line."
  ```

- **Battery low.** The battery low sensor works with the `user` account. It's handy for a handset that lives away from its cradle.
- **Wi-Fi signal.** The 0–4 bar history shows a handset with a poor spot, or a room that needs an access point.
- **Unreachable handset.** If Last pushed event stays old while calls are being made, the handset has stopped reaching Home Assistant; the Repairs notices say why when the integration can tell.

### A second account for an intercom

Handsets have more than one SIP account (three on the WP826). If one is set up as a dedicated intercom or door-station line, its SIP registered and unread voicemail sensors turn up next to account 1's. They're disabled by default for accounts that aren't set up; enable them on the device page.

## Troubleshooting

- **Entities unavailable now and then.** With the screen dark, the handset's Wi-Fi power saving can make it slow to answer. The integration retries once before giving up for that poll, so an occasional gap is expected; constant gaps aren't.
- **Kicked out of the handset's web interface.** Home Assistant is logged in with the same account you are, and took the session back (it waits 5 minutes after losing it, or less if you change something from Home Assistant). See [Before you start](#before-you-start).
- **Pushed events don't arrive** (states only change at the 30-second poll). Check Settings → Repairs first. Otherwise the handset probably can't reach Home Assistant: a firewall on the Home Assistant host blocking port 8123, or a local URL the handset can't resolve. The diagnostics download shows the push state and when the last event arrived.
- **An entity is unavailable on an untested model.** The handset doesn't report that setting, or reports it differently. A diagnostics download in a [device support report](https://github.com/eklundjon/ha-grandstream-phone/issues/new?template=device_support.yml) is enough to sort that out.

To report a problem, open the integration's device page, use ⋮ → **Download diagnostics**, and attach the file to a [bug report](https://github.com/eklundjon/ha-grandstream-phone/issues/new?template=bug_report.yml). Diagnostics include the handset's model, firmware and settings, but not its address, credentials, MAC, Wi-Fi name or any caller details.

Debug logs help too:

```yaml
logger:
  logs:
    custom_components.grandstream_phone: debug
```

## Contributing

Device reports are the most useful contribution: they're how a model moves from "probably works" to verified. Code contributions are welcome too.

Tests run against a fake handset that serves responses captured from a real one. Captures from a new model or firmware go in their own folder; see [tests/fixtures/README.md](tests/fixtures/README.md). To run the tests:

```bash
uv venv --python 3.14 .venv-test
uv pip install --python .venv-test/bin/python -r requirements_ha_latest.txt -r requirements_test.txt
.venv-test/bin/python -m pytest
```

`scripts/client_smoke.py` exercises the API client against a real handset, and changes nothing it doesn't put back.

## License

MIT. See [LICENSE](LICENSE). The Grandstream name and logo are trademarks of Grandstream Networks, Inc.
