# Network Tracker Zones

Network Tracker Zones assigns Home Assistant's native **Associated zone** option to connection-based `device_tracker` entities from an existing UniFi Network or MikroTik Router integration. Each rule maps one source integration entry to one geographic `zone.*`. The source integration continues to determine whether a client is connected; this component changes only where Home Assistant places a connected client.

It does not create trackers, poll network devices, modify source credentials, define zones, or change `person` configuration.

## Installation and configuration

1. In **HACS → ⋮ → Custom repositories**, add `https://github.com/bygadd/network-tracker-zones` as an **Integration** and download it.
2. Restart Home Assistant. HACS installs the files; configuration is performed in Home Assistant under **Settings → Devices & services → Integrations → Add integration → Network Tracker Zones**. You can also use the [direct setup link](https://my.home-assistant.io/redirect/config_flow_start/?domain=network_tracker_zones).
3. Select an existing UniFi Network or MikroTik Router source and an active geographic zone. Add a separate rule for each source entry. A source can have only one rule.

If the integration is absent from **Add integration**, verify that HACS reports it as downloaded and restart Home Assistant. The source and zone selectors are populated from existing Home Assistant entries; no controller IDs or credentials are entered here.

## Exact behavior

The integration matches only client connection trackers belonging to the selected source `config_entry_id`. It checks the tracker platform, `tracking_type: connection`, and the client unique-ID format used by the supported source. Infrastructure and position trackers are ignored. Each rule stores its own managed and protected registry IDs in Home Assistant's local storage.

| Tracker state | Action |
| --- | --- |
| Already registered and not previously managed by this rule, with no Associated zone value | Leave unchanged. Home Assistant uses `zone.home` as the effective default. The tracker is available for an explicit audit and repair. |
| Already registered and not previously managed, with an explicit Associated zone, including `zone.home` | Leave unchanged, even if it differs from or equals the rule target. |
| Registered while the rule is active, eligible, and without an Associated zone value | Assign the rule's target zone automatically and record that this rule wrote it. |
| Newly registered with an explicit Associated zone | Preserve the explicit value. |
| Previously assigned by this rule, then changed to a different value or cleared | Preserve the new value and stop managing that tracker. |
| An unmanaged source tracker appears while the rule is unloaded | Treat it as pre-existing at the next start; it requires explicit review. |

The integration reads the current `device_tracker.associated_zone` option. A missing or null value uses [Home Assistant's documented default](https://www.home-assistant.io/integrations/device_tracker/#connection-trackers), `zone.home`; an explicit `zone.home` string has the same effective Home state without relying on that default. **The registry does not record who set an existing value.** The integration identifies its own writes from its local managed-ID record. A later different or cleared value is treated as an external edit. It cannot identify an older value's author or detect an edit that writes the same value again.

## Review existing trackers

Open **Settings → Devices & services → Network Tracker Zones → Configure** on the rule for the relevant source, then choose **Review mismatched trackers**. The preview lists eligible trackers whose effective current zone differs from the rule target. A missing or null value is displayed as **unset (defaults to zone.home)**; a zone string is displayed as **explicit**. Select individual trackers or **All**, then review the list and check the confirmation box on the following screen. The preview field is for review only; leave its text unchanged. Opening the preview does not change any tracker.

At confirmation, the integration rechecks the source, tracker identity, target zone, and current option. It skips entries changed since the preview. Only `device_tracker.associated_zone` is written; other options are preserved. A repaired tracker becomes managed by the rule. To preserve a later individual choice, change or clear its Associated zone manually.

To change a rule's target, choose **Configure → Change target zone**. A separate confirmation screen shows the target, count, and currently managed trackers that would change. Review the list, leave the preview field unchanged, and check the confirmation box to apply the change. An empty confirmation cannot update the rule. Trackers protected as pre-existing or released after an external edit remain unchanged. Newly registered clients subsequently use the new target.

## Upgrading from 0.1.0 or 0.1.1

Earlier versions assigned the target zone to already registered trackers whose Associated zone was unset during initial setup. Version 0.1.2 stops this initial bulk assignment. It **does not automatically undo earlier writes**, because doing so could discard choices made after installation. Existing rule-managed trackers remain managed and appear in the preview when the rule target changes. To opt out an individual tracker, change or clear its Associated zone in Home Assistant.

Version 0.1.3 adds a visible preview field and a required confirmation box to both change workflows. If an older version shows an empty **Options** window, close it without submitting, update the integration in HACS, and restart Home Assistant.

## Scope and limitations

Supported sources are UniFi Network (`unifi`) and the custom MikroTik Router integration (`mikrotik_router`). UniFi AP Direct and Home Assistant's built-in MikroTik integration are not supported. This component cannot recover a tracker that the source integration failed to create because of an upstream unique-ID collision. It does not infer location from an access point within one source entry.

A newly created tracker can briefly report the default Home state before the registry association is applied. Removing a rule leaves previously written zone options in place. The integration has been tested with Home Assistant Core **2026.9.4**; other versions require verification. For a setup failure, check **Settings → System → Logs** for `network_tracker_zones` and include the relevant error in an [issue](https://github.com/bygadd/network-tracker-zones/issues).
