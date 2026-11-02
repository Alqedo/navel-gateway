"""Guided setup for one gateway with multiple registered Navel stoves."""

import ipaddress
import re
import socket
import uuid

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import DOMAIN
from .safety import DEFAULT_POLICY, policy_for


def stove_choices(hass):
    """Read names and identities only, never copy cloud credentials or PINs."""
    choices = {}
    for entry in hass.config_entries.async_entries("aguaiot"):
        for device in entry.data.get("ble_bootstrap_devices", []):
            for candidate in (device.get("product_serial"), device.get("id_device")):
                mac = re.sub(r"[:-]", "", str(candidate or "").strip()).upper()
                if re.fullmatch(r"[0-9A-F]{12}", mac):
                    choices[mac] = device.get("name") or mac
                    break
            else:
                try:
                    identity = str(uuid.UUID(str(device.get("id_device"))))
                except ValueError:
                    continue
                choices[identity] = device.get("name") or identity
    return choices


def valid_ip(value):
    address = ipaddress.IPv4Address(value)
    if (
        not address.is_private
        or address.is_loopback
        or address.is_unspecified
        or address.is_multicast
    ):
        raise ValueError("A private LAN address is required")
    return str(address)


def port_available():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("0.0.0.0", 1883))
            listener.listen(1)
    except OSError:
        return False
    return True


class StoveSetup:
    """Shared selection/address steps for initial setup and later changes."""

    _editing = False

    async def _selection(self, user_input=None):
        existing = self.config_entry.data.get("stoves", []) if self._editing else []
        self._choices = stove_choices(self.hass)
        for item in existing:
            self._choices.pop(item.get("agua_device_id"), None)
        # Preserve already registered stoves if the cloud cache is temporarily absent.
        self._choices.update(
            {
                item["device_mac"]: item.get("name") or item["device_mac"]
                for item in existing
            }
        )
        if not self._choices:
            return await self.async_step_manual()
        agua_data = self.hass.data.get("aguaiot", {})
        if not self._editing and (
            not isinstance(agua_data, dict)
            or agua_data.get("navel_gateway_api_version") != 2
        ):
            return await self.async_step_manual()
        errors = {}
        if user_input is not None:
            selected = user_input.get("devices", [])
            if not selected or any(mac not in self._choices for mac in selected):
                errors["base"] = "unknown_stove"
            elif len(set(selected)) > 16:
                errors["base"] = "too_many_stoves"
            elif not self._editing and not await self.hass.async_add_executor_job(
                port_available
            ):
                errors["base"] = "port_in_use"
            else:
                self._selected = list(dict.fromkeys(selected))
                self._pending = []
                self._existing = {item["device_mac"]: item for item in existing}
                return await self.async_step_address()
        return self.async_show_form(
            step_id="stoves" if self._editing else "user",
            last_step=False,
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "manual_setup", default=False
                    ): selector.BooleanSelector(),
                    vol.Required(
                        "devices",
                        default=[item["device_mac"] for item in existing]
                        or list(self._choices),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": mac, "label": name}
                                for mac, name in self._choices.items()
                            ],
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_manual(self, user_input=None):
        existing = (
            list(self.config_entry.data.get("stoves", [])) if self._editing else []
        )
        errors = {}
        if len(existing) >= 16:
            return self.async_abort(reason="too_many_stoves")
        if user_input is not None:
            mac = (
                re.sub(r"[:-]", "", str(user_input.get("device_mac", "")))
                .strip()
                .upper()
            )
            name = str(user_input.get("name", "")).strip()
            if not re.fullmatch(r"[0-9A-F]{12}", mac) or mac in {
                s["device_mac"] for s in existing
            }:
                errors["device_mac"] = "invalid_mac"
            if not name:
                errors["name"] = "invalid_name"
            try:
                address = valid_ip(user_input["stove_ip"])
                if address in {s["stove_ip"] for s in existing}:
                    raise ValueError("Duplicate address")
            except (ValueError, KeyError):
                errors["stove_ip"] = "invalid_ip"
            if (
                not errors
                and not self._editing
                and not await self.hass.async_add_executor_job(port_available)
            ):
                errors["base"] = "port_in_use"
            if not errors:
                self._pending = [
                    *existing,
                    {"device_mac": mac, "stove_ip": address, "name": name},
                ]
                return await self.async_step_network()
        return self.async_show_form(
            step_id="manual",
            errors=errors,
            last_step=False,
            data_schema=vol.Schema(
                {
                    vol.Required("name"): str,
                    vol.Required("stove_ip"): str,
                    vol.Required("device_mac"): str,
                }
            ),
        )

    async def async_step_address(self, user_input=None):
        index = len(self._pending)
        mac = self._selected[index]
        errors = {}
        needs_mac = re.fullmatch(r"[0-9A-F]{12}", mac) is None
        resolved_mac = mac
        if user_input is not None:
            if needs_mac:
                resolved_mac = re.sub(
                    r"[:-]", "", str(user_input.get("device_mac", ""))
                ).upper()
                if (
                    not re.fullmatch(r"[0-9A-F]{12}", resolved_mac)
                    or any(item["device_mac"] == resolved_mac for item in self._pending)
                    or resolved_mac in self._selected
                ):
                    errors["device_mac"] = "invalid_mac"
            try:
                address = valid_ip(user_input["stove_ip"])
            except (ValueError, KeyError):
                errors["stove_ip"] = "invalid_ip"
            if not errors:
                self._pending.append(
                    {
                        **self._existing.get(mac, {}),
                        **({"agua_device_id": mac} if needs_mac else {}),
                        "device_mac": resolved_mac,
                        "stove_ip": address,
                        "name": self._choices[mac],
                    }
                )
                if len(self._pending) == len(self._selected):
                    return await self.async_step_network()
                return await self.async_step_address()
        return self.async_show_form(
            step_id="address",
            last_step=False,
            errors=errors,
            description_placeholders={
                "stove_name": self._choices[mac],
                "position": str(index + 1),
                "total": str(len(self._selected)),
            },
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "stove_ip",
                        default=(user_input or self._existing.get(mac, {})).get(
                            "stove_ip", ""
                        ),
                    ): str,
                    **({vol.Required("device_mac"): str} if needs_mac else {}),
                }
            ),
        )

    async def async_step_network(self, user_input=None):
        if user_input is not None:
            data = {"stoves": self._pending}
            if self._editing:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=data
                )
                return self.async_create_entry(
                    title="", data={"revision": uuid.uuid4().hex}
                )
            await self.async_set_unique_id("navel_gateway")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Navel Gateway", data=data)
        return self.async_show_form(
            step_id="network", data_schema=vol.Schema({}), last_step=True
        )


class NavelGatewayConfigFlow(StoveSetup, ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_gateway")
        if user_input and user_input.get("manual_setup"):
            return await self.async_step_manual()
        return await self._selection(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return NavelGatewayOptionsFlow()


class NavelGatewayOptionsFlow(StoveSetup, OptionsFlowWithReload):
    _editing = True

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init", menu_options=["protection", "stoves", "manual", "mqtt"]
        )

    async def async_step_stoves(self, user_input=None):
        if user_input and user_input.get("manual_setup"):
            return await self.async_step_manual()
        return await self._selection(user_input)

    async def async_step_protection(self, user_input=None):
        stoves = self.config_entry.data["stoves"]
        if len(stoves) == 1:
            self._safety_mac = stoves[0]["device_mac"]
            return await self.async_step_safety()
        errors = {}
        if user_input is not None:
            if user_input.get("device") in {item["device_mac"] for item in stoves}:
                self._safety_mac = user_input["device"]
                return await self.async_step_safety()
            errors["base"] = "unknown_stove"
        return self.async_show_form(
            step_id="protection",
            last_step=False,
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("device"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {
                                    "value": item["device_mac"],
                                    "label": item.get("name") or item["device_mac"],
                                }
                                for item in stoves
                            ],
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_safety(self, user_input=None):
        stoves = self.config_entry.data["stoves"]
        stove = next(item for item in stoves if item["device_mac"] == self._safety_mac)
        values = policy_for(stove)
        errors = {}
        if user_input is not None:
            values.update(user_input)
            # NumberSelector produces floats even for step=1.
            try:
                for key in ("max_eeprom_writes_per_hour", "max_eeprom_writes_per_day"):
                    if isinstance(values[key], bool) or float(values[key]) != int(
                        values[key]
                    ):
                        raise ValueError("Use whole numbers")
                    values[key] = int(values[key])
                values = policy_for(values)
            except (ValueError, TypeError, OverflowError):
                errors["base"] = "invalid_limits"
            else:
                data = {
                    **self.config_entry.data,
                    "stoves": [
                        {**item, **values}
                        if item["device_mac"] == self._safety_mac
                        else dict(item)
                        for item in stoves
                    ],
                }
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=data
                )
                return self.async_create_entry(
                    title="", data={"revision": uuid.uuid4().hex}
                )
        return self.async_show_form(
            step_id="safety",
            last_step=True,
            errors=errors,
            description_placeholders={
                "stove_name": stove.get("name") or self._safety_mac
            },
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "allow_eeprom_writes", default=values["allow_eeprom_writes"]
                    ): selector.BooleanSelector(),
                    **{
                        vol.Required(key, default=values[key]): selector.NumberSelector(
                            selector.NumberSelectorConfig(
                                min=1,
                                max=maximum,
                                step=1,
                                mode=selector.NumberSelectorMode.BOX,
                            )
                        )
                        for key, maximum in list(DEFAULT_POLICY.items())[1:]
                    },
                }
            ),
        )

    async def async_step_mqtt(self, user_input=None):
        stoves = self.config_entry.data["stoves"]
        if user_input is not None and user_input.get("device") in {
            s["device_mac"] for s in stoves
        }:
            self._mqtt_mac = user_input["device"]
            return await self.async_step_mqtt_client()
        return self.async_show_form(
            step_id="mqtt",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {
                                    "value": s["device_mac"],
                                    "label": s.get("name") or s["device_mac"],
                                }
                                for s in stoves
                            ],
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_mqtt_client(self, user_input=None):
        stoves = self.config_entry.data["stoves"]
        stove = next(s for s in stoves if s["device_mac"] == self._mqtt_mac)
        errors = {}
        if user_input is not None:
            enabled = user_input.get("mqtt_client_enabled", False)
            values = {"mqtt_client_enabled": enabled}
            if enabled:
                try:
                    address = valid_ip(user_input.get("mqtt_client_ip", ""))
                    if address in {s["stove_ip"] for s in stoves}:
                        raise ValueError("Client cannot be a stove")
                    values["mqtt_client_ip"] = address
                except ValueError:
                    errors["mqtt_client_ip"] = "invalid_ip"
                password = user_input.get("mqtt_client_password") or stove.get(
                    "mqtt_client_password", ""
                )
                if not isinstance(password, str) or len(password) < 16:
                    errors["mqtt_client_password"] = "invalid_password"
                else:
                    values["mqtt_client_password"] = password
            if not errors:
                updated = {
                    k: v for k, v in stove.items() if not k.startswith("mqtt_client_")
                }
                updated.update(values)
                data = {
                    **self.config_entry.data,
                    "stoves": [
                        updated if s["device_mac"] == self._mqtt_mac else dict(s)
                        for s in stoves
                    ],
                }
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=data
                )
                # Credentials belong to config data, never duplicate them in options results.
                return self.async_create_entry(
                    title="", data={"revision": uuid.uuid4().hex}
                )
        return self.async_show_form(
            step_id="mqtt_client",
            errors=errors,
            last_step=True,
            description_placeholders={
                "stove_name": stove.get("name") or self._mqtt_mac,
                "username": f"navel-client-{self._mqtt_mac}",
            },
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "mqtt_client_enabled",
                        default=stove.get("mqtt_client_enabled", False),
                    ): selector.BooleanSelector(),
                    vol.Optional(
                        "mqtt_client_ip", default=stove.get("mqtt_client_ip", "")
                    ): str,
                    vol.Optional("mqtt_client_password"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
        )
