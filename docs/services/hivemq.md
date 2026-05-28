# Hivemq

HiveMQ Community Edition MQTT broker.

This is a stub. The full reference is written in Phase 8 (documentation site).

- Image: `hivemq/hivemq-ce:2025.5` (override with `HIVEMQ_IMAGE` in `.env`).
- Kind: mqtt-broker.
- Network when split: backend.
- Provides: mqtt-broker.

Preset `.env` values (edit to taste):

- `HIVEMQ_MQTT_PORT=1883`

Steps that cannot be file-seeded and land in `POST-SETUP.md`:

- `mqtt-engine-connection`: Linking an Ignition gateway to the broker needs the Cirrus Link MQTT Engine/Transmission module plus an MQTT server endpoint in the gateway, configured once the stack is up.
