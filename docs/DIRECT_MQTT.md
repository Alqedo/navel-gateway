# Direct MQTT

Agua IOT is not required for this mode. Home Assistant still hosts the gateway. The gateway forwards Navel messages and enforces write limits; your client implements device authentication, reads and controls.

## Configure

1. Add Navel Gateway. Enter a name, the stove's LAN IP and Wi-Fi MAC.
2. Redirect the stove's vendor hostname to Home Assistant as described in the README.
3. Open **Configure → Direct MQTT client**, select the stove, and enable access. Enter the IPv4 address of your script host and a password of at least 16 characters.
4. Connect using the settings below. Subscribe before publishing commands.

Additional stoves can be added under **Configure → Add stove manually**. Each stove has its own client password and allowed source IP. Use a unique client ID for each concurrent connection. Avoid multiple controllers changing the same stove at the same time.

| Setting | Value |
| --- | --- |
| Host | Home Assistant LAN IP |
| Port | `1883` |
| Protocol | MQTT 3.1.1, clean session |
| Username | `navel-client-AABBCCDDEEFF`, replace MAC with your stove's 12 uppercase hex digits |
| Password | Password saved in the options form |
| Client ID | Must start with `navel-user-`, followed by a unique suffix |
| Command topic | `navel/AABBCCDDEEFF` |
| Response topic | `master/responses/navel/AABBCCDDEEFF` |
| QoS | 0 or 1; responses are forwarded at QoS 0 |
| Retain | Always false |

Subscribe to both topics: firmware can announce Identity on the command topic. Wildcards and topics belonging to other stoves are rejected. The direct password is separate from the stove registration code and from Agua IOT's internal credentials.

MQTT is unencrypted. Use a trusted LAN, not an internet-facing endpoint. A container's source IP must match the configured allowed IP as seen by Home Assistant. Changing the password or disabling access reloads the gateway and disconnects clients. Leaving the password field blank retains the existing password.

## Navel messages

Commands are JSON envelopes with a unique `mi`, message type `mt`, receiver `r`, sender `s` and payload `pl`. The sender session UUID must remain consistent for that application session. Register offsets and interpretation depend on the stove model.

Before requesting readings, authenticate with `Cmd: Identity`, your stove MAC as `Id`, and its registration code as `Security`. Handle stove Identity and Ping announcements with acknowledgement messages. See [the read-only example](../examples/read_registers.py), which authenticates and requests RAM registers without sending writes.

```sh
pip install aiomqtt==2.5.1
# Set NAVEL_HOST, NAVEL_MAC, NAVEL_PASSWORD and NAVEL_SECURITY privately.
# NAVEL_OFFSETS is a comma-separated list of known RAM offsets for your stove.
python examples/read_registers.py
```

The example requires a connected stove, times out after 30 seconds and never reconnects or retries commands. It prints only register readings. It does not discover registration codes or interpret register values.

## Writes and receipts

`RequestWriting` must include a non-empty unique `mi`, `mt: Q`, and equally sized `Items`, `Values`, `Masks`, `BitData` and `Endianess` arrays. Use register definitions verified for your model. EEPROM writes share the same persistent per-stove limits as all other clients.

After a write, wait for an `mt: G` message with the same `mi` and `pl.Cmd: MqttWriteResult`. `pl.Accepted: true` means the gateway forwarded the command, not that the stove executed it. A negative receipt includes `pl.Reason`. A missing receipt means delivery is uncertain. Do not automatically retry a write. An MQTT PUBACK is not a write permission or execution confirmation.

Allowed application messages are Identity, Ping acknowledgements, RequestReading and RequestWriting. Wi-Fi provisioning and arbitrary vendor commands are not supported. There is no retained state, offline queue or replay.
