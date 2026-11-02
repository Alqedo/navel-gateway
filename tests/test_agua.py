"""Opt-in configuration must never alter unrelated or partial accounts."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_setup import component

agua = import_module(f"{component.__name__}.agua")
MAC = "AABBCCDDEEFF"


def context():
    account = SimpleNamespace(
        entry_id="account",
        data={
            "ble_bootstrap_devices": [
                {"id_device": "device-one", "product_serial": MAC, "name": "Stove"}
            ]
        },
        options={"language": "DEU", "connection_mode": "connection_cloud"},
    )
    hass = MagicMock()
    hass.data = {"aguaiot": {"mqtt_client_config_version": 1}}
    hass.config_entries.async_entries.return_value = [account]
    hass.config_entries.async_get_entry.return_value = account
    hass.config_entries.async_reload = AsyncMock(return_value=True)
    return hass, account


@pytest.mark.asyncio
async def test_no_opt_in_never_changes_agua():
    hass, account = context()
    entry = SimpleNamespace(data={"stoves": [{"device_mac": MAC}]})
    await agua.apply_links(hass, entry, MagicMock())
    hass.config_entries.async_update_entry.assert_not_called()
    hass.config_entries.async_reload.assert_not_awaited()
    assert account.options["connection_mode"] == "connection_cloud"


def test_partial_account_and_old_integration_are_not_eligible():
    hass, account = context()
    account.data["ble_bootstrap_devices"].append(
        {"id_device": "unregistered", "product_serial": "112233445566"}
    )
    assert agua.eligible_accounts(hass, [{"device_mac": MAC}]) == []
    hass.data = {"aguaiot": {"navel_gateway_api_version": 2}}
    assert agua.eligible_accounts(hass, [{"device_mac": MAC}]) == []


@pytest.mark.asyncio
async def test_opt_in_sets_only_generic_mqtt_options_and_keeps_other_settings():
    hass, account = context()
    stoves = [{"device_mac": MAC, "name": "Stove"}]
    requests = agua.eligible_accounts(hass, stoves)
    configured = agua.prepare_links(stoves, requests)
    assert "agua_mqtt_password" not in stoves[0]
    entry = SimpleNamespace(data={"stoves": configured, "agua_link_requests": requests})
    await agua.apply_links(hass, entry, SimpleNamespace(port=1883))
    calls = hass.config_entries.async_update_entry.call_args_list
    options = calls[0].kwargs["options"]
    assert calls[0].args == (account,)
    assert options["language"] == "DEU"
    assert options["connection_mode"] == "connection_local_mqtt"
    connection = options["mqtt_devices"]["device-one"]
    assert connection["hostname"] == "127.0.0.1"
    assert connection["write_confirmation"] is True
    assert len(connection["password"]) >= 32
    assert set(connection) == {
        "device_mac",
        "hostname",
        "port",
        "username",
        "password",
        "write_confirmation",
        "allow_eeprom_writes",
    }
    assert "agua_link_requests" not in calls[1].kwargs["data"]
    hass.config_entries.async_reload.assert_awaited_once_with("account")


@pytest.mark.asyncio
async def test_stale_opt_in_is_rejected_before_any_changes():
    hass, _account = context()
    stoves = [{"device_mac": MAC}]
    requests = agua.eligible_accounts(hass, stoves)
    hass.config_entries.async_entries.return_value = []
    entry = SimpleNamespace(
        data={
            "stoves": agua.prepare_links(stoves, requests),
            "agua_link_requests": requests,
        }
    )
    with pytest.raises(agua.HomeAssistantError):
        await agua.apply_links(hass, entry, MagicMock())
    hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_failed_reload_keeps_pending_request_and_client_protection():
    hass, account = context()
    account.options["mqtt_devices"] = {"device-one": {"allow_eeprom_writes": False}}
    hass.config_entries.async_reload.return_value = False
    stoves = [{"device_mac": MAC}]
    requests = agua.eligible_accounts(hass, stoves)
    entry = SimpleNamespace(
        data={
            "stoves": agua.prepare_links(stoves, requests),
            "agua_link_requests": requests,
        }
    )
    with pytest.raises(agua.HomeAssistantError, match="could not reload"):
        await agua.apply_links(hass, entry, SimpleNamespace(port=1883))
    hass.config_entries.async_update_entry.assert_called_once()
    options = hass.config_entries.async_update_entry.call_args.kwargs["options"]
    assert options["mqtt_devices"]["device-one"]["allow_eeprom_writes"] is False
    assert entry.data["agua_link_requests"] == requests


@pytest.mark.asyncio
async def test_saved_pending_link_does_not_interrupt_client_startup():
    hass, account = context()
    stoves = [{"device_mac": MAC}]
    requests = agua.eligible_accounts(hass, stoves)
    entry = SimpleNamespace(
        data={
            "stoves": agua.prepare_links(stoves, requests),
            "agua_link_requests": requests,
        }
    )
    gateway = SimpleNamespace(port=1883)
    await agua.apply_links(hass, entry, gateway)
    account.options = hass.config_entries.async_update_entry.call_args_list[0].kwargs[
        "options"
    ]
    hass.config_entries.async_update_entry.reset_mock()
    hass.config_entries.async_reload.reset_mock()
    await agua.apply_links(hass, entry, gateway)
    hass.config_entries.async_reload.assert_not_awaited()
    hass.config_entries.async_update_entry.assert_called_once()
    assert (
        "agua_link_requests"
        not in hass.config_entries.async_update_entry.call_args.kwargs["data"]
    )
