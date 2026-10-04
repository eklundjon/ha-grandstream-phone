"""Shared fixtures for the Grandstream Phone test suite."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.grandstream_phone.const import CONF_MODEL, DOMAIN

from .fake_phone import HOST, PASSWORD, SOURCE_MODEL, USERNAME, FakePhone

# PHACC is auto-discovered as a pytest plugin, but declaring it is the
# documented pattern and keeps fixture resolution explicit.
pytest_plugins = "pytest_homeassistant_custom_component"

# The fake handset's MAC (see fixtures/README.md), as format_mac() writes it.
UNIQUE_ID = "00:0b:82:12:34:56"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to load the custom integration in tests."""
    yield


@pytest.fixture
def phone() -> Generator[FakePhone]:
    """A fake handset standing in for the HTTP session the integration uses."""
    fake = FakePhone()
    with (
        patch("custom_components.grandstream_phone.coordinator.async_get_session", return_value=fake),
        patch("custom_components.grandstream_phone.config_flow.async_get_session", return_value=fake),
    ):
        yield fake


@pytest.fixture
def entry() -> MockConfigEntry:
    """A configured handset (not yet added to hass)."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"{SOURCE_MODEL} 10",
        unique_id=UNIQUE_ID,
        data={
            CONF_HOST: HOST,
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
            CONF_MODEL: SOURCE_MODEL,
        },
    )
