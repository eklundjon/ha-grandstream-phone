"""Tests for the entity platforms.

The fake handset serves states captured from a WP826, firmware 1.0.3.35,
including live ringing / connected / on-hold calls (fixtures/README.md).
"""

from __future__ import annotations

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.grandstream_phone.const import (
    DOMAIN,
    KEY_BACKLIGHT_TIMEOUT,
    KEY_DND,
    KEY_KEYPAD_BACKLIGHT,
    KEY_LCD_BRIGHTNESS,
    KEY_RING_VOLUME,
    KEY_UNREAD_VOICEMAIL,
)

from .conftest import UNIQUE_ID
from .fake_phone import FakePhone, load_fixture


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, platform: str, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{UNIQUE_ID}_{key}")


def _state(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = _entity_id(hass, platform, key)
    assert entity_id is not None, f"no {platform} {key}"
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


# ---- what gets created ------------------------------------------------------ #


async def test_entities_as_user(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    assert _state(hass, "number", "lcd_brightness") == "60.0"
    assert _state(hass, "number", "ring_volume") == "8.0"
    assert _state(hass, "select", "backlight_timeout") == "never"
    assert _state(hass, "select", "keypad_backlight") == "auto"
    assert _state(hass, "switch", "do_not_disturb") == STATE_OFF
    assert _state(hass, "sensor", "call_state") == "idle"
    assert _state(hass, "sensor", "unread_voicemail") == "0"
    assert _state(hass, "sensor", "wifi_signal") == "4"
    assert _state(hass, "binary_sensor", "ringing") == STATE_OFF
    assert _state(hass, "binary_sensor", "in_use") == STATE_OFF
    assert _state(hass, "binary_sensor", "sip_registered") == STATE_ON
    # Battery is admin-only, so a `user` session doesn't get the entity.
    assert _entity_id(hass, "sensor", "battery") is None


async def test_battery_as_admin(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    phone.role = "admin"
    await _setup(hass, entry)
    expected = load_fixture("battery_status")["battery"]["capacity"]
    assert _state(hass, "sensor", "battery") == str(expected)


async def test_entity_names_follow_the_device(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    assert _entity_id(hass, "number", "lcd_brightness") == "number.wp826_10_lcd_brightness"
    assert _entity_id(hass, "switch", "do_not_disturb") == "switch.wp826_10_do_not_disturb"


# ---- calls ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("line", "phone_status", "call_state", "ringing", "in_use"),
    [
        ("line_status_ringing", "phone_status_ringing", "ringing", STATE_ON, STATE_OFF),
        ("line_status_connected", "phone_status_busy", "connected", STATE_OFF, STATE_ON),
        ("line_status_onhold", "phone_status_busy", "on_hold", STATE_OFF, STATE_ON),
    ],
)
async def test_call_states(
    hass: HomeAssistant,
    phone: FakePhone,
    entry: MockConfigEntry,
    line: str,
    phone_status: str,
    call_state: str,
    ringing: str,
    in_use: str,
) -> None:
    await _setup(hass, entry)
    phone.line_fixture = line
    phone.phone_fixture = phone_status
    await _refresh(hass, entry)
    assert _state(hass, "sensor", "call_state") == call_state
    assert _state(hass, "binary_sensor", "ringing") == ringing
    assert _state(hass, "binary_sensor", "in_use") == in_use


async def test_unknown_line_state_is_unknown(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    original = phone._answer
    phone._answer = lambda m, p, kw: (  # type: ignore[method-assign]
        (200, {"response": "success", "body": [{"line": 1, "state": "dialing"}]})
        if p.endswith("line_status") and phone._authed(kw)
        else original(m, p, kw)
    )
    await _refresh(hass, entry)
    assert _state(hass, "sensor", "call_state") == STATE_UNKNOWN


async def test_voicemail_count(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    phone.values[KEY_UNREAD_VOICEMAIL] = "2"
    await _refresh(hass, entry)
    assert _state(hass, "sensor", "unread_voicemail") == "2"


# ---- writing settings -------------------------------------------------------- #


async def test_set_brightness(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": _entity_id(hass, "number", "lcd_brightness"), "value": 10},
        blocking=True,
    )
    assert phone.values[KEY_LCD_BRIGHTNESS] == "10"
    assert _state(hass, "number", "lcd_brightness") == "10.0"


async def test_set_ring_volume(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": _entity_id(hass, "number", "ring_volume"), "value": 3},
        blocking=True,
    )
    assert phone.values[KEY_RING_VOLUME] == "3"


async def test_select_backlight_timeout(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": _entity_id(hass, "select", "backlight_timeout"), "option": "15_s"},
        blocking=True,
    )
    assert phone.values[KEY_BACKLIGHT_TIMEOUT] == "15"
    assert _state(hass, "select", "backlight_timeout") == "15_s"


async def test_select_keypad_backlight(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": _entity_id(hass, "select", "keypad_backlight"), "option": "off"},
        blocking=True,
    )
    assert phone.values[KEY_KEYPAD_BACKLIGHT] == "0"


async def test_dnd_switch(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    entity_id = _entity_id(hass, "switch", "do_not_disturb")
    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    assert phone.values[KEY_DND] == "1"
    assert _state(hass, "switch", "do_not_disturb") == STATE_ON
    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    assert phone.values[KEY_DND] == "0"


async def test_rejected_write_is_reported(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    phone.ignored_writes = {KEY_DND}
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "switch", "turn_on",
            {"entity_id": _entity_id(hass, "switch", "do_not_disturb")},
            blocking=True,
        )
    # The translated message reaches the user, placeholders filled in. (HA
    # drops the final period when it formats the message.)
    assert str(err.value) == (
        "The handset did not apply the change (:dnd). "
        "The account may not be allowed to change that setting"
    )
    assert _state(hass, "switch", "do_not_disturb") == STATE_OFF


# ---- values the handset doesn't report ------------------------------------- #


async def test_unrecognized_values_read_as_unknown(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    # A model or firmware with other choices than the WP826's.
    phone.values[KEY_BACKLIGHT_TIMEOUT] = "45"
    phone.values[KEY_DND] = "2"
    phone.values[KEY_LCD_BRIGHTNESS] = "bright"
    await _setup(hass, entry)
    assert _state(hass, "select", "backlight_timeout") == STATE_UNKNOWN
    assert _state(hass, "switch", "do_not_disturb") == STATE_UNKNOWN
    assert _state(hass, "number", "lcd_brightness") == STATE_UNKNOWN


async def test_missing_settings_are_unavailable(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    for key in (KEY_RING_VOLUME, KEY_DND, KEY_UNREAD_VOICEMAIL, "AccountRegistered1"):
        del phone.values[key]
    await _refresh(hass, entry)
    assert _state(hass, "number", "ring_volume") == STATE_UNAVAILABLE
    assert _state(hass, "switch", "do_not_disturb") == STATE_UNAVAILABLE
    assert _state(hass, "sensor", "unread_voicemail") == STATE_UNAVAILABLE
    assert _state(hass, "binary_sensor", "sip_registered") == STATE_UNAVAILABLE
    # Others carry on.
    assert _state(hass, "number", "lcd_brightness") == "60.0"


async def test_unreachable_handset_makes_everything_unavailable(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    import aiohttp

    await _setup(hass, entry)
    phone.transport_errors = [aiohttp.ClientError("a"), aiohttp.ClientError("b")]
    await _refresh(hass, entry)
    assert _state(hass, "number", "lcd_brightness") == STATE_UNAVAILABLE
    assert _state(hass, "sensor", "call_state") == STATE_UNAVAILABLE
