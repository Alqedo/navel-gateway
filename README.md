# Navel Gateway

Local Wi-Fi connectivity for Micronova Navel 2.0 pellet stoves in Home Assistant. One gateway connects up to 16 stoves without a separate Mosquitto installation or manual MQTT credentials.

Use it with your own MQTT client, or connect an installed [Agua IOT](https://github.com/vincentwolsink/home_assistant_micronova_agua_iot) integration for stove controls. Agua IOT is optional.

The gateway detects cached Agua IOT stoves. After registering them, you can confirm that the gateway configures their local MQTT connection. Nothing changes in Agua IOT without this confirmation. Only complete accounts are eligible: all stoves belonging to that account must be registered. Other account settings are preserved.

This opt-in needs an Agua IOT build supporting generic per-device `mqtt_devices` settings, not the currently published upstream release. The gateway writes ordinary connection settings; Agua IOT does not discover or depend on this gateway. Standalone MQTT remains available independently.

## Requirements

- Home Assistant 2026.10.4 or newer; OS and Container installations are supported.
- A compatible Navel 2.0 stove. For direct MQTT, you need its Wi-Fi MAC, registration code and register definitions.
- Stable LAN addresses for Home Assistant and each stove.
- Ability to redirect the stove's vendor hostname through your router or DNS service.
- TCP port 1883 available on Home Assistant. Container users must expose it to the LAN or use host networking.

## Installation

1. Add `https://github.com/Alqedo/navel-gateway` to HACS as a custom **Integration** repository, install it, and restart Home Assistant. Alternatively, copy `custom_components/navel_gateway` into your HA configuration's `custom_components` directory.
2. Open **Settings → Devices & services → Add integration → Navel Gateway**.
3. Enter the stove name, LAN address and Wi-Fi MAC. If compatible Agua IOT data is available, you can select stoves from that list instead.
4. Redirect the vendor hostname, for example `remote.mcz.it`, to Home Assistant's LAN address. Keep an existing redirect if it already points there.
5. To connect detected Agua IOT stoves, confirm the optional connection change on the final setup screen. For your own scripts, open **Configure → Direct MQTT client**, select a stove, and enable access with the script host's IP and a password of at least 16 characters. See [Direct MQTT](docs/DIRECT_MQTT.md). Agua IOT can also be configured manually through its neutral MQTT settings.

Router configuration is manual. The gateway does not change your network or switch on a stove. When using Agua IOT, Local Wi-Fi applies to the entire account entry; all stoves in that entry need a configured local connection. Users with an existing MQTT broker can use Agua IOT's external-broker path instead.

If migrating from an older embedded endpoint, back up your HA configuration and update both integrations together. Two services cannot listen on port 1883 simultaneously. Do not reset an existing write history to bypass its limits.

## Everyday use

Each stove has a connection sensor and an EEPROM protection sensor. Open **Configure** to manage stove addresses or adjust write protection separately for each stove.

The interface supports English, German, French, Dutch and Portuguese. Integration icons are bundled locally, including a high-resolution gateway icon.

## Write protection

EEPROM writes, offsets `>= 32768`, are limited to 15 operations per rolling hour and 50 per rolling day by default. You can lower either limit or block EEPROM writes entirely. One command counts as one operation even when it contains several registers. Reads and RAM writes do not consume the budget.

Budgets are shared across application clients but isolated per stove. They are persisted and verified before forwarding. Unreadable history prevents startup; storage failures block EEPROM writes. Failed or uncertain sends retain their reservation. Any protections enforced by an application client also apply.

A positive gateway receipt confirms forwarding, not execution by the stove. Commands are never automatically replayed. These limits reduce write frequency; they do not establish the EEPROM's physical endurance.

## Troubleshooting

- **No stoves listed:** use manual setup with the stove's IP and Wi-Fi MAC. Agua IOT is not required.
- **Port already used:** use your existing broker through Agua IOT or resolve the conflict before enabling the gateway.
- **Disconnected stove:** check its address, MAC, DNS redirect and access to TCP 1883. Reconnection can take time after a restart.
- **Connected but no readings:** check your client's Navel authentication and register request, or Agua IOT compatibility and cached metadata. An MQTT connection alone does not prove application authentication.
- **Write blocked:** inspect the protection sensor and the per-stove settings. Do not delete stored budgets to clear a limit.

## Security and verification

The endpoint is for a trusted LAN, with no internet port forwarding. Stove sessions are restricted by source IP and MAC client ID; these are not cryptographic device authentication. Agua IOT uses generated per-stove credentials over loopback. Direct MQTT access is disabled by default and requires a per-stove password and an allowed client IP. See [SECURITY.md](SECURITY.md) for boundaries and reporting.

Automated tests cover protocol validation, multi-stove isolation, setup, persistence failures, write limits, translations and HA branding. A single physical stove has been tested for live readings and reconnection. Direct-client access is tested with simulated devices. Physical write execution, broader firmware compatibility remain unverified.

## Development

Use Python 3.14:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
ruff format --check .
pytest -q
```

Tests use simulated devices and loopback sockets. Use Conventional Commit messages such as `fix: preserve stove connection after reload`. Include regression tests for protocol or safety changes; never include credentials, raw Identity messages or HA storage dumps in reports.

## License

[MIT](LICENSE) · [Alqedo](https://alqedo.com)

Not affiliated with Micronova or stove manufacturers.
