"""Optional, user-confirmed configuration of an installed Agua IOT client."""

import re
import secrets

from homeassistant.exceptions import HomeAssistantError


def eligible_accounts(hass, stoves):
    """Return complete account mappings; never change a partial account's mode."""
    if hass.data.get("aguaiot", {}).get("mqtt_client_config_version") != 1:
        return []
    accounts = []
    for entry in hass.config_entries.async_entries("aguaiot"):
        devices = entry.data.get("ble_bootstrap_devices", [])
        matches = []
        for device in devices:
            if not isinstance(device.get("id_device"), str) or not device["id_device"]:
                break
            identity = device["id_device"]
            identities = {
                re.sub(r"[:-]", "", str(device.get(key, ""))).upper()
                for key in ("product_serial", "id_device")
            }
            stove = next(
                (
                    s
                    for s in stoves
                    if str(s.get("agua_device_id", "")).lower() == identity.lower()
                    or s["device_mac"] in identities
                ),
                None,
            )
            if stove is None:
                break
            matches.append({"device_id": identity, "device_mac": stove["device_mac"]})
        else:
            if matches:
                accounts.append({"entry_id": entry.entry_id, "devices": matches})
    return accounts


def prepare_links(stoves, accounts):
    """Generate private, stable loopback credentials only after consent."""
    selected = {d["device_mac"] for account in accounts for d in account["devices"]}
    return [
        {
            **s,
            **(
                {
                    "agua_mqtt_password": s.get("agua_mqtt_password")
                    or secrets.token_urlsafe(32)
                }
                if s["device_mac"] in selected
                else {}
            ),
        }
        for s in stoves
    ]


async def apply_links(hass, entry, gateway):
    """Apply a saved opt-in once, after the endpoint and platforms are ready."""
    requests = entry.data.get("agua_link_requests", [])
    if not requests:
        return
    # Revalidate identities and complete coverage at execution time.
    eligible = eligible_accounts(hass, entry.data["stoves"])
    if any(request not in eligible for request in requests):
        raise HomeAssistantError(
            "Agua IOT devices changed. Confirm their connection again in gateway options."
        )
    for request in requests:
        account = hass.config_entries.async_get_entry(request["entry_id"])
        devices = dict(account.options.get("mqtt_devices", {}))
        for device in request["devices"]:
            mac = device["device_mac"]
            stove = next(s for s in entry.data["stoves"] if s["device_mac"] == mac)
            devices[device["device_id"]] = {
                "allow_eeprom_writes": account.options.get("allow_eeprom_writes", True),
                **devices.get(device["device_id"], {}),
                "device_mac": mac,
                "hostname": "127.0.0.1",
                "port": gateway.port,
                "username": f"navel-agua-{mac}",
                "password": stove["agua_mqtt_password"],
                "write_confirmation": True,
            }
        options = {
            **account.options,
            "mqtt_devices": devices,
            "connection_mode": "connection_local_mqtt",
        }
        if options == account.options:
            # A previous attempt already saved these settings. Let HA finish
            # its normal startup/retry instead of cancelling it with a reload.
            continue
        hass.config_entries.async_update_entry(account, options=options)
        # OptionsFlowWithReload is not involved in an external config update.
        if not await hass.config_entries.async_reload(account.entry_id):
            raise HomeAssistantError(
                "Agua IOT could not reload. Check its connection and confirm again."
            )
    data = {
        key: value for key, value in entry.data.items() if key != "agua_link_requests"
    }
    hass.config_entries.async_update_entry(entry, data=data)
