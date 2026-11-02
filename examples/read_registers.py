"""Authenticate and read known RAM offsets; never write or retry commands."""

import asyncio
import json
import os
import uuid

import aiomqtt


async def main():
    mac = os.environ["NAVEL_MAC"].replace(":", "").replace("-", "").upper()
    if len(mac) != 12 or any(c not in "0123456789ABCDEF" for c in mac):
        raise ValueError("NAVEL_MAC must be a 12-digit MAC address")
    offsets = [int(value) for value in os.environ["NAVEL_OFFSETS"].split(",")]
    if not offsets or any(not 0 <= offset < 32768 for offset in offsets):
        raise ValueError("Choose known RAM offsets between 0 and 32767")
    security = os.environ["NAVEL_SECURITY"]
    session = str(uuid.uuid4())
    topic = f"navel/{mac}"
    async with aiomqtt.Client(
        hostname=os.environ["NAVEL_HOST"],
        port=1883,
        username=f"navel-client-{mac}",
        password=os.environ["NAVEL_PASSWORD"],
        identifier=f"navel-user-{uuid.uuid4().hex}",
    ) as client:

        async def send(kind, payload, message_id=None):
            await client.publish(
                topic,
                json.dumps(
                    {
                        "mi": message_id or str(uuid.uuid4()),
                        "mt": kind,
                        "r": {"rj": "Navel", "rs": "XM"},
                        "s": {"ss": "Apk", "sj": session},
                        "pl": payload,
                    }
                ),
                qos=0,
                retain=False,
            )

        async def authenticate():
            await send("Q", {"Cmd": "Identity", "Id": mac, "Security": security})

        await client.subscribe(topic)
        await client.subscribe(f"master/responses/navel/{mac}")
        await authenticate()
        async with asyncio.timeout(30):
            async for message in client.messages:
                if message.retain:
                    continue
                obj = json.loads(message.payload)
                pl = obj.get("pl", {})
                command = pl.get("Cmd") or pl.get("cmd")
                if obj.get("mt") == "C" and command in ("Identity", "Ping"):
                    await send(
                        "A",
                        {"cmd": command, "Success": True, "Result": 0},
                        obj.get("mi"),
                    )
                    if command == "Identity":
                        await authenticate()
                elif obj.get("mt") == "R" and command == "Identity":
                    await send(
                        "Q",
                        {
                            "Cmd": "RequestReading",
                            "Items": offsets,
                            "Masks": [65535] * len(offsets),
                        },
                    )
                elif obj.get("mt") == "R" and command == "RequestReading":
                    print(
                        json.dumps(
                            {
                                "Items": pl.get("Items", pl.get("items")),
                                "Values": pl.get("Values", pl.get("values")),
                            }
                        )
                    )
                    return


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (TimeoutError, aiomqtt.MqttError):
        raise SystemExit(
            "No reading received. Check connection, authentication and register offsets."
        ) from None
