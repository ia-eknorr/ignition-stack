"""Phase 7 acceptance tests for the ``POST-SETUP.md`` generator.

The matrix contract has two halves, and both are asserted here:

1. A stack with >=1 manual-secret connection gets one section per manual step,
   and each section carries the three things a user needs to finish it by hand:
   the deep-link URL to open, the in-UI screen path to navigate to, and the
   exact ``.env`` variable name to copy.
2. A fully-seedable stack (standalone + Postgres) states, unambiguously, that
   no manual steps are required.
"""

from __future__ import annotations

from pathlib import Path

from ignition_stack.compose import write_project
from ignition_stack.config import ProjectConfig
from ignition_stack.postsetup import generate_post_setup
from ignition_stack.profiles import ProfileOptions, build_profile
from ignition_stack.services.resolver import resolve


def _resolved(**kwargs: object) -> ProjectConfig:
    """Build + resolve a config the way the writer does before generating."""
    return resolve(ProjectConfig(**kwargs))  # type: ignore[arg-type]


def test_fully_seedable_stack_states_no_manual_steps() -> None:
    """Standalone + Postgres seeds everything; the doc must say so and list no steps."""
    body = generate_post_setup(_resolved(name="demo"))
    assert "no manual steps required" in body.lower()
    # No "## " heading means no per-step sections were emitted.
    assert "## " not in body


def test_manual_secret_connection_carries_url_screen_and_env_var() -> None:
    """An MQTT broker defers the gateway connection: copy the broker secret by hand.

    This is the canonical manual-secret case - the section must give all three
    elements the validation contract requires.
    """
    body = generate_post_setup(_resolved(name="demo", services=["chariot"]))

    assert "## Link the gateway to the MQTT broker (chariot)" in body
    # 1) deep-link URL into the gateway UI
    assert "http://localhost:9088" in body
    # 2) in-UI screen path
    assert "Config -> MQTT Engine" in body
    # 3) the exact .env variable name to copy
    assert "CHARIOT_ADMIN_PASSWORD" in body


def test_identity_provider_step_is_a_verification_not_a_paste() -> None:
    """Phase 5 seeds the OIDC connection; the section verifies, it does not configure.

    The Keycloak OIDC IdP is now file-seeded end to end (fixed demo client secret
    + embedded JWE), so the post-setup section mirrors the gateway-network /
    redundancy verification notes: confirm a Test Login works, don't paste a
    secret. It still carries the gateway + Keycloak deep-links and the demo
    credentials a reader needs to run the check.
    """
    body = generate_post_setup(_resolved(name="demo", services=["keycloak"]))

    # Framed as a verification, not a manual paste of a runtime-generated secret.
    assert "Verify the OIDC identity provider (Keycloak)" in body
    assert "Test Login" in body
    assert "verification, not a manual" in body
    # The seeded demo user the reader signs in as.
    assert "`demo` / `demo`" in body
    # Deep-links: Keycloak admin console (port) + the gateway IdP screen.
    assert "http://localhost:8081" in body
    assert "Identity Providers" in body


def test_one_section_per_deferred_connection() -> None:
    """Each service with a post_setup item contributes exactly one heading."""
    body = generate_post_setup(_resolved(name="demo", services=["chariot", "opcua-sim", "modbus-sim", "kafka"]))
    # Four services, each declaring one deferred connection -> four sections.
    assert body.count("\n## ") == 4


def test_writer_writes_post_setup_with_manual_step(tmp_path: Path) -> None:
    """The writer always emits POST-SETUP.md; here it carries the broker step."""
    write_project(ProjectConfig(name="demo", services=["chariot"]), tmp_path / "demo")
    body = (tmp_path / "demo" / "POST-SETUP.md").read_text(encoding="utf-8")
    assert "CHARIOT_ADMIN_PASSWORD" in body


def test_writer_writes_no_manual_steps_for_default_stack(tmp_path: Path) -> None:
    """A bare standalone+Postgres project still gets a POST-SETUP.md, stating none."""
    write_project(ProjectConfig(name="demo"), tmp_path / "demo")
    body = (tmp_path / "demo" / "POST-SETUP.md").read_text(encoding="utf-8")
    assert "no manual steps required" in body.lower()
    assert b"\r" not in (tmp_path / "demo" / "POST-SETUP.md").read_bytes()


# --------------------------------------------------------------------------- #
# IIoT overlay: gateway-aware, pre-filled MQTT steps (issue #43 Phase 3)
# --------------------------------------------------------------------------- #


def _iiot_post_setup(profile: str, name: str, **opts: object) -> str:
    config = build_profile(profile, name, ProfileOptions(iiot=True, **opts))  # type: ignore[arg-type]
    return generate_post_setup(resolve(config))


def test_iiot_hub_and_spoke_names_engine_hub_and_transmission_spokes() -> None:
    """Transmission steps name each spoke with prefilled Sparkplug identity;
    the Engine step names the hub. Broker endpoint comes from wires.mqtt."""
    body = _iiot_post_setup("hub-and-spoke", "plant", spokes=2)

    # Engine on the hub.
    assert "**MQTT Engine**" in body
    assert "On **hub**" in body
    assert "spBv1.0" in body  # Engine subscribes to all groups by default.

    # Transmission on each spoke, with Group ID = project, Edge Node ID = gw name.
    assert "**MQTT Transmission**" in body
    for spoke in ("spoke-1", "spoke-2"):
        assert f"On **{spoke}**" in body
        assert f"Edge Node ID = `{spoke}`" in body
    assert "Group ID = `plant`" in body

    # Broker endpoint from wires.mqtt (tcp://<broker-id>:<port>).
    assert "tcp://chariot:1883" in body
    # The trial note is present so users know no license is required.
    assert "trial" in body.lower()


def test_iiot_scaleout_engine_on_backend_transmission_on_frontends() -> None:
    body = _iiot_post_setup("scaleout", "edge", frontends=2)
    assert "On **backend**" in body
    for front in ("frontend-1", "frontend-2"):
        assert f"On **{front}**" in body
        assert f"Edge Node ID = `{front}`" in body


def test_iiot_standalone_single_gateway_runs_both_roles() -> None:
    body = _iiot_post_setup("standalone", "solo")
    assert "**MQTT Engine**" in body
    assert "**MQTT Transmission**" in body
    assert "On **gateway**" in body
    assert "Edge Node ID = `gateway`" in body
