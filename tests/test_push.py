"""Tests for pushed events: the webhook and the handset's event URL slots.

Slot numbers, the URL length limit and the GET-only, unauthenticated requests
are as observed on a WP826, firmware 1.0.3.35 (see fixtures/README.md).
"""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.grandstream_phone.const import DOMAIN
from custom_components.grandstream_phone.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.grandstream_phone.push import (
    EVENT_SLOTS,
    MAX_URL_LENGTH,
    UrlTooLong,
    build_event_urls,
)

from .conftest import UNIQUE_ID
from .fake_phone import EVENT_SLOT_KEYS, FakePhone

INTERNAL_URL = "http://192.168.1.10:8123"
FOREIGN = "http://192.168.1.99:8123/api/webhook/someone-elses?e=x"


@pytest.fixture
def internal_url(hass: HomeAssistant) -> None:
    hass.config.internal_url = INTERNAL_URL


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _ours(phone: FakePhone, entry: MockConfigEntry) -> dict[str, str]:
    path = f"/api/webhook/{entry.data[CONF_WEBHOOK_ID]}"
    return {k: phone.values[k] for k in EVENT_SLOT_KEYS if path in phone.values[k]}


def _writes(phone: FakePhone) -> list[dict[str, str]]:
    return [c.kwargs["json"]["pvalue"] for c in phone.calls_to("/cgi-bin/config_update")]


# ---- URLs --------------------------------------------------------------------- #


def test_urls_fit_and_carry_the_right_variables() -> None:
    urls = build_event_urls(INTERNAL_URL + "/", "a" * 64)
    assert set(urls) == set(EVENT_SLOTS)
    for slot, url in urls.items():
        assert len(url) <= MAX_URL_LENGTH
        parts = urlsplit(url)
        assert parts.path == "/api/webhook/" + "a" * 64
        query = parse_qs(parts.query)
        assert query["e"] == [EVENT_SLOTS[slot]]
        assert query["m"] == ["$mac"]
    assert parse_qs(urlsplit(urls["8310"]).query)["r"] == ["$remote"]
    assert parse_qs(urlsplit(urls["22568"]).query)["b"] == ["$battery"]


def test_url_too_long_is_refused() -> None:
    with pytest.raises(UrlTooLong):
        build_event_urls("http://" + "x" * 200 + ":8123", "a" * 64)


# ---- claiming at setup ------------------------------------------------------- #


async def test_setup_claims_empty_slots(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry, internal_url: None
) -> None:
    await _setup(hass, entry)
    assert entry.data[CONF_WEBHOOK_ID]  # created for entries from 0.1.x
    assert set(_ours(phone, entry)) == set(EVENT_SLOT_KEYS)
    assert all(v.startswith(INTERNAL_URL) for v in _ours(phone, entry).values())
    assert entry.runtime_data.push.state == "active"


async def test_reload_rewrites_nothing(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry, internal_url: None
) -> None:
    await _setup(hass, entry)
    writes = len(_writes(phone))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    # Slots already ours are left as they are: no flash writes on restart.
    assert len(_writes(phone)) == writes
    assert set(_ours(phone, entry)) == set(EVENT_SLOT_KEYS)


async def test_foreign_slots_are_left_alone(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry, internal_url: None
) -> None:
    phone.values["8310"] = FOREIGN
    phone.values["8316"] = "http://my-own-thing.local/dnd"
    await _setup(hass, entry)

    assert phone.values["8310"] == FOREIGN
    assert phone.values["8316"] == "http://my-own-thing.local/dnd"
    assert len(_ours(phone, entry)) == len(EVENT_SLOT_KEYS) - 2
    assert entry.runtime_data.push.state == "slots_in_use"

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"slots_in_use_{entry.entry_id}")
    assert issue is not None and issue.is_fixable
    assert issue.translation_placeholders["events"] == "incoming, dnd_on"
    # Hosts only: the other instance's webhook ID isn't shown.
    assert issue.translation_placeholders["hosts"] == "192.168.1.99:8123, my-own-thing.local"


async def test_take_over_repair(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry, internal_url: None
) -> None:
    phone.values["8310"] = FOREIGN
    await _setup(hass, entry)
    assert await async_setup_component(hass, "repairs", {})

    from custom_components.grandstream_phone.repairs import async_create_fix_flow

    issue_id = f"slots_in_use_{entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    flow.hass = hass
    flow.issue_id = issue_id
    result = await flow.async_step_init()
    assert result["step_id"] == "confirm"
    result = await flow.async_step_confirm({})
    assert result["type"] == "create_entry"

    assert set(_ours(phone, entry)) == set(EVENT_SLOT_KEYS)
    assert entry.runtime_data.push.state == "active"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_no_internal_url(hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry) -> None:
    # Without an internal URL, Home Assistant falls back to its detected local
    # address; this is the case where it has nothing at all.
    with patch(
        "custom_components.grandstream_phone.event_urls.get_url",
        side_effect=NoURLAvailableError,
    ):
        await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.push.state == "no_url"
    assert _ours(phone, entry) == {}
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"no_url_{entry.entry_id}") is not None


async def test_unreachable_during_claim_does_not_fail_setup(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry, internal_url: None
) -> None:
    original = phone._answer

    def flaky(method, path, kwargs):
        if path == "/cgi-bin/config_get" and "8310" in kwargs["params"]["pvalues"]:
            raise aiohttp.ClientError("gone")
        return original(method, path, kwargs)

    phone._answer = flaky  # type: ignore[method-assign]
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.push.state == "error"


# ---- releasing ---------------------------------------------------------------- #


async def test_disabling_the_entry_releases_only_ours(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry, internal_url: None
) -> None:
    phone.values["8310"] = FOREIGN
    await _setup(hass, entry)
    assert _ours(phone, entry)

    assert await hass.config_entries.async_set_disabled_by(
        entry.entry_id, ConfigEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert _ours(phone, entry) == {}
    assert phone.values["8310"] == FOREIGN
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"slots_in_use_{entry.entry_id}") is None


async def test_removing_the_entry_releases(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry, internal_url: None
) -> None:
    await _setup(hass, entry)
    assert _ours(phone, entry)
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _ours(phone, entry) == {}


async def test_plain_unload_keeps_slots(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry, internal_url: None
) -> None:
    await _setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert set(_ours(phone, entry)) == set(EVENT_SLOT_KEYS)


async def test_device_disable_releases_and_enable_claims(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry, internal_url: None
) -> None:
    await _setup(hass, entry)
    registry = dr.async_get(hass)
    device = dr.async_entries_for_config_entry(registry, entry.entry_id)[0]

    registry.async_update_device(device.id, disabled_by=dr.DeviceEntryDisabler.USER)
    await hass.async_block_till_done()
    assert _ours(phone, entry) == {}

    registry.async_update_device(device.id, disabled_by=None)
    await hass.async_block_till_done()
    assert set(_ours(phone, entry)) == set(EVENT_SLOT_KEYS)


async def test_setup_with_device_disabled_releases(
    hass: HomeAssistant, phone: FakePhone, entry: MockConfigEntry, internal_url: None
) -> None:
    await _setup(hass, entry)
    registry = dr.async_get(hass)
    device = dr.async_entries_for_config_entry(registry, entry.entry_id)[0]
    assert await hass.config_entries.async_unload(entry.entry_id)
    registry.async_update_device(device.id, disabled_by=dr.DeviceEntryDisabler.USER)
    assert set(_ours(phone, entry)) == set(EVENT_SLOT_KEYS)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _ours(phone, entry) == {}
    assert entry.runtime_data.push.state == "disabled"


# ---- the webhook ------------------------------------------------------------ #


async def test_webhook_records_event_and_refreshes(
    hass: HomeAssistant, hass_client_no_auth, phone: FakePhone, entry: MockConfigEntry, internal_url: None
) -> None:
    await _setup(hass, entry)
    client = await hass_client_no_auth()
    polls = len(phone.calls_to("/cgi-bin/api-get_line_status"))

    resp = await client.get(
        f"/api/webhook/{entry.data[CONF_WEBHOOK_ID]}",
        params={"e": "incoming", "m": "00:0B:82:12:34:56", "r": "+15555550100", "n": "Someone"},
    )
    assert resp.status == 200
    await hass.async_block_till_done()

    last = entry.runtime_data.push.last_event
    assert last is not None and last.event == "incoming"
    assert last.params["r"] == "+15555550100"
    # An immediate refresh, not a wait for the next poll.
    assert len(phone.calls_to("/cgi-bin/api-get_line_status")) == polls + 1


@pytest.mark.parametrize(
    "params",
    [
        {"e": "incoming", "m": "00:0B:82:AA:BB:CC"},  # another handset's MAC
        {"e": "incoming"},  # no MAC
        {"e": "something_else", "m": "00:0B:82:12:34:56"},  # not one of ours
    ],
)
async def test_webhook_ignores_strangers(
    hass: HomeAssistant, hass_client_no_auth, phone: FakePhone, entry: MockConfigEntry, internal_url: None, params
) -> None:
    await _setup(hass, entry)
    client = await hass_client_no_auth()
    resp = await client.get(f"/api/webhook/{entry.data[CONF_WEBHOOK_ID]}", params=params)
    assert resp.status == 200
    assert entry.runtime_data.push.last_event is None


async def test_webhook_is_get_only(
    hass: HomeAssistant, hass_client_no_auth, phone: FakePhone, entry: MockConfigEntry, internal_url: None
) -> None:
    await _setup(hass, entry)
    client = await hass_client_no_auth()
    resp = await client.post(
        f"/api/webhook/{entry.data[CONF_WEBHOOK_ID]}?e=incoming&m=00:0B:82:12:34:56"
    )
    assert entry.runtime_data.push.last_event is None
    assert resp.status in (200, 405)


async def test_diagnostics_redact_push_details(
    hass: HomeAssistant, hass_client_no_auth, phone: FakePhone, entry: MockConfigEntry, internal_url: None
) -> None:
    await _setup(hass, entry)
    client = await hass_client_no_auth()
    await client.get(
        f"/api/webhook/{entry.data[CONF_WEBHOOK_ID]}",
        params={"e": "incoming", "m": "00:0B:82:12:34:56", "r": "+15555550100", "n": "Someone"},
    )
    await hass.async_block_till_done()
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    for secret in (entry.data[CONF_WEBHOOK_ID], "+15555550100", "Someone", "00:0B:82:12:34:56", UNIQUE_ID):
        assert secret not in text, secret
    assert diag["push"]["state"] == "active"
    assert diag["push"]["last_event"]["event"] == "incoming"
