# Chariot

Cirrus Link Chariot MQTT broker (Sparkplug-aware, web UI).

This is a stub. The full reference is written in Phase 8 (documentation site).

- Image: `cirruslink/chariot:3.0.0` (override with `CHARIOT_IMAGE` in `.env`).
- Kind: mqtt-broker.
- Network when split: backend.
- Provides: mqtt-broker.

Preset `.env` values (edit to taste):

- `CHARIOT_ADMIN_PASSWORD=password`
- `CHARIOT_HTTP_PORT=8090`
- `CHARIOT_MQTT_PORT=1886`

Steps that cannot be file-seeded and land in `POST-SETUP.md`:

- `mqtt-engine-connection`: Linking an Ignition gateway to the broker needs the Cirrus Link MQTT Engine/Transmission module plus an MQTT server endpoint in the gateway, configured once the stack is up.
