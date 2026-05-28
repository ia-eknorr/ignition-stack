---
title: Chariot
description: Cirrus Link Chariot MQTT broker, Sparkplug-aware with a web UI.
---

# Chariot

Chariot is Cirrus Link's MQTT broker and the primary pick for Sparkplug SCADA demos. It pairs naturally with the Cirrus Link MQTT modules on the Ignition side and ships a web console for inspecting connections and topics.

| | |
| --- | --- |
| Image | `cirruslink/chariot:3.0.0` (override with `CHARIOT_IMAGE` in `.env`) |
| Catalog kind | `mqtt-broker` |
| Provides | `mqtt-broker` |
| Requires | nothing |
| Network when split is on | `backend` |

## Preset credentials and ports

The generated `.env` ships these defaults; edit them before `docker compose up`:

- `CHARIOT_ADMIN_PASSWORD=password` — broker admin console password.
- `CHARIOT_HTTP_PORT=8090` — host port for the Chariot web UI.
- `CHARIOT_MQTT_PORT=1886` — host port for the MQTT listener.

Chariot commissions itself from these environment values on first boot, so the broker is ready without a manual setup pass.

## Post-setup

The broker runs on its own; linking an Ignition gateway to it is a manual step:

- **`mqtt-engine-connection`** — connecting a gateway needs the Cirrus Link MQTT Engine or Transmission module plus an MQTT server endpoint pointed at the broker, configured once the stack is up.
