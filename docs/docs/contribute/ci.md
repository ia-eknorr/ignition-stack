---
title: Continuous integration
description: What the CI workflows prove on every pull request, the Linux compose smoke, and how the docs site deploys to GitHub Pages.
---

# Continuous integration

Two GitHub Actions workflows guard the project on every change: `ci.yml` and `docs.yml`, both running on pull requests and on pushes to `main`. A third, `release.yml`, runs only when a GitHub Release is published and is covered in [Releasing to PyPI](./releasing.md).

## `ci.yml`: cross-platform generation

The `test` job runs a matrix across `macos-latest`, `ubuntu-latest`, and `windows-latest`. On each OS it:

1. Installs the CLI with `uv sync --extra dev`.
2. Runs `ignition-stack init` for a standalone project, so the installed console script is exercised, not just imported.
3. Runs the full golden test suite with `uv run --extra dev pytest`.

The matrix uses `fail-fast: false` so a Windows-only break still reports alongside the macOS and Linux results instead of cancelling them.

### The line-ending guarantee

Every file the CLI writes must use LF endings on every OS, because the golden snapshots are byte-compared and a generated stack has to behave identically wherever it was created. Two things enforce this:

- `.gitattributes` sets `* text eol=lf`, so the committed golden files check out as LF even on Windows.
- `test_every_generated_file_is_lf_only` asserts the CLI writes LF.

The Windows job adds one more explicit step that re-generates a project and fails if any file contains a CR byte, keeping the guarantee visible in the job log.

## `ci.yml`: Linux compose smoke

The `compose-smoke` job runs only on `ubuntu-latest`. It generates a standalone + Postgres project, runs `docker compose up -d`, and polls `http://localhost:9088/StatusPing` until the gateway reports `"state":"RUNNING"`. If the gateway never reaches RUNNING within the timeout, the job prints the compose logs and fails the build. It always tears the stack down with `docker compose down -v`, including its volumes.

This proves the generated stack boots a real gateway with no UI prompts, which is the whole point of the env-driven commissioning and file seeding.

### Running the smoke locally

A richer end-to-end smoke lives behind the `smoke` pytest marker. It generates a multi-service stack and drives the gateway UI with Playwright, so it needs Docker and the `poc` extra:

```sh
uv sync --extra dev --extra poc
uv run playwright install chromium
uv run --extra dev --extra poc pytest -m smoke
```

The default `pytest` run excludes the marker, so you only pay for the boot when you ask for it.

## `docs.yml`: build and deploy

The `cli-reference` job checks that `reference/cli.md` still matches the live Typer command tree. The `build` job builds the Docusaurus site, which fails on any broken internal link.

On a push to `main`, the `build` job packages the built site and the `deploy` job publishes it to GitHub Pages at [ia-eknorr.github.io/ignition-stack](https://ia-eknorr.github.io/ignition-stack/). Pull requests build the site to catch breakage but never publish.

The deploy job needs Pages set to build from GitHub Actions. In the repository settings, under **Pages**, set the source to **GitHub Actions**. Without that, the `deploy-pages` step has nowhere to publish.

## Running the checks locally

```sh
# Golden tests, the same suite the matrix runs
uv run --extra dev pytest

# CLI reference drift only
uv run --extra dev pytest tests/test_docs_cli_reference.py

# Build the docs site
cd docs && npm ci && npm run build
```
