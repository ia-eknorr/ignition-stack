---
title: Updating module versions
description: Bump a third-party module or JDBC driver for a new Ignition release with one edit in modules.yaml.
---

# Updating module versions

Every third-party module and JDBC driver the CLI knows how to download, verify, and wire into a stack lives in one file: `modules.yaml` at the repo root. It is the single source of truth a maintainer bumps per Ignition release, validated against the `Catalog` schema in `ignition_stack/catalog/schema.py` at load. Bumping a module is a data edit to that file; you never touch engine code.

## Bumping an entry for a new Ignition release

1. Confirm the third-party module's compatibility with the target
   Ignition 8.3.x (vendor matrix; for Cirrus Link, `docs.chariot.io`).
2. Add the new version string to that entry's `ignition_versions` list.
   Resolution is exact-match, so do not drop the previous version until
   it falls out of support.
3. Download the artifact and compute the hash:
   ```sh
   shasum -a 256 path/to/Module.modl
   ```
4. Set `sha256` to the lowercase hex value. The catalog's sentinel is
   `UNPINNED`; `ignition-stack modules validate` rejects any entry still
   carrying it, which is the gate that prevents shipping a half-bumped
   catalog.
5. If the download URL has changed (vendor changed their path layout),
   update `download_url`. Then:
   ```sh
   ignition-stack modules validate
   ```
   to confirm the URL is reachable and every pinned sha matches.

## Module identifier vs install path

Each `kind: module` entry carries two distinct strings:

- `module_identifier` is the fully-qualified Java identifier inside the
  `.modl` (e.g. `com.cirruslink.mqtt.engine.gateway`). It is what
  Ignition's `GATEWAY_MODULES_ENABLED`, `ACCEPT_MODULE_LICENSES`, and
  `ACCEPT_MODULE_CERTS` env vars accept. Never a path.
- `install_path` is the absolute path inside the gateway container where
  the `.modl` is mounted/copied. Modules go under
  `/usr/local/bin/ignition/user-lib/modules/`, JDBC drivers under
  `/usr/local/bin/ignition/user-lib/jdbc/`.

Confusing the two means the gateway places the file but refuses to
auto-load it. See `scripts/seeding-poc/experiments/module-install/` for
the Phase 1 verification.

## EA-gated modules

The MCP module is Early Access. Its entry has
`requires_manual_download: true` and no `download_url`. Maintainers who
have a copy from the EA survey can point `local_source_path` at the
file on their host; `modules download` will copy from that path into
the cache. End users without the file get a clear "skip + see
POST-SETUP.md" message; nothing breaks.

When the module reaches GA, flip `requires_manual_download` to `false`,
set `download_url`, pin the sha, and the entry becomes ordinary.
