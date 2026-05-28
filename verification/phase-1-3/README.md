# Verification: ignition-stack phases 1-3

End-to-end smoke harness that exercises every behaviour shipped in phases 1, 2, and 3 of `ignition-stack` against real Docker, the real `inductiveautomation/ignition:8.3.6` image, and the actual files the CLI emits. This is a developer-facing harness, not CI. It complements the unit tests under `tests/` (which only cover the generator in isolation).

## How to run

From the repo root:

```sh
bash verification/phase-1-3/run-test.sh
```

The harness:
- creates `verification/phase-1-3/.venv/` and `pip install -e .` from the working-tree branch
- runs the `modules` (phase 3) checks first because they are fast and don't need Docker
- then `ignition-stack init`, `docker compose up --wait`, polls `/StatusPing` for `RUNNING`, queries `/system/gwinfo` for the platform name and version, confirms `/` redirects past commissioning, and inspects `/data` inside the container for the phase-1 bootstrap signatures (layered resources + deterministic gateway-network UUID)
- writes every check to `results.log` and tears the stack down at the end (containers + named volume removed)

The generated project tree is left at `verification/phase-1-3/generated/smoke/` after a successful run for inspection. It's regenerated on every run and gitignored, since it's just the output of `ignition-stack init` against the bundled `ignition_stack/templates/standalone-postgres/` template.

## Prerequisites

| Tool | Why |
| --- | --- |
| Docker (daemon running) | runs the gateway + Postgres |
| Python 3.11+ | installs the CLI in a local venv |
| Host port 9088 free | the generated compose maps the gateway HTTP port |

First run pulls ~700 MB for `inductiveautomation/ignition:8.3.6` if not cached.

## What each check proves

| Phase | Check | What it proves |
| --- | --- | --- |
| preflight | docker, python3, host port 9088 free, daemon reachable | environment is sane |
| install | `pip install -e .`, `ignition-stack --version`, bare invocation prints help | CLI builds, root callback wiring intact |
| 3 | `modules list` returns 5 catalog entries | catalog loader + pydantic schema accept `modules.yaml` |
| 3 | `modules list --ignition-version 8.3.6` succeeds | version-filter wiring works |
| 3 | `modules validate --skip-network` rejects UNPINNED shas | catalog gate prevents half-bumped releases shipping |
| 3 | `modules download --offline` fails closed with empty cache | offline path does not silently succeed |
| 3 | `modules download` reports skipped-manual for `mcp-module` | EA-gated entry handling works |
| 2 | `ignition-stack init smoke` writes the expected file tree | walking-skeleton generator runs end-to-end |
| 2 | every expected file present, `docker-bootstrap.sh` executable, `.env` correct | generator output is structurally complete |
| 1+2 | `docker compose up --wait` succeeds (db healthy + bootstrap exited 0) | compose health gating works |
| 1+2 | `/StatusPing` returns `{"state":"RUNNING"}` | gateway booted past STARTING |
| 1+2 | `/system/gwinfo` shows `ContextStatus=RUNNING`, `PlatformName=smoke`, `Version=8.3.6` | `-n` compose arg landed; gateway is the expected version |
| 1+2 | `/` redirects to `/Start` (not a commissioning page) | env-driven commissioning succeeded |
| 1+2 | gateway logs contain no `commissioning required` messages | commissioning sanity backstop |
| 1+2 | `config-mode.json` is present inside `/data` in the container | bootstrap layered the template resources |
| 1+2 | `gateway-network/uuid.txt` is non-empty | deterministic UUID writer ran |

A clean run is **25 of 25 PASS** in ~90s on a machine with the `inductiveautomation/ignition:8.3.6` image already cached. See `results.log` for the most recent timestamped output.

## Caveats

- `modules validate --skip-network` is _expected_ to exit non-zero today: `modules.yaml` has every `sha256` set to `UNPINNED`. The harness asserts that failure mode. Once a maintainer pins shas, the assertion in `run-test.sh` flips automatically (it reads `modules.yaml` first to decide which assertion to use).
- `modules download` is exercised only in `--offline` mode. We do not hit `inductiveautomation.com` or maven on every run; that would create flakiness and bandwidth cost for no added signal.
- The stack is torn down on every run (`docker compose down -v`) so reruns are idempotent. The generated project files (`docker-compose.yaml`, `.env`, `scripts/`, `services/`) remain on disk so they can be inspected or brought back up manually.
