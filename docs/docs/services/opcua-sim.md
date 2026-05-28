---
title: OPC-UA simulator
description: Microsoft OPC-PLC OPC-UA simulation server.
---

# OPC-UA simulator

Microsoft's OPC-PLC server, the mainstream containerized OPC-UA test server. It exposes an anonymous, unsecured endpoint with simulated nodes, which is ideal for exercising Ignition's outbound OPC-UA client.

| | |
| --- | --- |
| Image | `mcr.microsoft.com/iotedge/opc-plc:2.9.9` (override with `OPCUA_SIM_IMAGE` in `.env`) |
| Catalog kind | `simulator` |
| Provides | `opcua-server` |
| Requires | nothing |
| Network when split is on | `backend` |

## Preset ports

- `OPCUA_SIM_PORT=50000` — host port for the OPC-UA endpoint.

## Post-setup

- **`opc-ua-connection`** — the gateway's outbound OPC-UA connection config is file-seedable in principle (see the [seeding matrix](../reference/seeding-matrix.md)), but it is left to post-setup here so the endpoint URL and security mode can be confirmed against the running simulator. Add the connection in the gateway once the simulator is reachable.
