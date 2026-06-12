---
title: Expose gateways through a reverse proxy
description: Route gateways through a Traefik reverse proxy instead of host ports, joining a proxy you already run or scaffolding ia-eknorr/traefik-reverse-proxy.
---

# Expose gateways through a reverse proxy

By default each gateway publishes a host port (`localhost:9088`, `:9089`, …). The reverse-proxy option routes them through [Traefik](https://traefik.io/) instead: the gateways publish no host port, carry Traefik labels, and join the proxy's Docker network, and Traefik routes each one by hostname.

The routing host is `<route>.localtest.me`, where `<route>` is the project name for a single-gateway stack or `<project>-<gateway>` for a multi-gateway one. Every `*.localtest.me` name resolves to `127.0.0.1`, so the URLs work with no hosts-file edit.

## Two modes

| Mode | Flag | What it does |
| --- | --- | --- |
| external | `--reverse-proxy external` | Joins a proxy you already run on `--proxy-network` (default `proxy`). |
| scaffold | `--reverse-proxy scaffold` | Also lays down the [ia-eknorr/traefik-reverse-proxy](https://github.com/ia-eknorr/traefik-reverse-proxy) README under `--proxy-path` (default `reverse-proxy/`) so you can stand the proxy up. |

```sh
ignition-stack create demo --arch scale-out --reverse-proxy external
ignition-stack create demo --arch scale-out --reverse-proxy scaffold --proxy-path proxy
```

Omit the flag for plain host-port mapping.

## In the wizard

The exposure step offers the choice:

```text
? Expose gateways via
> Host ports
  Reverse proxy
```

Choosing **Reverse proxy** detects whether a `proxy` Docker network already exists on the host. If it does, the wizard offers to join it. Otherwise you name an existing network or scaffold the proxy:

```text
? Proxy network
> Name an existing network
  Scaffold ia-eknorr/traefik-reverse-proxy
```

## What gets generated

A proxied gateway carries the Traefik router labels (host rule, the gateway's `8088` web port as the target) and joins the external proxy network instead of mapping a host port. The `POST-SETUP.md` Connections reference lists each gateway's `*.localtest.me` URL, and — for a scaffold — points at the README that walks through installing the proxy in front of the stack. The link auto-forms once the proxy is up; the post-setup step is a verification, not manual wiring.

## Cloning

The exposure choice is part of the [configuration record](../concepts/configuration-record.md), so `create <name> -f` rebuilds the proxy setup from the saved config — the routes re-derive from the new project name automatically.
