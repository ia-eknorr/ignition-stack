---
title: Wire an IIoT pipeline
description: Add a Sparkplug MQTT pipeline to any topology with --iiot — a broker plus the Cirrus Link Transmission/Engine modules wired by role.
---

# Wire an IIoT pipeline

The `--iiot` overlay adds a Sparkplug B MQTT pipeline to any topology: an MQTT broker plus the Cirrus Link **Transmission** and **Engine** modules, wired to the gateways by role:

- **Edge-side gateways** (spokes in hub-and-spoke, frontends in scale-out) get Transmission — they publish Sparkplug messages to the broker.
- **Central gateways** (hub, backend) get Engine — they subscribe and aggregate.
- **Single-gateway stacks** (basic) get both — a self-contained publish/subscribe loop for local development.

The connection seeds are written at generation time: each gateway boots with its Sparkplug identity already set and its broker connection configured, so the pipeline forms with no UI clicks.

## Enable the overlay

Pass `--iiot` to `create` to add the overlay with the default broker (`chariot`):

```sh
ignition-stack create demo --arch hub-and-spoke --iiot
```

To choose a different broker, pass `--iiot-broker <slug>` (which implies `--iiot`):

```sh
ignition-stack create demo --arch hub-and-spoke --iiot-broker emqx
```

The broker slug must be a catalog `mqtt-broker` kind. The available options are:

| Slug | Broker | Notes |
| --- | --- | --- |
| `chariot` | Cirrus Link Chariot | Default. Requires a license — the generated stack ships a trial init container. Live-verified end-to-end on 8.3.6 + Chariot 3.0.0. |
| `emqx` | EMQX CE | Anonymous access; no credentials. Connection seeds are config-shaped (not separately live-verified). |
| `hivemq` | HiveMQ CE | Anonymous access; no credentials. Connection seeds are config-shaped. |
| `rabbitmq` | RabbitMQ | Credentials seeded via embedded JWE. Connection seeds are config-shaped. |

## In the wizard

The IIoT prompt appears after the redundancy step:

```text
? Add IIoT (MQTT/Sparkplug)?  No
```

Accepting opens a broker select defaulting to `chariot`. The overlay is applied to the resolved config before generation.

## What gets generated

For a `hub-and-spoke` stack with `--iiot` (three spokes, hub + broker on the same host):

- The broker service is added to the stack's service registry.
- Each spoke gets the **MQTT Transmission** module (`.modl` in `user-lib/modules/`, `ACCEPT_MODULE_LICENSES` and `ACCEPT_MODULE_CERTS` env vars set).
- The hub gets the **MQTT Engine** module.
- Both modules are pinned with fixed download URLs in `modules.yaml`.
- **Sparkplug identity seeds** are written for every Transmission gateway:
  - Group ID = the project name
  - Edge Node ID = the gateway name
  - Broker connection = `tcp://<broker-host>:<port>` with credentials embedded as a JWE blob
- The hub's **Engine seeds** point the same broker and subscribe to `spBv1.0/#` by default.

The Cirrus Link module identifier (e.g. `com.cirruslink.mqtt.transmission.gateway`) is read from the broker manifest rather than hardcoded, so different broker flavors can wire different module pairs.

## Chariot trial gate

Chariot's MQTT listener will not open without an active license. The generated stack ships a one-shot init container that fires the trial-start REST call at container-up time:

```sh
POST {chariot_base_url}/license?action=start-trial-timer
```

This gives a fresh deployment a working pipeline automatically. For a real deployment, replace the init container with a valid `LICENSE_TYPE` environment variable.

**Ignition trial interaction**: Ignition itself has a ~2-hour platform trial. The Cirrus modules run inside that trial window, so the pipeline will stop working when the trial expires. The reliable reset is the **Reset Trial** button in the Ignition gateway UI (or `POST /data/api/v1/trial`). A volume wipe is always a clean slate. Demo posture only.

## In the composer

The [composer](./wizard.md#the-composer) (reached from the summary's **Tweak** action) exposes an explicit action:

```text
? Composer action?  Add or remove IIoT (MQTT/Sparkplug)
```

This applies or removes the same overlay: one broker instance, Transmission attachments on edge-side roles, Engine on central roles. The broker select appears when wiring.

You can also wire a broker by hand and choose its role:

- **Add a service** → select the broker → attach to gateway(s) → choose `mqtt-transmission` or `mqtt-engine`.
- **Share an existing instance** → select the broker → choose the attachment role.

Every mutation is validated immediately; wiring an Edge gateway as Engine, or wiring two brokers, is rejected up front.

## In a declarative config

The overlay's result in a saved config is a `ServiceInstance` for the broker plus `mqtt-transmission` and `mqtt-engine` attachment roles on the gateways:

```yaml
name: plant
gateways:
  - name: hub
    role: hub
    services:
      - instance: chariot
        role: mqtt-engine
  - name: spoke-1
    role: spoke
    ignition_edition: edge
    services:
      - instance: chariot
        role: mqtt-transmission
service_instances:
  - id: chariot
    service: chariot
```

The attachment role drives which Cirrus module the resolver installs on that gateway. `consumer` (the default) attaches to the broker container's network without installing either module — useful for external subscribers.

Use `create --dry-run --iiot` to dump the resolved config and inspect the full shape before building.
