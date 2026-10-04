"""Focused checks for native client selection and safe option updates."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "network_tracker_zones"))
import app


class MappingTests(unittest.TestCase):
    def test_only_loaded_sources_and_active_zones(self):
        source = {
            "entry_id": "site-a-entry", "domain": "unifi", "title": "Plovdiv",
            "state": "loaded", "disabled_by": None,
        }
        mapping = {"site-a-entry": "zone.site_a"}
        self.assertEqual(
            app.valid_mappings(mapping, [source], {"zone.site_a"}, announce=False),
            {"site-a-entry": ("unifi", "zone.site_a")},
        )
        self.assertEqual(
            app.valid_mappings(mapping, [{**source, "disabled_by": "user"}],
                               {"zone.site_a"}, announce=False),
            {},
        )
        self.assertFalse(app.is_usable_zone({
            "entity_id": "zone.site_a", "state": "0", "attributes": {"passive": True}
        }))

    def test_exact_source_and_native_client_ids(self):
        unifi = {
            "entity_id": "device_tracker.phone",
            "config_entry_id": "site-a-entry",
            "platform": "unifi",
            "unique_id": "site_a-02:00:00:00:00:01",
            "disabled_by": None,
        }
        self.assertTrue(app.is_native_client(unifi, "unifi", "site-a-entry"))
        self.assertFalse(app.is_native_client(unifi, "unifi", "site-b-entry"))
        self.assertFalse(
            app.is_native_client(
                {**unifi, "unique_id": "02:00:00:00:00:01"}, "unifi", "site-a-entry"
            )
        )
        mikrotik = {
            **unifi,
            "platform": "mikrotik_router",
            "unique_id": "router2-host-02_00_00_00_00_01",
        }
        self.assertTrue(app.is_native_client(mikrotik, "mikrotik_router", "site-a-entry"))

    def test_reject_duplicate_or_invalid_zone(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "options.json"
            path.write_text(
                json.dumps(
                    {"mappings": [
                        {"source_entry_id": "one", "zone": "zone.home"},
                        {"source_entry_id": "one", "zone": "zone.other"},
                    ]}
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate"):
                app.load_mappings(path)
            path.write_text(
                json.dumps({"mappings": [{"source_entry_id": "one", "zone": "home"}]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "zone"):
                app.load_mappings(path)


class FakeWebSocket:
    def __init__(self, entry):
        self.entry = entry
        self.updates = []

    def request(self, command, **kwargs):
        if command == "config/entity_registry/list":
            return [self.entry]
        if command == "config/entity_registry/get_entries":
            return {self.entry["entity_id"]: self.entry}
        if command == "config/entity_registry/get":
            return self.entry
        if command == "config/entity_registry/update":
            self.updates.append(kwargs)
            return {"entity_entry": self.entry}
        raise AssertionError(command)


class ReconcileTests(unittest.TestCase):
    def make_entry(self, options=None):
        return {
            "id": "registry-1",
            "entity_id": "device_tracker.phone",
            "config_entry_id": "site-a-entry",
            "platform": "unifi",
            "unique_id": "site_a-02:00:00:00:00:01",
            "disabled_by": None,
            "capabilities": {"tracking_type": "connection"},
            "options": options or {},
        }

    def test_sets_zone_and_preserves_other_tracker_options(self):
        ws = FakeWebSocket(self.make_entry({"device_tracker": {"consider_home": 90}}))
        seen = set()
        with patch.object(app, "save_seen"):
            count = app.reconcile(ws, {"site-a-entry": ("unifi", "zone.site_a")}, seen)
        self.assertEqual(count, 1)
        self.assertEqual(seen, {"registry-1"})
        self.assertEqual(ws.updates, [{
            "entity_id": "device_tracker.phone",
            "options_domain": "device_tracker",
            "options": {"consider_home": 90, "associated_zone": "zone.site_a"},
        }])

    def test_preserves_manual_zone_and_manual_clear(self):
        ws = FakeWebSocket(
            self.make_entry({"device_tracker": {"associated_zone": "zone.manually_set"}})
        )
        seen = set()
        with patch.object(app, "save_seen"):
            self.assertEqual(
                app.reconcile(ws, {"site-a-entry": ("unifi", "zone.site_a")}, seen), 0
            )
            self.assertEqual(seen, {"registry-1"})
            ws.entry["options"] = {}
            self.assertEqual(
                app.reconcile(ws, {"site-a-entry": ("unifi", "zone.site_a")}, seen), 0
            )
        self.assertEqual(ws.updates, [])

    def test_two_unifi_sources_with_same_mac_get_separate_zones(self):
        first = self.make_entry()
        second = {
            **self.make_entry(),
            "id": "registry-2",
            "entity_id": "device_tracker.phone_2",
            "config_entry_id": "site-b-entry",
            "unique_id": "site_b-02:00:00:00:00:01",
        }

        class MultiSourceWebSocket:
            def __init__(self):
                self.entries = {e["entity_id"]: e for e in (first, second)}
                self.updates = []

            def request(self, command, **kwargs):
                if command == "config/entity_registry/list":
                    return list(self.entries.values())
                if command == "config/entity_registry/get_entries":
                    return {eid: self.entries[eid] for eid in kwargs["entity_ids"]}
                if command == "config/entity_registry/get":
                    return self.entries[kwargs["entity_id"]]
                if command == "config/entity_registry/update":
                    self.updates.append(kwargs)
                    return {"entity_entry": self.entries[kwargs["entity_id"]]}
                raise AssertionError(command)

        ws = MultiSourceWebSocket()
        seen = set()
        with patch.object(app, "save_seen"):
            count = app.reconcile(ws, {
                "site-a-entry": ("unifi", "zone.site_a"),
                "site-b-entry": ("unifi", "zone.site_b"),
            }, seen)
        self.assertEqual(count, 2)
        self.assertEqual(seen, {"registry-1", "registry-2"})
        self.assertEqual(
            {u["entity_id"]: u["options"]["associated_zone"] for u in ws.updates},
            {"device_tracker.phone": "zone.site_a", "device_tracker.phone_2": "zone.site_b"},
        )


class WebSocketProtocolTests(unittest.TestCase):
    def test_handshake_and_command_result(self):
        class FakeSocket:
            def __init__(self):
                self.responses = iter([
                    '{"type":"auth_required"}',
                    '{"type":"auth_ok"}',
                    '{"id":1,"type":"result","success":true,"result":[]}',
                ])
                self.sent = []

            def recv(self, timeout):
                return next(self.responses)

            def send(self, message):
                self.sent.append(json.loads(message))

        socket = FakeSocket()
        ws = app.HAWebSocket(socket, "test-token")
        self.assertEqual(ws.request("config/entity_registry/list"), [])
        self.assertEqual(socket.sent, [
            {"type": "auth", "access_token": "test-token"},
            {"id": 1, "type": "config/entity_registry/list"},
        ])


if __name__ == "__main__":
    unittest.main()
