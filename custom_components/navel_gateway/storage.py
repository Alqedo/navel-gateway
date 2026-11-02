"""Durable write reservations using Home Assistant's storage API."""

import json
from pathlib import Path

from homeassistant.helpers.storage import Store


class BudgetStore:
    """Confirm persistence because Store logs some write failures without raising."""

    def __init__(self, hass, entry_id):
        self.hass = hass
        self.store = Store(
            hass, 1, f"navel_gateway.{entry_id}.write_budget", atomic_writes=True
        )

    def _has_unreadable_history(self):
        path = Path(self.store.path)
        return path.exists() or any(path.parent.glob(f"{path.name}.corrupt.*"))

    async def async_load(self):
        history = await self.store.async_load()
        if history is None and await self.hass.async_add_executor_job(
            self._has_unreadable_history
        ):
            raise ValueError(
                "EEPROM history is unreadable; restore it before enabling writes"
            )
        return history

    def _verify(self, history):
        with Path(self.store.path).open() as source:
            saved = json.load(source)
        if saved.get("data") != history:
            raise OSError("EEPROM budget was not saved")

    async def async_save(self, history):
        await self.store.async_save(history)
        await self.hass.async_add_executor_job(self._verify, history)
