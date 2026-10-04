# Network Tracker Zones

Start the app once with empty `mappings`, then open its **Log**. Copy each UniFi Network or MikroTik Router `source_entry_id` you want to map. Find the destination zone entity ID in **Developer tools → States** (`zone.*`).

In **Configuration**, enter:

```yaml
mappings:
  - source_entry_id: 01EXAMPLE000000000000000001
    zone: zone.second_home
```

Save and restart the app. It checks for new client trackers at startup and every 60 seconds. Existing manual zone choices are preserved. [Full documentation](https://github.com/bygadd/network-tracker-zones#readme).
