# Phase 1 seeding POC

Retained scripts that back the Ignition 8.3 seedability investigation. See
[`docs/ignition-seeding-matrix.md`](../../docs/ignition-seeding-matrix.md) for
the matrix these scripts produce evidence for.

## Layout

- `baseline/` boots `inductiveautomation/ignition:8.3.6` with the
  `inductive-automation/template-ignition-project` resources bind-mounted in.
  This gives a working file-seeded gateway to inspect for the matrix rows that
  the template already covers.
- `experiments/` (added as the investigation runs) holds one subdirectory per
  load-bearing experiment (signature validation, plaintext secret, module
  install) with the minimal resource fixture needed to isolate that variable.
- `boot.py` is the automated regression gate required by Phase 1's validation
  criteria: it boots the gateway with the `db-connection` seed and exits
  non-zero if the connection does not appear in the gateway UI.
- `screenshots/` holds the captured UI evidence per matrix row.
- `runs/` (gitignored) holds per-run logs and transient compose state.

## Prerequisites

- Docker (compose v2).
- Python 3.11 with the `poc` extras installed (`uv pip install -e ".[poc]"`).
- Playwright Chromium (`playwright install chromium`).
- A local checkout of `inductive-automation/template-ignition-project` whose
  path you set as `TEMPLATE_PATH` in `baseline/.env`.

## Quickstart

```bash
cp baseline/.env.example baseline/.env
# edit TEMPLATE_PATH if your checkout lives somewhere other than the example
cd baseline && docker compose up -d
# wait for gateway, then point a browser at http://localhost:9088
```
