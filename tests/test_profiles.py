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

from ignition_stack.catalog.loader import load_catalog
from ignition_stack.cli import app
from ignition_stack.compose.engine import render_compose
from ignition_stack.compose.writer import write_project
from ignition_stack.config import ReverseProxyConfig
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
        pytest.fail(
            f"compose output diverges from golden '{rel_path}'.\n"
            "Run with UPDATE_GOLDENS=1 to update if the change is intentional.\n\n"
            f"{diff}"
        )


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

    def select(self, message: str, choices: Sequence[tuple[str, str]], default=None) -> str:
        return self._next()

    def text(self, message: str, default: str = "") -> str:
        return self._next()

    def confirm(self, message: str, default: bool = False) -> bool:
        return self._next()

    def integer(self, message: str, default: int, minimum: int = 0) -> int:
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
    """The matrix marks gateway-network-link partial: the link is set up via
    POST-SETUP (UI approval) rather than fully file-seeded. The scaleout
    profile must surface that step in the generated project."""
    config = build_profile("scaleout", "demo", ProfileOptions())
    assert config.profile == "scaleout"  # resolved-config assertion
    write_project(config, tmp_path / "demo")
    post_setup = (tmp_path / "demo" / "POST-SETUP.md").read_text(encoding="utf-8")
    assert "gateway-network" in post_setup.lower()
    assert "frontend" in post_setup
    assert "backend" in post_setup
    # Cite the matrix row name so the future Phase-1 follow-up that pins the
    # JSON schema can grep for it.
    assert "gateway-network-link" in post_setup


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


def test_scaleout_cli_reverse_proxy_traefik(tmp_path: Path) -> None:
    """--reverse-proxy traefik scaffolds the proxy README at the default path."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "init",
            "demo",
            "--profile",
            "scaleout",
            "--reverse-proxy",
            "traefik",
            "-o",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    readme = tmp_path / "demo" / "reverse-proxy" / "README.md"
    assert readme.is_file()
    assert "ia-eknorr/traefik-reverse-proxy" in readme.read_text(encoding="utf-8")


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
    # Scripted answers in wizard order: profile -> db -> edge_role -> proxy -> confirm
    prompter = ScriptedPrompter(
        [
            "mcp-n8n",  # profile
            "postgres",  # database
            "none",  # edge_role
            "external",  # reverse proxy
            True,  # summary confirm
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
# Reverse-proxy prompt (Phase 6 validation 4)
# --------------------------------------------------------------------------- #


def test_external_proxy_emits_plain_host_port_mapping(tmp_path: Path) -> None:
    """The 'I already have one' branch -> no proxy service, plain ports."""
    config = build_profile("standalone", "demo", ProfileOptions(reverse_proxy=None))
    write_project(config, tmp_path / "demo")
    compose = (tmp_path / "demo" / "docker-compose.yaml").read_text(encoding="utf-8")
    parsed = _parse(compose)
    services = parsed["services"]
    # Gateway has the plain host:container mapping; no proxy service in the file.
    assert "ports" in services["gateway"]
    assert "reverse-proxy" not in services
    assert "traefik" not in services
    # And no proxy scaffold was written.
    assert not (tmp_path / "demo" / "reverse-proxy").exists()


def test_install_traefik_emits_scaffold_at_chosen_path(tmp_path: Path) -> None:
    """The 'install Traefik' branch -> reverse-proxy/README.md at chosen path."""
    config = build_profile(
        "standalone",
        "demo",
        ProfileOptions(reverse_proxy=ReverseProxyConfig(path="infra/proxy")),
    )
    write_project(config, tmp_path / "demo")
    readme = tmp_path / "demo" / "infra" / "proxy" / "README.md"
    assert readme.is_file()
    body = readme.read_text(encoding="utf-8")
    assert "ia-eknorr/traefik-reverse-proxy" in body
    # And a POST-SETUP stub points at the proxy directory.
    post_setup = (tmp_path / "demo" / "POST-SETUP.md").read_text(encoding="utf-8")
    assert "infra/proxy" in post_setup


def test_install_traefik_wizard_branch(tmp_path: Path) -> None:
    """Wizard 'install Traefik' answer drives the same scaffold."""
    prompter = ScriptedPrompter(
        [
            "standalone",  # profile
            "postgres",  # database
            "none",  # edge_role
            "install",  # reverse proxy
            "reverse-proxy",  # path
            True,  # summary confirm
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    write_project(outcome.config, tmp_path / "demo")
    assert (tmp_path / "demo" / "reverse-proxy" / "README.md").is_file()


# --------------------------------------------------------------------------- #
# Wizard core
# --------------------------------------------------------------------------- #


def test_wizard_yellow_tier_confirmed_keeps_spoke_count() -> None:
    prompter = ScriptedPrompter(
        [
            "hub-and-spoke",  # profile
            6,  # spokes (yellow tier)
            "postgres",  # database
            "spoke",  # edge_role
            False,  # network split (off for hub-and-spoke)
            "external",  # proxy
            True,  # advisory confirm
            True,  # summary confirm
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    # 1 hub + 6 spokes = 7 gateways.
    assert len(outcome.config.gateways) == 7


def test_wizard_yellow_tier_declined_falls_back_to_4_spokes() -> None:
    prompter = ScriptedPrompter(
        [
            "hub-and-spoke",
            6,  # yellow tier
            "postgres",
            "spoke",
            False,  # network split
            "external",
            False,  # decline advisory
            True,  # summary confirm
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    # Rolled back to 4 spokes (still green) so the wizard doesn't dead-end.
    assert len(outcome.config.gateways) == 5  # 1 hub + 4 spokes


def test_wizard_red_tier_confirmed_sets_force() -> None:
    prompter = ScriptedPrompter(
        [
            "hub-and-spoke",
            12,  # red tier
            "postgres",
            "spoke",
            False,  # network split
            "external",
            True,  # acknowledge red
            True,  # summary confirm
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    assert outcome.options.force is True
    assert len(outcome.config.gateways) == 13


def test_wizard_red_tier_declined_falls_back() -> None:
    prompter = ScriptedPrompter(
        [
            "hub-and-spoke",
            12,
            "postgres",
            "spoke",
            False,  # network split
            "external",
            False,  # decline red
            True,  # summary confirm
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
            "scaleout",  # profile
            2,  # frontend count
            "postgres",  # database
            "none",  # edge_role
            True,  # network split
            "external",  # reverse proxy
            True,  # summary confirm
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
            "scaleout",
            1,  # single frontend
            "postgres",
            "none",
            False,  # network split off
            "external",
            True,
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    assert outcome.config.network_split is False


def test_wizard_summary_decline_marks_unconfirmed() -> None:
    prompter = ScriptedPrompter(
        [
            "standalone",
            "postgres",
            "none",
            "external",
            False,  # decline at summary
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed is False


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


def test_questionary_integer_coerces_and_validates(monkeypatch) -> None:
    """The integer adapter (the hub-and-spoke spoke-count prompt) feeds
    questionary a string default and an inline validator, then coerces the
    answer back to int. Pure coverage — this path was never broken — capturing
    the validator lets us exercise each branch directly."""
    import questionary

    captured: dict = {}

    def spy_text(message, *, default=None, validate=None, **kwargs):
        captured["default"] = default
        captured["validate"] = validate
        return _StubQuestion("5")

    monkeypatch.setattr(questionary, "text", spy_text)

    result = QuestionaryPrompter().integer("How many spokes?", default=3, minimum=2)
    assert result == 5  # answer coerced str -> int
    assert captured["default"] == "3"  # default coerced int -> str for the text prompt

    validate = captured["validate"]
    assert validate("abc") == "Enter an integer."
    assert validate("1") == "Must be >= 2."
    assert validate("2") is True
