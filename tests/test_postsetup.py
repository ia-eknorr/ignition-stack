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


def test_identity_provider_step_names_screen_and_env_keys() -> None:
    """Keycloak's OIDC secret is runtime-generated; its section guides the paste."""
    body = generate_post_setup(_resolved(name="demo", services=["keycloak"]))

    assert "Config -> Security -> Identity Providers" in body
    # The Keycloak admin console deep-link plus its preset .env keys.
    assert "http://localhost:8081" in body
    assert "KEYCLOAK_HTTP_PORT" in body


def test_one_section_per_deferred_connection() -> None:
    """Each service with a post_setup item contributes exactly one heading."""
    body = generate_post_setup(
        _resolved(name="demo", services=["chariot", "opcua-sim", "modbus-sim", "kafka"])
    )
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
