"""EEPROM budgets must survive concurrent clients, reloads and failed storage."""

import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

spec = importlib.util.spec_from_file_location(
    "navel_test_safety",
    Path(__file__).parents[1] / "custom_components/navel_gateway/safety.py",
)
safety = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safety)
MAC = "AABBCCDDEEFF"
OTHER = "112233445566"


def write(offset=32768, **extra):
    return json.dumps(
        {
            "mi": "test-write",
            "mt": "Q",
            "pl": {
                "Cmd": "RequestWriting",
                "Items": [offset],
                "Values": [20],
                "Masks": [65535],
                "BitData": [16],
                "Endianess": ["L"],
                **extra,
            },
        }
    )


@pytest.mark.asyncio
async def test_per_stove_limits_and_ram_reads():
    guard = safety.EepromGuard({MAC: {"allow_eeprom_writes": False}, OTHER: {}})
    with pytest.raises(safety.WriteBlocked, match="disabled"):
        await guard.reserve(MAC, write())
    await guard.reserve(MAC, write(32767))
    await guard.reserve(
        MAC, json.dumps({"mt": "Q", "pl": {"Cmd": "RequestReading", "Items": [32768]}})
    )
    await guard.reserve(OTHER, write())
    assert guard.status(MAC)["writes_last_day"] == 0
    assert guard.status(OTHER)["writes_last_day"] == 1


@pytest.mark.asyncio
async def test_concurrent_clients_cannot_overrun_budget():
    save = AsyncMock()
    guard = safety.EepromGuard({MAC: {"max_eeprom_writes_per_hour": 1}}, persist=save)
    results = await asyncio.gather(
        *(guard.reserve(MAC, write()) for _ in range(8)), return_exceptions=True
    )
    assert sum(isinstance(result, safety.WriteBlocked) for result in results) == 7
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_persisted_budget_survives_restart_and_clock_rollback():
    now = [100000.0]
    save = AsyncMock()
    stoves = {MAC: {"max_eeprom_writes_per_hour": 1, "max_eeprom_writes_per_day": 2}}
    first = safety.EepromGuard(stoves, persist=save, clock=lambda: now[0])
    await first.reserve(MAC, write())
    second = safety.EepromGuard(
        stoves, history=save.call_args.args[0], clock=lambda: now[0]
    )
    now[0] -= 60
    with pytest.raises(safety.WriteBlocked, match="hourly"):
        await second.reserve(MAC, write())
    now[0] += 3660
    await second.reserve(MAC, write())
    now[0] += 3600
    with pytest.raises(safety.WriteBlocked, match="daily"):
        await second.reserve(MAC, write())
    now[0] += 86400
    await second.reserve(MAC, write())
    assert second.status(MAC)["writes_last_day"] == 1


@pytest.mark.asyncio
async def test_storage_failure_blocks_eeprom_but_not_ram():
    guard = safety.EepromGuard(
        {MAC: {}}, persist=AsyncMock(side_effect=OSError("disk failed"))
    )
    with pytest.raises(safety.WriteBlocked, match="save"):
        await guard.reserve(MAC, write())
    with pytest.raises(safety.WriteBlocked, match="save"):
        await guard.reserve(MAC, write())
    await guard.reserve(MAC, write(1))
    assert guard.status(MAC)["state"] == "storage_error"


@pytest.mark.parametrize(
    "payload",
    [
        b"garbage",
        b"[]",
        b'{"mt":"Q"}',
        write(-1),
        write("32768"),
        write(True),
        write(65536),
        write(1, Values=[]),
        write(1, Items=[]),
        write(1, cmd="RequestWriting"),
        json.dumps({"mt": "Q", "pl": {"Cmd": "SetParameters"}}),
    ],
)
def test_malformed_or_unknown_commands_fail_closed(payload):
    with pytest.raises(safety.WriteBlocked):
        safety.EepromGuard({MAC: {}}).check(MAC, payload)


@pytest.mark.parametrize(
    "policy",
    [
        {"max_eeprom_writes_per_hour": 16},
        {"max_eeprom_writes_per_day": 51},
        {"max_eeprom_writes_per_hour": 0},
        {"allow_eeprom_writes": "false"},
        {"max_eeprom_writes_per_day": 1},
    ],
)
def test_policy_cannot_disable_limits(policy):
    with pytest.raises(ValueError):
        safety.EepromGuard({MAC: policy})
