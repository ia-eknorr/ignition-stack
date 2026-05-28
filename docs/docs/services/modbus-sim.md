---
title: Modbus simulator
description: Modbus TCP simulation server for the gateway Modbus driver.
---

# Modbus simulator

A Modbus TCP simulation server (`oitc/modbus-server`) that serves simulated holding and input registers, for exercising Ignition's Modbus driver without real hardware.

| | |
| --- | --- |
| Image | `oitc/modbus-server:2.2.0` (override with `MODBUS_SIM_IMAGE` in `.env`) |
| Catalog kind | `simulator` |
| Provides | `modbus-server` |
| Requires | nothing |
| Network when split is on | `backend` |

## Preset ports

- `MODBUS_SIM_PORT=5020` — host port for the Modbus TCP listener.

## Post-setup

- **`device-connection`** — the gateway Modbus device connection (unit ID, register mappings) is set up in the gateway UI once the simulator is reachable.
