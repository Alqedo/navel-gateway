"""Navel endpoint for direct MQTT clients and optional Agua IOT controls."""

import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

from .broker import NavelGateway
from .storage import BudgetStore

DOMAIN = "navel_gateway"
PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start only after the user explicitly configures the companion."""
    stoves = entry.data.get("stoves") or [
        {"stove_ip": entry.data["stove_ip"], "device_mac": entry.data["device_mac"]}
    ]
    store = BudgetStore(hass, entry.entry_id)
    try:
        history = await store.async_load()
        gateway = NavelGateway(stoves=stoves, history=history, persist=store.async_save)
    except (ValueError, OSError, HomeAssistantError) as err:
        raise ConfigEntryNotReady("Cannot restore the EEPROM write budget") from err
    try:
        await gateway.start()
    except OSError as err:
        raise ConfigEntryNotReady(
            "Cannot open the Navel endpoint on port 1883. Check whether another service already uses it."
        ) from err
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = gateway
    entry.runtime_data = gateway

    async def close_on_stop(_event):
        await gateway.close()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, close_on_stop)
    )
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except (Exception, asyncio.CancelledError):
        # Release the listener even if a platform fails during partial setup.
        await gateway.close()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    gateway = hass.data[DOMAIN].pop(entry.entry_id)
    await gateway.close()
    return True
