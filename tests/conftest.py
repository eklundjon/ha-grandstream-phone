"""Shared fixtures for the Grandstream Phone test suite."""

from __future__ import annotations

import pytest

# PHACC is auto-discovered as a pytest plugin, but declaring it is the
# documented pattern and keeps fixture resolution explicit.
pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to load the custom integration in tests."""
    yield
