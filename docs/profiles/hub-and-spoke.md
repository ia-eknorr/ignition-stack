# Hub-and-spoke profile

One central hub gateway plus N Edge spoke gateways. Spokes default to the
Edge edition. The spoke count drives a RAM advisory:

- green: 1-4 spokes - proceed without prompting.
- yellow: 5-8 spokes - wizard asks for confirmation.
- red: 9+ spokes - requires `--force` non-interactively.

The RAM math is conservative (~1.5 GB per gateway); the wizard surfaces the
estimate alongside available memory so the cost is visible at decision time.

This is a stub. The full guide is written in Phase 8 (documentation site).

- Gateways: 1 hub + N spokes.
- Default database: Postgres.
- Network split: off (hub-and-spoke is typically single-tier).
- Edge role: `spoke` by default; override via `--edge-role`.

Run non-interactively:

```sh
ignition-stack init demo --profile hub-and-spoke --spokes 3
ignition-stack init big-demo --profile hub-and-spoke --spokes 12 --force
```
