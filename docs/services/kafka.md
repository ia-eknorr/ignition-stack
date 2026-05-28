# Kafka

Single-node Kafka broker (KRaft mode) for Event Streams demos.

This is a stub. The full reference is written in Phase 8 (documentation site).

- Image: `confluentinc/cp-kafka:8.2.1` (override with `KAFKA_IMAGE` in `.env`).
- Kind: streaming.
- Network when split: backend.
- Provides: kafka-broker.

Preset `.env` values (edit to taste):

- `KAFKA_PORT=9094`
- `KAFKA_CLUSTER_ID=4L6g3nShT-eMCtK--X86sw`

Steps that cannot be file-seeded and land in `POST-SETUP.md`:

- `kafka-connector`: Configure Ignition's built-in Kafka connector to point at kafka:9092 in the gateway once the broker is up.
