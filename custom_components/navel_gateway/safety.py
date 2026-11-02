"""Per-stove EEPROM protection, independent of the application client."""

from __future__ import annotations

import asyncio
import json
import time

DEFAULT_POLICY = {
    "allow_eeprom_writes": True,
    "max_eeprom_writes_per_hour": 15,
    "max_eeprom_writes_per_day": 50,
}


class WriteBlocked(ValueError):
    """A command must not reach the stove."""


def policy_for(stove):
    policy = {key: stove.get(key, default) for key, default in DEFAULT_POLICY.items()}
    if type(policy["allow_eeprom_writes"]) is not bool:
        raise ValueError("Invalid EEPROM permission")
    for key, maximum in list(DEFAULT_POLICY.items())[1:]:
        if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
            raise ValueError("EEPROM limits must stay within the protection ceiling")
    if policy["max_eeprom_writes_per_hour"] > policy["max_eeprom_writes_per_day"]:
        raise ValueError("The hourly limit must not exceed the daily limit")
    return policy


class EepromGuard:
    """Reserve a write budget durably before forwarding any EEPROM command.

    One RequestWriting containing EEPROM offsets counts as one operation.
    Reservations survive failed sends: uncertain delivery must not free a budget.
    """

    def __init__(self, stoves, *, history=None, persist=None, clock=time.time):
        self.policies = {mac: policy_for(stove) for mac, stove in stoves.items()}
        self.history = history if history is not None else {}
        self._persist = persist
        self._clock = clock
        self._lock = asyncio.Lock()
        self.storage_failed = False
        if not isinstance(self.history, dict) or any(
            not isinstance(stamps, list)
            or any(
                type(stamp) not in (int, float) or not 0 <= stamp < float("inf")
                for stamp in stamps
            )
            for stamps in self.history.values()
        ):
            raise ValueError("Invalid EEPROM write history")

    def status(self, mac):
        now = self._clock()
        recent = [stamp for stamp in self.history.get(mac, []) if now - stamp < 86400]
        hourly = sum(now - stamp < 3600 for stamp in recent)
        policy = self.policies[mac]
        state = "ready"
        if self.storage_failed:
            state = "storage_error"
        elif not policy["allow_eeprom_writes"]:
            state = "disabled"
        elif len(recent) >= policy["max_eeprom_writes_per_day"]:
            state = "daily_limit"
        elif hourly >= policy["max_eeprom_writes_per_hour"]:
            state = "hourly_limit"
        return {
            "state": state,
            "writes_last_hour": hourly,
            "writes_last_day": len(recent),
            **policy,
        }

    def check(self, mac, payload):
        """Validate all application messages; return whether they write EEPROM."""
        try:
            obj = json.loads(payload)
        except (ValueError, TypeError, UnicodeError) as err:
            raise WriteBlocked("Invalid Navel command") from err
        if not isinstance(obj, dict) or not isinstance(obj.get("pl"), dict):
            raise WriteBlocked("Invalid Navel command")
        pl = obj["pl"]
        if "Cmd" in pl and "cmd" in pl:
            raise WriteBlocked("Ambiguous Navel command")
        cmd = pl.get("Cmd", pl.get("cmd"))
        if obj.get("mt") == "A" and cmd in ("Identity", "Ping"):
            return False
        if obj.get("mt") != "Q" or cmd not in (
            "Identity",
            "RequestReading",
            "RequestWriting",
        ):
            raise WriteBlocked("Unsupported Navel command")
        if cmd != "RequestWriting":
            return False
        if not isinstance(obj.get("mi"), str) or not obj["mi"] or "cmd" in pl:
            raise WriteBlocked("Invalid register write identity")
        items = pl.get("Items")
        if (
            not isinstance(items, list)
            or not items
            or any(type(item) is not int or not 0 <= item <= 65535 for item in items)
            or any(
                not isinstance(pl.get(key), list) or len(pl[key]) != len(items)
                for key in ("Values", "Masks", "BitData", "Endianess")
            )
        ):
            raise WriteBlocked("Invalid register write")
        if not any(item >= 32768 for item in items):
            return False
        state = self.status(mac)["state"]
        messages = {
            "disabled": "EEPROM writes are disabled for this stove in Navel Gateway.",
            "hourly_limit": "This stove has reached its hourly EEPROM write limit. Try again later.",
            "daily_limit": "This stove has reached its daily EEPROM write limit. Try again later.",
            "storage_error": "Navel Gateway cannot save the write budget. EEPROM writes are blocked.",
        }
        if state != "ready":
            raise WriteBlocked(messages[state])
        return True

    async def reserve(self, mac, payload):
        # A shared persistence lock prevents simultaneous clients from saving
        # snapshots out of order. Budgets themselves are separate per stove.
        async with self._lock:
            if not self.check(mac, payload):
                return
            now = self._clock()
            self.history[mac] = [
                stamp for stamp in self.history.get(mac, []) if now - stamp < 86400
            ]
            self.history[mac].append(now)
            if self._persist is not None:
                try:
                    await self._persist(
                        {key: list(value) for key, value in self.history.items()}
                    )
                except Exception as err:
                    self.storage_failed = True
                    raise WriteBlocked(
                        "Could not save the EEPROM write budget; write blocked."
                    ) from err
