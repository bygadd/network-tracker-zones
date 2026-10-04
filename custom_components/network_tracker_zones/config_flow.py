"""Choose an existing source entry and an active zone; no credentials."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import valid_source, valid_zone
from .const import CONF_SOURCE, CONF_ZONE, DOMAIN, SOURCES


def zone_schema(default=vol.UNDEFINED):
    return vol.Required(CONF_ZONE, default=default), selector.EntitySelector(
        selector.EntitySelectorConfig(domain="zone")
    )


def association_label(value: str | None) -> str:
    """Describe registry presence without claiming to know who set a value."""
    return "unset (defaults to zone.home)" if value is None else f"{value} (explicit)"


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create one rule per existing source config entry."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            source = valid_source(self.hass, user_input[CONF_SOURCE])
            if source is None:
                errors[CONF_SOURCE] = "invalid_source"
            elif not valid_zone(self.hass, user_input[CONF_ZONE]):
                errors[CONF_ZONE] = "invalid_zone"
            else:
                await self.async_set_unique_id(source.entry_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=source.title, data=user_input)
        sources = [
            selector.SelectOptionDict(value=entry.entry_id, label=entry.title)
            for entry in self.hass.config_entries.async_entries()
            if entry.domain in SOURCES
        ]
        zone_key, zone_value = zone_schema()
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required(CONF_SOURCE): selector.SelectSelector(selector.SelectSelectorConfig(options=sources, mode=selector.SelectSelectorMode.DROPDOWN)),
            zone_key: zone_value,
        }), errors=errors)


class OptionsFlow(config_entries.OptionsFlow):
    """Change a rule or review and explicitly repair existing trackers."""

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init", menu_options=["change_zone", "audit"]
        )

    async def async_step_change_zone(self, user_input=None):
        errors = {}
        if user_input is not None:
            if valid_source(self.hass, self.config_entry.data[CONF_SOURCE]) is None:
                errors["base"] = "invalid_source"
            elif not valid_zone(self.hass, user_input[CONF_ZONE]):
                errors[CONF_ZONE] = "invalid_zone"
            else:
                rule = getattr(self.config_entry, "runtime_data", None)
                if not hasattr(rule, "preview_zone_change") or rule.stopped:
                    return self.async_abort(reason="rule_unavailable")
                self._zone_change_original = self.config_entry.options.get(
                    CONF_ZONE, self.config_entry.data[CONF_ZONE]
                )
                self._zone_change_target = user_input[CONF_ZONE]
                if self._zone_change_target == self._zone_change_original:
                    return self.async_abort(reason="zone_unchanged")
                self._zone_change_snapshot = rule.preview_zone_change(
                    self._zone_change_target
                )
                return await self.async_step_confirm_zone_change()
        zone_key, zone_value = zone_schema(self.config_entry.options.get(CONF_ZONE, self.config_entry.data[CONF_ZONE]))
        return self.async_show_form(step_id="change_zone", data_schema=vol.Schema({zone_key: zone_value}), errors=errors)

    async def async_step_confirm_zone_change(self, user_input=None):
        """Show the exact managed trackers affected before changing a rule."""
        snapshot = getattr(self, "_zone_change_snapshot", None)
        if snapshot is None:
            return self.async_abort(reason="rule_unavailable")
        if user_input is not None:
            rule = getattr(self.config_entry, "runtime_data", None)
            if not hasattr(rule, "preview_zone_change") or rule.stopped:
                return self.async_abort(reason="rule_unavailable")
            if (
                self.config_entry.options.get(CONF_ZONE, self.config_entry.data[CONF_ZONE])
                != self._zone_change_original
                or not valid_zone(self.hass, self._zone_change_target)
                or rule.preview_zone_change(self._zone_change_target) != snapshot
            ):
                return self.async_abort(reason="zone_preview_stale")
            return self.async_create_entry(
                title="", data={**self.config_entry.options, CONF_ZONE: self._zone_change_target}
            )
        return self.async_show_form(
            step_id="confirm_zone_change",
            data_schema=vol.Schema({}),
            description_placeholders={
                "count": str(len(snapshot)),
                "target": self._zone_change_target,
                "trackers": "\n".join(snapshot.values()) or "—",
            },
            last_step=True,
        )

    async def async_step_audit(self, user_input=None):
        """Show exact source-scoped mismatches and let the user select repairs."""
        rule = getattr(self.config_entry, "runtime_data", None)
        if not hasattr(rule, "audit") or rule.stopped:
            return self.async_abort(reason="rule_unavailable")
        target = self.config_entry.options.get(CONF_ZONE, self.config_entry.data[CONF_ZONE])
        if (
            valid_source(self.hass, self.config_entry.data[CONF_SOURCE]) is not rule.source
            or not valid_zone(self.hass, target)
        ):
            return self.async_abort(reason="rule_unavailable")
        mismatches = rule.audit(target)
        if not mismatches:
            return self.async_abort(
                reason="no_mismatches" if rule.eligible_count() else "no_trackers"
            )

        by_entity_id = {item["entity_id"]: item for item in mismatches}
        errors = {}
        if user_input is not None:
            selected = user_input.get("trackers", [])
            if "__all__" in selected:
                selected = list(by_entity_id)
            if not selected or any(entity_id not in by_entity_id for entity_id in selected):
                errors["trackers"] = "invalid_selection"
            else:
                self._audit_target = target
                self._audit_snapshot = {
                    by_entity_id[entity_id]["registry_id"]: by_entity_id[entity_id]["current"]
                    for entity_id in selected
                }
                self._audit_names = selected
                return await self.async_step_confirm()

        options = [
            selector.SelectOptionDict(
                value="__all__", label=f"All {len(mismatches)} mismatches"
            )
        ]
        options.extend(
            selector.SelectOptionDict(
                value=item["entity_id"],
                label=f'{item["entity_id"]}: {association_label(item["current"])} → {target}',
            )
            for item in mismatches
        )
        return self.async_show_form(
            step_id="audit",
            data_schema=vol.Schema({
                vol.Required("trackers"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        multiple=True,
                    )
                )
            }),
            description_placeholders={"count": str(len(mismatches)), "target": target},
            errors=errors,
        )

    async def async_step_confirm(self, user_input=None):
        """A separate confirmation prevents the audit itself from writing."""
        snapshot = getattr(self, "_audit_snapshot", None)
        if snapshot is None:
            return self.async_abort(reason="rule_unavailable")
        if user_input is not None:
            rule = getattr(self.config_entry, "runtime_data", None)
            if not hasattr(rule, "async_replace_mismatches") or rule.stopped:
                return self.async_abort(reason="rule_unavailable")
            count = await rule.async_replace_mismatches(self._audit_target, snapshot)
            return self.async_abort(
                reason="repair_complete", description_placeholders={"count": str(count)}
            )
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "count": str(len(snapshot)),
                "target": self._audit_target,
                "trackers": "\n".join(self._audit_names),
            },
            last_step=True,
        )
