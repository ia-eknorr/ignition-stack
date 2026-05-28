# Standalone profile

One Ignition 8.3 gateway plus an optional SQL database. The default starter
shape and the lightest demo to spin up.

This is a stub. The full guide is written in Phase 8 (documentation site).

- Gateways: 1 (`gateway`).
- Default database: Postgres.
- Network split: off.
- Reverse proxy: optional (the wizard offers `ia-eknorr/traefik-reverse-proxy`).

Run non-interactively:

```sh
ignition-stack init demo --profile standalone
```
