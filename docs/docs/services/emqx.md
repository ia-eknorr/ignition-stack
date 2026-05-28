---
title: EMQX
description: EMQX MQTT broker with a management dashboard.
---

# EMQX

EMQX is an MQTT broker with a built-in management dashboard, useful when a demo benefits from a visual view of clients, topics, and subscriptions.

| | |
| --- | --- |
| Image | `emqx/emqx:6.2.0` (override with `EMQX_IMAGE` in `.env`) |
| Catalog kind | `mqtt-broker` |
| Provides | `mqtt-broker` |
| Requires | nothing |
| Network when split is on | `backend` |

## Preset credentials and ports

The generated `.env` ships these defaults; edit them before `docker compose up`:

- `EMQX_MQTT_PORT=1884` — host port for the MQTT listener.
- `EMQX_DASHBOARD_PORT=18083` — host port for the management dashboard.
- `EMQX_DASHBOARD_PASSWORD=public` — dashboard password (the dashboard user is `admin`).

## Post-setup

- **`mqtt-engine-connection`** — connecting a gateway needs the Cirrus Link MQTT Engine or Transmission module plus an MQTT server endpoint pointed at the broker, configured once the stack is up.
