"""Set the initial associated zone of native network client trackers.

This runs in a Home Assistant Supervisor app. It never creates tracker entities
and never changes an existing associated-zone option.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen

LOG = logging.getLogger("network_tracker_zones")
OPTIONS_PATH = Path("/data/options.json")
SEEN_PATH = Path("/data/seen.json")
REST_ROOT = "http://supervisor/core/api"
WS_URL = "ws://supervisor/core/websocket"
POLL_SECONDS = 60
SUPPORTED_DOMAINS = {"unifi", "mikrotik_router"}
MAC = r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}"
UNIFI_CLIENT_UID = re.compile(rf"^.+-{MAC}$", re.IGNORECASE)
MIKROTIK_HOST_UID = re.compile(
    r"^.+-host-[0-9a-f]{2}(?:[_-][0-9a-f]{2}){5}$", re.IGNORECASE
)
ZONE_ID = re.compile(r"^zone\.[a-z0-9_]+$")


class HAError(Exception):
    """Home Assistant rejected an API request."""


def load_mappings(path: Path = OPTIONS_PATH) -> dict[str, str]:
    """Read exact config-entry-ID to zone-ID mappings from app options."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("mappings", [])
    if not isinstance(rows, list):
        raise ValueError("mappings must be a list")
    mappings: dict[str, str] = {}
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"mappings row {index} must be an object")
        source = row.get("source_entry_id")
        zone = row.get("zone")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"mappings row {index} has no source_entry_id")
        if not isinstance(zone, str) or not ZONE_ID.fullmatch(zone):
            raise ValueError(f"mappings row {index} needs a zone.* entity ID")
        source = source.strip()
        if source in mappings:
            raise ValueError(f"duplicate source_entry_id in mappings: {source}")
        mappings[source] = zone
    return mappings


def load_seen(path: Path = SEEN_PATH) -> set[str]:
    """Remember which registry entries have been considered already.

This also lets a user manually clear an assigned zone without it being
reapplied at the next scan or app restart.
    """
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("seen"), list):
        raise ValueError(f"Invalid state file: {path}")
    return set(data["seen"])


def save_seen(seen: set[str], path: Path = SEEN_PATH) -> None:
    """Atomically persist registry IDs after successful handling."""
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps({"seen": sorted(seen)}, separators=(",", ":")), encoding="utf-8"
    )
    os.replace(temp, path)


def rest_get(token: str, path: str) -> object:
    request = Request(
        REST_ROOT + path,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response)


class HAWebSocket:
    """Small synchronous WebSocket client for serialized registry operations."""

    def __init__(self, socket: object, token: str) -> None:
        self.socket = socket
        self.next_id = 1
        greeting = self._receive()
        if greeting.get("type") != "auth_required":
            raise HAError(f"Unexpected WebSocket greeting: {greeting.get('type')}")
        self.socket.send(json.dumps({"type": "auth", "access_token": token}))
        answer = self._receive()
        if answer.get("type") != "auth_ok":
            raise HAError(f"WebSocket authentication failed: {answer.get('type')}")

    def _receive(self) -> dict:
        message = json.loads(self.socket.recv(timeout=30))
        if not isinstance(message, dict):
            raise HAError("Invalid WebSocket response")
        return message

    def request(self, command: str, **parameters: object) -> object:
        request_id = self.next_id
        self.next_id += 1
        self.socket.send(json.dumps({"id": request_id, "type": command, **parameters}))
        response = self._receive()
        if response.get("type") != "result" or response.get("id") != request_id:
            raise HAError(f"Unexpected response to {command}")
        if not response.get("success"):
            error = response.get("error") or {}
            raise HAError(f"{command}: {error.get('code')}: {error.get('message')}")
        return response.get("result")


def is_native_client(entry: dict, source_domain: str, source_id: str) -> bool:
    """Reject trackers from other sources and UniFi infrastructure devices."""
    if (
        not entry.get("entity_id", "").startswith("device_tracker.")
        or entry.get("config_entry_id") != source_id
        or entry.get("platform") != source_domain
        or entry.get("disabled_by") is not None
    ):
        return False
    unique_id = entry.get("unique_id")
    if not isinstance(unique_id, str):
        return False
    if source_domain == "unifi":
        return bool(UNIFI_CLIENT_UID.fullmatch(unique_id))
    if source_domain == "mikrotik_router":
        return bool(MIKROTIK_HOST_UID.fullmatch(unique_id))
    return False


def is_connection_tracker(entry: dict) -> bool:
    capabilities = entry.get("capabilities")
    return isinstance(capabilities, dict) and capabilities.get("tracking_type") == "connection"


def associated_zone(entry: dict) -> str | None:
    options = entry.get("options") or {}
    if not isinstance(options, dict):
        return None
    tracker_options = options.get("device_tracker") or {}
    if not isinstance(tracker_options, dict):
        return None
    zone = tracker_options.get("associated_zone")
    return zone if isinstance(zone, str) and zone else None


def describe_sources(entries: list[dict]) -> None:
    sources = [e for e in entries if isinstance(e, dict) and e.get("domain") in SUPPORTED_DOMAINS]
    if not sources:
        LOG.warning("No UniFi Network or MikroTik Router config entries found")
    for entry in sources:
        LOG.info(
            "Source: %s | %s | source_entry_id=%s",
            entry.get("domain"), entry.get("title"), entry.get("entry_id"),
        )


def is_usable_zone(state: object) -> bool:
    if not isinstance(state, dict):
        return False
    if not str(state.get("entity_id", "")).startswith("zone."):
        return False
    if state.get("state") in ("unavailable", "unknown"):
        return False
    attributes = state.get("attributes") or {}
    return not (isinstance(attributes, dict) and attributes.get("passive") is True)


def environment(token: str) -> tuple[list[dict], set[str], tuple]:
    """Get the current source entries and usable zone entities."""
    entries = rest_get(token, "/config/config_entries/entry")
    states = rest_get(token, "/states")
    if not isinstance(entries, list) or not isinstance(states, list):
        raise HAError("Unexpected Core REST response")
    zone_ids = {
        state["entity_id"]
        for state in states
        if is_usable_zone(state)
    }
    catalog = (
        tuple(sorted(
            (str(entry.get("entry_id")), str(entry.get("domain")),
             str(entry.get("title")), str(entry.get("state")),
             str(entry.get("disabled_by")))
            for entry in entries
            if isinstance(entry, dict) and entry.get("domain") in SUPPORTED_DOMAINS
        )),
        tuple(sorted(zone_ids)),
    )
    return entries, zone_ids, catalog


def valid_mappings(
    configured: dict[str, str], entries: list[dict], zone_ids: set[str],
    *, announce: bool = True,
) -> dict[str, tuple[str, str]]:
    source_by_id = {e.get("entry_id"): e for e in entries if isinstance(e, dict)}
    valid: dict[str, tuple[str, str]] = {}
    for source_id, zone in configured.items():
        source = source_by_id.get(source_id)
        if source is None:
            if announce:
                LOG.warning("Skipping mapping %s: config entry not found", source_id)
        elif source.get("domain") not in SUPPORTED_DOMAINS:
            if announce:
                LOG.warning(
                    "Skipping mapping %s: unsupported integration %s",
                    source_id, source.get("domain"),
                )
        elif source.get("disabled_by") is not None or source.get("state") != "loaded":
            if announce:
                LOG.warning(
                    "Skipping mapping %s: config entry is disabled or not loaded", source_id
                )
        elif zone not in zone_ids:
            if announce:
                LOG.warning("Skipping mapping %s: zone %s does not exist", source_id, zone)
        else:
            valid[source_id] = (source["domain"], zone)
            if announce:
                LOG.info("Active mapping: %s (%s) -> %s", source.get("title"), source_id, zone)
    return valid


def reconcile(ws: HAWebSocket, mappings: dict[str, tuple[str, str]], seen: set[str]) -> int:
    """Set a zone once, preserving user changes and other tracker options."""
    if not mappings:
        return 0
    registry = ws.request("config/entity_registry/list")
    if not isinstance(registry, list):
        raise HAError("Unexpected entity registry list response")
    candidates = [
        item
        for item in registry
        if isinstance(item, dict)
        and (mapping := mappings.get(item.get("config_entry_id")))
        and is_native_client(item, mapping[0], item["config_entry_id"])
        and item.get("id") not in seen
    ]
    if not candidates:
        return 0

    updated = 0
    handled_existing = False
    # get_entries includes capabilities; list deliberately omits them.
    for start in range(0, len(candidates), 100):
        batch = candidates[start : start + 100]
        details = ws.request(
            "config/entity_registry/get_entries",
            entity_ids=[item["entity_id"] for item in batch],
        )
        if not isinstance(details, dict):
            raise HAError("Unexpected entity registry detail response")
        for candidate in batch:
            entity_id = candidate["entity_id"]
            detail = details.get(entity_id)
            if not isinstance(detail, dict):
                continue
            source_id = detail.get("config_entry_id")
            mapping = mappings.get(source_id)
            if not mapping or not is_native_client(detail, mapping[0], source_id):
                continue
            if not is_connection_tracker(detail):
                continue
            registry_id = detail.get("id")
            if not isinstance(registry_id, str) or registry_id in seen:
                continue
            if associated_zone(detail):
                seen.add(registry_id)
                handled_existing = True
                continue

            # Re-read immediately before writing; this still cannot make
            # read/merge/write atomic against a concurrent UI edit.
            fresh = ws.request("config/entity_registry/get", entity_id=entity_id)
            if (
                not isinstance(fresh, dict)
                or fresh.get("id") != registry_id
                or not is_native_client(fresh, mapping[0], source_id)
                or not is_connection_tracker(fresh)
            ):
                continue
            if associated_zone(fresh):
                seen.add(registry_id)
                handled_existing = True
                continue
            current_options = fresh.get("options") or {}
            tracker_options = current_options.get("device_tracker") or {}
            if not isinstance(tracker_options, dict):
                raise HAError(f"Invalid tracker options for {entity_id}")
            new_options = {**tracker_options, "associated_zone": mapping[1]}
            ws.request(
                "config/entity_registry/update",
                entity_id=entity_id,
                options_domain="device_tracker",
                options=new_options,
            )
            seen.add(registry_id)
            save_seen(seen)
            updated += 1
            LOG.info("Set %s -> %s", entity_id, mapping[1])
    if handled_existing:
        save_seen(seen)
    return updated


def main() -> None:
    from websockets.sync.client import connect

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN is missing; set homeassistant_api: true")
    configured = load_mappings()
    seen = load_seen()
    LOG.info("Loaded %s mapping(s); %s tracker(s) previously handled", len(configured), len(seen))
    previous_catalog = None
    while True:
        try:
            entries, zone_ids, catalog = environment(token)
            catalog_changed = catalog != previous_catalog
            if catalog_changed:
                describe_sources(entries)
                LOG.info("Available zones: %s", ", ".join(sorted(zone_ids)) or "none")
                previous_catalog = catalog
            if not configured:
                LOG.info("No mappings configured; add source_entry_id and zone in app Configuration")
                while True:
                    time.sleep(3600)
            mappings = valid_mappings(configured, entries, zone_ids, announce=catalog_changed)
            with connect(WS_URL, open_timeout=20, max_size=16 * 1024 * 1024) as socket:
                ws = HAWebSocket(socket, token)
                while True:
                    changed = reconcile(ws, mappings, seen)
                    if changed:
                        LOG.info("Assigned zones to %s new tracker(s)", changed)
                    time.sleep(POLL_SECONDS)
                    # Refresh source and zone existence in case HA config changes.
                    entries, zone_ids, catalog = environment(token)
                    if catalog != previous_catalog:
                        describe_sources(entries)
                        LOG.info("Available zones: %s", ", ".join(sorted(zone_ids)) or "none")
                        previous_catalog = catalog
                        mappings = valid_mappings(configured, entries, zone_ids)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            LOG.exception("Home Assistant connection or scan failed; retrying in 30 seconds")
            time.sleep(30)


if __name__ == "__main__":
    main()
