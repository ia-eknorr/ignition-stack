"""Tests for disabling built-in IA modules via the GATEWAY_MODULES_ENABLED whitelist.

The feature inverts a friendly blocklist (``disable_builtins=['vision']``) into the
gateway's strict whitelist env var (enable every built-in except the disabled ones,
plus any added third-party modules). The seeding-matrix doc records that the env var
quarantines anything unlisted; ``tests/test_builtin_catalog_smoke.py`` re-verifies that
against a live image. These unit tests cover the pure translation layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ignition_stack.catalog.builtins import load_builtin_catalog
from ignition_stack.catalog.loader import load_catalog
from ignition_stack.cli import _options_from_config, app
from ignition_stack.compose.engine import render_compose
from ignition_stack.config import dump_config, load_config
from ignition_stack.config.schema import GatewayConfig, ProjectConfig
from ignition_stack.profiles import ProfileOptions, build_profile
from ignition_stack.services.resolver import resolve

VISION = "com.inductiveautomation.vision"
SFC = "com.inductiveautomation.sfc"
PERSPECTIVE = "com.inductiveautomation.perspective"


def _enabled_line(compose: str) -> str:
    lines = [ln for ln in compose.splitlines() if "GATEWAY_MODULES_ENABLED" in ln]
    return lines[0] if lines else ""


# --------------------------------------------------------------------------- #
# Built-in catalog
# --------------------------------------------------------------------------- #


def test_builtin_catalog_version_matches_default_image() -> None:
    """A fast guard (no Docker): if someone bumps the default Ignition image
    without re-deriving builtin_modules.yaml, the version drift is caught here.
    The smoke guard then verifies the module *set* against the live image."""
    default_tag = ProjectConfig.model_fields["ignition_image"].default
    assert default_tag.endswith(f":{load_builtin_catalog().ignition_version}"), (
        f"builtin_modules.yaml is pinned to {load_builtin_catalog().ignition_version} "
        f"but the default image is {default_tag}; re-derive the built-in catalog"
    )


def test_builtin_catalog_loads_and_has_unique_slugs() -> None:
    cat = load_builtin_catalog()
    slugs = [m.slug for m in cat.modules]
    assert len(slugs) == len(set(slugs))
    # The modules a demo user is most likely to drop are present.
    assert {"vision", "sfc", "perspective"} <= cat.slugs


def test_identifiers_excluding_drops_only_named_slugs() -> None:
    cat = load_builtin_catalog()
    enabled = cat.identifiers_excluding(["vision", "sfc"])
    assert VISION not in enabled
    assert SFC not in enabled
    assert PERSPECTIVE in enabled
    # Exactly the two named slugs are removed; everything else stays.
    assert len(enabled) == len(cat.modules) - 2


def test_identifiers_excluding_empty_keeps_all() -> None:
    cat = load_builtin_catalog()
    assert cat.identifiers_excluding([]) == [m.identifier for m in cat.modules]


# --------------------------------------------------------------------------- #
# Schema validation
# --------------------------------------------------------------------------- #


def test_unknown_disable_slug_raises_with_valid_list() -> None:
    with pytest.raises(ValueError, match="unknown built-in module slug"):
        GatewayConfig(name="gateway", disable_builtins=["visionn"])


def test_known_disable_slugs_accepted() -> None:
    gw = GatewayConfig(name="gateway", disable_builtins=["vision", "sfc"])
    assert gw.disable_builtins == ["vision", "sfc"]


# --------------------------------------------------------------------------- #
# Engine whitelist emission
# --------------------------------------------------------------------------- #


def test_no_whitelist_when_nothing_disabled() -> None:
    compose = render_compose(ProjectConfig(name="demo"))
    assert "GATEWAY_MODULES_ENABLED" not in compose


def test_disable_emits_whitelist_excluding_named_keeping_rest() -> None:
    config = ProjectConfig(
        name="demo",
        gateways=[GatewayConfig(name="gateway", disable_builtins=["vision", "sfc"])],
    )
    line = _enabled_line(render_compose(config))
    assert line, "expected a GATEWAY_MODULES_ENABLED line"
    assert VISION not in line
    assert f"{SFC}," not in line and not line.rstrip('"').endswith(SFC)
    assert PERSPECTIVE in line


def test_added_module_is_folded_into_whitelist() -> None:
    """A third-party module must stay whitelisted, or the whitelist would
    quarantine the very module we just installed."""
    config = ProjectConfig(
        name="demo",
        gateways=[
            GatewayConfig(
                name="gateway",
                modules=["mqtt-engine"],
                disable_builtins=["vision"],
            )
        ],
    )
    line = _enabled_line(render_compose(config, catalog=load_catalog()))
    assert "com.cirruslink.mqtt.engine.gateway" in line
    assert VISION not in line


def test_whitelist_is_deterministic() -> None:
    config = ProjectConfig(
        name="demo",
        gateways=[GatewayConfig(name="gateway", disable_builtins=["sfc"])],
    )
    assert render_compose(config) == render_compose(config)


def test_disabling_all_builtins_emits_empty_whitelist_not_omitted() -> None:
    """Disabling every built-in must emit an EMPTY whitelist (quarantines all),
    never omit the var - omission would silently re-enable everything, the exact
    inversion the feature exists to prevent."""
    all_slugs = [m.slug for m in load_builtin_catalog().modules]
    config = ProjectConfig(
        name="demo",
        gateways=[GatewayConfig(name="gateway", disable_builtins=all_slugs)],
    )
    compose = render_compose(config)
    assert 'GATEWAY_MODULES_ENABLED: ""' in compose


# --------------------------------------------------------------------------- #
# Profile + resolver plumbing
# --------------------------------------------------------------------------- #


def test_profile_applies_disable_to_every_gateway() -> None:
    config = build_profile("scaleout", "demo", ProfileOptions(disable_builtins=("vision",)))
    assert config.gateways  # sanity
    assert all(gw.disable_builtins == ["vision"] for gw in config.gateways)


def test_redundancy_backup_inherits_disable_builtins() -> None:
    config = build_profile(
        "standalone",
        "demo",
        ProfileOptions(redundant_role="gateway", disable_builtins=("vision", "sfc")),
    )
    resolved = resolve(config)
    backups = [gw for gw in resolved.gateways if gw.name.endswith("-backup")]
    assert backups, "redundancy should expand a backup node"
    for backup in backups:
        assert backup.disable_builtins == ["vision", "sfc"]


# --------------------------------------------------------------------------- #
# CLI flag (--disable-builtin)
# --------------------------------------------------------------------------- #


def test_cli_disable_builtin_flag_emits_whitelist(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "init",
            "demo",
            "--profile",
            "standalone",
            "--disable-builtin",
            "vision",
            "--disable-builtin",
            "sfc",
            "-o",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    compose = (tmp_path / "demo" / "docker-compose.yaml").read_text(encoding="utf-8")
    line = _enabled_line(compose)
    assert line and VISION not in line and PERSPECTIVE in line


def test_cli_unknown_disable_builtin_exits_2(tmp_path: Path) -> None:
    """A typo in the flag is rejected (exit 2), not silently ignored - the
    post-construction mutation path is validated too."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "init",
            "demo",
            "--profile",
            "standalone",
            "--disable-builtin",
            "visionn",
            "-o",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2
    assert "unknown built-in module slug" in result.stdout
    assert not (tmp_path / "demo").exists()


# --------------------------------------------------------------------------- #
# Declarative round-trip (dump -> edit -> --from-file)
# --------------------------------------------------------------------------- #


def test_disable_builtins_survives_dump_load_round_trip(tmp_path: Path) -> None:
    """The dump -> edit -> --from-file flow is first-class here, so disable_builtins
    must round-trip through the config serializer intact."""
    config = ProjectConfig(
        name="demo",
        gateways=[GatewayConfig(name="gateway", disable_builtins=["vision", "sfc"])],
    )
    path = tmp_path / "config.yaml"
    path.write_text(dump_config(config, "yaml"), encoding="utf-8")
    reloaded = load_config(path)
    assert reloaded.gateways[0].disable_builtins == ["vision", "sfc"]


def test_switch_profile_recovers_disable_builtins() -> None:
    """Reshaping a stack (switch-profile) must carry the disabled built-ins, or a
    profile switch would silently re-enable Vision/SFC. Recovery is the slugs
    disabled on every gateway."""
    config = build_profile("scaleout", "demo", ProfileOptions(disable_builtins=("vision", "sfc")))
    recovered = _options_from_config(config)
    assert set(recovered.disable_builtins) == {"vision", "sfc"}
