# Modbus Sim

Modbus TCP simulation server for the gateway Modbus driver.

This is a stub. The full reference is written in Phase 8 (documentation site).

- Image: `oitc/modbus-server:2.2.0` (override with `MODBUS_SIM_IMAGE` in `.env`).
- Kind: simulator.
- Network when split: backend.
- Provides: modbus-server.

Preset `.env` values (edit to taste):

- `MODBUS_SIM_PORT=5020`

Steps that cannot be file-seeded and land in `POST-SETUP.md`:

- `device-connection`: The gateway Modbus device connection (unit ID, register mappings) is set up in the gateway UI once the simulator is reachable.
