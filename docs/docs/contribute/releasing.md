---
title: Releasing to PyPI
description: Cut a release so pipx install ignition-stack picks up a new version, and the one-time PyPI Trusted Publishing setup.
---

# Releasing to PyPI

A new version reaches users through PyPI: once published, `pipx install ignition-stack` and `pipx upgrade ignition-stack` pick it up. Publishing is automated. Creating a GitHub Release builds the package and uploads it through the `release.yml` workflow, authenticated to PyPI with Trusted Publishing (OpenID Connect), so no API token lives in the repository.

## Cutting a release

1. Bump `version` in `pyproject.toml` (for example `0.1.0` to `0.2.0`) and merge that change to `main`. PyPI refuses to overwrite a version that already exists, so every release needs a fresh number.
2. Create a GitHub Release whose tag is the same version prefixed with `v`, for example `v0.2.0`, targeting `main`.
3. Publishing it triggers `release.yml`. The workflow builds the wheel and sdist, checks the tag matches the built version, and publishes to PyPI.

The tag-versus-version check is deliberate: if the `v0.2.0` tag does not match the version built from `pyproject.toml`, the workflow fails before publishing rather than shipping a package under the wrong number.

## One-time PyPI setup

Trusted Publishing has to be authorized once on PyPI before the first release. Because the project does not exist on PyPI yet, register it as a **pending** publisher:

1. Sign in to [PyPI](https://pypi.org/) and open **Your account → Publishing**.
2. Under **Add a new pending publisher**, fill in:
   - **PyPI project name:** `ignition-stack`
   - **Owner:** `ia-eknorr`
   - **Repository name:** `ignition-stack`
   - **Workflow name:** `release.yml`
   - **Environment name:** `pypi`
3. Save it.

The first published release then creates the project and claims the name. After that the same publisher keeps authorizing every release with no further setup.

The `pypi` environment named above is also a GitHub Actions environment. Leave it open for hands-off releases, or add a required reviewer to it in the repository settings so a person has to approve each publish before it runs.

## Installing from source

Publishing to PyPI does not replace the from-source path. `pipx install git+https://github.com/ia-eknorr/ignition-stack.git` still installs straight from `main`, and `@<branch>` still installs a branch, which is the quickest way to try an unreleased change.
