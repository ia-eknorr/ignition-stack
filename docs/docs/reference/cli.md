---
title: CLI reference
description: Every ignition-stack command, argument, and option, generated from the live Typer app.
---

# CLI reference

:::info Generated page
This page is generated from the Typer command tree by `docs/gen_cli_reference.py`.
Do not edit it by hand. Regenerate it with `npm run gen:cli` (or
`uv run python docs/gen_cli_reference.py`); a CI drift check fails if it falls
out of sync with the CLI.
:::

Generate ready-to-run Docker Compose stacks for Ignition 8.3 SCADA demos.

## Global options

**Options**

- `--version` (flag): Show ignition-stack version and exit.
- `--install-completion` (flag): Install completion for the current shell.
- `--show-completion` (flag): Show completion for the current shell, to copy it or customize the installation.

## `ignition-stack create`

Generate a new Ignition stack at ``<output-dir>/<name>``.

With ``--from-file``, builds from a saved config file. With ``--arch``,
runs non-interactively from the named architecture and its flags. With
neither, walks the interactive wizard. ``--dry-run`` resolves the config
and prints it instead of writing anything.

```text
ignition-stack create [OPTIONS] NAME
```

**Arguments**

- `NAME` (required): Project name. Becomes the directory, the compose project, and the gateway name.

**Options**

- `--arch`, `-a` `TEXT`: System architecture to materialize (skips the wizard): - basic: One full Ignition 8.3 gateway + Postgres. The default starter stack. - hub-and-spoke: Central hub gateway + N Edge spoke gateways. Spoke count > 8 needs --force. - scale-out: Frontend + backend Ignition gateways via gateway network + Postgres.
- `--spokes` `INTEGER` (default `3`): Spoke gateway count for the hub-and-spoke architecture (ignored otherwise).
- `--frontends` `INTEGER` (default `1`): Frontend gateway count for the scale-out architecture (ignored otherwise).
- `--network-split`, `--no-network-split` (flag): Force the frontend/backend network split on or off. Default follows the architecture (scale-out splits, hub-and-spoke does not).
- `--reverse-proxy` `TEXT`: Route gateways through a Traefik reverse proxy instead of host ports. 'external' joins a proxy you already run (on --proxy-network); 'scaffold' also lays down the ia-eknorr/traefik-reverse-proxy README at --proxy-path. Omit for plain host-port mapping.
- `--proxy-network` `TEXT` (default `proxy`): External Docker network the proxy routes on (with --reverse-proxy). Defaults to 'proxy'.
- `--proxy-path` `TEXT` (default `reverse-proxy`): Relative directory the scaffolded proxy README lives in (with --reverse-proxy scaffold).
- `--force` (flag): Bypass the hub-and-spoke red-tier RAM advisory.
- `--edge-role` `TEXT`: Gateway role that runs the Ignition Edge edition. Scale-out runs all gateways standard by default; hub-and-spoke defaults its spokes to Edge. Pass 'none' to disable the architecture's edge default; pass a role name ('frontend', 'hub', 'gateway', ...) to opt that role in.
- `--redundant` `TEXT`: Make a single gateway role redundant, expanding it into a master + backup pair (e.g. 'backend' for scale-out, 'hub' for hub-and-spoke, 'gateway' for basic). Frontends and spokes are replicated, not paired, and are rejected.
- `--disable-builtin` `TEXT` (default `[]`): Built-in IA module to turn off on every gateway (repeatable), e.g. --disable-builtin vision --disable-builtin sfc. Emits a GATEWAY_MODULES_ENABLED whitelist of everything else. Slugs tab-complete; an unknown slug is rejected with the full valid list.
- `--iiot`, `--no-iiot` (flag): Overlay an MQTT/Sparkplug IIoT pipeline: add a broker and wire the Cirrus Link Transmission/Engine modules across the gateways by role (spokes/frontends transmit, hub/backend run Engine; a single gateway runs both). Defaults the broker to 'chariot'.
- `--iiot-broker` `TEXT`: MQTT broker slug the IIoT overlay wires to (implies --iiot). Must be a catalog 'mqtt-broker' kind (e.g. 'chariot', 'emqx', 'hivemq'). Defaults to 'chariot' when --iiot is given without this flag.
- `--from-file`, `-f` `PATH`: Build from a saved config file (YAML or JSON, as dumped by --dry-run) instead of an architecture or the wizard. Mutually exclusive with --arch. The project name argument overrides the file's name.
- `--dry-run` (flag): Resolve the config and print it (see --output-format) without writing any files. The dump is the full build input; redirect it to a file, edit it, and rebuild with --from-file.
- `--output-format` `TEXT`: Format for the --dry-run dump: 'yaml' (default) or 'json'.
- `--output-dir`, `-o` `PATH`: Parent directory the project is written into. Defaults to the current directory.

## `ignition-stack modules`

Inspect and prepare the module + driver catalog.

```text
ignition-stack modules COMMAND [ARGS]...
```

### `ignition-stack modules download`

Materialise selected catalog entries into the host-side cache.

```text
ignition-stack modules download [OPTIONS] [NAMES...]
```

**Arguments**

- `NAMES...` (optional): Entries to download. Omit to download every non-manual entry.

**Options**

- `--catalog` `PATH`: Path to a modules.yaml. Defaults to the bundled catalog.
- `--ignition-version` `TEXT`: Filter to entries verified for this exact version.
- `--cache-dir` `PATH` (default `modules/cache`): Destination directory for cached artifacts.
- `--offline` (flag): No network calls. Fails if any selected entry is missing from the cache.

### `ignition-stack modules list`

Show every catalog entry as a table.

```text
ignition-stack modules list [OPTIONS]
```

**Options**

- `--catalog` `PATH`: Path to a modules.yaml. Defaults to the bundled catalog.
- `--ignition-version` `TEXT`: Filter to entries verified for this exact version.

### `ignition-stack modules validate`

Confirm schema integrity, pinned shas, and (optionally) URL reachability.

```text
ignition-stack modules validate [OPTIONS]
```

**Options**

- `--catalog` `PATH`: Path to a modules.yaml. Defaults to the bundled catalog.
- `--skip-network` (flag): Only validate the schema; skip URL reachability.
