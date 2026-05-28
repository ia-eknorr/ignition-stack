---
title: RabbitMQ
description: RabbitMQ broker with the MQTT plugin and management UI enabled.
---

# RabbitMQ

RabbitMQ is primarily an AMQP message broker; the bundled `enabled_plugins` turns on its MQTT plugin so it can serve MQTT clients alongside AMQP. Reach for it when a demo spans both protocols.

| | |
| --- | --- |
| Image | `rabbitmq:4.3.1-management` (override with `RABBITMQ_IMAGE` in `.env`) |
| Catalog kind | `mqtt-broker` |
| Provides | `mqtt-broker` |
| Requires | nothing |
| Network when split is on | `backend` |

The `-management` image tag is what supplies the web management UI.

## Preset credentials and ports

The generated `.env` ships these defaults; edit them before `docker compose up`:

- `RABBITMQ_USER=ignition` — broker and management UI user.
- `RABBITMQ_PASSWORD=ignition` — broker and management UI password.
- `RABBITMQ_MQTT_PORT=1885` — host port for the MQTT listener.
- `RABBITMQ_MGMT_PORT=15672` — host port for the management UI.

## Post-setup

- **`mqtt-engine-connection`** — connecting a gateway needs the Cirrus Link MQTT Engine or Transmission module plus an MQTT server endpoint pointed at the broker, configured once the stack is up.
