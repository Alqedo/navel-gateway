"""Show physical stove connectivity independently of the application session."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from . import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            NavelConnectionSensor(entry.runtime_data, mac)
            for mac in entry.runtime_data.device_macs
        ]
    )


class NavelEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, gateway, mac):
        self._gateway = gateway
        self._mac = mac
        self._attr_unique_id = f"{mac}_connection"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            name=gateway.stoves[mac]["name"],
            manufacturer="Micronova",
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self._gateway.add_listener(self.async_write_ha_state))


class NavelConnectionSensor(NavelEntity, BinarySensorEntity):
    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self):
        return self._gateway.is_stove_connected(self._mac)
