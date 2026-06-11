"""The per-gateway composer, reached via the wizard summary's *tweak* action.

The composer is the per-gateway service editor the architecture-first wizard
lands in through the summary's *tweak* action, with the built config pre-filled.
These tests drive ``wizard.walk`` end to end with a scripted prompter, the same
harness ``test_architectures.py`` uses, and assert on the resolved registry the
composer produced.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ignition_stack.architectures import ArchOptions, build_architecture
from ignition_stack.catalog.builtins import default_builtin_catalog
from ignition_stack.compose.engine import render_compose
from ignition_stack.compose.writer import _render_env
from ignition_stack.config import dump_config, load_config
from ignition_stack.services.loader import load_all_services
from ignition_stack.services.resolver import resolve
from ignition_stack.wizard import BACK, walk
from ignition_stack.wizard_composer import (
    module_choices_for_gateway,
    mqtt_broker_choices,
    service_choices_for_gateway,
)


class ScriptedPrompter:
    """Pre-recorded answers, in order; also records checkbox choice triples.

    Mirrors ``test_architectures.ScriptedPrompter`` (kept local: the tests directory
    is not a package). ``checkbox_choices`` captures the ``(value, label,
    checked)`` triples each checkbox prompt offered, so tests can assert what
    was *pre-checked*, not just what the script answered.
    """

    def __init__(self, answers: list) -> None:
        self._answers = iter(answers)
        self.checkbox_choices: list[list[tuple[str, str, bool]]] = []

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
        self.checkbox_choices.append(list(choices))
        return self._next()


def _attachments(config, gw_name: str) -> set[tuple[str, str]]:
    gw = next(g for g in config.gateways if g.name == gw_name)
    return {(att.instance, att.role) for att in gw.services}


# --------------------------------------------------------------------------- #
# Wizard summary -> tweak handoff
# --------------------------------------------------------------------------- #


def test_tweak_handoff_adds_emqx_and_keeps_everything_else() -> None:
    """Basic+postgres -> tweak -> add emqx -> generate: the final
    config carries the emqx attachment and is otherwise identical to the
    pre-tweak build (resolved)."""
    prompter = ScriptedPrompter(
        [
            "basic",  # architecture
            "postgres",  # database
            "none",  # edge_role
            False,  # redundancy
            False,  # wire IIoT? -> no
            False,  # customize modules? -> accept lean default
            "ports",  # exposure: host ports
            False,  # services stage: add a service? -> no
            "tweak",  # summary action -> composer, pre-filled
            # composer loop:
            "add",  # action
            "emqx",  # which service
            "attach",  # placement: attach to gateway(s) (single gw auto-attaches)
            "consumer",  # broker role -> plain consumer
            "done",  # action
            "generate",  # composer summary
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    final = outcome.config

    assert any(inst.id == "emqx" and inst.service == "emqx" for inst in final.service_instances)
    assert ("emqx", "consumer") in _attachments(final, "gateway")

    # Strip the emqx delta; the remainder must equal the pre-tweak resolved build.
    stripped = final.model_copy(deep=True)
    stripped.service_instances = [inst for inst in stripped.service_instances if inst.id != "emqx"]
    stripped.gateways[0].services = [att for att in stripped.gateways[0].services if att.instance != "emqx"]
    expected = resolve(
        build_architecture(
            "basic",
            "demo",
            ArchOptions(disable_builtins=outcome.options.disable_builtins),
        )
    )
    assert stripped.model_dump(mode="json") == expected.model_dump(mode="json")


def test_summary_cancel_marks_unconfirmed() -> None:
    prompter = ScriptedPrompter(
        [
            "basic",
            "postgres",
            "none",
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",
            False,  # services stage: add a service? -> no
            "cancel",  # summary action
        ]
    )
    assert walk("demo", prompter).confirmed is False


def test_summary_preview_then_generate(capsys) -> None:
    """Preview at the summary prints the resolved config dump, then re-shows
    the prompt; choosing generate afterwards confirms the outcome and leaves
    the config identical to a direct generate."""
    base_prompter = ScriptedPrompter(
        [
            "basic",
            "postgres",
            "none",  # edge_role
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",
            False,  # services stage: add a service? -> no
            "generate",  # direct generate (reference)
        ]
    )
    base_outcome = walk("demo", base_prompter)

    preview_prompter = ScriptedPrompter(
        [
            "basic",
            "postgres",
            "none",
            False,
            False,
            False,
            "ports",
            False,  # services stage: add a service? -> no
            "preview",  # show the dump once …
            "generate",  # … then confirm
        ]
    )
    outcome = walk("demo", preview_prompter)
    assert outcome.confirmed
    # Config produced after preview must equal the no-preview path.
    assert outcome.config.model_dump(mode="json") == base_outcome.config.model_dump(mode="json")
    # The YAML dump was printed to stdout.
    out = capsys.readouterr().out
    assert "gateways" in out


def test_summary_preview_then_cancel(capsys) -> None:
    """Preview prints the dump and then re-shows the prompt; cancelling marks
    the outcome as unconfirmed."""
    prompter = ScriptedPrompter(
        [
            "basic",
            "postgres",
            "none",
            False,
            False,
            False,
            "ports",
            False,  # services stage: add a service? -> no
            "preview",  # print the dump …
            "cancel",  # … then bail
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed is False
    # The dump was still printed before the user cancelled.
    out = capsys.readouterr().out
    assert "gateways" in out


# --------------------------------------------------------------------------- #
# Composer reached via the summary's tweak action
# --------------------------------------------------------------------------- #


def test_composer_tweak_hub_and_spoke_with_shared_keycloak(capsys) -> None:
    """Hub-and-spoke with edge spokes and no database: tweak into the composer,
    attach keycloak to the hub, share it with an edge spoke (allowed: idp is not
    never_on_edge), reuse the auto-added postgres via the singleton-share path,
    and have a second database on the hub rejected with the state intact."""
    prompter = ScriptedPrompter(
        [
            "hub-and-spoke",  # architecture
            2,  # spokes
            "none",  # database -> start with no DB; the composer populates it
            "spoke",  # edge_role -> spokes run Edge
            False,  # network split
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",  # exposure
            False,  # services stage: add a service? -> no (use the composer instead)
            "tweak",  # summary -> composer, pre-filled
            # add keycloak to the hub (idp; multi-gateway -> placement checkbox):
            "add",
            "keycloak",
            "attach",
            ["hub"],
            # share keycloak with the edge spoke (allowed):
            "share",
            "keycloak",
            "spoke-1",
            # attach the hub to the auto-added postgres (singleton reused; postgres
            # is never_on_edge so the hub is the only eligible target -> auto):
            "add",
            "postgres",
            "attach",
            # a second database on the hub must be rejected (error surfaced):
            "add",
            "mariadb",
            "attach",
            # finish:
            "done",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    config = outcome.config

    # keycloak attached to hub and the edge spoke; its backing postgres exists.
    assert ("keycloak", "consumer") in _attachments(config, "hub")
    assert ("keycloak", "consumer") in _attachments(config, "spoke-1")
    # The hub shares the auto-added 'db' (keycloak's backing store).
    assert ("db", "consumer") in _attachments(config, "hub")
    # The second database was rejected: no mariadb instance, hub still has one db.
    assert not any(inst.service == "mariadb" for inst in config.service_instances)
    out = capsys.readouterr().out
    assert "error" in out and "database" in out
    # Edge spokes hold no database attachment.
    db_ids = {inst.id for inst in config.service_instances if inst.is_database}
    for spoke in ("spoke-1", "spoke-2"):
        assert not {a for a in _attachments(config, spoke) if a[0] in db_ids}


def test_databases_not_offered_to_edge_gateways() -> None:
    """The composer filters never_on_edge services out of an Edge gateway's
    catalog choices instead of erroring after selection."""
    catalog = load_all_services()
    config = resolve(build_architecture("hub-and-spoke", "demo", ArchOptions(spokes=1, database_kind=None)))
    hub = next(gw for gw in config.gateways if gw.name == "hub")
    spoke = next(gw for gw in config.gateways if gw.name == "spoke-1")
    assert spoke.ignition_edition == "edge"

    hub_slugs = {value for value, _ in service_choices_for_gateway(hub, catalog)}
    spoke_slugs = {value for value, _ in service_choices_for_gateway(spoke, catalog)}
    databases = {slug for slug, m in catalog.items() if m.kind == "database"}
    assert databases <= hub_slugs
    assert not (databases & spoke_slugs)
    # Non-database services (brokers, idp) remain offered on Edge.
    assert "emqx" in spoke_slugs and "keycloak" in spoke_slugs


def test_mqtt_broker_choices_lists_catalog_brokers_chariot_first() -> None:
    choices = mqtt_broker_choices()
    slugs = [value for value, _ in choices]
    catalog = load_all_services()
    assert set(slugs) == {slug for slug, m in catalog.items() if m.kind == "mqtt-broker"}
    assert slugs[0] == "chariot"


# --------------------------------------------------------------------------- #
# Per-gateway modules (#42 absorbed, per-gateway in the composer)
# --------------------------------------------------------------------------- #


def test_composer_per_gateway_modules_precheck_follows_that_gateways_db() -> None:
    """A pristine gateway's per-gateway checkbox pre-checks the curated set plus
    the JDBC driver for the database THIS gateway attaches to.

    A no-database basic stack whose single gateway is attached to a mariadb (id
    != slug guards against the legacy shorthand) carries no ``disable_builtins``,
    so ``module_choices_for_gateway`` pre-checks the #42 curated default plus the
    mariadb driver - not the static default."""
    config = resolve(
        build_architecture(
            "basic",
            "demo",
            ArchOptions(database_kind="mariadb"),
        )
    )
    gw = config.gateways[0]
    assert not gw.disable_builtins  # pristine: no per-gateway module choice yet
    choices = module_choices_for_gateway(config, gw)
    prechecked = {value for value, _, checked in choices if checked}
    catalog = default_builtin_catalog()
    assert prechecked == catalog.default_enabled_slugs | {"mariadb-jdbc-driver"}


def test_module_choices_precheck_current_state_when_already_customized() -> None:
    """A gateway that already carries disable_builtins is pre-checked with its
    current enabled set, not the curated default."""
    config = resolve(build_architecture("basic", "demo", ArchOptions(disable_builtins=("vision", "sfc"))))
    gw = config.gateways[0]
    choices = module_choices_for_gateway(config, gw)
    prechecked = {value for value, _, checked in choices if checked}
    assert prechecked == default_builtin_catalog().slugs - {"vision", "sfc"}


# --------------------------------------------------------------------------- #
# Flagship composability: the issue's three-gateway heterogeneous stack
# --------------------------------------------------------------------------- #


def test_composer_expresses_the_issue_heterogeneous_stack() -> None:
    """gw1 (hub) runs EMQX (Engine side) + Keycloak; gw2 shares the same
    Keycloak and has its own Mongo; the edge spoke publishes over MQTT
    (Transmission) and never touches a database."""
    prompter = ScriptedPrompter(
        [
            "hub-and-spoke",  # architecture
            2,  # spokes
            "none",  # database -> none; the composer populates the registry
            "spoke",  # spokes run Edge
            False,  # network split
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",  # exposure
            False,  # services stage: add a service? -> no (use the composer)
            "tweak",  # summary -> composer
            # gw2 = spoke-1, flipped to standard so it may hold a database:
            "edition",
            "spoke-1",
            "standard",
            # gw1 = hub: emqx as the central Engine side (multi-gateway placement):
            "add",
            "emqx",
            "attach",
            ["hub"],
            "mqtt-engine",  # broker attachment role
            # edge spoke publishes through the same broker:
            "share",
            "emqx",
            "spoke-2",
            "mqtt-transmission",
            # gw1: keycloak,
            "add",
            "keycloak",
            "attach",
            ["hub"],
            # gw2 shares the SAME keycloak instance:
            "share",
            "keycloak",
            "spoke-1",
            # gw2 gets its own mongo (never_on_edge -> spoke-2 excluded from attach):
            "add",
            "mongo",
            "attach",
            ["spoke-1"],
            "done",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    config = outcome.config

    # Registry: emqx + keycloak + mongo + keycloak's auto-added postgres.
    by_id = {inst.id: inst.service for inst in config.service_instances}
    assert by_id == {"emqx": "emqx", "keycloak": "keycloak", "mongo": "mongo", "db": "postgres"}

    assert _attachments(config, "hub") == {("emqx", "mqtt-engine"), ("keycloak", "consumer")}
    assert _attachments(config, "spoke-1") == {("keycloak", "consumer"), ("mongo", "consumer")}
    assert _attachments(config, "spoke-2") == {("emqx", "mqtt-transmission")}

    # The mqtt attachments installed the matching Cirrus modules.
    by_name = {gw.name: gw for gw in config.gateways}
    assert "mqtt-engine" in by_name["hub"].modules
    assert "mqtt-transmission" in by_name["spoke-2"].modules

    # Keycloak's backing store is registry-level: attached to no gateway.
    assert not any(att.instance == "db" for gw in config.gateways for att in gw.services)
    # And it hosts keycloak's logical database.
    db = next(inst for inst in config.service_instances if inst.id == "db")
    assert "keycloak" in db.extra_databases


# --------------------------------------------------------------------------- #
# Round-trip fixed point
# --------------------------------------------------------------------------- #


def test_composer_config_round_trips_as_fixed_point(tmp_path: Path) -> None:
    """A composer-built heterogeneous config survives dump -> load -> resolve
    unchanged (the declarative -f parity contract)."""
    prompter = ScriptedPrompter(
        [
            "hub-and-spoke",
            2,
            "none",  # database
            "spoke",
            False,  # network split
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",  # exposure
            False,  # services stage: add a service? -> no (use the composer)
            "tweak",  # summary -> composer
            "edition",
            "spoke-1",
            "standard",
            "add",
            "emqx",
            "attach",
            ["hub"],
            "mqtt-engine",
            "share",
            "emqx",
            "spoke-2",
            "mqtt-transmission",
            "add",
            "keycloak",
            "attach",
            ["hub"],
            "share",
            "keycloak",
            "spoke-1",
            "add",
            "mongo",
            "attach",
            ["spoke-1"],
            "done",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed

    path = tmp_path / "stack.yaml"
    path.write_text(dump_config(outcome.config, "yaml"), encoding="utf-8")
    reloaded = resolve(load_config(path))
    assert reloaded.model_dump(mode="json") == outcome.config.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# Edit-loop resilience + remaining actions
# --------------------------------------------------------------------------- #


def test_composer_remove_share_rename_and_iiot_round_trip() -> None:
    """Exercise stack-level add, rename, the IIoT wire/unwire toggle, and
    remove (with last-attachment instance pruning) through the loop."""
    prompter = ScriptedPrompter(
        [
            "scale-out",  # architecture
            1,  # frontends
            "none",  # database
            "none",  # edge_role
            True,  # network split
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",  # exposure
            False,  # services stage: add a service? -> no (use the composer)
            "tweak",  # summary -> composer
            # flat (unattached) n8n:
            "flat",
            "n8n",
            False,  # MCP drop-in for n8n? -> no
            # rename it:
            "rename",
            "n8n",
            "automation",  # new id
            # wire IIoT with the default broker:
            "iiot",
            "chariot",
            # unwire it again:
            "iiot",
            True,  # confirm unwire
            # add postgres to the backend, then remove that sole attachment
            # (row 0: the preset itself attaches nothing) which prunes the
            # now-unused instance:
            "add",
            "postgres",
            "attach",
            ["backend"],
            "remove",
            "0",
            "done",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    config = outcome.config

    # The renamed stack-level instance survives with no attachments.
    assert any(inst.id == "automation" and inst.service == "n8n" for inst in config.service_instances)
    assert not any(att.instance == "automation" for gw in config.gateways for att in gw.services)
    # IIoT was unwired: no broker, no mqtt attachments, no Cirrus modules.
    assert not any(inst.service == "chariot" for inst in config.service_instances)
    assert not any(att.role.startswith("mqtt-") for gw in config.gateways for att in gw.services)
    assert not any(m in {"mqtt-engine", "mqtt-transmission"} for gw in config.gateways for m in gw.modules)
    # The removed postgres attachment pruned the now-unused instance.
    assert not any(inst.is_database for inst in config.service_instances)


# --------------------------------------------------------------------------- #
# Phase B: the main-flow services stage (add / flat / placement / follow-ups)
# --------------------------------------------------------------------------- #


def _services_stage_basic_no_db(extra: list) -> ScriptedPrompter:
    """A basic, no-database walk that reaches the services stage, then ``extra``.

    The services stage's leading "Add a service?" confirm is the first of
    ``extra``; the caller scripts the add-flow and the final ``True/False`` plus
    the summary action.
    """
    return ScriptedPrompter(
        [
            "basic",  # architecture
            "none",  # database -> none (keep the registry empty up front)
            "none",  # edge_role
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",  # exposure
            *extra,
        ]
    )


def test_services_stage_add_attached_service() -> None:
    """The services stage attaches a service to the single gateway (auto-attach),
    and the recorded config carries the attachment."""
    prompter = _services_stage_basic_no_db(
        [
            True,  # add a service? -> yes
            "emqx",  # which service
            "attach",  # placement (single gateway auto-attaches)
            "consumer",  # broker role
            False,  # add another? -> no
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    config = outcome.config
    assert any(inst.id == "emqx" for inst in config.service_instances)
    assert ("emqx", "consumer") in _attachments(config, "gateway")


def test_services_stage_add_flat_service_renders_into_compose() -> None:
    """A flat (unattached) service has no attachment yet still renders fully into
    the compose file (image, healthcheck, env) via the engine path (issue #67)."""
    prompter = _services_stage_basic_no_db(
        [
            True,  # add a service? -> yes
            "emqx",  # which service
            "flat",  # placement: don't wire it
            False,  # add another? -> no
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    config = outcome.config
    # The instance exists with no attachment anywhere.
    assert any(inst.id == "emqx" for inst in config.service_instances)
    assert not any(att.instance == "emqx" for gw in config.gateways for att in gw.services)
    # And it renders as a real compose service (image + ports) via the engine.
    rendered = render_compose(config)
    assert "emqx:" in rendered
    assert "${EMQX_IMAGE}" in rendered


def test_services_stage_flat_second_database_is_legal() -> None:
    """A flat second Postgres alongside the attached one is legal (issue #67): the
    attached db wires the gateway, the flat one just stands up a spare container."""
    prompter = _services_stage_basic_no_db(
        [
            # attach the first postgres to the gateway:
            True,
            "postgres",
            "attach",
            # add a SECOND postgres, flat (unattached):
            True,
            "postgres",
            "flat",
            False,  # done adding
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    config = outcome.config
    pg = [inst for inst in config.service_instances if inst.service == "postgres"]
    assert len(pg) == 2
    attached = {att.instance for gw in config.gateways for att in gw.services}
    # Exactly one of the two is attached; the other is the deliberately-flat spare.
    assert len({p.id for p in pg} & attached) == 1
    # Both render as distinct compose services.
    rendered = render_compose(config)
    for inst in pg:
        assert f"{inst.id}:" in rendered


def test_services_stage_n8n_offers_mcp_dropin() -> None:
    """Adding n8n offers the MCP drop-in toggle; accepting sets mcp_dropin, which
    restores the wizard access lost when the mcp-n8n profile was removed."""
    prompter = _services_stage_basic_no_db(
        [
            True,  # add a service? -> yes
            "n8n",  # which service
            "attach",  # placement
            True,  # MCP drop-in? -> yes
            False,  # done adding
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    assert outcome.config.mcp_dropin is True
    assert any(inst.service == "n8n" for inst in outcome.config.service_instances)


def test_services_stage_back_out_before_adding() -> None:
    """Backing out of the services stage's first "Add a service?" returns to the
    prior step (exposure) without recording any service."""
    prompter = ScriptedPrompter(
        [
            "basic",
            "none",  # database
            "none",  # edge_role
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",  # exposure
            BACK,  # services stage "Add a service?" -> back to exposure
            "ports",  # exposure re-asked
            False,  # services stage: add a service? -> no
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    # No services were added.
    assert not outcome.config.non_database_instances()


def test_env_overrides_round_trip_and_emit_into_compose() -> None:
    """The env action sets a gateway env override and a service-instance env
    override; both survive dump/load/resolve and emit into the generated files."""
    prompter = _services_stage_basic_no_db(
        [
            # add an attached n8n so there is a service instance to target:
            True,
            "n8n",
            "attach",
            False,  # no MCP drop-in
            False,  # done adding
            "tweak",  # into the composer for the env action
            # set a gateway env override:
            "env",
            "gw:gateway",
            "IGNITION_UID=2000",  # KEY=VALUE
            "",  # blank line ends entry
            # set a service-instance env override:
            "env",
            "inst:n8n",
            "N8N_LOG_LEVEL=debug",
            "",
            "done",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    config = outcome.config

    gw = next(g for g in config.gateways if g.name == "gateway")
    assert gw.env.get("IGNITION_UID") == "2000"
    n8n = next(i for i in config.service_instances if i.service == "n8n")
    assert n8n.env.get("N8N_LOG_LEVEL") == "debug"

    # Round-trip: the dump carries both overrides.
    text = dump_config(config, "yaml")
    assert "IGNITION_UID" in text and "N8N_LOG_LEVEL" in text

    # Compose emission: the gateway override lands in its environment block.
    rendered = render_compose(config)
    assert "IGNITION_UID:" in rendered and "2000" in rendered

    # .env emission for the service-instance override.
    env_text = _render_env(config)
    assert "N8N_LOG_LEVEL=debug" in env_text


def test_env_override_round_trips_as_fixed_point(tmp_path: Path) -> None:
    """An env-override config survives dump -> load -> resolve unchanged."""
    prompter = _services_stage_basic_no_db(
        [
            True,
            "n8n",
            "attach",
            False,
            False,
            "tweak",
            "env",
            "gw:gateway",
            "IGNITION_UID=2000",
            "",
            "done",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    path = tmp_path / "stack.yaml"
    path.write_text(dump_config(outcome.config, "yaml"), encoding="utf-8")
    reloaded = resolve(load_config(path))
    assert reloaded.model_dump(mode="json") == outcome.config.model_dump(mode="json")
