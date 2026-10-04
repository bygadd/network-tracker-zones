"""Exercise Home Assistant's real config and options flow managers."""
import pytest
from unittest.mock import patch
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.data_entry_flow import InvalidData
from homeassistant.loader import async_get_integration_descriptions
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.network_tracker_zones import Rule
from custom_components.network_tracker_zones.const import DOMAIN


async def start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def test_listed_in_add_integration_not_helpers(hass):
    descriptions = await async_get_integration_descriptions(hass)
    assert DOMAIN in descriptions["custom"]["integration"]
    assert DOMAIN not in descriptions["custom"]["helper"]


async def test_create_and_duplicate(hass, source, zones):
    form = await start(hass)
    assert form["type"] == FlowResultType.FORM
    data = {"source_entry_id": source.entry_id, "zone": "zone.alpha"}
    result = await hass.config_entries.flow.async_configure(form["flow_id"], data)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == data
    assert result["result"].unique_id == source.entry_id
    second = await start(hass)
    result = await hass.config_entries.flow.async_configure(second["flow_id"], data)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    await hass.async_block_till_done()


@pytest.mark.parametrize("source_kind,zone,error", [
    ("missing", "zone.alpha", {"source_entry_id": "invalid_source"}),
    ("unsupported", "zone.alpha", {"source_entry_id": "invalid_source"}),
    ("valid", "zone.missing", {"zone": "invalid_zone"}),
    ("valid", "zone.passive", {"zone": "invalid_zone"}),
    ("valid", "sensor.alpha", {"zone": "invalid_zone"}),
])
async def test_invalid_selections(hass, source, zones, source_kind, zone, error):
    hass.states.async_set("zone.passive", "0", {"passive": True})
    hass.states.async_set("sensor.alpha", "0")
    unsupported = MockConfigEntry(domain="mikrotik", data={})
    unsupported.add_to_hass(hass)
    source_id = unsupported.entry_id if source_kind == "unsupported" else source.entry_id
    form = await start(hass)
    if source_kind == "missing":
        with patch.object(source, "async_remove", return_value=None):
            await hass.config_entries.async_remove(source.entry_id)
    if source_kind == "unsupported" or zone == "sensor.alpha":
        with pytest.raises(InvalidData):
            await hass.config_entries.flow.async_configure(form["flow_id"], {"source_entry_id": source_id, "zone": zone})
        return
    result = await hass.config_entries.flow.async_configure(form["flow_id"], {"source_entry_id": source_id, "zone": zone})
    assert result["errors"] == error


async def test_options_merge_and_validation(hass, source, zones):
    entry = MockConfigEntry(domain=DOMAIN, data={"source_entry_id": source.entry_id, "zone": "zone.alpha"}, options={"future_option": True})
    entry.add_to_hass(hass)
    menu = await hass.config_entries.options.async_init(entry.entry_id)
    assert menu["type"] == FlowResultType.MENU
    form = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "change_zone"}
    )
    result = await hass.config_entries.options.async_configure(form["flow_id"], {"zone": "zone.missing"})
    assert result["errors"] == {"zone": "invalid_zone"}
    result = await hass.config_entries.options.async_configure(form["flow_id"], {"zone": "zone.beta"})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options == {"zone": "zone.beta", "future_option": True}


async def test_options_audit_and_confirm_repair(hass, source, zones, registry, tracker):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"source_entry_id": source.entry_id, "zone": "zone.alpha"},
    )
    entry.add_to_hass(hass)
    rule = Rule(hass, entry, source)
    entry.runtime_data = rule
    mismatched = tracker()
    registry.async_update_entity_options(
        mismatched.entity_id, "device_tracker", {"associated_zone": "zone.beta"}
    )

    menu = await hass.config_entries.options.async_init(entry.entry_id)
    audit = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "audit"}
    )
    assert audit["type"] == FlowResultType.FORM
    assert audit["description_placeholders"]["count"] == "1"
    assert registry.async_get(mismatched.entity_id).options["device_tracker"]["associated_zone"] == "zone.beta"

    confirm = await hass.config_entries.options.async_configure(
        audit["flow_id"], {"trackers": ["__all__"]}
    )
    assert confirm["step_id"] == "confirm"
    assert confirm["description_placeholders"]["count"] == "1"
    result = await hass.config_entries.options.async_configure(confirm["flow_id"], {})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "repair_complete"
    assert result["description_placeholders"]["count"] == "1"
    assert registry.async_get(mismatched.entity_id).options["device_tracker"]["associated_zone"] == "zone.alpha"


async def test_options_audit_home_default_is_not_mismatch(hass, source, zones, tracker):
    hass.states.async_set("zone.home", "0", {"passive": False})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"source_entry_id": source.entry_id, "zone": "zone.home"},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = Rule(hass, entry, source)
    tracker()
    menu = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "audit"}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_mismatches"


async def test_options_audit_reports_when_source_has_no_trackers(hass, source, zones):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"source_entry_id": source.entry_id, "zone": "zone.alpha"},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = Rule(hass, entry, source)
    menu = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "audit"}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_trackers"
