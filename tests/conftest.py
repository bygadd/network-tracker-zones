"""Real Home Assistant fixtures for the custom integration."""
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def hass_storage():
    """Use real Store IO in the isolated configuration directory."""
    return {}


@pytest.fixture(autouse=True)
def enable_integration(enable_custom_integrations, hass, tmp_path):
    """Load this repository's custom component."""
    hass.config.config_dir = str(tmp_path)


@pytest.fixture
def source(hass):
    entry = MockConfigEntry(domain="unifi", title="Example network", data={"site": "site_alpha"})
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def zones(hass):
    for name in ("alpha", "beta"):
        hass.states.async_set(f"zone.{name}", "0", {"friendly_name": name.title(), "passive": False, "latitude": 10, "longitude": 20, "radius": 100})


@pytest.fixture
def registry(hass):
    from homeassistant.helpers import entity_registry as er
    return er.async_get(hass)


@pytest.fixture
def tracker(registry, source):
    def make(mac="02:00:00:00:00:01", **kwargs):
        return registry.async_get_or_create(
            "device_tracker", "unifi", f"site_alpha-{mac}",
            config_entry=source, capabilities={"tracking_type": "connection"}, **kwargs,
        )
    return make
