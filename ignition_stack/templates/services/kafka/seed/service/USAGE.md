# Kafka (single-node KRaft)

A single-node Kafka broker. No ZooKeeper - it runs as its own KRaft controller.

- In-stack bootstrap server (for the gateway and other containers):
  `kafka:9092`.
- Host bootstrap server (for local tools): `localhost:${KAFKA_PORT}`.

Create a topic and produce from the host:

```bash
docker compose exec kafka kafka-topics --bootstrap-server kafka:9092 \
  --create --topic demo --partitions 1 --replication-factor 1
```

Ignition connects via the built-in Kafka connector (Event Streams) - point it
at `kafka:9092`. No third-party module is required.
