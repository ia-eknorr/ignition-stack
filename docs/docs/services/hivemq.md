---
title: HiveMQ
description: HiveMQ Community Edition MQTT broker.
---

# HiveMQ

HiveMQ Community Edition is a lightweight MQTT broker that boots with anonymous access. It is the simplest broker in the catalog: no credentials to manage, just an MQTT listener.

| | |
| --- | --- |
| Image | `hivemq/hivemq-ce:2025.5` (override with `HIVEMQ_IMAGE` in `.env`) |
| Catalog kind | `mqtt-broker` |
| Provides | `mqtt-broker` |
| Requires | nothing |
| Network when split is on | `backend` |

## Preset ports

- `HIVEMQ_MQTT_PORT=1883` — host port for the MQTT listener (the standard MQTT port).

HiveMQ CE accepts anonymous connections out of the box, so there is no preset password.

## Post-setup

- **`mqtt-engine-connection`** — connecting a gateway needs the Cirrus Link MQTT Engine or Transmission module plus an MQTT server endpoint pointed at the broker, configured once the stack is up.
