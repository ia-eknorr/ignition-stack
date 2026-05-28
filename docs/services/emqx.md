# Emqx

EMQX MQTT broker with management dashboard.

This is a stub. The full reference is written in Phase 8 (documentation site).

- Image: `emqx/emqx:6.2.0` (override with `EMQX_IMAGE` in `.env`).
- Kind: mqtt-broker.
- Network when split: backend.
- Provides: mqtt-broker.

Preset `.env` values (edit to taste):

- `EMQX_MQTT_PORT=1884`
- `EMQX_DASHBOARD_PORT=18083`
- `EMQX_DASHBOARD_PASSWORD=public`

Steps that cannot be file-seeded and land in `POST-SETUP.md`:

- `mqtt-engine-connection`: Linking an Ignition gateway to the broker needs the Cirrus Link MQTT Engine/Transmission module plus an MQTT server endpoint in the gateway, configured once the stack is up.
