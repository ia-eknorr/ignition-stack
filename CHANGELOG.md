# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.1] - 2026-06-12

### Fixed

- **Documentation accuracy pass.** Corrected stale or incorrect claims the
  architecture-first restructure left behind: the `scale-out` reverse-proxy flag
  value (`external`/`scaffold`, not the removed `traefik`), the Keycloak admin
  credentials (`admin` / `admin`), the `add-a-service` manifest field table (now
  lists `singleton`, `placement`, `wires`, and `connection`), the path-filtered
  `docs.yml` trigger, the `GATEWAY_MODULES_ENABLED` whitelist semantics, the
  `mssql-jdbc-driver` wizard-default label, and the golden-test snapshot paths.
  Documentation only — no code or generated-output changes.

## [0.6.0] - 2026-06-11

### Added

- **POST-SETUP.md now ends with a Connections reference section.** Every
  service in the stack — gateways, attached databases and services, and flat
  (unattached) instances — gets one row listing its in-network address (what a
  sibling container uses), its host-access URL or port, and its credentials as
  env-var sources with defaults (e.g. `` `DB_PASSWORD` in `.env` (default:
  `ignition`) ``). Proxy mode shows the `*.localtest.me` URL for gateway rows;
  ports mode shows `localhost:<PORT>`. The Chariot MQTT quirk (MQTT user
  `admin/changeme`, decoupled from `ADMIN_PASSWORD`) is called out as a note on
  that service's row. Each service manifest gains an optional `connection:` block
  (`in_network`, `host_port_env`, `credential_env`, `note`) so adding a new
  service only requires a manifest entry, no generator code changes.
- **Wizard progress breadcrumb.** A one-line `[N/M] label › label › …`
  header is printed above each prompt so users know their position in the
  flow and how many questions remain. The counter and total are computed from
  the *applicable* steps for the current architecture (profile-dependent steps
  such as spoke count and network split are excluded from the total until the
  architecture answer makes them relevant), so the total updates correctly
  when the user backs up and changes the architecture selection. The trail
  elides the middle with `…` when it would exceed 72 characters, always
  keeping the first completed step and the current step visible.

- **Services are now a first-class stage in the main wizard flow.** After
  exposure and before the summary, the wizard loops "Add a service?": pick from
  the kind-grouped catalog, choose where it goes, answer only the questions that
  service needs, and repeat. The recorded selections build the resolved config
  directly — no detour through the composer required. The composer remains the
  summary's **Tweak** destination, and the two now share one set of action
  implementations (`add` / `share` / `flat` / `remove` / `modules` / `edition` /
  `env` / `iiot` / `rename`).
- **Flat (unattached) services.** When adding a service you choose its
  placement: attach it to one or more gateways (a multi-select for multi-gateway
  architectures, auto-attach for a single gateway), or leave it deliberately
  unwired. A flat service still renders fully into `docker-compose.yaml` (image,
  env, healthcheck, networks) so you can connect it by hand — a Kafka broker to
  experiment with, a second Postgres you'll wire yourself. The resolver's
  single-broker / one-database-kind / singleton bounds are now scoped to
  *attachments*: a flat second Postgres alongside the attached one is legal, as
  is a flat extra broker, while genuinely stack-global singletons (Keycloak)
  stay capped at one. Manifests express this with a new `singleton_scope`
  (`global` | `attached`) field.
- **Per-block environment-variable overrides.** A new `env` composer/services
  action sets free-form `KEY=VALUE` overrides on a gateway or a service
  instance. Keys are validated as shell-safe identifiers; a gateway's overrides
  emit into its compose `environment:` block, a service instance's into the
  project `.env`. Both round-trip through dump/load. `GatewayConfig` gains an
  `env` dict to carry them (the model already accepted per-instance `env`).
- **n8n MCP drop-in is reachable again.** Adding n8n offers the Ignition MCP
  module drop-in toggle, which scaffolds `modules/dropin/`. This restores the
  capability the removed `mcp-n8n` profile provided, now expressible as Basic +
  n8n + MCP drop-in.
- **Back navigation in the wizard.** Select prompts carry a dim `← Back`
  choice and back-able confirms render as Yes/No/Back, so a wrong answer no
  longer means Ctrl-C and starting over. Going back re-asks from that step
  forward with the prior answers replayed as defaults; an answer that is no
  longer legal for the changed earlier choice falls back to the step's
  default, and steps that stop applying are skipped in both directions.
- **Config preview at the summary.** The wizard summary (and the composer's
  done-select) gains a **Preview** action that prints the fully resolved
  config — the same output as `init --dry-run` — and returns to the summary,
  so the exact project can be inspected before choosing generate or cancel.

### Changed

- **Profiles are now architectures, mirroring Ignition's own vocabulary.** The
  tool's building blocks are renamed to Ignition's [documented system
  architectures](https://docs.inductiveautomation.com/docs/8.3/system-architectures):
  `standalone` → `basic`, `scaleout` → `scale-out`, `hub-and-spoke` unchanged.
  This is a breaking rename end to end (no userbase yet): `init --profile` →
  `init --arch`, the `switch-profile` command → `switch-arch`, the lifecycle
  record's `profile` field → `architecture`, and the `ignition_stack.profiles`
  package → `ignition_stack.architectures` (`ProfileOptions` → `ArchOptions`,
  `build_profile` → `build_architecture`, `list_profiles` →
  `list_architectures`, `ProfileError` → `ArchitectureError`). Shell completion,
  docs nav (the Profiles section is now Architectures), and golden fixtures
  follow. Old configuration records that carry the `profile` field no longer
  load; regenerate them.
- **The wizard is architecture-first.** The two-track "How do you want to
  build?" gate is gone. The first prompt is now the architecture select itself
  (`basic — one gateway`, `scale-out — frontend/backend tiers`, `hub-and-spoke —
  central hub, edge spokes`), and the per-gateway composer is reached exactly one
  way: the summary's **Tweak** action, which hands it the built, resolved config
  pre-filled. The standalone Custom-track entry point and its topology-preset
  prompt are removed.
- **Reverse proxy now actually routes the gateways.** The wizard's proxy
  question is redesigned: the first prompt is exposure — host ports (default) or
  a reverse proxy. Choosing the proxy detects an existing `proxy` Docker network
  (the one `ia-eknorr/traefik-reverse-proxy` creates) and offers to join it,
  otherwise asks for the network name or scaffolds the repo. When a proxy is
  selected the compose engine attaches every gateway to the external proxy
  network and emits the Traefik labels Traefik needs — `traefik.enable=true`, a
  project-scoped `Host(<project>[-<gateway>].localtest.me)` router rule, the
  `web` entrypoint, and `loadbalancer.server.port=8088` — and drops the
  host-port publish (the proxy is the front door). Previously every proxy option
  was a no-op that only wrote a README. `ReverseProxyConfig` gains `mode`
  (`external` / `scaffold`) and `network`; `init` gains `--proxy-network` and its
  `--reverse-proxy` flag now takes the mode. The two redundant wizard options
  ("external" / "skip", both no-ops) are gone.
- **Docs restructured around the architecture-first mental model, with a
  conciseness pass throughout.** The Profiles section is now Architectures
  (index plus one page per architecture, each linking the matching IA system
  architecture). The wizard guide is rewritten to cover the step machine, back
  navigation, the breadcrumb header, the services stage (attach-or-flat), env
  overrides, preview, and the current composer action set — every quoted prompt
  now matches `wizard.py` / `wizard_composer.py` verbatim. A new reverse-proxy
  guide documents external vs. scaffold modes, network detection, Traefik
  labels, and the `*.localtest.me` URLs. Service pages carry the Connections
  reference facts (the Chariot `admin/changeme` MQTT quirk, n8n's wizard MCP
  drop-in). Stale vocabulary ("profile", "Quick/Custom track", "Wire an MQTT
  pipeline", arrow-key hints) is removed, and every page is tightened for a
  reader who knows Docker and Ignition basics.
- **Wizard prompt copy reads like a standard CLI.** Questions and choice
  labels are short and neutral: the "(recommended)" steering is gone (the
  default cursor position carries the suggestion), the IIoT gate asks about
  the outcome (`Add IIoT (MQTT/Sparkplug)?`) instead of naming Cirrus Link
  before offering four brokers, service manifest summaries are tightened, and
  punctuation/separators are consistent throughout. The misleading
  `(Use arrow keys)` instruction is suppressed on selects (j/k and arrows both
  work) and the checkbox hint shrinks to `(space to toggle)`.

### Removed

- **The `mcp-n8n` profile.** It was not an Ignition system architecture — it is
  Basic + the n8n service + the MCP module drop-in. The architecture, its
  registration, wizard/list references, tests, and golden fixture are deleted.
  The `mcp_dropin` config field and all drop-in / post-setup machinery stay
  intact, so the declarative `--from-file` path still scaffolds the MCP drop-in
  alongside n8n (covered by a new test). A guided wizard path for n8n + MCP
  returns in a later release as part of the service-composition stage.

## [0.5.0] - 2026-06-11

### Added

- **Per-gateway service composition.** A stack is now a registry of named
  `ServiceInstance`s plus explicit `ServiceAttachment`s that wire each instance
  to the gateways that use it, with a role on every edge. One gateway can run a
  different mix than the next, and a single instance - a database, a Keycloak, a
  broker - can be shared across gateways instead of duplicated. The familiar
  `database` / `services` fields stay as shorthand for the common single-gateway
  shape; the resolver lowers them into the registry, so old configs keep
  building byte-for-byte while the richer form becomes expressible. The resolver
  enforces the invariants this opens up: multiple databases are allowed only
  when their kinds are distinct, a gateway holds at most one database
  connection, and singletons (database / identity / broker) appear once.
- **Edge gateways never carry a database.** An Ignition Edge gateway is a leaf
  node, so attaching it to any `never_on_edge` service (every database) is
  rejected at config-construction time rather than failing at boot. The wizard's
  composer filters those services out of an Edge gateway's catalog entirely, with
  a note, so the invalid choice is never offered.
- **IIoT overlay.** `init --iiot` (and `--iiot-broker <slug>`, which implies it)
  adds an MQTT broker and wires the Cirrus Link Transmission/Engine modules
  across the gateways by role: edge-side gateways (spokes, frontends) publish
  Sparkplug through Transmission, central gateways (hub, backend) aggregate
  through Engine, and a single-gateway shape runs both for a self-contained loop.
  The broker defaults to `chariot` (Cirrus Link's own), and the overlay reads the
  Transmission/Engine module slugs from the broker manifest rather than
  hardcoding them. The Cirrus Link modules are pinned to 5.0.3 with fixed
  download URLs, so a wired stack fetches a known-good module set. The
  Transmission/Engine **connections are now file-seeded too**: each edge gateway
  boots with its Sparkplug identity already set (Group ID = project, Edge Node ID
  = gateway) and pointed at the broker, and the central gateway's Engine boots
  subscribed - a Chariot pipeline forms with no UI clicks, verified end to end on
  8.3.6 (`verification/iiot-spike/`). Chariot's licensed MQTT listener is started
  via a one-shot trial init container (replaceable by a real `LICENSE_TYPE` env).
  The seeds are role-scoped - Transmission lands only on transmission-attached
  gateways, Engine only on engine-attached ones - and `.j2` seed files render the
  per-gateway identity at generation time. EMQX/HiveMQ/RabbitMQ receive the same
  config-shaped seeds (credentials stripped for the anonymous brokers); only the
  Chariot pipeline was live-verified.
- **Zero-touch Keycloak OIDC seeding.** Adding Keycloak pre-seeds a realm, an
  Ignition OIDC client with a fixed demo client secret, and a `demo` / `demo`
  user, and writes the gateway's identity-provider config to match - so an OIDC
  login works on first boot with no console clicks. The fixed secret is a
  demo-only convenience; rotate it for anything that leaves a workstation.
  Keycloak's own backing database is pulled in as an unattached, registry-level
  dependency so it never collides with a gateway's data store.
- **The two-track wizard.** `init` without `--profile` opens with one question:
  *Quick* walks the linear profile flow (now including an MQTT-pipeline confirm),
  and its summary offers a three-way **generate / tweak / cancel** where *tweak*
  hands the built stack to the composer pre-filled. *Custom* starts from a
  topology preset and drops into a composer for per-gateway service composition -
  attach a service to a gateway, share an instance across gateways, set
  per-gateway editions and modules, and wire or unwire the MQTT pipeline. Every
  edit is validated before it lands: a mutation that would break an invariant
  prints one error line and leaves the composition untouched. Both doors produce
  the same kind of project and the same saved lifecycle record.

### Changed

- **Declarative round-trip and switch-profile now carry the full registry.** A
  dumped config round-trips through dump → load → resolve to a fixed point even
  for a heterogeneous, hand-composed stack, and `switch-profile` carries the
  per-gateway instances, shared attachments, second databases, and IIoT wiring
  across a reshape - re-mapping attachments onto the new topology's roles and
  dropping with an advisory only what the target shape cannot host.
- **The wizard's module step is now opt-in.** Instead of enabling every built-in
  and offering an opt-out "select modules to DISABLE" checklist, the wizard
  pre-selects a lean curated set - Perspective, OPC-UA, SQL Bridge, the historian
  pair, Alarm Notification, Reporting - and the user adds or removes from there.
  The common path is one keystroke: declining "Customize the enabled gateway
  modules?" accepts the lean default. The JDBC driver follows the chosen database
  (Postgres → PostgreSQL driver, MariaDB/MySQL → the wire-compatible MariaDB
  driver, Mongo/none → no driver) rather than shipping all three. The curated set
  lives in `builtin_modules.yaml` as a `default_enabled` flag next to the pinned
  catalog, so it survives image bumps. Only the interactive wizard changed: the
  stored `disable_builtins` shape, the `GATEWAY_MODULES_ENABLED` whitelist math,
  the `--disable-builtin` CLI flag, and the non-interactive profile path are all
  unchanged, so existing builds render byte-for-byte identically.

## [0.4.0] - 2026-06-09

### Added

- **Auto-forming gateway-network links.** The `scaleout` and `hub-and-spoke`
  profiles now wire their Gateway Network with no UI approval: each connecting
  gateway (every frontend → backend, every spoke → hub) gets a plain, non-SSL
  outgoing connection on port 8088, and every participant runs an `Unrestricted`
  incoming policy so the link is accepted on sight - the same proven pattern the
  redundancy link already rode (and what the `publicdemo-all` dev stack uses).
  Plain transport stays a demo-only default; cross-host or production
  deployments should switch to SSL on port 8060 with approved certificates.
  Verified end-to-end on 8.3.6: Edge spokes and standard frontends both reach
  `Running` against their hub/backend on first boot with zero clicks. A config
  that aggregates into an Edge gateway is rejected up front (an aggregation link
  may only be `edge → standard` or `standard → standard`, since Edge is a leaf
  edition) - so `scaleout --edge-role backend` now errors with that guidance.
  Edge-to-Edge redundancy pairs are unaffected (a separate mechanism).
- **Update notifier.** A real command on an interactive terminal prints a
  one-line notice when a newer release is on PyPI, with the upgrade command
  tailored to the detected install method (pipx, `uv tool`, or pip). The check
  is cached and runs at most once a day, fails silently when offline, never
  delays the command, and is suppressed in non-interactive use (CI, pipes) or
  by setting `IGNITION_STACK_NO_UPDATE_CHECK`. It only notifies; it never
  installs anything.

### Changed

- The `gateway-network-link` POST-SETUP step is now a verification of an
  auto-formed link rather than a manual procedure, and fires for both `scaleout`
  and `hub-and-spoke` (previously it was scaleout-only and walked through a
  hand-approval on the SSL port).

## [0.3.0] - 2026-06-09

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

[0.6.1]: https://github.com/ia-eknorr/ignition-stack/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/ia-eknorr/ignition-stack/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/ia-eknorr/ignition-stack/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/ia-eknorr/ignition-stack/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/ia-eknorr/ignition-stack/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ia-eknorr/ignition-stack/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/ia-eknorr/ignition-stack/releases/tag/v0.1.1
[0.1.0]: https://github.com/ia-eknorr/ignition-stack/releases/tag/v0.1.0
