"""Behavior tests use the real entity registry and Store."""
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.network_tracker_zones import Rule, async_setup_entry, async_unload_entry
from custom_components.network_tracker_zones.const import DOMAIN


def rule(hass, source, zone="zone.alpha"):
    entry = MockConfigEntry(domain=DOMAIN, title="Example rule", data={"source_entry_id": source.entry_id, "zone": zone})
    entry.add_to_hass(hass)
    return entry


def association(registry, entity):
    return registry.async_get(entity.entity_id).options.get("device_tracker", {}).get("associated_zone")


async def test_initial_future_and_capability_late(hass, source, zones, registry, tracker):
    first = tracker()
    late = registry.async_get_or_create("device_tracker", "unifi", "site_alpha-02:00:00:00:00:02", config_entry=source)
    entry = rule(hass, source)
    await async_setup_entry(hass, entry)
    assert association(registry, first) is None
    assert association(registry, late) is None
    future = tracker("02:00:00:00:00:03")
    registry.async_update_entity(late.entity_id, capabilities={"tracking_type": "connection"})
    await hass.async_block_till_done()
    assert association(registry, future) == "zone.alpha"
    assert association(registry, late) is None
    assert {first.id, late.id} <= entry.runtime_data.preexisting
    await async_unload_entry(hass, entry)


async def test_existing_unset_requires_explicit_repair_even_after_restart(hass, source, zones, registry, tracker):
    client = tracker()
    entry = rule(hass, source)
    await async_setup_entry(hass, entry)
    assert association(registry, client) is None
    assert client.id in entry.runtime_data.preexisting
    await async_unload_entry(hass, entry)

    await async_setup_entry(hass, entry)
    assert association(registry, client) is None
    preview = entry.runtime_data.audit("zone.alpha")[0]
    assert preview["current"] is None
    assert await entry.runtime_data.async_replace_mismatches(
        "zone.alpha", {client.id: None}
    ) == 1
    assert client.id not in entry.runtime_data.preexisting
    assert association(registry, client) == "zone.alpha"
    await async_unload_entry(hass, entry)


async def test_upgrade_preserves_old_managed_ids_and_protects_other_existing(
    hass, source, zones, registry, tracker
):
    import json
    from pathlib import Path

    old_managed = tracker()
    untouched = tracker("02:00:00:00:00:02")
    registry.async_update_entity_options(
        old_managed.entity_id, "device_tracker", {"associated_zone": "zone.alpha"}
    )
    entry = rule(hass, source)
    previous = Rule(hass, entry, source)
    previous.managed[old_managed.id] = "zone.alpha"
    await previous.async_save()
    path = Path(previous.store.path)
    data = json.loads(await hass.async_add_executor_job(path.read_text))
    del data["data"]["preexisting"]  # Stored by versions 0.1.0 and 0.1.1.
    await hass.async_add_executor_job(path.write_text, json.dumps(data))

    await async_setup_entry(hass, entry)
    assert entry.runtime_data.managed[old_managed.id] == "zone.alpha"
    assert untouched.id in entry.runtime_data.preexisting
    assert association(registry, untouched) is None
    await async_unload_entry(hass, entry)


@pytest.mark.parametrize("manual", ["zone.home", "zone.beta", "zone.alpha"])
async def test_explicit_choices_preserved(hass, source, zones, registry, tracker, manual):
    entity = tracker()
    registry.async_update_entity_options(entity.entity_id, "device_tracker", {"associated_zone": manual})
    entry = rule(hass, source)
    await async_setup_entry(hass, entry)
    hass.config_entries.async_update_entry(entry, options={"zone": "zone.beta"})
    await entry.runtime_data.async_scan()
    assert association(registry, entity) == manual
    await async_unload_entry(hass, entry)


@pytest.mark.parametrize("manual", [None, "zone.home"])
async def test_manual_choice_survives_restart_and_target_change(hass, source, zones, registry, tracker, manual):
    entity = tracker()
    entry = rule(hass, source)
    await async_setup_entry(hass, entry)
    registry.async_update_entity_options(entity.entity_id, "device_tracker", {} if manual is None else {"associated_zone": manual})
    await hass.async_block_till_done()
    await async_unload_entry(hass, entry)
    hass.config_entries.async_update_entry(entry, options={"zone": "zone.beta"})
    await async_setup_entry(hass, entry)
    assert association(registry, entity) == manual
    await async_unload_entry(hass, entry)


async def test_change_updates_managed_only_and_merges_options(hass, source, zones, registry, tracker):
    manual = tracker("02:00:00:00:00:02")
    registry.async_update_entity_options(manual.entity_id, "device_tracker", {"associated_zone": "zone.home"})
    entry = rule(hass, source)
    await async_setup_entry(hass, entry)
    managed = tracker()
    registry.async_update_entity_options(managed.entity_id, "device_tracker", {"consider_home": 10})
    registry.async_update_entity_options(managed.entity_id, "sensor", {"display_precision": 2})
    await hass.async_block_till_done()
    hass.config_entries.async_update_entry(entry, options={"zone": "zone.beta"})
    await entry.runtime_data.async_scan()
    assert association(registry, managed) == "zone.beta"
    assert association(registry, manual) == "zone.home"
    assert registry.async_get(managed.entity_id).options == {"device_tracker": {"consider_home": 10, "associated_zone": "zone.beta"}, "sensor": {"display_precision": 2}}
    await async_unload_entry(hass, entry)


async def test_exact_sources_independent_same_mac(hass, source, zones, registry, tracker):
    second = MockConfigEntry(domain="unifi", title="Other network", data={"site": "site_beta"})
    second.add_to_hass(hass)
    a, b = rule(hass, source), rule(hass, second, "zone.beta")
    await async_setup_entry(hass, a)
    await async_setup_entry(hass, b)
    first = tracker()
    other = registry.async_get_or_create("device_tracker", "unifi", "site_beta-02:00:00:00:00:01", config_entry=second, capabilities={"tracking_type": "connection"})
    await hass.async_block_till_done()
    assert association(registry, first) == "zone.alpha"
    assert association(registry, other) == "zone.beta"
    await async_unload_entry(hass, a)
    await async_unload_entry(hass, b)


@pytest.mark.parametrize("platform,uid,capabilities", [
    ("unifi", "02:00:00:00:00:01", {"tracking_type": "connection"}),
    ("unifi", "site_alpha-device-02:00:00:00:00:01", {"tracking_type": "connection"}),
    ("unifi", "site_alpha-02:00:00:00:00:01", {"tracking_type": "gps"}),
    ("unifi_direct", "site_alpha-02:00:00:00:00:01", {"tracking_type": "connection"}),
    ("mikrotik", "site_alpha-02:00:00:00:00:01", {"tracking_type": "connection"}),
])
async def test_non_clients_excluded(hass, source, zones, registry, platform, uid, capabilities):
    entity = registry.async_get_or_create("device_tracker", platform, uid, config_entry=source, capabilities=capabilities)
    entry = rule(hass, source)
    await async_setup_entry(hass, entry)
    assert association(registry, entity) is None
    await async_unload_entry(hass, entry)


async def test_mikrotik_client_identity(hass, zones, registry):
    source = MockConfigEntry(domain="mikrotik_router", title="Example router", data={"name": "Example Router"})
    source.add_to_hass(hass)
    entry = rule(hass, source)
    await async_setup_entry(hass, entry)
    client = registry.async_get_or_create("device_tracker", "mikrotik_router", "example router-host-02_00_00_00_00_01", config_entry=source, capabilities={"tracking_type": "connection"})
    infrastructure = registry.async_get_or_create("device_tracker", "mikrotik_router", "example router-router", config_entry=source, capabilities={"tracking_type": "connection"})
    await hass.async_block_till_done()
    assert association(registry, client) == "zone.alpha"
    assert association(registry, infrastructure) is None
    await async_unload_entry(hass, entry)


async def test_unload_and_reload_listener(hass, source, zones, registry, tracker):
    entry = rule(hass, source)
    await async_setup_entry(hass, entry)
    await async_unload_entry(hass, entry)
    entity = tracker()
    await hass.async_block_till_done()
    assert association(registry, entity) is None
    await async_setup_entry(hass, entry)
    assert association(registry, entity) is None
    later = tracker("02:00:00:00:00:02")
    await hass.async_block_till_done()
    assert association(registry, later) == "zone.alpha"
    await async_unload_entry(hass, entry)


async def test_failed_persistence_does_not_write_zone(hass, source, zones, registry, tracker):
    from homeassistant.exceptions import ConfigEntryNotReady
    entity = tracker()
    entry = rule(hass, source)
    with patch("custom_components.network_tracker_zones.Store.async_save", return_value=None):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)
    assert association(registry, entity) is None


async def test_target_change_during_save_does_not_write_stale_zone(hass, source, zones, registry, tracker):
    entity = tracker()
    entry = rule(hass, source)
    runtime = Rule(hass, entry, source)
    save = runtime.async_save
    calls = 0

    async def save_then_change_target():
        nonlocal calls
        await save()
        calls += 1
        if calls == 1:
            hass.config_entries.async_update_entry(entry, options={"zone": "zone.beta"})

    with patch.object(runtime, "async_save", side_effect=save_then_change_target):
        await runtime.async_apply(entity.entity_id)
    assert association(registry, entity) is None
    assert entity.id not in runtime.managed

    await runtime.async_apply(entity.entity_id)
    assert association(registry, entity) == "zone.beta"


async def test_native_scanner_state_and_disconnect(hass, source, zones, registry):
    from datetime import timedelta
    import logging
    from homeassistant.components.device_tracker import ScannerEntity
    from homeassistant.helpers.entity_platform import EntityPlatform

    class Client(ScannerEntity):
        _attr_unique_id = "site_alpha-02:00:00:00:00:01"
        _attr_name = "Synthetic client"
        _attr_should_poll = False
        connected = True

        @property
        def unique_id(self):
            return self._attr_unique_id

        @property
        def is_connected(self):
            return self.connected

    platform = EntityPlatform(hass=hass, logger=logging.getLogger(__name__), domain="device_tracker", platform_name="unifi", platform=None, scan_interval=timedelta(seconds=30), entity_namespace=None)
    platform.config_entry = source
    scanner = Client()
    await platform.async_add_entities([scanner])
    assert hass.states.get(scanner.entity_id).state == "home"
    entry = rule(hass, source)
    await async_setup_entry(hass, entry)
    await hass.async_block_till_done()
    assert hass.states.get(scanner.entity_id).state == "home"
    preview = entry.runtime_data.audit("zone.alpha")[0]
    assert await entry.runtime_data.async_replace_mismatches(
        "zone.alpha", {preview["registry_id"]: preview["current"]}
    ) == 1
    await hass.async_block_till_done()
    assert hass.states.get(scanner.entity_id).state == "Alpha"
    scanner.connected = False
    scanner.async_write_ha_state()
    assert hass.states.get(scanner.entity_id).state == "not_home"
    await async_unload_entry(hass, entry)
    await platform.async_reset()


async def test_corrupt_state_is_not_forgotten(hass, source, zones, registry, tracker):
    from pathlib import Path
    from homeassistant.exceptions import ConfigEntryNotReady
    entity = tracker()
    entry = rule(hass, source)
    path = Path(hass.config.path(".storage", f"{DOMAIN}.{entry.entry_id}"))
    await hass.async_add_executor_job(path.parent.mkdir, 0o755, True, True)
    await hass.async_add_executor_job(path.write_text, "broken-json")
    with pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(hass, entry)
    assert association(registry, entity) is None
    assert await hass.async_add_executor_job(path.read_text) == "broken-json"


async def test_audit_manual_mismatch_and_one_shot_replace(hass, source, zones, registry, tracker):
    client = tracker()
    registry.async_update_entity_options(
        client.entity_id, "device_tracker", {"associated_zone": "zone.home", "consider_home": 17}
    )
    entry = rule(hass, source)
    await async_setup_entry(hass, entry)
    runtime = entry.runtime_data

    preview = runtime.audit("zone.alpha")
    assert preview == [{
        "registry_id": client.id,
        "entity_id": client.entity_id,
        "current": "zone.home",
        "expected": "zone.alpha",
    }]
    assert association(registry, client) == "zone.home"
    assert await runtime.async_replace_mismatches(
        "zone.alpha", {client.id: preview[0]["current"]}
    ) == 1
    assert registry.async_get(client.entity_id).options["device_tracker"] == {
        "associated_zone": "zone.alpha", "consider_home": 17
    }
    assert runtime.audit("zone.alpha") == []

    # After the explicit repair, a later manual edit remains a manual choice.
    registry.async_update_entity_options(
        client.entity_id, "device_tracker", {"associated_zone": "zone.beta", "consider_home": 17}
    )
    await hass.async_block_till_done()
    await runtime.async_scan()
    assert association(registry, client) == "zone.beta"
    assert client.id in runtime.excluded
    await async_unload_entry(hass, entry)


async def test_audit_and_repair_only_selected_source(hass, source, zones, registry, tracker):
    first = tracker()
    second_source = MockConfigEntry(domain="unifi", title="Other network", data={"site": "site_beta"})
    second_source.add_to_hass(hass)
    other = registry.async_get_or_create(
        "device_tracker", "unifi", "site_beta-02:00:00:00:00:01",
        config_entry=second_source, capabilities={"tracking_type": "connection"}
    )
    for entity in (first, other):
        registry.async_update_entity_options(entity.entity_id, "device_tracker", {"associated_zone": "zone.home"})
    a, b = rule(hass, source), rule(hass, second_source, "zone.beta")
    await async_setup_entry(hass, a)
    await async_setup_entry(hass, b)
    assert [item["registry_id"] for item in a.runtime_data.audit("zone.alpha")] == [first.id]
    assert [item["registry_id"] for item in b.runtime_data.audit("zone.beta")] == [other.id]

    # Even a forged selection from another source is ignored.
    assert await a.runtime_data.async_replace_mismatches(
        "zone.alpha", {other.id: "zone.home", first.id: "zone.home"}
    ) == 1
    assert association(registry, first) == "zone.alpha"
    assert association(registry, other) == "zone.home"
    await async_unload_entry(hass, a)
    await async_unload_entry(hass, b)


async def test_repair_skips_stale_preview_and_target(hass, source, zones, registry, tracker):
    client = tracker()
    registry.async_update_entity_options(client.entity_id, "device_tracker", {"associated_zone": "zone.home"})
    entry = rule(hass, source)
    await async_setup_entry(hass, entry)
    runtime = entry.runtime_data
    snapshot = runtime.audit("zone.alpha")[0]

    registry.async_update_entity_options(client.entity_id, "device_tracker", {"associated_zone": "zone.beta"})
    assert await runtime.async_replace_mismatches(
        "zone.alpha", {client.id: snapshot["current"]}
    ) == 0
    assert association(registry, client) == "zone.beta"

    fresh = runtime.audit("zone.alpha")[0]
    hass.config_entries.async_update_entry(entry, options={"zone": "zone.beta"})
    assert await runtime.async_replace_mismatches(
        "zone.alpha", {client.id: fresh["current"]}
    ) == 0
    assert association(registry, client) == "zone.beta"
    await async_unload_entry(hass, entry)


async def test_unset_association_is_effectively_home(hass, source, zones, registry, tracker):
    hass.states.async_set("zone.home", "0", {"passive": False})
    entry = rule(hass, source)
    await async_setup_entry(hass, entry)
    client = tracker()
    await hass.async_block_till_done()

    # Clearing a managed value is a manual choice; audit keeps the raw None
    # for stale-preview protection, while comparing with HA's effective home.
    registry.async_update_entity_options(client.entity_id, "device_tracker", {})
    await hass.async_block_till_done()
    assert entry.runtime_data.audit("zone.alpha")[0]["current"] is None
    assert await entry.runtime_data.async_replace_mismatches("zone.alpha", {client.id: None}) == 1
    assert association(registry, client) == "zone.alpha"
    registry.async_update_entity_options(client.entity_id, "device_tracker", {})
    await hass.async_block_till_done()
    hass.config_entries.async_update_entry(entry, options={"zone": "zone.home"})
    assert entry.runtime_data.audit("zone.home") == []
    await async_unload_entry(hass, entry)
