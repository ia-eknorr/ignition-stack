# Rabbitmq

RabbitMQ broker with the MQTT plugin and management UI enabled.

This is a stub. The full reference is written in Phase 8 (documentation site).

- Image: `rabbitmq:4.3.1-management` (override with `RABBITMQ_IMAGE` in `.env`).
- Kind: mqtt-broker.
- Network when split: backend.
- Provides: mqtt-broker.

Preset `.env` values (edit to taste):

- `RABBITMQ_USER=ignition`
- `RABBITMQ_PASSWORD=ignition`
- `RABBITMQ_MQTT_PORT=1885`
- `RABBITMQ_MGMT_PORT=15672`

Steps that cannot be file-seeded and land in `POST-SETUP.md`:

- `mqtt-engine-connection`: Linking an Ignition gateway to the broker needs the Cirrus Link MQTT Engine/Transmission module plus an MQTT server endpoint in the gateway, configured once the stack is up.
