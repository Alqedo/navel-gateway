"""Restricted Navel MQTT endpoint, independent of Home Assistant.

This is a companion endpoint for a registered stove and local application clients,
not a general purpose broker. It supports MQTT 3.1.1 with QoS 0/1, no retention,
no persistent sessions, and only that stove's two Navel topics.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field

from .safety import EepromGuard, WriteBlocked, policy_for

_LOGGER = logging.getLogger(__name__)

MAX_PACKET_SIZE = 65536
MAX_CONNECTIONS = 64


class ProtocolError(ValueError):
    """Invalid or unsupported MQTT packet."""


def mqtt_string(value: str) -> bytes:
    """Encode an MQTT UTF-8 string."""
    encoded = value.encode()
    return len(encoded).to_bytes(2, "big") + encoded


def packet(header: int, body: bytes) -> bytes:
    """Encode a bounded MQTT packet."""
    length = len(body)
    if length > MAX_PACKET_SIZE:
        raise ProtocolError("Packet exceeds endpoint limit")
    remaining = bytearray()
    while True:
        digit = length % 128
        length //= 128
        remaining.append(digit | (128 if length else 0))
        if not length:
            break
    return bytes([header]) + bytes(remaining) + body


async def read_packet(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Read an MQTT frame without unbounded allocations."""
    header = (await reader.readexactly(1))[0]
    length = 0
    multiplier = 1
    for _ in range(4):
        digit = (await reader.readexactly(1))[0]
        length += (digit & 127) * multiplier
        if length > MAX_PACKET_SIZE:
            raise ProtocolError("Packet exceeds endpoint limit")
        if not digit & 128:
            return header, await reader.readexactly(length)
        multiplier *= 128
    raise ProtocolError("Invalid MQTT length")


class Fields:
    """Bounds checked MQTT payload reader."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, length: int) -> bytes:
        if self.pos + length > len(self.data):
            raise ProtocolError("Truncated MQTT field")
        result = self.data[self.pos : self.pos + length]
        self.pos += length
        return result

    def number(self) -> int:
        return int.from_bytes(self.take(2), "big")

    def string(self) -> str:
        result = self.take(self.number()).decode("utf-8")
        if "\x00" in result:
            raise ProtocolError("Invalid MQTT string")
        return result

    def finished(self) -> None:
        if self.pos != len(self.data):
            raise ProtocolError("Unexpected MQTT fields")


@dataclass(eq=False)
class Session:
    """One in-memory, nonpersistent MQTT session."""

    writer: asyncio.StreamWriter
    client_id: str
    stove: bool
    keepalive: int
    device_mac: str
    subscriptions: set[str] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, header: int, body: bytes) -> None:
        async with self.lock:
            self.writer.write(packet(header, body))
            await asyncio.wait_for(self.writer.drain(), 5)


class NavelGateway:
    """One endpoint for registered stoves and scoped, loopback-only HA clients."""

    api_version = 2

    def __init__(
        self,
        stove_ip=None,
        device_mac=None,
        *,
        stoves=None,
        port=1883,
        bind_host="0.0.0.0",
        history=None,
        persist=None,
    ):
        configured = (
            stoves
            if stoves is not None
            else [{"stove_ip": stove_ip, "device_mac": device_mac}]
        )
        if not 1 <= len(configured) <= 16:
            raise ValueError("Configure between one and sixteen Navel stoves")
        self.stoves = {}
        for item in configured:
            mac = str(item["device_mac"]).upper().replace(":", "").replace("-", "")
            if (
                len(mac) != 12
                or any(c not in "0123456789ABCDEF" for c in mac)
                or mac in self.stoves
            ):
                raise ValueError("Each stove needs a unique 12 digit Navel MAC")
            self.stoves[mac] = {
                "stove_ip": str(ipaddress.IPv4Address(item["stove_ip"])),
                "name": item.get("name") or mac,
                "agua_device_id": item.get("agua_device_id"),
                **policy_for(item),
            }
        self._direct_clients = {}
        for item in configured:
            if item.get("mqtt_client_enabled", False):
                mac = str(item["device_mac"]).upper().replace(":", "").replace("-", "")
                address = ipaddress.IPv4Address(item["mqtt_client_ip"])
                password = item.get("mqtt_client_password", "")
                if (
                    not address.is_private
                    or address.is_unspecified
                    or address.is_multicast
                ):
                    raise ValueError(
                        "Direct MQTT clients require a private IPv4 address"
                    )
                if str(address) in {
                    stove["stove_ip"] for stove in self.stoves.values()
                }:
                    raise ValueError("Direct MQTT client must not use a stove address")
                if not isinstance(password, str) or len(password) < 16:
                    raise ValueError(
                        "Direct MQTT password must contain at least 16 characters"
                    )
                self._direct_clients[mac] = (str(address), password)
        self._agua_passwords = {
            str(item["device_mac"]).upper().replace(":", "").replace("-", ""): item[
                "agua_mqtt_password"
            ]
            for item in configured
            if item.get("agua_mqtt_password")
        }
        self.guard = EepromGuard(self.stoves, history=history, persist=persist)
        self.device_macs = tuple(self.stoves)
        self.device_mac = self.device_macs[0]
        self.port = port
        self.bind_host = bind_host
        self._passwords = {mac: secrets.token_urlsafe(32) for mac in self.stoves}
        self._server = None
        self._sessions = set()
        self._identities = {}
        self._tasks = set()
        self._writers = set()
        self._listeners = set()
        self._closing = False

    @staticmethod
    def topics_for(mac):
        return {f"navel/{mac}", f"master/responses/navel/{mac}"}

    @property
    def connected(self):
        return any(self.is_stove_connected(mac) for mac in self.stoves)

    def is_stove_connected(self, mac):
        return any(
            s.stove and s.device_mac == mac and not s.writer.is_closing()
            for s in self._sessions
        )

    def connection_info_for(self, mac):
        """Private, device-scoped runtime credentials, never logged or exported."""
        return {
            "hostname": "127.0.0.1",
            "port": self.port,
            "username": f"navel-companion-{mac}",
            "password": self._passwords[mac],
        }

    @property
    def connection_info(self):
        """Connection data for a single-stove caller."""
        return self.connection_info_for(self.device_mac)

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    async def start(self) -> None:
        if self._server is not None:
            return
        self._closing = False
        self._server = await asyncio.start_server(
            self._accept, self.bind_host, self.port
        )
        self.port = self._server.sockets[0].getsockname()[1]

    def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        address = str(ipaddress.ip_address(peer[0])) if peer else ""
        if (
            self._closing
            or address
            not in {
                "127.0.0.1",
                *(item["stove_ip"] for item in self.stoves.values()),
                *(item[0] for item in self._direct_clients.values()),
            }
            or len(self._writers) >= MAX_CONNECTIONS
        ):
            writer.close()
            return
        self._writers.add(writer)
        task = asyncio.create_task(self._handle(reader, writer, address))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _connect(
        self, body: bytes, writer: asyncio.StreamWriter, address: str
    ) -> Session:
        fields = Fields(body)
        if fields.string() != "MQTT" or fields.take(1) != b"\x04":
            raise ProtocolError("Only MQTT 3.1.1 is supported")
        flags = fields.take(1)[0]
        if flags & 1 or (flags & 64 and not flags & 128):
            raise ProtocolError("Invalid CONNECT flags")
        keepalive = fields.number()
        client_id = fields.string()
        if flags & 4:
            if flags & 24 == 24:
                raise ProtocolError("Invalid will QoS")
            fields.string()  # Will topic, deliberately never forwarded or retained.
            fields.take(fields.number())
        elif flags & 56:
            raise ProtocolError("Invalid will flags")
        username = fields.string() if flags & 128 else ""
        password = fields.string() if flags & 64 else ""
        fields.finished()
        stove = (
            client_id in self.stoves and address == self.stoves[client_id]["stove_ip"]
        )
        mac = client_id if stove else username.removeprefix("navel-companion-")
        local_client = (
            address == "127.0.0.1"
            and client_id.startswith("navel-ha-")
            and mac in self._passwords
            and username == f"navel-companion-{mac}"
            and hmac.compare_digest(password.encode(), self._passwords[mac].encode())
        )
        direct_mac = username.removeprefix("navel-client-")
        direct = self._direct_clients.get(direct_mac)
        direct_client = (
            direct is not None
            and address == direct[0]
            and username == f"navel-client-{direct_mac}"
            and client_id.startswith("navel-user-")
            and hmac.compare_digest(password.encode(), direct[1].encode())
        )
        agua_mac = username.removeprefix("navel-agua-")
        agua_client = (
            address == "127.0.0.1"
            and client_id.startswith("navel-user-")
            and agua_mac in self._agua_passwords
            and username == f"navel-agua-{agua_mac}"
            and hmac.compare_digest(
                password.encode(), self._agua_passwords[agua_mac].encode()
            )
        )
        if direct_client:
            mac = direct_mac
        elif agua_client:
            mac = agua_mac
        if not stove and not local_client and not direct_client and not agua_client:
            raise ProtocolError("Connection not authorized")
        # Shared firmware credentials are not a device secret. Stove connections
        # are constrained by their registered IP and client ID instead.
        return Session(writer, client_id, stove, keepalive, mac)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, address: str
    ) -> None:
        session = None
        try:
            async with asyncio.timeout(10):
                header, body = await read_packet(reader)
            if header != 0x10:
                raise ProtocolError("CONNECT required")
            try:
                session = self._connect(body, writer, address)
            except ProtocolError:
                writer.write(packet(0x20, b"\x00\x05"))
                await writer.drain()
                raise
            # MQTT requires replacing an existing session with the same client ID.
            for old in tuple(self._sessions):
                if old.client_id == session.client_id:
                    old.writer.close()
            self._sessions.add(session)
            await session.send(0x20, b"\x00\x00")
            if session.stove:
                self._notify()
            while True:
                timeout = max(10, session.keepalive * 1.5) if session.keepalive else 300
                async with asyncio.timeout(timeout):
                    header, body = await read_packet(reader)
                if header == 0xE0 and not body:
                    break
                await self._process(session, header, body)
        except ProtocolError as err:
            _LOGGER.debug("Navel protocol rejected: %s", err)
        except (
            WriteBlocked,
            UnicodeError,
            asyncio.IncompleteReadError,
            OSError,
            TimeoutError,
        ):
            # Do not log MQTT payloads, CONNECT credentials or Identity data.
            _LOGGER.debug("Navel connection closed or rejected")
        finally:
            if session is not None:
                self._sessions.discard(session)
                if session.stove:
                    identity = self._identities.get(session.device_mac)
                    if identity and identity[0] is session:
                        self._identities.pop(session.device_mac, None)
                    self._notify()
            self._writers.discard(writer)
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 2)
            except (OSError, TimeoutError):
                _LOGGER.debug("Navel socket closed during cleanup")

    async def _process(self, session: Session, header: int, body: bytes) -> None:
        fields = Fields(body)
        if header == 0xC0 and not body:
            await session.send(0xD0, b"")
        elif header == 0x82:
            identifier = fields.take(2)
            results = bytearray()
            added = set()
            while fields.pos < len(body):
                topic = fields.string()
                qos = fields.take(1)[0]
                if topic in self.topics_for(session.device_mac) and qos <= 1:
                    if topic not in session.subscriptions:
                        added.add(topic)
                    session.subscriptions.add(topic)
                    results.append(0)
                else:
                    results.append(0x80)
            if not results or identifier == b"\x00\x00":
                raise ProtocolError("Invalid subscription")
            await session.send(0x90, identifier + results)
            identity = self._identities.get(session.device_mac)
            if not session.stove and identity:
                source, topic, payload = identity
                if topic in added and not source.writer.is_closing():
                    # A late client needs the current stove session's handshake.
                    # Never replay readings, writes or data from a closed session.
                    await session.send(0x30, mqtt_string(topic) + payload)
        elif header == 0xA2:
            identifier = fields.take(2)
            if identifier == b"\x00\x00" or fields.pos == len(body):
                raise ProtocolError("Invalid unsubscription")
            while fields.pos < len(body):
                session.subscriptions.discard(fields.string())
            await session.send(0xB0, identifier)
        elif header >> 4 == 3:
            qos = (header >> 1) & 3
            if qos > 1 or header & 1:
                raise ProtocolError("Retained and QoS 2 messages are not supported")
            topic = fields.string()
            if topic not in self.topics_for(session.device_mac):
                raise ProtocolError("Topic not permitted")
            if not session.stove and topic != f"navel/{session.device_mac}":
                raise ProtocolError(
                    "Application clients can only publish stove commands"
                )
            identifier = fields.take(2) if qos else None
            if identifier == b"\x00\x00":
                raise ProtocolError("Invalid publish identifier")
            payload = body[fields.pos :]
            if session.stove:
                try:
                    obj = json.loads(payload)
                    if (
                        isinstance(obj, dict)
                        and obj.get("mt") == "C"
                        and isinstance(obj.get("pl"), dict)
                        and obj["pl"].get("Cmd", obj["pl"].get("cmd")) == "Identity"
                    ):
                        self._identities[session.device_mac] = (session, topic, payload)
                except (ValueError, TypeError, UnicodeError):
                    pass
            targets = [
                target
                for target in tuple(self._sessions)
                if target is not session
                and target.stove != session.stove
                and target.device_mac == session.device_mac
                and topic in target.subscriptions
                and not target.writer.is_closing()
            ]
            if _LOGGER.isEnabledFor(logging.DEBUG):
                try:
                    diagnostic = json.loads(payload)
                    command = diagnostic.get("pl", {}).get("Cmd") or diagnostic.get(
                        "pl", {}
                    ).get("cmd")
                    if command not in (
                        "Identity",
                        "Ping",
                        "RequestReading",
                        "RequestWriting",
                    ):
                        command = "other"
                    _LOGGER.debug(
                        "Navel route: stove=%s command=%s recipients=%s",
                        session.stove,
                        command,
                        len(targets),
                    )
                except (ValueError, TypeError, AttributeError, UnicodeError):
                    pass
            writing = False
            if not session.stove:
                try:
                    self.guard.check(session.device_mac, payload)
                    obj = json.loads(payload)
                    writing = obj["pl"].get("Cmd") == "RequestWriting"
                    if writing and not targets:
                        raise WriteBlocked(
                            "The stove is disconnected. No write was sent."
                        )
                    await self.guard.reserve(session.device_mac, payload)
                except WriteBlocked as err:
                    if identifier is not None:
                        await session.send(0x40, identifier)
                    await self._write_receipt(session, payload, False, str(err))
                    return
                finally:
                    self._notify()
            if identifier is not None:
                await session.send(0x40, identifier)
            delivered = False
            for target in targets:
                if (
                    target is not session
                    and target.stove != session.stove
                    and target.device_mac == session.device_mac
                    and topic in target.subscriptions
                ):
                    try:
                        await target.send(0x30, mqtt_string(topic) + payload)
                        delivered = True
                    except (OSError, TimeoutError):
                        target.writer.close()
            if writing:
                await self._write_receipt(
                    session,
                    payload,
                    delivered,
                    ""
                    if delivered
                    else "Stove connection lost. Delivery is uncertain; the write was not retried.",
                )
        elif header == 0x40 and len(body) == 2:
            return
        else:
            raise ProtocolError("Unsupported MQTT packet")

    async def _write_receipt(self, session, payload, accepted, reason):
        try:
            obj = json.loads(payload)
        except (ValueError, TypeError, UnicodeError):
            return
        if not isinstance(obj, dict) or not isinstance(obj.get("mi"), str):
            return
        response = json.dumps(
            {
                "mi": obj["mi"],
                "mt": "G",
                "pl": {
                    "Cmd": "MqttWriteResult"
                    if session.client_id.startswith("navel-user-")
                    else "GatewayWriteResult",
                    "Accepted": accepted,
                    "Reason": reason,
                },
            }
        ).encode()
        await session.send(
            0x30, mqtt_string(f"master/responses/navel/{session.device_mac}") + response
        )

    async def close(self) -> None:
        self._closing = True
        server, self._server = self._server, None
        if server is not None:
            server.close()
        for writer in tuple(self._writers):
            writer.close()
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        if server is not None:
            await server.wait_closed()
        self._sessions.clear()
        self._writers.clear()
        self._listeners.clear()
