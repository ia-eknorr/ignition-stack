"""Phase 7 acceptance tests for the ``POST-SETUP.md`` generator.

The matrix contract has two halves, and both are asserted here:

1. A stack with >=1 manual-secret connection gets one section per manual step,
   and each section carries the three things a user needs to finish it by hand:
   the deep-link URL to open, the in-UI screen path to navigate to, and the
   exact ``.env`` variable name to copy.
2. A fully-seedable stack (basic + Postgres) states, unambiguously, that
   no manual steps are required.
"""

from __future__ import annotations

from pathlib import Path

from ignition_stack.architectures import ArchOptions, build_architecture
from ignition_stack.compose import write_project
from ignition_stack.config import ProjectConfig, ReverseProxyConfig, ServiceAttachment, ServiceInstance
from ignition_stack.config.schema import GatewayConfig
from ignition_stack.postsetup import generate_post_setup
from ignition_stack.services.resolver import resolve


def _resolved(**kwargs: object) -> ProjectConfig:
    """Build + resolve a config the way the writer does before generating."""
    return resolve(ProjectConfig(**kwargs))  # type: ignore[arg-type]


def test_fully_seedable_stack_states_no_manual_steps() -> None:
    """Standalone + Postgres seeds everything; the doc must say so and list no steps.

    The Connections reference section is always appended (issue #68), so the
    body now contains ``## Connections reference`` even for fully-seedable stacks.
    The test only asserts no *manual-step* ``## `` heading appears before the
    connections divider (``---``).
    """
    body = generate_post_setup(_resolved(name="demo"))
    assert "no manual steps required" in body.lower()
    # The body before the connections divider must carry no ## heading.
    pre_connections = body.split("---", 1)[0]
    assert "## " not in pre_connections


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
    """Each service with a post_setup item contributes exactly one heading.

    The Connections reference section (issue #68) adds one additional ``## ``
    heading after the ``---`` divider, so we count only those before the divider.
    """
    body = generate_post_setup(_resolved(name="demo", services=["chariot", "opcua-sim", "modbus-sim", "kafka"]))
    # Count manual-step headings only (before the connections divider).
    pre_connections = body.split("---", 1)[0]
    # Four services, each declaring one deferred connection -> four sections.
    assert pre_connections.count("\n## ") == 4


def test_writer_writes_post_setup_with_manual_step(tmp_path: Path) -> None:
    """The writer always emits POST-SETUP.md; here it carries the broker step."""
    write_project(ProjectConfig(name="demo", services=["chariot"]), tmp_path / "demo")
    body = (tmp_path / "demo" / "POST-SETUP.md").read_text(encoding="utf-8")
    assert "CHARIOT_ADMIN_PASSWORD" in body


def test_writer_writes_no_manual_steps_for_default_stack(tmp_path: Path) -> None:
    """A bare basic+Postgres project still gets a POST-SETUP.md, stating none."""
    write_project(ProjectConfig(name="demo"), tmp_path / "demo")
    body = (tmp_path / "demo" / "POST-SETUP.md").read_text(encoding="utf-8")
    assert "no manual steps required" in body.lower()
    assert b"\r" not in (tmp_path / "demo" / "POST-SETUP.md").read_bytes()


# --------------------------------------------------------------------------- #
# IIoT overlay: gateway-aware, pre-filled MQTT steps (issue #43 Phase 3)
# --------------------------------------------------------------------------- #


def _iiot_post_setup(arch: str, name: str, **opts: object) -> str:
    config = build_architecture(arch, name, ArchOptions(iiot=True, **opts))  # type: ignore[arg-type]
    return generate_post_setup(resolve(config))


def test_iiot_hub_and_spoke_names_engine_hub_and_transmission_spokes() -> None:
    """The chariot pipeline is now seeded, so the step is a verification: it names
    each spoke's seeded Sparkplug identity and the hub's Engine, plus the two
    trial caveats. Broker endpoint comes from wires.mqtt."""
    body = _iiot_post_setup("hub-and-spoke", "plant", spokes=2)

    # Framed as a verification (chariot seeds the connections), not a manual paste.
    assert "Verify the MQTT Sparkplug pipeline (chariot)" in body
    assert "verification, not a manual" in body

    # Engine on the hub.
    assert "Engine on hub" in body

    # Transmission on each spoke, with Group ID = project, Edge Node ID = gw name.
    for spoke in ("spoke-1", "spoke-2"):
        assert f"Transmission on {spoke}" in body
        assert f"Edge Node ID `{spoke}`" in body
    assert "Group ID `plant`" in body

    # Broker endpoint from wires.mqtt (tcp://<broker-id>:<port>).
    assert "tcp://chariot:1883" in body
    # Both trial caveats: Ignition's 2h platform trial + the broker license gate.
    assert "2-hour platform trial" in body
    assert "chariot-trial" in body


def test_iiot_scale_out_engine_on_backend_transmission_on_frontends() -> None:
    body = _iiot_post_setup("scale-out", "edge", frontends=2)
    assert "Engine on backend" in body
    for front in ("frontend-1", "frontend-2"):
        assert f"Transmission on {front}" in body
        assert f"Edge Node ID `{front}`" in body


def test_iiot_basic_single_gateway_runs_both_roles() -> None:
    body = _iiot_post_setup("basic", "solo")
    assert "Engine on gateway" in body
    assert "Transmission on gateway" in body
    assert "Edge Node ID `gateway`" in body


def test_iiot_unverified_broker_keeps_manual_procedure() -> None:
    """A broker whose seeded connection was not live-verified (emqx) stays a
    manual paste, not a verification - only chariot was proven."""
    body = _iiot_post_setup("hub-and-spoke", "plant", spokes=2, iiot_broker="emqx")
    assert "Link the gateway to the MQTT broker (emqx)" in body
    assert "set by hand" in body
    assert "Group ID = `plant`" in body
    assert "tcp://emqx:1883" in body


# --------------------------------------------------------------------------- #
# Connections reference section (issue #68 Phase C)
# --------------------------------------------------------------------------- #


def _connections(body: str) -> str:
    """Extract the Connections section from a POST-SETUP.md body."""
    return body.split("---", 1)[1] if "---" in body else ""


def test_connections_section_always_present() -> None:
    """The Connections reference section appears in all POST-SETUP.md outputs."""
    # Fully-seedable stack (no manual steps).
    seedable = generate_post_setup(_resolved(name="demo"))
    assert "## Connections reference" in seedable

    # Stack with deferred steps.
    with_steps = generate_post_setup(_resolved(name="demo", services=["chariot"]))
    assert "## Connections reference" in with_steps


def test_connections_section_lists_gateways() -> None:
    """Each gateway in the stack has a row with its web UI URL."""
    body = generate_post_setup(_resolved(name="demo"))
    conn = _connections(body)
    # Single gateway: label is just "gateway"
    assert "### gateway" in conn
    assert "http://gateway:8088" in conn
    assert "http://localhost:9088" in conn


def test_connections_section_postgres_exact_strings() -> None:
    """Postgres connection strings and credentials are exact."""
    body = generate_post_setup(_resolved(name="demo"))
    conn = _connections(body)
    assert "jdbc:postgresql://db:5432/ignition" in conn
    assert "`DB_USER` in `.env` (default: `ignition`)" in conn
    assert "`DB_PASSWORD` in `.env` (default: `ignition`)" in conn


def test_connections_section_chariot_exact_strings() -> None:
    """Chariot MQTT connection string, credentials, and quirk note are exact."""
    body = generate_post_setup(_resolved(name="demo", services=["chariot"]))
    conn = _connections(body)
    assert "tcp://chariot:1883" in conn
    assert "`CHARIOT_ADMIN_PASSWORD` in `.env` (default: `password`)" in conn
    # The Chariot quirk must be stated correctly: MQTT auth is the image-seeded
    # admin/changeme, NOT CHARIOT_ADMIN_PASSWORD (web admin UI only).
    assert "`admin` / `changeme`" in conn
    assert "only sets the web admin UI password" in conn


def test_connections_section_keycloak_exact_strings() -> None:
    """Keycloak in-network URI and host-access URL are exact."""
    body = generate_post_setup(_resolved(name="demo", services=["keycloak"]))
    conn = _connections(body)
    assert "http://keycloak:8080" in conn
    assert "localhost:8081" in conn
    assert "`KEYCLOAK_ADMIN_USER` in `.env` (default: `admin`)" in conn
    assert "`KEYCLOAK_ADMIN_PASSWORD` in `.env` (default: `admin`)" in conn


def test_connections_section_proxy_mode_gateway_url() -> None:
    """In proxy mode the gateway row shows the localtest.me URL, not localhost:PORT."""
    config = resolve(ProjectConfig(name="demo", reverse_proxy=ReverseProxyConfig(mode="external")))
    body = generate_post_setup(config)
    conn = _connections(body)
    assert "http://demo.localtest.me" in conn
    # The plain host:port must NOT appear for a proxied gateway.
    assert "localhost:9088" not in conn


def test_connections_section_flat_broker_uses_instance_id() -> None:
    """A flat (unattached) broker row uses its instance id, not the manifest slug."""
    config = resolve(
        ProjectConfig(
            name="demo",
            service_instances=[
                ServiceInstance(id="db", service="postgres"),
                ServiceInstance(id="mqtt", service="chariot"),
            ],
            gateways=[GatewayConfig(name="gateway", services=[ServiceAttachment(instance="db")])],
        )
    )
    body = generate_post_setup(config)
    conn = _connections(body)
    # The flat instance id "mqtt" appears in the in-network address.
    assert "tcp://mqtt:1883" in conn
    # Label includes the slug + the custom id.
    assert "chariot (mqtt)" in conn


def test_connections_section_attached_and_flat_both_appear() -> None:
    """Both attached (wired) and flat (unattached) services appear in Connections."""
    config = resolve(
        ProjectConfig(
            name="demo",
            service_instances=[
                ServiceInstance(id="db", service="postgres"),
                ServiceInstance(id="flat-db", service="postgres"),
            ],
        )
    )
    body = generate_post_setup(config)
    conn = _connections(body)
    assert "jdbc:postgresql://db:5432/ignition" in conn
    assert "jdbc:postgresql://flat-db:5432/ignition" in conn
