"""Exercise the standalone endpoint only on loopback and ephemeral ports."""

import asyncio
import importlib.util
import sys
from contextlib import suppress
from pathlib import Path

import aiomqtt
import pytest
import pytest_asyncio

spec = importlib.util.spec_from_file_location(
    "navel_test_broker",
    Path(__file__).parents[1] / "custom_components/navel_gateway/broker.py",
    submodule_search_locations=[
        str(Path(__file__).parents[1] / "custom_components/navel_gateway")
    ],
)
broker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = broker
spec.loader.exec_module(broker)
MAC = "AABBCCDDEEFF"
TOPIC = f"navel/{MAC}"
RESPONSE = f"master/responses/navel/{MAC}"


@pytest_asyncio.fixture
async def gateway():
    endpoint = broker.NavelGateway("127.0.0.1", MAC, port=0, bind_host="127.0.0.1")
    await endpoint.start()
    yield endpoint
    await endpoint.close()


async def stove_connect(gateway, client_id=MAC):
    reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
    connect = (
        broker.mqtt_string("MQTT") + b"\x04\x02\x00\x0a" + broker.mqtt_string(client_id)
    )
    writer.write(broker.packet(0x10, connect))
    await writer.drain()
    assert await asyncio.wait_for(broker.read_packet(reader), 2) == (0x20, b"\x00\x00")
    writer.write(broker.packet(0x82, b"\x00\x01" + broker.mqtt_string(TOPIC) + b"\x00"))
    await writer.drain()
    assert await broker.read_packet(reader) == (0x90, b"\x00\x01\x00")
    return reader, writer


@pytest.mark.asyncio
async def test_actual_mqtt_client_and_stove_bidirectional(gateway):
    reader, writer = await stove_connect(gateway)
    assert gateway.connected
    async with aiomqtt.Client(
        **gateway.connection_info, identifier="navel-ha-test"
    ) as client:
        await client.subscribe(TOPIC)
        await client.subscribe(RESPONSE)
        await client.publish(
            TOPIC, b'{"mt":"Q","pl":{"Cmd":"RequestReading","Items":[1]}}'
        )
        header, body = await asyncio.wait_for(broker.read_packet(reader), 2)
        assert header == 0x30
        assert (
            body
            == broker.mqtt_string(TOPIC)
            + b'{"mt":"Q","pl":{"Cmd":"RequestReading","Items":[1]}}'
        )
        # A QoS 1 stove response is acknowledged, then routed as QoS 0.
        writer.write(
            broker.packet(
                0x32, broker.mqtt_string(RESPONSE) + b"\x00\x02" + b'{"mt":"R"}'
            )
        )
        await writer.drain()
        assert await broker.read_packet(reader) == (0x40, b"\x00\x02")
        reply = await asyncio.wait_for(anext(client.messages), 2)
        assert str(reply.topic) == RESPONSE
        assert reply.payload == b'{"mt":"R"}'
        # The application does not receive an echo of its own command.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(client.messages), 0.05)
        writer.write(broker.packet(0xC0, b""))
        await writer.drain()
        assert await broker.read_packet(reader) == (0xD0, b"")
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_unknown_client_cannot_connect(gateway):
    reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
    body = (
        broker.mqtt_string("MQTT") + b"\x04\x02\x00\x0a" + broker.mqtt_string("unknown")
    )
    writer.write(broker.packet(0x10, body))
    await writer.drain()
    assert await asyncio.wait_for(broker.read_packet(reader), 2) == (0x20, b"\x00\x05")
    assert await reader.read(1) == b""
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_wrong_application_password_rejected(gateway):
    info = {**gateway.connection_info, "password": "invalid-test-password"}
    with pytest.raises(aiomqtt.MqttError):
        async with aiomqtt.Client(**info, identifier="navel-ha-test", timeout=1):
            pytest.fail("Unauthenticated client was accepted")


@pytest.mark.asyncio
async def test_unrelated_topics_are_rejected(gateway):
    async with aiomqtt.Client(
        **gateway.connection_info, identifier="navel-ha-test"
    ) as client:
        results = await client.subscribe("homeassistant/#")
        assert results[0] == 128 or results[0].value == 128


@pytest.mark.asyncio
async def test_duplicate_stove_session_replaces_old_connection(gateway):
    old_reader, old_writer = await stove_connect(gateway)
    _, new_writer = await stove_connect(gateway)
    assert await asyncio.wait_for(old_reader.read(1), 2) == b""
    assert gateway.connected
    old_writer.close()
    new_writer.close()
    await old_writer.wait_closed()
    await new_writer.wait_closed()


@pytest.mark.asyncio
async def test_partial_connect_shutdown_releases_socket(gateway):
    reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
    writer.write(b"\x10\x80")
    await writer.drain()
    port = gateway.port
    await gateway.close()
    with suppress(ConnectionResetError):
        assert await asyncio.wait_for(reader.read(1), 2) == b""
    writer.close()
    with suppress(ConnectionResetError):
        await writer.wait_closed()
    server = await asyncio.start_server(lambda _r, w: w.close(), "127.0.0.1", port)
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b"\x30\xff\xff\xff\xff\x7f", b"\x30\xff\xff\x7f"])
async def test_oversized_or_invalid_lengths_rejected(raw):
    reader = asyncio.StreamReader()
    reader.feed_data(raw)
    with pytest.raises(broker.ProtocolError):
        await broker.read_packet(reader)


@pytest.mark.asyncio
async def test_retained_packet_is_not_forwarded(gateway):
    reader, writer = await stove_connect(gateway)
    writer.write(broker.packet(0x31, broker.mqtt_string(TOPIC) + b"retained"))
    await writer.drain()
    assert await asyncio.wait_for(reader.read(1), 2) == b""
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_multiple_stoves_share_port_with_scoped_client_access():
    other_mac = "112233445566"
    endpoint = broker.NavelGateway(
        stoves=[
            {"stove_ip": "127.0.0.1", "device_mac": MAC},
            {"stove_ip": "127.0.0.1", "device_mac": other_mac},
        ],
        port=0,
        bind_host="127.0.0.1",
    )
    await endpoint.start()
    try:
        async with aiomqtt.Client(
            **endpoint.connection_info_for(MAC), identifier="navel-ha-test"
        ) as client:
            accepted = await client.subscribe(TOPIC)
            assert accepted[0] == 0 or accepted[0].value == 0
            denied = await client.subscribe(f"navel/{other_mac}")
            assert denied[0] == 128 or denied[0].value == 128
        distinct_credentials = (
            endpoint.connection_info_for(MAC)["password"]
            != endpoint.connection_info_for(other_mac)["password"]
        )
        assert distinct_credentials
    finally:
        await endpoint.close()


@pytest.mark.asyncio
async def test_non_ascii_password_is_rejected_cleanly(gateway):
    reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
    body = (
        broker.mqtt_string("MQTT")
        + b"\x04\xc2\x00\x0a"
        + broker.mqtt_string("navel-ha-test")
        + broker.mqtt_string(f"navel-companion-{MAC}")
        + broker.mqtt_string("ungültig")
    )
    try:
        writer.write(broker.packet(0x10, body))
        await writer.drain()
        assert await asyncio.wait_for(broker.read_packet(reader), 2) == (
            0x20,
            b"\x00\x05",
        )
        assert await asyncio.wait_for(reader.read(1), 2) == b""
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header,body",
    [
        (0xA2, b"\x00\x01"),
        (0xA2, b"\x00\x00" + broker.mqtt_string(TOPIC)),
        (0x32, broker.mqtt_string(TOPIC) + b"\x00\x00payload"),
    ],
)
async def test_invalid_packet_identifiers_and_empty_unsubscribe(gateway, header, body):
    reader, writer = await stove_connect(gateway)
    try:
        writer.write(broker.packet(header, body))
        await writer.drain()
        assert await asyncio.wait_for(reader.read(1), 2) == b""
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_gateway_rejects_eeprom_on_wire_and_keeps_reading(gateway):
    import json

    reader, writer = await stove_connect(gateway)
    gateway.guard.policies[MAC]["allow_eeprom_writes"] = False
    async with aiomqtt.Client(
        **gateway.connection_info, identifier="navel-ha-safe"
    ) as client:
        await client.subscribe(RESPONSE)
        payload = {
            "mi": "blocked-write",
            "mt": "Q",
            "pl": {
                "Cmd": "RequestWriting",
                "Items": [32768],
                "Values": [20],
                "Masks": [65535],
                "BitData": [16],
                "Endianess": ["L"],
            },
        }
        await client.publish(TOPIC, json.dumps(payload))
        receipt = json.loads(
            (await asyncio.wait_for(anext(client.messages), 2)).payload
        )
        assert receipt["pl"]["Accepted"] is False
        assert "disabled" in receipt["pl"]["Reason"]
        # The next frame delivered to the stove must be the read, never the write.
        read = b'{"mt":"Q","pl":{"Cmd":"RequestReading","Items":[1]}}'
        await client.publish(TOPIC, read)
        header, body = await asyncio.wait_for(broker.read_packet(reader), 2)
        assert header == 0x30
        assert body == broker.mqtt_string(TOPIC) + read
        assert gateway.guard.status(MAC)["writes_last_day"] == 0
    writer.close()
    await writer.wait_closed()
