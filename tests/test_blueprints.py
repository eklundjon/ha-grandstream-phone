"""The automation blueprints in blueprints/: each validates, and the ones with logic behave."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from homeassistant.components.automation.config import (
    AUTOMATION_BLUEPRINT_SCHEMA,
    async_validate_config_item,
)
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml_dict
from pytest_homeassistant_custom_component.common import async_mock_service

BLUEPRINTS = Path(__file__).parent.parent / "blueprints" / "automation" / "grandstream_phone"

CALL = "event.kitchen_call"
PLAYER = "media_player.living_room"
# Scene restore checks what a player supports before resuming it.
PLAYER_ATTRS = {"supported_features": int(MediaPlayerEntityFeature.PAUSE | MediaPlayerEntityFeature.PLAY)}

# Inputs for each blueprint, as a user would fill them in.
INPUTS: dict[str, dict[str, Any]] = {
    "night_mode": {
        "brightness_entities": ["number.kitchen_lcd_brightness"],
        "timeout_entities": ["select.kitchen_backlight_timeout"],
        "missed_call_entities": ["select.kitchen_missed_call_backlight_while_charging"],
    },
    "pause_media_during_calls": {
        "call_events": [CALL],
        "media_players": [PLAYER, "media_player.kitchen"],
    },
    "missed_call_notification": {
        "call_events": [CALL],
        "notify": [
            {"action": "test.notify", "data": {"message": "Missed call from {{ call.caller }}"}}
        ],
    },
    "dnd_follows_entity": {
        "control": "input_boolean.sleep_mode",
        "dnd_switches": ["switch.kitchen_do_not_disturb"],
    },
}


def _automation(name: str, **overrides: Any) -> dict[str, Any]:
    path = BLUEPRINTS / f"{name}.yaml"
    blueprint = Blueprint(
        load_yaml_dict(path), expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA
    )
    inputs = BlueprintInputs(
        blueprint,
        {"use_blueprint": {"path": path.name, "input": {**INPUTS[name], **overrides}}},
    )
    inputs.validate()
    return {"id": name, "alias": name, **inputs.async_substitute()}


def test_every_blueprint_has_inputs() -> None:
    assert sorted(p.stem for p in BLUEPRINTS.glob("*.yaml")) == sorted(INPUTS)


@pytest.mark.parametrize("name", sorted(INPUTS))
async def test_blueprint_validates(hass: HomeAssistant, name: str) -> None:
    # Raises on anything Home Assistant would reject when saving the automation.
    assert await async_validate_config_item(hass, name, _automation(name)) is not None


async def _start(hass: HomeAssistant, name: str, **overrides: Any) -> None:
    assert await async_setup_component(
        hass, "automation", {"automation": [_automation(name, **overrides)]}
    )
    await hass.async_block_till_done()


def _call(hass: HomeAssistant, event_type: str, n: int, **attributes: str) -> None:
    # Each event entity update is a new timestamp state.
    hass.states.async_set(
        CALL, f"2026-11-06T20:00:{n:02d}+00:00", {"event_type": event_type, **attributes}
    )


async def test_pause_media_resumes_after_call(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "scene", {})
    hass.states.async_set(CALL, "2026-11-06T19:00:00+00:00", {"event_type": "ended"})
    hass.states.async_set(PLAYER, "playing", PLAYER_ATTRS)
    hass.states.async_set("media_player.kitchen", "idle", PLAYER_ATTRS)
    pause = async_mock_service(hass, "media_player", "media_pause")
    play = async_mock_service(hass, "media_player", "media_play")
    await _start(hass, "pause_media_during_calls")

    _call(hass, "incoming", 1)
    await hass.async_block_till_done()
    # Only what was playing.
    assert [c.data["entity_id"] for c in pause] == [[PLAYER]]
    hass.states.async_set(PLAYER, "paused", PLAYER_ATTRS)

    _call(hass, "answered", 2)
    await hass.async_block_till_done()
    assert len(pause) == 1

    _call(hass, "ended", 3)
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in play] == [PLAYER]
    # The snapshot is gone, so a later call without music resumes nothing.
    assert hass.states.get("scene.pause_media_during_calls_media") is None


async def test_pause_media_on_answer_ignores_unanswered_ring(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "scene", {})
    hass.states.async_set(CALL, "2026-11-06T19:00:00+00:00", {"event_type": "ended"})
    hass.states.async_set(PLAYER, "playing", PLAYER_ATTRS)
    hass.states.async_set("media_player.kitchen", "idle", PLAYER_ATTRS)
    pause = async_mock_service(hass, "media_player", "media_pause")
    play = async_mock_service(hass, "media_player", "media_play")
    await _start(hass, "pause_media_during_calls", pause_on="answered")

    _call(hass, "incoming", 1)
    _call(hass, "missed", 2)
    await hass.async_block_till_done()
    assert pause == []
    assert play == []


async def test_missed_call_notification(hass: HomeAssistant) -> None:
    hass.states.async_set(CALL, "2026-11-06T19:00:00+00:00", {"event_type": "ended"})
    notify = async_mock_service(hass, "test", "notify")
    await _start(hass, "missed_call_notification")

    _call(hass, "answered", 1, number="+15555550100", name="Kitchen Phone")
    _call(hass, "missed", 2, number="+15555550100", name="Kitchen Phone")
    _call(hass, "missed", 3, number="+15555550100", name="")
    await hass.async_block_till_done()
    assert [c.data["message"] for c in notify] == [
        "Missed call from Kitchen Phone",
        # No name: falls back to the number.
        "Missed call from +15555550100",
    ]


async def test_dnd_follows_entity_inverted(hass: HomeAssistant) -> None:
    hass.states.async_set("input_boolean.sleep_mode", "on")
    turn_on = async_mock_service(hass, "switch", "turn_on")
    turn_off = async_mock_service(hass, "switch", "turn_off")
    await _start(hass, "dnd_follows_entity", invert=True)

    hass.states.async_set("input_boolean.sleep_mode", "off")
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in turn_on] == [["switch.kitchen_do_not_disturb"]]
    hass.states.async_set("input_boolean.sleep_mode", "on")
    await hass.async_block_till_done()
    assert len(turn_off) == 1
