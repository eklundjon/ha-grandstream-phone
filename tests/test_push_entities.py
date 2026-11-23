"""Tests for what pushed events do to entities.

Event sequences and caller details are as observed on WP826 handsets: 1.0.3.35
and 1.0.1.87 (where the hang-up event reports the caller's number as the name).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.const import CONF_WEBHOOK_ID, STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.grandstream_phone.binary_sensor import BINARY_SENSORS
from custom_components.grandstream_phone.const import DOMAIN, KEY_DND
from custom_components.grandstream_phone.coordinator import GrandstreamData, apply_push
from custom_components.grandstream_phone.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.grandstream_phone.sensor import line_state

from .conftest import UNIQUE_ID
from .fake_phone import FakePhone, load_fixture

MAC = "00:0B:82:12:34:56"
CALLER = {"r": "10", "n": "Kitchen Phone"}


@pytest.fixture(autouse=True)
def internal_url(hass: HomeAssistant) -> None:
    hass.config.internal_url = "http://192.168.1.10:8123"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, platform: str, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{UNIQUE_ID}_{key}")


def _state(hass: HomeAssistant, platform: str, key: str) -> State:
    entity_id = _entity_id(hass, platform, key)
    assert entity_id is not None, f"no {platform} {key}"
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def _push(hass: HomeAssistant, client, entry: MockConfigEntry, event: str, **params: str) -> None:
    resp = await client.get(
        f"/api/webhook/{entry.data[CONF_WEBHOOK_ID]}", params={"e": event, "m": MAC, **params}
    )
    assert resp.status == 200
    await hass.async_block_till_done()


# ---- apply_push: the inferred state ----------------------------------------- #


def _data(*states: str, phone: str = "available") -> GrandstreamData:
    return GrandstreamData(
        values={KEY_DND: "0"},
        line_status=[
            {"line": i + 1, "state": s, "remotenumber": "", "remotename": ""}
            for i, s in enumerate(states)
        ],
        phone_status=phone,
    )


def _states(data: GrandstreamData) -> list[str]:
    return [line["state"] for line in data.line_status]


def test_apply_incoming_answer_hang_up() -> None:
    data = apply_push(_data("idle", "idle", "idle"), "incoming", CALLER)
    assert _states(data) == ["ringing", "idle", "idle"]
    assert data.line_status[0]["remotenumber"] == "10"
    assert data.phone_status == "ringing"

    data = apply_push(data, "answered", CALLER)
    assert _states(data) == ["connected", "idle", "idle"]
    assert data.phone_status == "busy"

    data = apply_push(data, "terminated", CALLER)
    assert _states(data) == ["idle", "idle", "idle"]
    assert data.phone_status == "available"


def test_apply_missed_and_idle() -> None:
    data = apply_push(_data("idle", "idle"), "incoming", CALLER)
    assert _states(apply_push(data, "missed", CALLER)) == ["idle", "idle"]
    data = apply_push(_data("connected", "onhold", phone="busy"), "idle", {})
    assert _states(data) == ["idle", "idle"]
    assert data.phone_status == "available"


def test_apply_outgoing_connects_a_line() -> None:
    data = apply_push(_data("idle", "idle"), "outgoing", {"r": "*86", "n": "*86"})
    assert _states(data) == ["connected", "idle"]
    assert data.phone_status == "busy"


def test_apply_hang_up_leaves_other_calls() -> None:
    data = _data("connected", "connected", phone="busy")
    data.line_status[0]["remotenumber"] = "11"
    data.line_status[1]["remotenumber"] = "10"
    data = apply_push(data, "terminated", CALLER)
    assert _states(data) == ["connected", "idle"]
    assert data.phone_status == "busy"


def test_apply_dnd_and_ignored_events() -> None:
    data = _data("idle")
    assert apply_push(data, "dnd_on", {}).values[KEY_DND] == "1"
    assert apply_push(data, "dnd_off", {}).values[KEY_DND] == "0"
    # `busy` arrives just before `incoming`; applying it would flash "in use".
    assert apply_push(data, "busy", {}) is data


# ---- through the webhook ------------------------------------------------------- #


async def test_call_events_fire_with_caller_details(
    hass: HomeAssistant, hass_client_no_auth, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    client = await hass_client_no_auth()
    assert _state(hass, "event", "call").state == STATE_UNKNOWN

    await _push(hass, client, entry, "incoming", **CALLER)
    state = _state(hass, "event", "call")
    assert state.attributes["event_type"] == "incoming"
    assert state.attributes["number"] == "10"
    assert state.attributes["name"] == "Kitchen Phone"

    await _push(hass, client, entry, "answered", **CALLER)
    assert _state(hass, "event", "call").attributes["event_type"] == "answered"

    # 1.0.1.87 reports the number as the name on hang-up; the entity keeps the name.
    await _push(hass, client, entry, "terminated", r="10", n="10")
    state = _state(hass, "event", "call")
    assert state.attributes["event_type"] == "ended"
    assert state.attributes["name"] == "Kitchen Phone"


async def test_outgoing_call_ends_with_the_far_end(
    hass: HomeAssistant, hass_client_no_auth, phone: FakePhone, entry: MockConfigEntry
) -> None:
    # After an outgoing call, the hang-up reports the handset's own number and
    # name (seen on 1.0.1.71 and 1.0.3.35). The entity reports who was called.
    await _setup(hass, entry)
    client = await hass_client_no_auth()

    await _push(hass, client, entry, "outgoing", r="21", n="21")
    await _push(hass, client, entry, "terminated", r="30", n="Bedroom Phone")
    state = _state(hass, "event", "call")
    assert state.attributes["event_type"] == "ended"
    assert (state.attributes["number"], state.attributes["name"]) == ("21", "21")

    # The next incoming call is reported as sent, hang-up included.
    await _push(hass, client, entry, "incoming", **CALLER)
    await _push(hass, client, entry, "terminated", **CALLER)
    state = _state(hass, "event", "call")
    assert (state.attributes["number"], state.attributes["name"]) == ("10", "Kitchen Phone")


async def test_outgoing_call_cancelled_while_ringing_ends(
    hass: HomeAssistant, hass_client_no_auth, phone: FakePhone, entry: MockConfigEntry
) -> None:
    # Hanging up before the far end answers sends idle alone, no terminated.
    await _setup(hass, entry)
    client = await hass_client_no_auth()
    entity_id = _entity_id(hass, "event", "call")
    fired: list[tuple[str, str, str]] = []

    @callback
    def record(event: Event) -> None:
        a = event.data["new_state"].attributes
        fired.append((a["event_type"], a["number"], a["name"]))

    async_track_state_change_event(hass, entity_id, record)

    await _push(hass, client, entry, "outgoing", r="30", n="30")
    await _push(hass, client, entry, "idle")
    assert fired == [("outgoing", "30", "30"), ("ended", "30", "30")]

    # A call that connected ends once, on terminated; the idle after it adds nothing.
    fired.clear()
    await _push(hass, client, entry, "outgoing", r="31", n="31")
    await _push(hass, client, entry, "established", r="31", n="31")
    await _push(hass, client, entry, "terminated", r="40", n="Office Phone")
    await _push(hass, client, entry, "idle")
    assert fired == [("outgoing", "31", "31"), ("ended", "31", "31")]

    # An incoming call that goes idle without missed or terminated isn't
    # reported as ended, and isn't remembered for the next call either.
    fired.clear()
    await _push(hass, client, entry, "incoming", **CALLER)
    await _push(hass, client, entry, "idle")
    await _push(hass, client, entry, "terminated", r="20", n="20")
    assert fired == [("incoming", "10", "Kitchen Phone"), ("ended", "20", "20")]


async def test_repeated_call_event_is_dropped(
    hass: HomeAssistant, hass_client_no_auth, phone: FakePhone, entry: MockConfigEntry, freezer
) -> None:
    # 1.0.3.35 sends outgoing twice, ~35 ms apart. The repeat would fire the
    # event again and mark a second line connected.
    await _setup(hass, entry)
    client = await hass_client_no_auth()
    coordinator = entry.runtime_data
    fired: list[str] = []

    @callback
    def record(event: Event) -> None:
        fired.append(event.data["new_state"].attributes["event_type"])

    async_track_state_change_event(hass, _entity_id(hass, "event", "call"), record)

    with patch.object(coordinator, "async_request_refresh", AsyncMock()):
        await _push(hass, client, entry, "outgoing", r="40", n="40")
        await _push(hass, client, entry, "outgoing", r="40", n="40")
        assert fired == ["outgoing"]
        assert _states(coordinator.data).count("connected") == 1

        # The same call placed again later is a new call.
        await _push(hass, client, entry, "idle")
        freezer.tick(2)
        await _push(hass, client, entry, "outgoing", r="40", n="40")
    assert fired == ["outgoing", "ended", "outgoing"]


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        (("ringback", "idle"), "dialing"),
        (("dialing",), "dialing"),
        (("calling",), "dialing"),
        (("conference",), "connected"),
        (("onhold", "dialing"), "dialing"),  # e.g. calling someone to transfer to
        (("ringing", "connected"), "ringing"),
        (("idle", "idle"), "idle"),
        (("failed",), None),
    ],
)
def test_line_state_from_raw_states(states: tuple[str, ...], expected: str | None) -> None:
    data = _data(*states)
    assert line_state(data) == expected
    in_use = next(d for d in BINARY_SENSORS if d.key == "in_use")
    assert in_use.is_on_fn(data) == (expected in ("connected", "dialing", "on_hold"))


async def test_non_call_events_do_not_fire_call_events(
    hass: HomeAssistant, hass_client_no_auth, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    client = await hass_client_no_auth()
    for event in ("busy", "idle", "established", "dnd_on"):
        await _push(hass, client, entry, event, **CALLER)
    assert _state(hass, "event", "call").state == STATE_UNKNOWN


async def test_pushed_events_update_entities_without_a_fetch(
    hass: HomeAssistant, hass_client_no_auth, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    client = await hass_client_no_auth()
    coordinator = entry.runtime_data
    with patch.object(coordinator, "async_request_refresh", AsyncMock()):
        await _push(hass, client, entry, "incoming", **CALLER)
        assert _state(hass, "binary_sensor", "ringing").state == STATE_ON
        assert _state(hass, "sensor", "call_state").state == "ringing"

        await _push(hass, client, entry, "answered", **CALLER)
        assert _state(hass, "binary_sensor", "ringing").state == STATE_OFF
        assert _state(hass, "binary_sensor", "in_use").state == STATE_ON
        assert _state(hass, "sensor", "call_state").state == "connected"

        await _push(hass, client, entry, "terminated", **CALLER)
        assert _state(hass, "binary_sensor", "in_use").state == STATE_OFF
        assert _state(hass, "sensor", "call_state").state == "idle"

        await _push(hass, client, entry, "dnd_on")
        assert _state(hass, "switch", "do_not_disturb").state == STATE_ON


# ---- battery low --------------------------------------------------------------- #


async def test_battery_low_follows_crossings(
    hass: HomeAssistant, hass_client_no_auth, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    client = await hass_client_no_auth()
    assert _state(hass, "binary_sensor", "battery_low").state == STATE_UNKNOWN

    await _push(hass, client, entry, "battery_low", b="19")
    state = _state(hass, "binary_sensor", "battery_low")
    assert state.state == STATE_ON
    assert state.attributes["level"] == 19

    await _push(hass, client, entry, "battery_ok", b="61")
    assert _state(hass, "binary_sensor", "battery_low").state == STATE_OFF


async def test_battery_level_falls_back_to_level_variable(
    hass: HomeAssistant, hass_client_no_auth, phone: FakePhone, entry: MockConfigEntry
) -> None:
    # 1.0.1.71 sends $battery empty; $level arrives as e.g. "82%".
    await _setup(hass, entry)
    client = await hass_client_no_auth()

    await _push(hass, client, entry, "battery_low", b="", l="82%")
    assert _state(hass, "binary_sensor", "battery_low").attributes["level"] == 82

    await _push(hass, client, entry, "battery_low", b="19", l="19%")
    assert _state(hass, "binary_sensor", "battery_low").attributes["level"] == 19


async def test_battery_low_restores_after_restart(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    entity_id = f"binary_sensor.{entry.title.lower().replace(' ', '_')}_battery_low"
    er.async_get(hass).async_get_or_create(
        "binary_sensor", DOMAIN, f"{UNIQUE_ID}_battery_low", suggested_object_id=entity_id.split(".")[1]
    )
    mock_restore_cache(hass, [State(entity_id, STATE_ON, {"level": 18, "reported": "2026-10-04T12:00:00+00:00"})])
    await _setup(hass, entry)
    state = hass.states.get(entity_id)
    assert state is not None and state.state == STATE_ON
    assert state.attributes["level"] == 18


# ---- on charger (admin) ---------------------------------------------------------- #


@pytest.mark.parametrize(
    ("status", "expected"), [("Full", STATE_ON), ("Charging", STATE_ON), ("Discharging", STATE_OFF)]
)
async def test_on_charger_for_admin(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry, status: str, expected: str
) -> None:
    phone.role = "admin"
    original = phone._answer
    battery = {**load_fixture("battery_status"), "battery": {**load_fixture("battery_status")["battery"], "status": status}}
    phone._answer = lambda m, p, kw: (  # type: ignore[method-assign]
        (200, battery) if p.endswith("battery_status") and phone._authed(kw) else original(m, p, kw)
    )
    await _setup(hass, entry)
    assert _state(hass, "binary_sensor", "on_charger").state == expected


async def test_no_on_charger_for_user(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    await _setup(hass, entry)
    assert _entity_id(hass, "binary_sensor", "on_charger") is None


# ---- last pushed event, diagnostics ------------------------------------------------ #


async def test_last_pushed_event_sensor(
    hass: HomeAssistant, hass_client_no_auth, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    assert _state(hass, "sensor", "last_pushed_event").state == STATE_UNKNOWN
    client = await hass_client_no_auth()
    await _push(hass, client, entry, "dnd_off")
    state = _state(hass, "sensor", "last_pushed_event")
    assert state.state != STATE_UNKNOWN
    assert state.attributes["event"] == "dnd_off"


async def test_diagnostics_for_a_disabled_entry(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry
) -> None:
    await _setup(hass, entry)
    assert await hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER)
    await hass.async_block_till_done()
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert set(diag) == {"entry"}
    assert diag["entry"]["data"]["password"] == "**REDACTED**"
