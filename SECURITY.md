# Security

## Deployment boundaries

Navel firmware uses unencrypted MQTT. Keep the endpoint on a trusted LAN; do not expose port 1883 to the internet. Registered source addresses and MAC client IDs restrict access but can be impersonated by an attacker on that network. Vendor-shared firmware credentials are not private device secrets.

With user consent, the gateway can store a separate per-stove loopback credential for Agua IOT and configure its generic MQTT settings. These credentials persist across reloads and are included in HA backups. The previous runtime API remains available for existing installations, with independently rotating runtime credentials. Optional direct clients authenticate with a separate password and a fixed source IPv4 address per stove. These passwords persist in Home Assistant config data; protect backups accordingly. Disabling direct access removes its stored credentials and reloads the gateway, disconnecting existing sessions. Topic permissions isolate stoves. The endpoint limits packet sizes, handshakes, connections and idle sessions. It does not support general broker use, retained messages or persistent sessions.

Only Identity, Ping acknowledgements, register reads and protected register writes are accepted from application clients. Network provisioning commands are excluded. EEPROM protection is enforced before forwarding and requires durable budget storage.

Protect Home Assistant's configuration and backups: they can contain cloud credentials and device registration codes. Do not attach them to issues. Avoid raw MQTT captures, which may contain the same information.

## Reporting

For vulnerabilities, use the repository's **Security → Report a vulnerability** option if enabled. If unavailable, open an issue asking for a private contact without exploit details or secrets. For ordinary bugs, include HA and integration versions, expected behavior, and sanitized error messages.

The project is a development preview. Security fixes target the latest main branch; no older release maintenance policy is promised.

The current stove session’s Identity handshake is held in memory for late application subscribers. It is discarded when that stove session closes. Readings and write commands are never replayed.
