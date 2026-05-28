# OPC-UA Sim

Microsoft OPC-PLC OPC-UA simulation server.

This is a stub. The full reference is written in Phase 8 (documentation site).

- Image: `mcr.microsoft.com/iotedge/opc-plc:2.9.9` (override with `OPCUA_SIM_IMAGE` in `.env`).
- Kind: simulator.
- Network when split: backend.
- Provides: opcua-server.

Preset `.env` values (edit to taste):

- `OPCUA_SIM_PORT=50000`

Steps that cannot be file-seeded and land in `POST-SETUP.md`:

- `opc-ua-connection`: The gateway's outbound OPC-UA connection config is file-seedable, but is left to post-setup here so the endpoint URL and security mode can be confirmed against the running simulator.
