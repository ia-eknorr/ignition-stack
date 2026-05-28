# Scaleout profile

Frontend + backend Ignition gateways networked together via the gateway
network, plus a shared SQL database. The frontend gateway defaults to the
Edge edition - the canonical scaleout shape in the field.

This is a stub. The full guide is written in Phase 8 (documentation site).

- Gateways: 2 (`frontend`, `backend`); both join the `frontend` and `backend` networks.
- Default database: Postgres.
- Network split: on.
- Edge role: `frontend` by default; override via `--edge-role`.
- Reverse proxy: optional.

Run non-interactively:

```sh
ignition-stack init demo --profile scaleout
```
