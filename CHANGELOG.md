# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Disable built-in modules.** `disable_builtins` on a gateway (and
  `init --disable-builtin <slug>`, repeatable, plus a wizard multi-select) turns
  off shipped IA modules such as Vision or SFC. Because the gateway's
  `GATEWAY_MODULES_ENABLED` env var is a strict whitelist (anything unlisted is
  quarantined at boot), the generator inverts the blocklist into "enable every
  other built-in plus any added modules", emitting the var only when something
  is disabled so existing stacks are byte-unchanged. The full built-in set is
  pinned in `builtin_modules.yaml` and re-verified against the live gateway image
  by an opt-in smoke guard; `switch-profile` carries the disabled set across a
  reshape.

## [0.2.0] - 2026-06-03

### Added

- **Declarative config.** `init --dry-run` dumps the fully resolved
  `ProjectConfig` to stdout (YAML by default, JSON via `--output-format`), and
  `init -f/--from-file` rebuilds a project from a saved config file. A dumped
  config round-trips byte-for-byte against the equivalent profile build, so a
  stack is now fully described by a single portable, hand-editable artifact.
- **Reshaped scaleout profile.** Scaleout now generates N frontend gateways plus
  one all-standard backend by default, with Edge opt-in through `--edge-role`
  rather than being the frontend default. The wizard and non-interactive `init`
  flags expose three choices the profile previously hardcoded: frontend count,
  the frontend/backend network split (tri-state, defaulting per profile), and
  reverse-proxy selection. `switch-profile` recovers the frontend count and
  network split so a reshape preserves the existing topology.
- **Gateway redundancy.** `init --redundant <role>` (and the matching wizard
  prompt) expands a single workhorse role into a master/backup pair: the
  scaleout `backend`, the hub-and-spoke `hub`, or the standalone `gateway`.
  Generation pre-seeds each node's `redundancy.xml` role and opens a plain
  Gateway Network link so the pair boots already paired with no UI step;
  replicated tiers (frontends, spokes) are rejected, and mixed-edition pairs
  fail validation since Ignition redundancy is edition-matched.

### Changed

- Every `init` now writes the lifecycle record unconditionally. The lifecycle
  record and the declarative dump are the same artifact, produced through one
  serialization path (`config/io.py`), so every generated project is reshapeable
  and dumpable.

### Removed

- The `--keep-cli` flag and the one-shot / SE-demo lifecycle split. The record
  that those modes gated is now always written, making the distinction
  unnecessary.

### Fixed

- Configuration validation errors no longer surface the internal pydantic
  `Value error,` prefix, so messages read cleanly.

## [0.1.1] - 2026-06-03

Relicensing release. No functional changes to the CLI or generated stacks.

### Changed

- License changed from Apache-2.0 to MIT, to match the sibling Ignition
  projects. A new PyPI version is required because PyPI will not overwrite an
  already-published release.
- The package version is now single-sourced from `ignition_stack/__init__.py`
  and read by `pyproject.toml` through `[tool.hatch.version]`, so the two can no
  longer drift apart.

## [0.1.0] - 2026-06-03

First public release. `ignition-stack` generates self-contained Docker Compose
projects for Ignition 8.3 SCADA demos and sales-engineering engagements: pick an
architecture, answer a few prompts, and `docker compose up` brings a gateway to
RUNNING with no manual UI steps.

### Added

- **`init` command** that writes a self-contained project (`docker-compose.yaml`,
  `.env`, bootstrap script, and a gateway resources tree) and boots it with
  env-driven commissioning, so first boot needs no UI.
- **Architecture profiles** `standalone`, `scaleout`, `hub-and-spoke`, and
  `mcp-n8n`, selectable with `--profile` or through an interactive wizard, with
  per-role Ignition Edge selection and a RAM-based advisory that gates oversized
  hub-and-spoke stacks behind `--force`.
- **Service catalog** covering Postgres, MySQL, MariaDB, and MongoDB; the
  Chariot, HiveMQ, EMQX, and RabbitMQ MQTT brokers; Kafka; Keycloak; OPC-UA and
  Modbus simulators; and n8n. A dependency resolver wires implicit needs such as
  Keycloak to a database and MySQL to its JDBC driver.
- **Module and driver catalog** (`modules.yaml`) with `modules list`,
  `modules validate`, and `modules download`, including checksum verification and
  an offline cache.
- **Compose generation engine** that preserves YAML anchors, supports an opt-in
  frontend/backend network split, and merges per-gateway environment overrides.
- **Lifecycle support**: one-shot and SE-demo (`--keep-cli`) modes, a generated
  `Makefile`, `reset`, `switch-profile`, project-scoped `wipe`, and a
  `POST-SETUP.md` that lists only the steps that cannot be pre-seeded.
- **Shell completion** for bash, zsh, fish, and PowerShell via
  `--install-completion`, including dynamic completion of profile names, edge
  roles, and catalog entry names.
- **Documentation site** built with Docusaurus, including a CLI reference
  generated from the live command tree and the Ignition 8.3 seeding matrix.
- **Cross-platform CI** running on macOS, Linux, and Windows, a Linux
  `docker compose up` smoke that waits for the gateway to reach RUNNING, and
  automated deployment of the documentation site to GitHub Pages.

### Distribution

- Installable with `pipx install ignition-stack` from PyPI, or from source with
  `pipx install git+https://github.com/ia-eknorr/ignition-stack.git`.
- Releases publish to PyPI automatically through GitHub Actions using Trusted
  Publishing (OIDC), with no stored API token.

[Unreleased]: https://github.com/ia-eknorr/ignition-stack/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/ia-eknorr/ignition-stack/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/ia-eknorr/ignition-stack/releases/tag/v0.1.1
[0.1.0]: https://github.com/ia-eknorr/ignition-stack/releases/tag/v0.1.0
