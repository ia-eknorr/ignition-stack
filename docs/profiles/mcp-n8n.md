# MCP + n8n profile

One Ignition gateway plus the n8n workflow engine, scaffolded with a
`modules/dropin/` directory for the Early-Access Ignition MCP module.

Because the MCP module is gated behind an EA survey, this profile does not
download it. After running `init`:

1. Request the module from the EA survey at
   <https://inductiveautomation.com/early-access>.
2. Drop the resulting `.modl` into `modules/dropin/`.
3. `docker compose up -d`.

The generated `POST-SETUP.md` carries the same steps for quick reference.

This is a stub. The full guide is written in Phase 8 (documentation site).

- Gateways: 1 (`gateway`).
- Services: n8n (always); other catalog services optional.
- Default database: Postgres.
- MCP module: manual drop-in.

Run non-interactively:

```sh
ignition-stack init demo --profile mcp-n8n
```
