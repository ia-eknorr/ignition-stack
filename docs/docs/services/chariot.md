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

- `CHARIOT_ADMIN_PASSWORD=password`: broker **web admin console** password.
- `CHARIOT_HTTP_PORT=8090`: host port for the Chariot web UI.
- `CHARIOT_MQTT_PORT=1886`: host port for the MQTT listener.

Chariot commissions itself from these on first boot.

## The MQTT admin/changeme quirk

MQTT clients authenticate as **`admin` / `changeme`** — a user the image auto-seeds with full ACL, independent of `CHARIOT_ADMIN_PASSWORD`. That env var only sets the **web admin UI** password; no env var changes the MQTT password. The [IIoT overlay's](../guides/iiot-overlay.md) seeded Transmission/Engine connections carry `admin/changeme` as an embedded JWE blob, verified live on 8.3.6 + Chariot 3.0.0.

## License gate

Chariot will not open its MQTT listener without an active license. The IIoT overlay ships a one-shot init container that REST-starts the trial on `up`; see [Chariot trial gate](../guides/iiot-overlay.md#chariot-trial-gate).

## Post-setup

Linking a gateway is automatic when the broker is part of the [IIoT overlay](../guides/iiot-overlay.md) — the Cirrus connections are file-seeded. Adding Chariot on its own (no overlay) leaves the gateway link to you: it needs the MQTT Engine or Transmission module plus a server endpoint pointed at the broker.
