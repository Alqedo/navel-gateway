"""Readable protection status for each stove."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .binary_sensor import NavelEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            NavelProtectionSensor(entry.runtime_data, mac)
            for mac in entry.runtime_data.device_macs
        ]
    )


class NavelProtectionSensor(NavelEntity, SensorEntity):
    _attr_translation_key = "eeprom_protection"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        "ready",
        "disabled",
        "hourly_limit",
        "daily_limit",
        "storage_error",
    ]
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:shield-check-outline"
    # Polling refreshes expired rolling limits even when there is no traffic.
    _attr_should_poll = True

    def __init__(self, gateway, mac):
        super().__init__(gateway, mac)
        self._attr_unique_id = f"{mac}_eeprom_protection"

    @property
    def native_value(self):
        return self._gateway.guard.status(self._mac)["state"]

    @property
    def extra_state_attributes(self):
        return {
            key: value
            for key, value in self._gateway.guard.status(self._mac).items()
            if key != "state"
        }
