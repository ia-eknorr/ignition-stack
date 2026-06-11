"""Phase 6 acceptance tests: architecture profiles + wizard + advisory.

Validation criteria from 03-plan.md Phase 6:

1. ``ignition-stack init demo --profile scaleout`` emits two networked
   gateways (frontend + backend) + a DB; gateway-network link present.
2. ``--profile hub-and-spoke --spokes 3`` emits 1 hub + 3 spoke gateways;
   ``--spokes 12`` without ``--force`` exits non-zero with the red-tier
   message; with ``--force`` it proceeds.
3. Wizard for ``mcp-n8n`` produces an n8n service + ``modules/dropin/``
   dir + a POST-SETUP entry for the EA-gated MCP module; per-role edition
   yields IGNITION_EDITION=edge on the chosen role only.
4. Reverse-proxy prompt: "I already have one" emits a plain host-port
   mapping; "install Traefik" emits the ia-eknorr/traefik-reverse-proxy
   setup at the chosen location.

Goldens follow the existing ``UPDATE_GOLDENS=1`` convention.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from io import StringIO
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from typer.testing import CliRunner

from ignition_stack.catalog.builtins import default_builtin_catalog, jdbc_driver_for
from ignition_stack.catalog.loader import load_catalog
from ignition_stack.cli import app
from ignition_stack.compose.engine import render_compose
from ignition_stack.compose.writer import write_project
from ignition_stack.config import GatewayConfig, ProjectConfig, ReverseProxyConfig
from ignition_stack.profiles import (
    ProfileError,
    ProfileOptions,
    build_profile,
    list_profiles,
    spoke_advisory,
)
from ignition_stack.services.resolver import resolve
from ignition_stack.wizard import QuestionaryPrompter, walk

GOLDEN_DIR = Path(__file__).parent / "golden" / "profiles"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _parse(text: str) -> dict:
    return YAML().load(StringIO(text))


def _render(config_builder) -> str:
    """Resolve then render. Loads the modules catalog when needed."""
    resolved = resolve(config_builder)
    needs_catalog = any(gw.modules for gw in resolved.gateways)
    return render_compose(resolved, catalog=load_catalog() if needs_catalog else None)


def _check_or_update_golden(rel_path: str, actual: str) -> None:
    golden = GOLDEN_DIR / rel_path
    if os.environ.get("UPDATE_GOLDENS") == "1":
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(actual, encoding="utf-8")
        return
    assert golden.exists(), f"missing golden {golden}; run with UPDATE_GOLDENS=1 to create"
    expected = golden.read_text(encoding="utf-8")
    if expected != actual:
        from difflib import unified_diff

        diff = "\n".join(
            unified_diff(
                expected.splitlines(),
                actual.splitlines(),
                fromfile=f"golden:{rel_path}",
                tofile="rendered",
                lineterm="",
                n=2,
            )
        )
        pytest.fail(f"compose output diverges from golden '{rel_path}'.\nRun with UPDATE_GOLDENS=1 to update if the change is intentional.\n\n{diff}")


class ScriptedPrompter:
    """Test prompter that returns pre-recorded answers in order.

    The wizard's ``Prompter`` protocol is intentionally small (select / text /
    confirm / integer); this drives the wizard programmatically so we don't
    need a TTY or Questionary's own test harness in CI.
    """

    def __init__(self, answers: list) -> None:
        self._answers = iter(answers)

    def _next(self):
        try:
            return next(self._answers)
        except StopIteration as exc:
            raise AssertionError("ScriptedPrompter ran out of answers") from exc

    def select(self, message: str, choices: Sequence[tuple[str, str]], default=None, allow_back: bool = False):
        return self._next()

    def text(self, message: str, default: str = "") -> str:
        return self._next()

    def confirm(self, message: str, default: bool = False, allow_back: bool = False):
        return self._next()

    def integer(self, message: str, default: int, minimum: int = 0, allow_back: bool = False):
        return self._next()

    def checkbox(self, message: str, choices: Sequence[tuple[str, str, bool]]) -> list:
        return self._next()


# --------------------------------------------------------------------------- #
# Registry: the four profile slugs exist with stable summaries
# --------------------------------------------------------------------------- #


def test_registry_contains_all_four_profiles() -> None:
    slugs = {p.slug for p in list_profiles()}
    assert slugs == {"standalone", "scaleout", "hub-and-spoke", "mcp-n8n"}


# --------------------------------------------------------------------------- #
# Standalone profile
# --------------------------------------------------------------------------- #


def test_standalone_resolves_to_phase2_shape() -> None:
    config = build_profile("standalone", "demo", ProfileOptions())
    assert config.profile == "standalone"
    assert len(config.gateways) == 1
    assert config.gateways[0].name == "gateway"
    assert config.network_split is False
    assert config.database is not None and config.database.kind == "postgres"


def test_standalone_with_edge_role_flips_edition() -> None:
    config = build_profile("standalone", "demo", ProfileOptions(edge_role="gateway"))
    assert config.gateways[0].ignition_edition == "edge"


def test_standalone_golden() -> None:
    config = build_profile("standalone", "demo", ProfileOptions())
    _check_or_update_golden("standalone/docker-compose.yaml", _render(config))


# --------------------------------------------------------------------------- #
# Scaleout profile (Phase 6 validation 1)
# --------------------------------------------------------------------------- #


def test_scaleout_emits_two_gateways_plus_database() -> None:
    config = build_profile("scaleout", "demo", ProfileOptions())
    assert config.profile == "scaleout"
    assert [g.name for g in config.gateways] == ["frontend", "backend"]
    assert config.gateways[0].role == "frontend"
    assert config.gateways[1].role == "backend"
    assert config.network_split is True
    assert config.database is not None and config.database.kind == "postgres"


def test_scaleout_defaults_all_standard() -> None:
    """The default scaleout shape runs every gateway on the standard edition;
    Edge is opt-in via --edge-role, not the default."""
    config = build_profile("scaleout", "demo", ProfileOptions())
    assert all(g.ignition_edition == "standard" for g in config.gateways)


def test_scaleout_edge_role_none_keeps_everything_standard() -> None:
    config = build_profile("scaleout", "demo", ProfileOptions(edge_role="none"))
    assert all(g.ignition_edition == "standard" for g in config.gateways)


def test_scaleout_edge_role_frontend_flips_only_frontends() -> None:
    config = build_profile("scaleout", "demo", ProfileOptions(frontends=2, edge_role="frontend"))
    editions = {g.name: g.ignition_edition for g in config.gateways}
    assert editions == {"frontend-1": "edge", "frontend-2": "edge", "backend": "standard"}


def test_scaleout_single_frontend_keeps_bare_name() -> None:
    config = build_profile("scaleout", "demo", ProfileOptions())
    assert [g.name for g in config.gateways] == ["frontend", "backend"]
    assert [g.http_port for g in config.gateways] == [9088, 9089]


def test_scaleout_multiple_frontends_are_numbered() -> None:
    config = build_profile("scaleout", "demo", ProfileOptions(frontends=2))
    assert [g.name for g in config.gateways] == ["frontend-1", "frontend-2", "backend"]
    assert [g.role for g in config.gateways] == ["frontend", "frontend", "backend"]
    assert [g.http_port for g in config.gateways] == [9088, 9089, 9090]


def test_scaleout_network_split_can_be_forced_off() -> None:
    config = build_profile("scaleout", "demo", ProfileOptions(network_split=False))
    assert config.network_split is False


def test_hub_and_spoke_network_split_can_be_forced_on() -> None:
    config = build_profile("hub-and-spoke", "demo", ProfileOptions(spokes=2, network_split=True))
    assert config.network_split is True


def test_scaleout_golden_and_renders_valid_yaml() -> None:
    config = build_profile("scaleout", "demo", ProfileOptions())
    rendered = _render(config)
    parsed = _parse(rendered)
    assert "frontend" in parsed["services"]
    assert "backend" in parsed["services"]
    assert "db" in parsed["services"]
    assert set(parsed["networks"].keys()) == {"frontend", "backend"}
    _check_or_update_golden("scaleout/docker-compose.yaml", rendered)


def test_scaleout_emits_gateway_network_post_setup(tmp_path: Path) -> None:
    """The scaleout gateway-network links auto-form from env (plain port 8088,
    Unrestricted policy), so POST-SETUP carries a *verify* step - not a manual
    UI approval - that names both ends of each link."""
    config = build_profile("scaleout", "demo", ProfileOptions())
    assert config.profile == "scaleout"  # resolved-config assertion
    write_project(config, tmp_path / "demo")
    post_setup = (tmp_path / "demo" / "POST-SETUP.md").read_text(encoding="utf-8")
    assert "gateway-network" in post_setup.lower()
    assert "frontend" in post_setup
    assert "backend" in post_setup
    # It is a verification of an auto-formed plain (8088) link, not a manual step.
    assert "Verify the gateway-network link" in post_setup
    assert "8088" in post_setup


def test_hub_and_spoke_emits_gateway_network_post_setup(tmp_path: Path) -> None:
    """Hub-and-spoke now auto-forms each spoke -> hub link, so it surfaces the
    same plain-8088 verify step (it never did before: the old gate was
    scaleout-only)."""
    config = build_profile("hub-and-spoke", "demo", ProfileOptions(spokes=2))
    write_project(config, tmp_path / "demo")
    post_setup = (tmp_path / "demo" / "POST-SETUP.md").read_text(encoding="utf-8")
    assert "Verify the gateway-network link" in post_setup
    assert "hub" in post_setup
    assert "spoke-1" in post_setup and "spoke-2" in post_setup
    assert "8088" in post_setup


def test_edge_aggregation_target_rejected_edge_to_edge() -> None:
    """A hand-authored config that aggregates one Edge gateway into another is
    rejected: Edge is a leaf edition, so a GAN aggregation link can't target it."""
    with pytest.raises(ValueError, match="Edge edition; aggregate into a standard"):
        ProjectConfig(
            name="demo",
            database=None,
            gateways=[
                GatewayConfig(name="leaf", ignition_edition="edge", gan_outgoing=["central"]),
                GatewayConfig(name="central", ignition_edition="edge"),
            ],
        )


def test_edge_aggregation_target_rejected_standard_to_edge() -> None:
    """`scaleout --edge-role backend` makes the aggregation target (backend) Edge,
    which is the same backwards shape (standard frontends into an Edge backend) and
    is rejected with the same guidance."""
    with pytest.raises(ValueError, match="Edge edition; aggregate into a standard"):
        build_profile("scaleout", "demo", ProfileOptions(edge_role="backend"))


def test_edge_to_standard_aggregation_allowed() -> None:
    """The normal leaf->aggregator shapes stay valid: edge spokes -> standard hub
    and standard frontend -> standard backend both build without error."""
    # edge -> standard (hub-and-spoke default: Edge spokes, standard hub)
    build_profile("hub-and-spoke", "demo", ProfileOptions(spokes=2))
    # standard -> standard (scaleout default)
    build_profile("scaleout", "demo", ProfileOptions())


def test_scaleout_via_cli_writes_project(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["init", "demo", "--profile", "scaleout", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    compose = (tmp_path / "demo" / "docker-compose.yaml").read_text(encoding="utf-8")
    parsed = _parse(compose)
    assert {"frontend", "backend", "db"} <= set(parsed["services"])


def test_scaleout_cli_two_frontends_no_split(tmp_path: Path) -> None:
    """--frontends 2 --no-network-split writes two frontend services on a single
    shared network (no frontend/backend split)."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "init",
            "demo",
            "--profile",
            "scaleout",
            "--frontends",
            "2",
            "--no-network-split",
            "-o",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    parsed = _parse((tmp_path / "demo" / "docker-compose.yaml").read_text(encoding="utf-8"))
    assert {"frontend-1", "frontend-2", "backend", "db"} <= set(parsed["services"])
    # network split off -> no named frontend/backend networks block.
    assert "networks" not in parsed or not parsed["networks"]


def test_scaleout_cli_reverse_proxy_scaffold(tmp_path: Path) -> None:
    """--reverse-proxy scaffold lays down the proxy README at the default path."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "init",
            "demo",
            "--profile",
            "scaleout",
            "--reverse-proxy",
            "scaffold",
            "-o",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    readme = tmp_path / "demo" / "reverse-proxy" / "README.md"
    assert readme.is_file()
    assert "ia-eknorr/traefik-reverse-proxy" in readme.read_text(encoding="utf-8")


def test_cli_reverse_proxy_external_with_network(tmp_path: Path) -> None:
    """--reverse-proxy external --proxy-network joins that network, no scaffold."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "init",
            "demo",
            "--profile",
            "standalone",
            "--reverse-proxy",
            "external",
            "--proxy-network",
            "edge-net",
            "-o",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert not (tmp_path / "demo" / "reverse-proxy").exists()
    parsed = _parse((tmp_path / "demo" / "docker-compose.yaml").read_text(encoding="utf-8"))
    assert parsed["networks"]["edge-net"] == {"external": True}


def test_cli_reverse_proxy_bad_mode_exits_two(tmp_path: Path) -> None:
    """An unknown --reverse-proxy mode is a clean usage error, not a traceback."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["init", "demo", "--profile", "standalone", "--reverse-proxy", "traefik", "-o", str(tmp_path)],
    )
    assert result.exit_code == 2


# --------------------------------------------------------------------------- #
# Hub-and-spoke profile (Phase 6 validation 2)
# --------------------------------------------------------------------------- #


def test_hub_and_spoke_three_spokes() -> None:
    config = build_profile("hub-and-spoke", "demo", ProfileOptions(spokes=3))
    names = [g.name for g in config.gateways]
    assert names == ["hub", "spoke-1", "spoke-2", "spoke-3"]
    # Hub stays standard; spokes default to Edge.
    assert config.gateways[0].ignition_edition == "standard"
    assert all(g.ignition_edition == "edge" for g in config.gateways[1:])


def test_hub_and_spoke_red_tier_without_force_raises() -> None:
    with pytest.raises(ProfileError):
        build_profile("hub-and-spoke", "demo", ProfileOptions(spokes=12))


def test_hub_and_spoke_red_tier_with_force_proceeds() -> None:
    config = build_profile("hub-and-spoke", "demo", ProfileOptions(spokes=12, force=True))
    assert len(config.gateways) == 13  # 1 hub + 12 spokes


def test_hub_and_spoke_cli_spokes_3(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "init",
            "demo",
            "--profile",
            "hub-and-spoke",
            "--spokes",
            "3",
            "-o",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    compose = (tmp_path / "demo" / "docker-compose.yaml").read_text(encoding="utf-8")
    parsed = _parse(compose)
    expected_gateways = {"hub", "spoke-1", "spoke-2", "spoke-3"}
    assert expected_gateways <= set(parsed["services"])


def test_hub_and_spoke_cli_red_tier_exits_non_zero(tmp_path: Path) -> None:
    """``--spokes 12`` without ``--force`` exits with code 3 (advisory)."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "init",
            "demo",
            "--profile",
            "hub-and-spoke",
            "--spokes",
            "12",
            "-o",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 3, result.stdout
    # Project must not have been written.
    assert not (tmp_path / "demo").exists() or not any((tmp_path / "demo").iterdir())


def test_hub_and_spoke_cli_red_tier_with_force_proceeds(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "init",
            "demo",
            "--profile",
            "hub-and-spoke",
            "--spokes",
            "12",
            "--force",
            "-o",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout


def test_hub_and_spoke_3_spokes_golden() -> None:
    config = build_profile("hub-and-spoke", "demo", ProfileOptions(spokes=3))
    _check_or_update_golden("hub-and-spoke/docker-compose.yaml", _render(config))


# --------------------------------------------------------------------------- #
# Advisory tiers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "spokes,expected_tier",
    [
        (0, "green"),
        (1, "green"),
        (4, "green"),
        (5, "yellow"),
        (8, "yellow"),
        (9, "red"),
        (12, "red"),
    ],
)
def test_advisory_tier_matches_design(spokes: int, expected_tier: str) -> None:
    advisory = spoke_advisory(spokes, available_bytes=64 * 1024**3)  # 64 GB host
    assert advisory.tier == expected_tier


def test_advisory_message_mentions_counts_and_ram() -> None:
    advisory = spoke_advisory(12, available_bytes=16 * 1024**3)
    assert "12" in advisory.message
    assert "13 gateways" in advisory.message or "13" in advisory.message
    assert "GB" in advisory.message


def test_advisory_rejects_negative_spoke_count() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        spoke_advisory(-1)


# --------------------------------------------------------------------------- #
# mcp-n8n profile (Phase 6 validation 3)
# --------------------------------------------------------------------------- #


def test_mcp_n8n_resolved_config_includes_n8n_and_dropin() -> None:
    config = build_profile("mcp-n8n", "demo", ProfileOptions())
    assert config.profile == "mcp-n8n"
    assert "n8n" in config.services
    assert config.mcp_dropin is True


def test_mcp_n8n_writer_emits_dropin_and_post_setup(tmp_path: Path) -> None:
    config = build_profile("mcp-n8n", "demo", ProfileOptions())
    write_project(config, tmp_path / "demo")
    dropin_readme = tmp_path / "demo" / "modules" / "dropin" / "README.md"
    post_setup = tmp_path / "demo" / "POST-SETUP.md"
    assert dropin_readme.is_file()
    assert "MCP" in dropin_readme.read_text(encoding="utf-8")
    assert post_setup.is_file()
    body = post_setup.read_text(encoding="utf-8")
    assert "MCP" in body
    assert "EA" in body or "early-access" in body.lower() or "Early-Access" in body


def test_mcp_n8n_wizard_flow_writes_expected_project(tmp_path: Path) -> None:
    """Validation: wizard for mcp-n8n produces n8n + dropin + POST-SETUP entry."""
    # Scripted answers in wizard order: track -> profile -> db -> edge_role ->
    # iiot -> modules -> proxy -> summary action
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "mcp-n8n",  # profile
            "postgres",  # database
            "none",  # edge_role
            False,  # wire IIoT? -> no
            False,  # customize modules? -> accept lean default
            "ports",  # exposure: host ports
            "generate",  # summary action
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    write_project(outcome.config, tmp_path / "demo")
    assert (tmp_path / "demo" / "modules" / "dropin" / "README.md").is_file()
    assert (tmp_path / "demo" / "POST-SETUP.md").is_file()
    # n8n service rendered in the compose file.
    compose = (tmp_path / "demo" / "docker-compose.yaml").read_text(encoding="utf-8")
    assert "n8n:" in compose


def test_per_role_edition_only_overrides_chosen_role() -> None:
    """Edge edition on a single role does not flip the others."""
    config = build_profile("scaleout", "demo", ProfileOptions(edge_role="frontend"))
    editions = {g.role: g.ignition_edition for g in config.gateways}
    assert editions == {"frontend": "edge", "backend": "standard"}


def test_mcp_n8n_golden() -> None:
    config = build_profile("mcp-n8n", "demo", ProfileOptions())
    _check_or_update_golden("mcp-n8n/docker-compose.yaml", _render(config))


# --------------------------------------------------------------------------- #
# Reverse-proxy: host ports vs. proxy, with detection
# --------------------------------------------------------------------------- #


def test_host_ports_emit_plain_mapping(tmp_path: Path) -> None:
    """No reverse_proxy -> plain host:container mapping, no labels, no proxy net."""
    config = build_profile("standalone", "demo", ProfileOptions(reverse_proxy=None))
    write_project(config, tmp_path / "demo")
    compose = (tmp_path / "demo" / "docker-compose.yaml").read_text(encoding="utf-8")
    parsed = _parse(compose)
    gateway = parsed["services"]["gateway"]
    assert "ports" in gateway
    assert "labels" not in gateway
    assert "networks" not in parsed or "proxy" not in (parsed.get("networks") or {})
    assert not (tmp_path / "demo" / "reverse-proxy").exists()


def test_external_proxy_emits_labels_and_drops_host_port(tmp_path: Path) -> None:
    """External mode -> Traefik labels, proxy network joined, host port dropped,
    and NO scaffold README (the proxy already exists)."""
    config = build_profile(
        "standalone",
        "demo",
        ProfileOptions(reverse_proxy=ReverseProxyConfig(mode="external", network="proxy")),
    )
    write_project(config, tmp_path / "demo")
    compose = (tmp_path / "demo" / "docker-compose.yaml").read_text(encoding="utf-8")
    parsed = _parse(compose)
    gateway = parsed["services"]["gateway"]
    # Host port dropped; routed via labels instead.
    assert "ports" not in gateway
    labels = gateway["labels"]
    assert "traefik.enable=true" in labels
    assert "traefik.http.routers.demo.rule=Host(`demo.localtest.me`)" in labels
    assert "traefik.http.services.demo.loadbalancer.server.port=8088" in labels
    # Gateway joins the external proxy network (plus default for the DB).
    assert "proxy" in gateway["networks"]
    assert "default" in gateway["networks"]
    # The proxy network is declared external; the stack does not create it.
    assert parsed["networks"]["proxy"] == {"external": True}
    # External mode scaffolds nothing.
    assert not (tmp_path / "demo" / "reverse-proxy").exists()
    # POST-SETUP carries the routed URL as a verification step.
    post_setup = (tmp_path / "demo" / "POST-SETUP.md").read_text(encoding="utf-8")
    assert "http://demo.localtest.me" in post_setup


def test_external_proxy_custom_network_name(tmp_path: Path) -> None:
    """A non-default network name flows into both the join and the declaration."""
    config = build_profile(
        "standalone",
        "demo",
        ProfileOptions(reverse_proxy=ReverseProxyConfig(mode="external", network="edge-net")),
    )
    parsed = _parse(_render(config))
    assert "edge-net" in parsed["services"]["gateway"]["networks"]
    assert parsed["networks"]["edge-net"] == {"external": True}


def test_multi_gateway_proxy_routes_are_project_scoped(tmp_path: Path) -> None:
    """Each gateway in a multi-gateway stack gets a project-scoped router host."""
    config = build_profile(
        "scaleout",
        "demo",
        ProfileOptions(reverse_proxy=ReverseProxyConfig(mode="external", network="proxy")),
    )
    parsed = _parse(_render(config))
    fe_labels = parsed["services"]["frontend"]["labels"]
    be_labels = parsed["services"]["backend"]["labels"]
    assert "traefik.http.routers.demo-frontend.rule=Host(`demo-frontend.localtest.me`)" in fe_labels
    assert "traefik.http.routers.demo-backend.rule=Host(`demo-backend.localtest.me`)" in be_labels


def test_scaffold_proxy_writes_readme_and_joins_network(tmp_path: Path) -> None:
    """Scaffold mode -> README at chosen path AND the gateway joins the network."""
    config = build_profile(
        "standalone",
        "demo",
        ProfileOptions(reverse_proxy=ReverseProxyConfig(mode="scaffold", network="proxy", path="infra/proxy")),
    )
    write_project(config, tmp_path / "demo")
    readme = tmp_path / "demo" / "infra" / "proxy" / "README.md"
    assert readme.is_file()
    assert "ia-eknorr/traefik-reverse-proxy" in readme.read_text(encoding="utf-8")
    # The compose still wires the gateway to the scaffolded network.
    parsed = _parse((tmp_path / "demo" / "docker-compose.yaml").read_text(encoding="utf-8"))
    assert "proxy" in parsed["services"]["gateway"]["networks"]
    post_setup = (tmp_path / "demo" / "POST-SETUP.md").read_text(encoding="utf-8")
    assert "infra/proxy" in post_setup


def test_wizard_proxy_joins_detected_network(monkeypatch) -> None:
    """Proxy + detected 'proxy' network + confirm -> external mode on 'proxy'."""
    monkeypatch.setattr("ignition_stack.wizard._detect_proxy_network", lambda: ["bridge", "proxy"])
    prompter = ScriptedPrompter(
        [
            "quick",  # track
            "standalone",  # profile
            "postgres",  # database
            "none",  # edge_role
            False,  # redundancy
            False,  # IIoT
            False,  # customize modules
            "proxy",  # exposure -> reverse proxy
            True,  # join detected 'proxy' network?
            "generate",  # summary
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    proxy = outcome.config.reverse_proxy
    assert proxy is not None and proxy.mode == "external" and proxy.network == "proxy"


def test_wizard_proxy_names_network_when_undetected(monkeypatch) -> None:
    """No 'proxy' network detected -> user names the network (external mode)."""
    monkeypatch.setattr("ignition_stack.wizard._detect_proxy_network", lambda: [])
    prompter = ScriptedPrompter(
        [
            "quick",
            "standalone",
            "postgres",
            "none",
            False,
            False,
            False,
            "proxy",  # exposure
            "named",  # name an existing network
            "edge-net",  # network name
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    proxy = outcome.config.reverse_proxy
    assert proxy is not None and proxy.mode == "external" and proxy.network == "edge-net"


def test_wizard_proxy_scaffold_branch(tmp_path: Path, monkeypatch) -> None:
    """No detection + scaffold choice -> scaffold mode + README written."""
    monkeypatch.setattr("ignition_stack.wizard._detect_proxy_network", lambda: [])
    prompter = ScriptedPrompter(
        [
            "quick",
            "standalone",
            "postgres",
            "none",
            False,
            False,
            False,
            "proxy",  # exposure
            "scaffold",  # scaffold the repo
            "reverse-proxy",  # path
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    proxy = outcome.config.reverse_proxy
    assert proxy is not None and proxy.mode == "scaffold"
    write_project(outcome.config, tmp_path / "demo")
    assert (tmp_path / "demo" / "reverse-proxy" / "README.md").is_file()


def test_wizard_host_ports_skip_detection(monkeypatch) -> None:
    """Choosing host ports must never shell out to Docker."""

    def _boom() -> list[str]:
        raise AssertionError("detection ran for the host-ports path")

    monkeypatch.setattr("ignition_stack.wizard._detect_proxy_network", _boom)
    prompter = ScriptedPrompter(
        [
            "quick",
            "standalone",
            "postgres",
            "none",
            False,
            False,
            False,
            "ports",  # exposure -> host ports
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.config.reverse_proxy is None


# --------------------------------------------------------------------------- #
# Wizard core
# --------------------------------------------------------------------------- #


def test_wizard_yellow_tier_confirmed_keeps_spoke_count() -> None:
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "hub-and-spoke",  # profile
            6,  # spokes (yellow tier)
            "postgres",  # database
            "spoke",  # edge_role
            False,  # network split (off for hub-and-spoke)
            False,  # redundancy
            False,  # wire IIoT? -> no
            False,  # customize modules? -> accept lean default
            "ports",  # exposure: host ports
            True,  # advisory confirm
            "generate",  # summary action
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    # 1 hub + 6 spokes = 7 gateways.
    assert len(outcome.config.gateways) == 7


def test_wizard_yellow_tier_declined_falls_back_to_4_spokes() -> None:
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "hub-and-spoke",
            6,  # yellow tier
            "postgres",
            "spoke",
            False,  # network split
            False,  # redundancy
            False,  # wire IIoT? -> no
            False,  # customize modules? -> accept lean default
            "ports",
            False,  # decline advisory
            "generate",  # summary action
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    # Rolled back to 4 spokes (still green) so the wizard doesn't dead-end.
    assert len(outcome.config.gateways) == 5  # 1 hub + 4 spokes


def test_wizard_red_tier_confirmed_sets_force() -> None:
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "hub-and-spoke",
            12,  # red tier
            "postgres",
            "spoke",
            False,  # network split
            False,  # redundancy
            False,  # wire IIoT? -> no
            False,  # customize modules? -> accept lean default
            "ports",
            True,  # acknowledge red
            "generate",  # summary action
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    assert outcome.options.force is True
    assert len(outcome.config.gateways) == 13


def test_wizard_red_tier_declined_falls_back() -> None:
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "hub-and-spoke",
            12,
            "postgres",
            "spoke",
            False,  # network split
            False,  # redundancy
            False,  # wire IIoT? -> no
            False,  # customize modules? -> accept lean default
            "ports",
            False,  # decline red
            "generate",  # summary action
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    # Rolled back to 4 spokes.
    assert len(outcome.config.gateways) == 5


def test_wizard_scaleout_frontends_and_network_split() -> None:
    """Scaleout wizard: frontend count + network-split confirm flow through to
    the resolved config."""
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "scaleout",  # profile
            2,  # frontend count
            "postgres",  # database
            "none",  # edge_role
            True,  # network split
            False,  # redundancy
            False,  # wire IIoT? -> no
            False,  # customize modules? -> accept lean default
            "ports",  # exposure: host ports
            "generate",  # summary action
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    # 2 frontends + 1 backend.
    assert len(outcome.config.gateways) == 3
    assert [g.name for g in outcome.config.gateways] == ["frontend-1", "frontend-2", "backend"]
    assert outcome.config.network_split is True


def test_wizard_scaleout_network_split_declined() -> None:
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "scaleout",
            1,  # single frontend
            "postgres",
            "none",
            False,  # network split off
            False,  # redundancy
            False,  # wire IIoT? -> no
            False,  # customize modules? -> accept lean default
            "ports",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    assert outcome.config.network_split is False


def test_wizard_summary_decline_marks_unconfirmed() -> None:
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "standalone",
            "postgres",
            "none",
            False,  # redundancy
            False,  # wire IIoT? -> no
            False,  # customize modules? -> accept lean default
            "ports",
            "cancel",  # cancel at summary
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed is False


# Module step (issue 42): the wizard pre-selects a curated default-enabled set
# and stores the *un*selected remainder as disable_builtins. These tests assert
# the inversion is exact and that the JDBC driver follows the database, rather
# than mirroring which slugs happen to be in the default set (that invariant
# lives in test_disable_builtins.py).


def _expected_lean_disable(db_kind: str) -> tuple[str, ...]:
    """The disable_builtins the decline path should yield for ``db_kind``: every
    built-in outside the curated default set and the matching JDBC driver."""
    cat = default_builtin_catalog()
    enabled = set(cat.default_enabled_slugs)
    driver = jdbc_driver_for(db_kind)
    if driver is not None:
        enabled.add(driver)
    return tuple(sorted(cat.slugs - enabled))


def test_wizard_modules_decline_applies_lean_default() -> None:
    """The one-keystroke common path (decline "Customize?") must still produce a
    lean gateway: disable_builtins is everything outside the curated set + the
    matching JDBC driver. Wiring this to empty would make the feature a no-op on
    the path everyone takes."""
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "standalone",  # profile
            "postgres",  # database
            "none",  # edge_role
            False,  # redundancy
            False,  # wire IIoT? -> no
            False,  # customize modules? -> accept lean default, no checkbox
            "ports",  # exposure: host ports
            "generate",  # summary action
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed

    expected = _expected_lean_disable("postgres")
    assert outcome.options.disable_builtins == expected
    # The default set is kept, the rest disabled, the matching driver kept and
    # the non-matching one dropped.
    disabled = set(outcome.options.disable_builtins)
    assert "perspective" not in disabled and "vision" in disabled
    assert "postgresql-jdbc-driver" not in disabled
    assert "mariadb-jdbc-driver" in disabled
    # Applied uniformly to every gateway.
    for gw in outcome.config.gateways:
        assert gw.disable_builtins == list(expected)
    # The summary shows the lean ENABLED set by display name, not the long
    # disabled list.
    assert any("Perspective" in line and "PostgreSQL JDBC Driver" in line for line in outcome.summary_lines)


def test_wizard_modules_jdbc_driver_follows_database() -> None:
    """Choosing MariaDB enables the MariaDB JDBC driver and disables the
    Postgres one - the driver tracks the database, not a static default."""
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "standalone",
            "mariadb",  # database -> MariaDB JDBC driver
            "none",
            False,  # redundancy
            False,  # wire IIoT? -> no
            False,  # accept lean default
            "ports",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    disabled = set(outcome.options.disable_builtins)
    assert "mariadb-jdbc-driver" not in disabled
    assert "postgresql-jdbc-driver" in disabled


def test_wizard_modules_customize_inverts_enabled_selection() -> None:
    """Customizing and choosing exactly which modules to ENABLE stores the
    inverse as disable_builtins, and the choice flows to every gateway."""
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "scaleout",  # profile
            1,  # frontend count
            "postgres",  # database
            "none",  # edge_role
            True,  # network split
            False,  # redundancy
            False,  # wire IIoT? -> no
            True,  # customize modules? -> opens checkbox
            ["perspective", "vision"],  # ENABLE only these two
            "ports",  # exposure: host ports
            "generate",  # summary action
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed

    all_slugs = default_builtin_catalog().slugs
    expected = tuple(sorted(all_slugs - {"perspective", "vision"}))
    assert outcome.options.disable_builtins == expected
    disabled = set(outcome.options.disable_builtins)
    assert "perspective" not in disabled and "vision" not in disabled
    assert "opc-ua" in disabled  # a default-on module the user chose to drop
    for gw in outcome.config.gateways:
        assert gw.disable_builtins == list(expected)


def test_wizard_modules_no_database_enables_no_jdbc_driver() -> None:
    """With no database, no JDBC driver is pre-enabled, so all three drivers end
    up disabled."""
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "standalone",
            "none",  # database -> None, no JDBC driver
            "none",  # edge_role
            False,  # redundancy
            False,  # wire IIoT? -> no
            False,  # accept lean default
            "ports",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    disabled = set(outcome.options.disable_builtins)
    assert {"postgresql-jdbc-driver", "mariadb-jdbc-driver", "mssql-jdbc-driver"} <= disabled


# --------------------------------------------------------------------------- #
# Phase 7 track gate: quick is the unchanged linear flow
# --------------------------------------------------------------------------- #


def test_wizard_quick_track_matches_profile_build_exactly() -> None:
    """For the same answers, the quick track yields exactly the config the
    non-interactive profile path builds: the track gate and the new prompts
    (IIoT declined) change nothing about the produced object."""
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "standalone",
            "postgres",
            "none",  # edge_role
            False,  # redundancy
            False,  # wire IIoT? -> no
            False,  # accept lean module default
            "ports",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    expected = build_profile("standalone", "demo", ProfileOptions(disable_builtins=outcome.options.disable_builtins))
    assert outcome.config.model_dump(mode="json") == expected.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# IIoT confirm (phase 7): the quick track gains an MQTT-pipeline overlay prompt
# --------------------------------------------------------------------------- #


def test_wizard_iiot_declined_leaves_no_broker() -> None:
    """Declining the IIoT confirm wires no broker and no mqtt attachments."""
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "standalone",
            "postgres",
            "none",  # edge_role
            False,  # redundancy
            False,  # wire IIoT? -> no
            False,  # accept lean default
            "ports",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.options.iiot is False
    assert not any(inst.service in {"chariot", "emqx", "hivemq", "rabbitmq"} for inst in outcome.config.service_instances)
    assert not any(att.role in {"mqtt-transmission", "mqtt-engine"} for gw in outcome.config.gateways for att in gw.services)


def test_wizard_iiot_accepted_default_chariot_wired_by_role() -> None:
    """Accepting IIoT with the default broker wires chariot, Transmission to the
    edge-side role and Engine to the central role (apply_iiot semantics)."""
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "scaleout",
            1,  # frontend count
            "postgres",
            "none",  # edge_role
            True,  # network split
            False,  # redundancy
            True,  # wire IIoT? -> yes
            "chariot",  # broker (default)
            False,  # accept lean default
            "ports",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.options.iiot is True
    assert outcome.options.iiot_broker == "chariot"
    assert any(inst.id == "chariot" for inst in outcome.config.service_instances)
    by_name = {gw.name: gw for gw in outcome.config.gateways}
    frontend_roles = {att.role for att in by_name["frontend"].services if att.instance == "chariot"}
    backend_roles = {att.role for att in by_name["backend"].services if att.instance == "chariot"}
    assert frontend_roles == {"mqtt-transmission"}
    assert backend_roles == {"mqtt-engine"}


# --------------------------------------------------------------------------- #
# Real QuestionaryPrompter adapter
#
# Regression: `select()` must hand questionary the choice *value* (the slug)
# as `default`, not its rendered label. Questionary validates `default`
# against choice values and raises ValueError otherwise — which crashed
# `init` at the very first profile prompt. The ScriptedPrompter used above
# bypasses this adapter, so it needs its own coverage.
#
# These spy on questionary's entry points rather than letting prompt_toolkit
# build a real prompt: construction queries the Windows console screen buffer
# and crashes in headless CI (NoConsoleScreenBufferError). The spy captures
# what the adapter passes and asserts questionary's own contract directly.
# --------------------------------------------------------------------------- #


class _StubQuestion:
    """Stands in for the questionary ``Question`` the spies return, so the
    adapter's ``.unsafe_ask()`` call yields a known answer without any
    prompt_toolkit / console machinery."""

    def __init__(self, answer: str) -> None:
        self._answer = answer

    def unsafe_ask(self) -> str:
        return self._answer


def test_questionary_select_default_resolves_to_choice_value(monkeypatch) -> None:
    import questionary

    # Same (value, label) shape the wizard builds: a padded label whose text
    # is *not* a valid questionary default.
    choices = [(p.slug, f"{p.slug:<14} - {p.summary}") for p in list_profiles()]
    captured: dict = {}

    def spy_select(message, *, choices, default=None, **kwargs):
        captured["choices"] = choices
        captured["default"] = default
        return _StubQuestion(choices[0].value)

    monkeypatch.setattr(questionary, "select", spy_select)

    answer = QuestionaryPrompter().select("Architecture profile?", choices, default="standalone")

    # questionary's contract: `default` must be a choice value (or None). The
    # bug passed the rendered label, which is not in this set.
    values = [c.value for c in captured["choices"]]
    assert captured["default"] in values
    assert captured["default"] == "standalone"
    assert answer == values[0]  # round-trips the selected slug


def test_questionary_select_drops_unknown_default(monkeypatch) -> None:
    """A default that isn't among the choices falls back to None rather than
    being passed through — questionary would reject an unknown default."""
    import questionary

    choices = [(p.slug, p.summary) for p in list_profiles()]
    captured: dict = {}

    def spy_select(message, *, choices, default=None, **kwargs):
        captured["default"] = default
        return _StubQuestion("scaleout")

    monkeypatch.setattr(questionary, "select", spy_select)

    answer = QuestionaryPrompter().select("Profile?", choices, default="does-not-exist")
    assert captured["default"] is None
    assert answer == "scaleout"


def test_questionary_checkbox_forwards_checked_flag(monkeypatch) -> None:
    """The opt-in module step relies on questionary pre-checking the default
    set. The adapter must forward each triple's ``checked`` flag onto the
    ``Choice``; dropping it would render every row unchecked and silently defeat
    the curated default. The ScriptedPrompter bypasses this adapter, so it needs
    its own guard."""
    import questionary

    captured: dict = {}

    def spy_checkbox(message, *, choices, **kwargs):
        captured["choices"] = choices
        return _StubQuestion([choices[0].value])

    monkeypatch.setattr(questionary, "checkbox", spy_checkbox)

    triples = [("perspective", "Perspective", True), ("vision", "Vision", False)]
    answer = QuestionaryPrompter().checkbox("Select modules to ENABLE:", triples)

    checked_by_value = {c.value: c.checked for c in captured["choices"]}
    assert checked_by_value == {"perspective": True, "vision": False}
    assert answer == ["perspective"]
