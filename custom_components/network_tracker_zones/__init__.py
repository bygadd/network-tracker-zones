"""Associate network client trackers with a native Home Assistant zone."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.components import device_tracker
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import CONF_SOURCE, CONF_ZONE, DOMAIN, SOURCES

_LOGGER = logging.getLogger(__name__)
_MAC = r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}"
_SLUG_MAC = r"[0-9a-f]{2}(?:_[0-9a-f]{2}){5}"


def valid_source(hass: HomeAssistant, source_id: str) -> ConfigEntry | None:
    """Resolve only a supported source config entry."""
    source = hass.config_entries.async_get_entry(source_id)
    return source if source and source.domain in SOURCES else None


def valid_zone(hass: HomeAssistant, zone_id: str) -> bool:
    """Require an existing, active geographic zone."""
    state = hass.states.get(zone_id)
    return bool(state and state.domain == "zone" and not state.attributes.get("passive", False))


class Rule:
    """Serialize registry events and distinguish managed from pre-existing IDs."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, source: ConfigEntry) -> None:
        self.hass, self.entry, self.source = hass, entry, source
        self.registry = er.async_get(hass)
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}", atomic_writes=True)
        self.managed: dict[str, str] = {}
        self.excluded: set[str] = set()
        self.preexisting: set[str] = set()
        self.lock = asyncio.Lock()
        self.stopped = False
        self.unsubscribers = []

    @callback
    def stop(self) -> None:
        self.stopped = True
        while self.unsubscribers:
            self.unsubscribers.pop()()

    async def async_load(self) -> None:
        """Fail closed on unreadable or invalid state rather than forget choices."""
        # Store otherwise renames corrupt JSON and returns None (fresh state).
        try:
            raw = await self.hass.async_add_executor_job(Path(self.store.path).read_text)
        except FileNotFoundError:
            pass
        else:
            json.loads(raw)
        data = await self.store.async_load()
        if data is None:
            return
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("managed"), dict)
            or not isinstance(data.get("excluded"), list)
            or not isinstance(data.get("preexisting", []), list)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in data["managed"].items())
            or not all(isinstance(k, str) for k in data["excluded"])
            or not all(isinstance(k, str) for k in data.get("preexisting", []))
        ):
            raise ValueError("Invalid saved rule state")
        self.managed = data["managed"]
        self.excluded = set(data["excluded"])
        self.preexisting = set(data.get("preexisting", []))

    async def async_save(self) -> None:
        """Verify persistence: HA Store logs some write errors without raising."""
        data = {
            "managed": dict(self.managed),
            "excluded": sorted(self.excluded),
            "preexisting": sorted(self.preexisting),
        }
        await self.store.async_save(data)
        saved = json.loads(await self.hass.async_add_executor_job(Path(self.store.path).read_text))
        if saved.get("data") != data:
            raise OSError("Rule state was not persisted")

    async def async_snapshot_existing(self) -> None:
        """Protect all source trackers present before this rule starts writing."""
        before = len(self.preexisting)
        for entity in er.async_entries_for_config_entry(self.registry, self.source.entry_id):
            if (
                entity.domain == "device_tracker"
                and entity.id not in self.managed
                and entity.id not in self.excluded
            ):
                self.preexisting.add(entity.id)
        if len(self.preexisting) != before:
            await self.async_save()

    def eligible(self, entity: er.RegistryEntry) -> bool:
        """Match the exact source and its client identity, never infrastructure."""
        if (
            entity.config_entry_id != self.source.entry_id
            or entity.domain != "device_tracker"
            or entity.platform != self.source.domain
            or not entity.capabilities
            or entity.capabilities.get("tracking_type") != "connection"
        ):
            return False
        if self.source.domain == "unifi":
            prefix = self.source.data.get("site")
            pattern = f"{re.escape(prefix)}-{_MAC}" if isinstance(prefix, str) and prefix else ""
        else:
            name = self.source.data.get("name")
            pattern = f"{re.escape(name.lower())}-host-{_SLUG_MAC}" if isinstance(name, str) and name else ""
        return bool(pattern and re.fullmatch(pattern, entity.unique_id))

    def audit(self, target: str) -> list[dict]:
        """Preview client trackers whose saved association differs from this rule."""
        if (
            self.stopped
            or valid_source(self.hass, self.source.entry_id) is not self.source
            or self.entry.options.get(CONF_ZONE, self.entry.data[CONF_ZONE]) != target
            or not valid_zone(self.hass, target)
        ):
            return []
        mismatches = []
        for entity in er.async_entries_for_config_entry(self.registry, self.source.entry_id):
            if not self.eligible(entity):
                continue
            current = entity.options.get("device_tracker", {}).get("associated_zone")
            # Native HA treats an unset association as the default home zone.
            if (current or "zone.home") != target:
                mismatches.append({
                    "registry_id": entity.id,
                    "entity_id": entity.entity_id,
                    "current": current,
                    "expected": target,
                })
        return mismatches

    def eligible_count(self) -> int:
        """Count client trackers that belong to this exact source."""
        return sum(
            self.eligible(entity)
            for entity in er.async_entries_for_config_entry(self.registry, self.source.entry_id)
        )

    def preview_zone_change(self, target: str) -> dict[str, str]:
        """Return managed trackers that a confirmed target change would update."""
        if (
            self.stopped
            or valid_source(self.hass, self.source.entry_id) is not self.source
            or not valid_zone(self.hass, target)
        ):
            return {}
        affected = {}
        for entity in er.async_entries_for_config_entry(self.registry, self.source.entry_id):
            current = entity.options.get("device_tracker", {}).get("associated_zone")
            if (
                self.eligible(entity)
                and entity.id in self.managed
                and current == self.managed[entity.id]
                and current != target
            ):
                affected[entity.id] = f"{entity.entity_id}: {current} → {target}"
        return affected

    async def async_replace_mismatches(
        self, target: str, expected_by_id: dict[str, str | None]
    ) -> int:
        """Explicitly replace previewed values that have not changed since review."""
        changed = 0
        async with self.lock:
            if self.stopped:
                return 0
            for registry_id, expected in expected_by_id.items():
                # Recheck the source, rule, zone, entity identity and current value
                # after confirmation. A later registry edit wins over this preview.
                preview = next(
                    (item for item in self.audit(target) if item["registry_id"] == registry_id),
                    None,
                )
                if preview is None or preview["current"] != expected:
                    continue
                entity_id = preview["entity_id"]
                old_managed = self.managed.get(registry_id)
                old_excluded = registry_id in self.excluded
                old_preexisting = registry_id in self.preexisting
                self.managed[registry_id] = target
                self.excluded.discard(registry_id)
                self.preexisting.discard(registry_id)
                try:
                    # Persist ownership before touching HA, as in the normal scan.
                    await self.async_save()
                    latest = self.registry.async_get(entity_id)
                    if (
                        self.stopped
                        or not latest
                        or latest.id != registry_id
                        or not self.eligible(latest)
                        or valid_source(self.hass, self.source.entry_id) is not self.source
                        or self.entry.options.get(CONF_ZONE, self.entry.data[CONF_ZONE]) != target
                        or not valid_zone(self.hass, target)
                        or latest.options.get("device_tracker", {}).get("associated_zone") != expected
                    ):
                        self._restore_ownership(registry_id, old_managed, old_excluded, old_preexisting)
                        await self.async_save()
                        continue
                    options = dict(latest.options.get("device_tracker", {}))
                    options["associated_zone"] = target
                    self.registry.async_update_entity_options(entity_id, "device_tracker", options)
                except Exception:
                    self._restore_ownership(registry_id, old_managed, old_excluded, old_preexisting)
                    await self.async_save()
                    raise
                changed += 1
        return changed

    def _restore_ownership(
        self, registry_id: str, managed: str | None, excluded: bool, preexisting: bool = False
    ) -> None:
        """Undo an uncommitted write without forgetting the prior classification."""
        if managed is None:
            self.managed.pop(registry_id, None)
        else:
            self.managed[registry_id] = managed
        if excluded:
            self.excluded.add(registry_id)
        else:
            self.excluded.discard(registry_id)
        if preexisting:
            self.preexisting.add(registry_id)
        else:
            self.preexisting.discard(registry_id)

    async def async_scan(self, _hass=None, _entry=None) -> None:
        """Scan existing entities or handle a target change under the same lock."""
        async with self.lock:
            if self.stopped:
                return
            for entity in er.async_entries_for_config_entry(self.registry, self.source.entry_id):
                await self.async_apply(entity.entity_id)

    async def async_apply(self, entity_id: str) -> None:
        """Claim empty options or update our last value; preserve every other choice."""
        entity = self.registry.async_get(entity_id)
        target = self.entry.options.get(CONF_ZONE, self.entry.data[CONF_ZONE])
        if (
            not entity
            or valid_source(self.hass, self.source.entry_id) is not self.source
            or not self.eligible(entity)
            or not valid_zone(self.hass, target)
        ):
            return
        if entity.id in self.excluded or entity.id in self.preexisting:
            return
        current = entity.options.get("device_tracker", {}).get("associated_zone")
        previous = self.managed.get(entity.id)
        if (previous is not None and current != previous) or (previous is None and current is not None):
            self.managed.pop(entity.id, None)
            self.excluded.add(entity.id)
            await self.async_save()
            return
        if current == target:
            return
        # Save ownership before changing the registry. Interrupted writes become
        # exclusions on restart, so uncertain individual choices stay untouched.
        old_managed = self.managed.get(entity.id)
        self.managed[entity.id] = target
        await self.async_save()
        latest = self.registry.async_get(entity_id)
        if (
            self.stopped
            or not latest
            or latest.id != entity.id
            or not self.eligible(latest)
            or valid_source(self.hass, self.source.entry_id) is not self.source
            or self.entry.options.get(CONF_ZONE, self.entry.data[CONF_ZONE]) != target
            or not valid_zone(self.hass, target)
        ):
            self._restore_ownership(entity.id, old_managed, False)
            await self.async_save()
            return
        if latest.options.get("device_tracker", {}).get("associated_zone") != current:
            self.managed.pop(entity.id, None)
            self.excluded.add(entity.id)
            await self.async_save()
            return
        options = dict(latest.options.get("device_tracker", {}))
        options["associated_zone"] = target
        self.registry.async_update_entity_options(entity_id, "device_tracker", options)

    @callback
    def event(self, event) -> None:
        """Queue create/update events; own updates are naturally idempotent."""
        entity = self.registry.async_get(event.data["entity_id"])
        if not self.stopped and entity and entity.config_entry_id == self.source.entry_id and event.data["action"] in ("create", "update"):
            self.entry.async_create_task(self.hass, self.async_event(event.data["entity_id"]), "associate tracker zone")

    async def async_event(self, entity_id: str) -> None:
        async with self.lock:
            if not self.stopped:
                try:
                    await self.async_apply(entity_id)
                except Exception:
                    self.stopped = True
                    _LOGGER.exception("Rule stopped because its state could not be saved; reload the integration to retry")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Protect existing trackers before subscribing and scanning for new ones."""
    if not hasattr(device_tracker, "CONF_ASSOCIATED_ZONE") or not hasattr(er.EntityRegistry, "async_update_entity_options"):
        raise ConfigEntryNotReady("Home Assistant lacks native tracker zone association")
    source = valid_source(hass, entry.data[CONF_SOURCE])
    target = entry.options.get(CONF_ZONE, entry.data[CONF_ZONE])
    if source is None or not valid_zone(hass, target):
        raise ConfigEntryNotReady("Source or active zone is unavailable")
    rule = Rule(hass, entry, source)
    unsubscribe = None
    try:
        await rule.async_load()
        await rule.async_snapshot_existing()
        unsubscribe = hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, rule.event)
        await rule.async_scan()
    except Exception as err:
        rule.stopped = True
        if unsubscribe:
            unsubscribe()
        raise ConfigEntryNotReady("Unable to load or persist rule state") from err
    entry.runtime_data = rule
    rule.unsubscribers = [unsubscribe, entry.add_update_listener(rule.async_scan)]
    entry.async_on_unload(rule.stop)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop pending handlers and unsubscribe; native options intentionally remain."""
    rule = entry.runtime_data
    rule.stop()
    async with rule.lock:
        pass
    return True
