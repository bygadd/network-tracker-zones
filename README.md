# Network Tracker Zones

A Home Assistant custom integration that assigns an **Associated zone** to client connection trackers from each UniFi Network or MikroTik Router source. It uses the native trackers and their existing credentials; it creates no new tracker entities.

## Install with HACS

1. Open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/bygadd/network-tracker-zones` with type **Integration**.
3. Download **Network Tracker Zones** in HACS and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration → Network Tracker Zones**. Choose a source hub/router and an active zone. Add one rule per source.

The integration finds the available sources and zones in dropdowns. You do not need to search logs for IDs.

## Existing trackers

By default, the rule fills unassigned trackers and preserves existing explicit zone choices. To inspect old trackers, open the rule's **Configure → Review mismatched trackers**. The list shows each eligible tracker and its current zone → the zone selected for that source. An unset associated zone effectively means `zone.home`; it is a mismatch only when the source targets another zone.

You can select **all** mismatches or individual trackers. The next screen asks for confirmation before replacing their associated zones. The repair is a one-time action, but repaired trackers then belong to that rule: changing its target zone later updates them too. A later manual change to a tracker remains yours. The integration rechecks each tracker before writing and skips any changed after the preview. It changes only the `device_tracker.associated_zone` option and preserves other tracker options.

Changing a rule's target zone automatically updates trackers still managed by that rule. It does not overwrite prior manual choices; use the review action if you also want to replace those.

## Scope and limits

Supported sources: UniFi Network (`unifi`) and **MikroTik Router** (`mikrotik_router`). The rule matches the exact source config entry and native client identity. Infrastructure trackers, GPS trackers, native MikroTik, and UniFi AP Direct are excluded. The source integration must create its own tracker; this helper cannot restore a tracker lost to an upstream identity collision.

Removing a rule leaves already written associated zones in place. The first state update of a new client may briefly use the default home zone before its association is set. Rule state is stored locally in Home Assistant, including manual exclusions.

The code and focused tests were validated against Home Assistant Core **2026.9.4**. A live installation on your Home Assistant has not yet been verified. For developers, `requirements_test.txt` pins the test environment; no external runtime Python package, polling service, HACS release, or container image is required.

## Български

Добави `https://github.com/bygadd/network-tracker-zones` в **HACS → Custom repositories** с тип **Integration**, изтегли интеграцията, рестартирай Home Assistant и я добави от **Settings → Devices & services**. За всеки UniFi Network хъб или MikroTik Router избери зона от списъка.

Интеграцията запазва ръчно зададените зони. В **Configure → Провери несъответствията** ще видиш кои съществуващи тракери са в друга зона. Избери всички или само някои и потвърди, за да ги коригираш еднократно. Новите тракери се асоциират автоматично.
