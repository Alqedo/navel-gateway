"""Test companion onboarding and lifecycle without binding a real service port."""

import importlib.util
import ipaddress
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

BASE = Path(__file__).parents[1] / "custom_components/navel_gateway"
spec = importlib.util.spec_from_file_location(
    "navel_gateway_test_component",
    BASE / "__init__.py",
    submodule_search_locations=[str(BASE)],
)
component = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = component
spec.loader.exec_module(component)
flow_spec = importlib.util.spec_from_file_location(
    f"{spec.name}.config_flow", BASE / "config_flow.py"
)
flow = importlib.util.module_from_spec(flow_spec)
sys.modules[flow_spec.name] = flow
flow_spec.loader.exec_module(flow)


@pytest.fixture(autouse=True)
def mock_budget_store(monkeypatch):
    store = MagicMock(async_load=AsyncMock(return_value=None), async_save=AsyncMock())
    monkeypatch.setattr(component, "BudgetStore", MagicMock(return_value=store))
    return store


@pytest.mark.parametrize("address", ["172.16.1.20", "192.168.1.20", "10.0.0.8"])
def test_private_stove_addresses(address):
    assert flow.valid_ip(address) == address


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "0.0.0.0", "8.8.8.8", "224.0.0.1", "::1", "bad"]
)
def test_rejects_non_lan_stove_addresses(address):
    with pytest.raises((ValueError, ipaddress.AddressValueError)):
        flow.valid_ip(address)


def test_choices_only_copy_identity_and_name():
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = [
        MagicMock(
            data={
                "ble_bootstrap_devices": [
                    {
                        "product_serial": "aa:bb:cc:dd:ee:ff",
                        "name": "Living room",
                        "device_info": {"reg_code": "dummy-test-only"},
                    },
                    {
                        "product_serial": "not-a-mac",
                        "id_device": "112233445566",
                        "name": "Kitchen",
                    },
                    {"product_serial": "invalid", "id_device": "invalid"},
                ]
            }
        )
    ]
    assert flow.stove_choices(hass) == {
        "AABBCCDDEEFF": "Living room",
        "112233445566": "Kitchen",
    }


@pytest.mark.asyncio
async def test_setup_failure_releases_endpoint(monkeypatch):
    hass, entry = MagicMock(), MagicMock()
    hass.data = {}
    entry.entry_id = "gateway-test"
    entry.data = {"stove_ip": "192.168.1.20", "device_mac": "AABBCCDDEEFF"}
    hass.config_entries.async_forward_entry_setups = AsyncMock(
        side_effect=RuntimeError("platform failed")
    )
    endpoint = MagicMock(start=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(component, "NavelGateway", MagicMock(return_value=endpoint))
    with pytest.raises(RuntimeError, match="platform failed"):
        await component.async_setup_entry(hass, entry)
    endpoint.close.assert_awaited_once()
    assert hass.data[component.DOMAIN] == {}


@pytest.mark.asyncio
async def test_port_conflict_does_not_change_other_services(monkeypatch):
    hass, entry = MagicMock(), MagicMock()
    hass.data = {}
    entry.data = {"stove_ip": "192.168.1.20", "device_mac": "AABBCCDDEEFF"}
    endpoint = MagicMock(start=AsyncMock(side_effect=OSError("port in use")))
    monkeypatch.setattr(component, "NavelGateway", MagicMock(return_value=endpoint))
    with pytest.raises(component.ConfigEntryNotReady, match="port 1883"):
        await component.async_setup_entry(hass, entry)
    assert hass.data == {}
    hass.config_entries.async_forward_entry_setups.assert_not_called()


@pytest.mark.asyncio
async def test_guided_setup_requires_network_confirmation(monkeypatch):
    wizard = flow.NavelGatewayConfigFlow()
    wizard.hass = MagicMock()
    wizard.hass.data = {"aguaiot": {"navel_gateway_api_version": 2}}
    wizard.hass.async_add_executor_job = AsyncMock(return_value=True)
    wizard.hass.config_entries.async_entries.return_value = [
        MagicMock(
            data={
                "ble_bootstrap_devices": [
                    {"product_serial": "AABBCCDDEEFF", "name": "Living room"}
                ]
            }
        )
    ]
    monkeypatch.setattr(wizard, "_async_current_entries", lambda: [])
    monkeypatch.setattr(wizard, "async_set_unique_id", AsyncMock())
    monkeypatch.setattr(wizard, "_abort_if_unique_id_configured", MagicMock())
    result = await wizard.async_step_user({"devices": ["AABBCCDDEEFF"]})
    assert result["step_id"] == "address"
    result = await wizard.async_step_address({"stove_ip": "192.168.1.20"})
    assert result["step_id"] == "network"
    result = await wizard.async_step_network({})
    assert result["data"] == {
        "stoves": [
            {
                "device_mac": "AABBCCDDEEFF",
                "stove_ip": "192.168.1.20",
                "name": "Living room",
            }
        ]
    }


@pytest.mark.asyncio
async def test_setup_without_cached_stove_is_explained(monkeypatch):
    wizard = flow.NavelGatewayConfigFlow()
    wizard.hass = MagicMock()
    wizard.hass.data = {"aguaiot": {"navel_gateway_api_version": 2}}
    wizard.hass.config_entries.async_entries.return_value = []
    monkeypatch.setattr(wizard, "_async_current_entries", lambda: [])
    result = await wizard.async_step_user()
    assert result["reason"] == "setup_aguaiot_first"


@pytest.mark.asyncio
async def test_multiple_stove_addresses_are_saved_individually(monkeypatch):
    wizard = flow.NavelGatewayConfigFlow()
    wizard.hass = MagicMock()
    wizard.hass.data = {"aguaiot": {"navel_gateway_api_version": 2}}
    wizard.hass.async_add_executor_job = AsyncMock(return_value=True)
    wizard.hass.config_entries.async_entries.return_value = [
        MagicMock(
            data={
                "ble_bootstrap_devices": [
                    {"product_serial": "AABBCCDDEEFF", "name": "Living room"},
                    {"product_serial": "112233445566", "name": "Kitchen"},
                ]
            }
        )
    ]
    monkeypatch.setattr(wizard, "_async_current_entries", lambda: [])
    monkeypatch.setattr(wizard, "async_set_unique_id", AsyncMock())
    monkeypatch.setattr(wizard, "_abort_if_unique_id_configured", MagicMock())
    result = await wizard.async_step_user({"devices": ["AABBCCDDEEFF", "112233445566"]})
    assert result["description_placeholders"]["stove_name"] == "Living room"
    result = await wizard.async_step_address({"stove_ip": "192.168.1.20"})
    assert result["description_placeholders"]["stove_name"] == "Kitchen"
    await wizard.async_step_address({"stove_ip": "192.168.1.21"})
    result = await wizard.async_step_network({})
    assert [item["stove_ip"] for item in result["data"]["stoves"]] == [
        "192.168.1.20",
        "192.168.1.21",
    ]


@pytest.mark.asyncio
async def test_options_can_remove_stove_and_preserve_remaining_address(monkeypatch):
    wizard = flow.NavelGatewayOptionsFlow()
    wizard.hass = MagicMock()
    wizard.hass.data = {"aguaiot": {"navel_gateway_api_version": 2}}
    entry = MagicMock(
        data={
            "stoves": [
                {
                    "device_mac": "AABBCCDDEEFF",
                    "stove_ip": "192.168.1.20",
                    "name": "Living room",
                },
                {
                    "device_mac": "112233445566",
                    "stove_ip": "192.168.1.21",
                    "name": "Kitchen",
                },
            ]
        }
    )
    monkeypatch.setattr(type(wizard), "config_entry", property(lambda _self: entry))
    wizard.hass.config_entries.async_entries.return_value = []
    result = await wizard.async_step_stoves({"devices": ["112233445566"]})
    assert result["description_placeholders"]["stove_name"] == "Kitchen"
    await wizard.async_step_address({"stove_ip": "192.168.1.21"})
    result = await wizard.async_step_network({})
    assert [item["device_mac"] for item in result["data"]["stoves"]] == ["112233445566"]
    wizard.hass.config_entries.async_update_entry.assert_called_once_with(
        entry, data=result["data"]
    )


@pytest.mark.asyncio
async def test_safety_options_change_only_selected_stove(monkeypatch):
    wizard = flow.NavelGatewayOptionsFlow()
    wizard.hass = MagicMock()
    entry = MagicMock(
        data={
            "stoves": [
                {
                    "device_mac": "AABBCCDDEEFF",
                    "stove_ip": "192.168.1.20",
                    "name": "Living room",
                },
                {
                    "device_mac": "112233445566",
                    "stove_ip": "192.168.1.21",
                    "name": "Kitchen",
                },
            ]
        }
    )
    monkeypatch.setattr(type(wizard), "config_entry", property(lambda _self: entry))
    result = await wizard.async_step_init()
    assert result["type"] == "menu"
    result = await wizard.async_step_protection({"device": "112233445566"})
    assert result["description_placeholders"] == {"stove_name": "Kitchen"}
    invalid = await wizard.async_step_safety(
        {
            "allow_eeprom_writes": True,
            "max_eeprom_writes_per_hour": 10,
            "max_eeprom_writes_per_day": 5,
        }
    )
    assert invalid["errors"] == {"base": "invalid_limits"}
    wizard.hass.config_entries.async_update_entry.assert_not_called()
    saved = await wizard.async_step_safety(
        {
            "allow_eeprom_writes": False,
            "max_eeprom_writes_per_hour": 3.0,
            "max_eeprom_writes_per_day": 10.0,
        }
    )
    assert saved["data"]["stoves"][0] == entry.data["stoves"][0]
    assert saved["data"]["stoves"][1]["allow_eeprom_writes"] is False
    assert saved["data"]["stoves"][1]["max_eeprom_writes_per_hour"] == 3


@pytest.mark.asyncio
async def test_setup_loads_write_history_before_listening(
    monkeypatch, mock_budget_store
):
    hass, entry = MagicMock(), MagicMock()
    hass.data = {}
    entry.entry_id = "test"
    entry.data = {
        "stoves": [{"device_mac": "AABBCCDDEEFF", "stove_ip": "192.168.1.20"}]
    }
    history = {"AABBCCDDEEFF": [123456]}
    mock_budget_store.async_load.return_value = history
    endpoint = MagicMock(start=AsyncMock(), close=AsyncMock())
    factory = MagicMock(return_value=endpoint)
    monkeypatch.setattr(component, "NavelGateway", factory)
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    await component.async_setup_entry(hass, entry)
    factory.assert_called_once_with(
        stoves=entry.data["stoves"],
        history=history,
        persist=mock_budget_store.async_save,
    )


@pytest.mark.asyncio
async def test_budget_storage_detects_silent_save_failure(tmp_path, monkeypatch):
    storage = sys.modules[f"{spec.name}.storage"]
    import json

    path = tmp_path / "budget"
    path.write_text(json.dumps({"data": {}}))
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))
    store = MagicMock(
        path=str(path), async_save=AsyncMock(), async_load=AsyncMock(return_value=None)
    )
    monkeypatch.setattr(storage, "Store", MagicMock(return_value=store))
    budget = storage.BudgetStore(hass, "test")
    with pytest.raises(OSError, match="not saved"):
        await budget.async_save({"AABBCCDDEEFF": [123456]})
    with pytest.raises(ValueError, match="unreadable"):
        await budget.async_load()
    path.rename(tmp_path / "budget.corrupt.test")
    with pytest.raises(ValueError, match="unreadable"):
        await budget.async_load()


@pytest.mark.asyncio
async def test_cancelled_setup_releases_endpoint(monkeypatch):
    import asyncio

    hass, entry = MagicMock(), MagicMock()
    hass.data = {}
    entry.entry_id = "cancelled"
    entry.data = {
        "stoves": [{"stove_ip": "192.168.1.20", "device_mac": "AABBCCDDEEFF"}]
    }
    hass.config_entries.async_forward_entry_setups = AsyncMock(
        side_effect=asyncio.CancelledError
    )
    endpoint = MagicMock(start=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(component, "NavelGateway", MagicMock(return_value=endpoint))
    with pytest.raises(asyncio.CancelledError):
        await component.async_setup_entry(hass, entry)
    endpoint.close.assert_awaited_once()
    assert hass.data[component.DOMAIN] == {}


@pytest.mark.asyncio
async def test_cloud_uuid_stove_can_be_bound_to_wifi_mac(monkeypatch):
    identity = "12345678-1234-4234-8234-123456789abc"
    wizard = flow.NavelGatewayConfigFlow()
    wizard.hass = MagicMock()
    wizard.hass.data = {"aguaiot": {"navel_gateway_api_version": 2}}
    wizard.hass.async_add_executor_job = AsyncMock(return_value=True)
    wizard.hass.config_entries.async_entries.return_value = [
        MagicMock(
            data={
                "ble_bootstrap_devices": [
                    {
                        "id_device": identity.upper(),
                        "product_serial": "1234567890123",
                        "name": "Stove",
                    }
                ]
            }
        )
    ]
    monkeypatch.setattr(wizard, "_async_current_entries", lambda: [])
    monkeypatch.setattr(wizard, "async_set_unique_id", AsyncMock())
    monkeypatch.setattr(wizard, "_abort_if_unique_id_configured", MagicMock())
    result = await wizard.async_step_user({"devices": [identity]})
    assert result["step_id"] == "address"
    result = await wizard.async_step_address(
        {"stove_ip": "192.168.1.20", "device_mac": "bad"}
    )
    assert result["errors"] == {"device_mac": "invalid_mac"}
    await wizard.async_step_address(
        {"stove_ip": "192.168.1.20", "device_mac": "aa:bb:cc:dd:ee:ff"}
    )
    result = await wizard.async_step_network({})
    assert result["data"]["stoves"][0]["device_mac"] == "AABBCCDDEEFF"
    assert result["data"]["stoves"][0]["agua_device_id"] == identity


@pytest.mark.asyncio
async def test_editing_uuid_stove_preserves_binding_and_protection(monkeypatch):
    identity = "12345678-1234-4234-8234-123456789abc"
    stove = {
        "device_mac": "AABBCCDDEEFF",
        "agua_device_id": identity,
        "stove_ip": "192.168.1.20",
        "name": "Stove",
        "allow_eeprom_writes": False,
        "max_eeprom_writes_per_hour": 3,
        "max_eeprom_writes_per_day": 10,
    }
    wizard = flow.NavelGatewayOptionsFlow()
    wizard.hass = MagicMock()
    entry = MagicMock(data={"stoves": [stove]})
    monkeypatch.setattr(type(wizard), "config_entry", property(lambda _self: entry))
    wizard.hass.config_entries.async_entries.return_value = [
        MagicMock(
            data={"ble_bootstrap_devices": [{"id_device": identity, "name": "Stove"}]}
        )
    ]
    await wizard.async_step_stoves()
    assert list(wizard._choices) == ["AABBCCDDEEFF"]
    await wizard.async_step_stoves({"devices": ["AABBCCDDEEFF"]})
    await wizard.async_step_address({"stove_ip": "192.168.1.21"})
    result = await wizard.async_step_network({})
    assert result["data"]["stoves"] == [{**stove, "stove_ip": "192.168.1.21"}]
