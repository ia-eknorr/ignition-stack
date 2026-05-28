---
title: Kafka
description: Single-node Kafka broker (KRaft mode) for Event Streams demos.
---

# Kafka

A single-node Kafka broker running in KRaft mode, for Event Streams demos. Ignition talks to it through its built-in Kafka connector and Event Streams modules, so no third-party `.modl` is needed on the gateway side.

| | |
| --- | --- |
| Image | `confluentinc/cp-kafka:8.2.1` (override with `KAFKA_IMAGE` in `.env`) |
| Catalog kind | `streaming` |
| Provides | `kafka-broker` |
| Requires | nothing |
| Network when split is on | `backend` |

## Preset configuration

The generated `.env` ships these defaults; edit them before `docker compose up`:

- `KAFKA_PORT=9094` — host port for external Kafka clients.
- `KAFKA_CLUSTER_ID=4L6g3nShT-eMCtK--X86sw` — KRaft cluster ID. KRaft mode requires a stable cluster ID; this one is baked in so the single-node broker formats its storage deterministically. Leave it unless you have a reason to rotate it.

## Post-setup

- **`kafka-connector`** — configure Ignition's built-in Kafka connector to point at `kafka:9092` from inside the gateway once the broker is up. Containers on the same network reach the broker at `kafka:9092`; the `9094` host port is for clients outside Docker.
