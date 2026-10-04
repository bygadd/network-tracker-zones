# Network Tracker Zones

Home Assistant Supervisor app that assigns an **Associated zone** to client `device_tracker` entities from a chosen UniFi Network hub or MikroTik Router integration instance. Each source has its own zone. The app uses Home Assistant's existing trackers; it does not create trackers or connect to routers.

## Install

1. In Home Assistant, open **Settings → Add-ons (Apps) → Add-on Store → ⋮ → Repositories**.
2. Add `https://github.com/bygadd/network-tracker-zones`.
3. Install **Network Tracker Zones** from the store and start it.
4. Open its **Log**. It lists the supported sources with their titles and `source_entry_id` values. No router credentials are needed.
5. In **Configuration**, add one mapping per source. Find zone entity IDs under **Developer tools → States** by filtering for `zone.`. Save and restart the app.

Example configuration (replace the example IDs with those from your log):

```yaml
mappings:
  - source_entry_id: 01EXAMPLEUNIFI00000000000001
    zone: zone.home
  - source_entry_id: 01EXAMPLEUNIFI00000000000002
    zone: zone.second_home
  - source_entry_id: 01EXAMPLEMIKROTIK0000000001
    zone: zone.office
```

The app checks for new trackers every 60 seconds and also processes eligible existing trackers. It leaves an already assigned zone alone. If you later change or clear a zone manually on a tracker managed by the app, it leaves your choice alone. Changing a source mapping does not rewrite previously assigned trackers; edit those in Home Assistant if needed. If you edit tracker options at exactly the same time as the app, Home Assistant's API does not provide an atomic update, so one of the two edits could overwrite the other.

Supported sources are **UniFi Network** (`unifi`) and **MikroTik Router** (`mikrotik_router`) connection trackers. UniFi AP Direct is not included. The native source integration must create its own tracker; this app cannot restore a tracker lost to a source-integration identity collision.

The app needs `homeassistant_api` access so it can read config entries and update entity registry options. It does not require privileged mode, access to `/config`, router credentials, or a Home Assistant long-lived token. Its small local state is kept in the app's `/data` directory. It never sends data to the GitHub repository. Uninstalling the app removes this local state; after reinstall, a previously cleared zone may be assigned again.

For updates, push a change with a higher `network_tracker_zones/config.yaml` version, then use the Store's **Check for updates → Update** flow. No HACS installation, GitHub Release, or separate image registry is needed.
