"""Exercise Home Assistant's real config and options flow managers."""
import pytest
from unittest.mock import patch
from probatio import to_field_list
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.data_entry_flow import InvalidData
from homeassistant.loader import async_get_integration_descriptions
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers import config_validation as cv
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.network_tracker_zones import Rule, async_setup_entry, async_unload_entry
from custom_components.network_tracker_zones.const import DOMAIN


async def start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def test_listed_in_add_integration_not_helpers(hass):
    descriptions = await async_get_integration_descriptions(hass)
    assert DOMAIN in descriptions["custom"]["integration"]
    assert DOMAIN not in descriptions["custom"]["helper"]


async def test_custom_translations_include_confirmation_steps(hass):
    for language in ("en", "bg"):
        translated = await async_get_translations(
            hass, language, "options", integrations={DOMAIN}
        )
        assert f"component.{DOMAIN}.options.step.confirm_zone_change.title" in translated
        assert f"component.{DOMAIN}.options.step.confirm.title" in translated
        assert f"component.{DOMAIN}.options.step.confirm_zone_change.data.preview" in translated
        assert f"component.{DOMAIN}.options.step.confirm_zone_change.data.confirm" in translated
        assert f"component.{DOMAIN}.options.step.confirm.data.preview" in translated
        assert f"component.{DOMAIN}.options.step.confirm.data.confirm" in translated


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
    entry.runtime_data = Rule(hass, entry, source)
    menu = await hass.config_entries.options.async_init(entry.entry_id)
    assert menu["type"] == FlowResultType.MENU
    form = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "change_zone"}
    )
    result = await hass.config_entries.options.async_configure(form["flow_id"], {"zone": "zone.missing"})
    assert result["errors"] == {"zone": "invalid_zone"}
    result = await hass.config_entries.options.async_configure(form["flow_id"], {"zone": "zone.beta"})
    assert result["step_id"] == "confirm_zone_change"
    assert result["description_placeholders"]["count"] == "0"
    fields = to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)
    assert [field["name"] for field in fields] == ["preview", "confirm"]
    assert fields[0]["default"].startswith("zone.beta (0)")
    assert fields[1]["default"] is False
    assert result["data_schema"]({})["confirm"] is False
    preview = result["data_schema"]({})["preview"]
    blocked = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert blocked["type"] == FlowResultType.FORM
    assert blocked["errors"] == {"confirm": "confirmation_required"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"preview": preview, "confirm": True})
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
    preview = confirm["data_schema"]({})["preview"]
    blocked = await hass.config_entries.options.async_configure(confirm["flow_id"], {})
    assert blocked["errors"] == {"confirm": "confirmation_required"}
    result = await hass.config_entries.options.async_configure(confirm["flow_id"], {"preview": preview, "confirm": True})
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


async def test_target_change_previews_only_managed_trackers(hass, source, zones, registry, tracker):
    existing = tracker()
    entry = MockConfigEntry(
        domain=DOMAIN, data={"source_entry_id": source.entry_id, "zone": "zone.alpha"}
    )
    entry.add_to_hass(hass)
    await async_setup_entry(hass, entry)
    new = tracker("02:00:00:00:00:02")
    await hass.async_block_till_done()
    assert registry.async_get(existing.entity_id).options == {}
    assert registry.async_get(new.entity_id).options["device_tracker"]["associated_zone"] == "zone.alpha"

    menu = await hass.config_entries.options.async_init(entry.entry_id)
    form = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "change_zone"}
    )
    preview = await hass.config_entries.options.async_configure(
        form["flow_id"], {"zone": "zone.beta"}
    )
    assert preview["step_id"] == "confirm_zone_change"
    assert preview["description_placeholders"]["count"] == "1"
    assert new.entity_id in preview["description_placeholders"]["trackers"]
    assert existing.entity_id not in preview["description_placeholders"]["trackers"]
    assert entry.options == {}

    review_text = preview["data_schema"]({})["preview"]
    assert new.entity_id in review_text
    assert existing.entity_id not in review_text
    blocked = await hass.config_entries.options.async_configure(preview["flow_id"], {"preview": "changed", "confirm": True})
    assert blocked["errors"] == {"preview": "preview_modified"}
    result = await hass.config_entries.options.async_configure(preview["flow_id"], {"preview": review_text, "confirm": True})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert registry.async_get(existing.entity_id).options == {}
    assert registry.async_get(new.entity_id).options["device_tracker"]["associated_zone"] == "zone.beta"
    await async_unload_entry(hass, entry)


async def test_target_change_rejects_stale_preview(hass, source, zones, registry, tracker):
    entry = MockConfigEntry(
        domain=DOMAIN, data={"source_entry_id": source.entry_id, "zone": "zone.alpha"}
    )
    entry.add_to_hass(hass)
    await async_setup_entry(hass, entry)
    client = tracker()
    await hass.async_block_till_done()
    menu = await hass.config_entries.options.async_init(entry.entry_id)
    form = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "change_zone"}
    )
    preview = await hass.config_entries.options.async_configure(
        form["flow_id"], {"zone": "zone.beta"}
    )

    registry.async_update_entity_options(
        client.entity_id, "device_tracker", {"associated_zone": "zone.home"}
    )
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_configure(preview["flow_id"], {"preview": preview["data_schema"]({})["preview"], "confirm": True})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "zone_preview_stale"
    assert entry.options == {}
    assert registry.async_get(client.entity_id).options["device_tracker"]["associated_zone"] == "zone.home"
    await async_unload_entry(hass, entry)
