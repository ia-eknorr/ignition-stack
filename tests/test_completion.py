"""Shell-completion callbacks.

Typer invokes these on <TAB>; the contract is that they filter by the typed
prefix, source their candidates from the live registry / bundled catalog, and
never raise. Calling them directly (no shell needed) keeps this in line with
the rest of the golden-style suite.
"""

from __future__ import annotations

import pytest

from ignition_stack.completion import (
    complete_edge_role,
    complete_module_name,
    complete_profile,
)


def test_complete_profile_lists_every_slug_with_summary() -> None:
    results = complete_profile("")
    slugs = [slug for slug, _ in results]
    assert set(slugs) == {"standalone", "scaleout", "hub-and-spoke", "mcp-n8n"}
    # Each candidate carries the profile's one-line summary for the menu.
    assert all(summary for _, summary in results)


def test_complete_profile_filters_on_prefix() -> None:
    assert [slug for slug, _ in complete_profile("s")] == ["scaleout", "standalone"]
    assert complete_profile("zzz") == []


def test_complete_edge_role_includes_none_sentinel_and_filters() -> None:
    assert "none" in complete_edge_role("")
    assert complete_edge_role("fr") == ["frontend"]


def test_complete_module_name_sources_bundled_catalog() -> None:
    names = complete_module_name("")
    assert "mqtt-engine" in names
    assert complete_module_name("mqtt-") == [
        "mqtt-engine",
        "mqtt-transmission",
        "mqtt-distributor",
    ]


def test_complete_module_name_never_raises_on_broken_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A malformed/missing catalog must degrade to "no suggestions" rather than
    # raising into the user's shell line.
    def boom(_path: object) -> object:
        raise RuntimeError("catalog exploded")

    monkeypatch.setattr("ignition_stack.catalog.loader.load_catalog", boom)
    assert complete_module_name("") == []
