"""Phase 7 (issue #43): the Custom-track composer + the Quick->tweak handoff.

The composer is the per-gateway service editor both wizard tracks can land in:
the Custom track starts there from a bare topology preset; the Quick track
reaches it through the summary's *tweak* action with the built config
pre-filled. These tests drive ``wizard.walk`` end to end with a scripted
prompter, the same harness ``test_profiles.py`` uses, and assert on the
resolved registry the composer produced.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ignition_stack.catalog.builtins import default_builtin_catalog
from ignition_stack.config import dump_config, load_config
from ignition_stack.profiles import ProfileOptions, build_profile
from ignition_stack.services.loader import load_all_services
from ignition_stack.services.resolver import resolve
from ignition_stack.wizard import walk
from ignition_stack.wizard_composer import (
    module_choices_for_gateway,
    mqtt_broker_choices,
    service_choices_for_gateway,
)


class ScriptedPrompter:
    """Pre-recorded answers, in order; also records checkbox choice triples.

    Mirrors ``test_profiles.ScriptedPrompter`` (kept local: the tests directory
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

    def select(self, message: str, choices: Sequence[tuple[str, str]], default=None) -> str:
        return self._next()

    def text(self, message: str, default: str = "") -> str:
        return self._next()

    def confirm(self, message: str, default: bool = False) -> bool:
        return self._next()

    def integer(self, message: str, default: int, minimum: int = 0) -> int:
        return self._next()

    def checkbox(self, message: str, choices: Sequence[tuple[str, str, bool]]) -> list:
        self.checkbox_choices.append(list(choices))
        return self._next()


def _attachments(config, gw_name: str) -> set[tuple[str, str]]:
    gw = next(g for g in config.gateways if g.name == gw_name)
    return {(att.instance, att.role) for att in gw.services}


# --------------------------------------------------------------------------- #
# Quick -> tweak handoff
# --------------------------------------------------------------------------- #


def test_tweak_handoff_adds_emqx_and_keeps_everything_else() -> None:
    """Quick standalone+postgres -> tweak -> add emqx -> generate: the final
    config carries the emqx attachment and is otherwise identical to the
    pre-tweak build (resolved)."""
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            "standalone",  # profile
            "postgres",  # database
            "none",  # edge_role
            False,  # redundancy
            False,  # wire IIoT? -> no
            False,  # customize modules? -> accept lean default
            "external",  # reverse proxy
            "tweak",  # summary action -> composer, pre-filled
            # composer loop:
            "add",  # action
            "gateway",  # which gateway
            "emqx",  # which service
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
        build_profile(
            "standalone",
            "demo",
            ProfileOptions(disable_builtins=outcome.options.disable_builtins),
        )
    )
    assert stripped.model_dump(mode="json") == expected.model_dump(mode="json")


def test_quick_summary_cancel_marks_unconfirmed() -> None:
    prompter = ScriptedPrompter(
        [
            "quick",
            "standalone",
            "postgres",
            "none",
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "external",
            "cancel",  # summary action
        ]
    )
    assert walk("demo", prompter).confirmed is False


def test_quick_summary_preview_then_generate(capsys) -> None:
    """Preview at the summary prints the resolved config dump, then re-shows
    the prompt; choosing generate afterwards confirms the outcome and leaves
    the config identical to a direct generate."""
    base_prompter = ScriptedPrompter(
        [
            "quick",
            "standalone",
            "postgres",
            "none",  # edge_role
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "external",
            "generate",  # direct generate (reference)
        ]
    )
    base_outcome = walk("demo", base_prompter)

    preview_prompter = ScriptedPrompter(
        [
            "quick",
            "standalone",
            "postgres",
            "none",
            False,
            False,
            False,
            "external",
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


def test_quick_summary_preview_then_cancel(capsys) -> None:
    """Preview prints the dump and then re-shows the prompt; cancelling marks
    the outcome as unconfirmed."""
    prompter = ScriptedPrompter(
        [
            "quick",
            "standalone",
            "postgres",
            "none",
            False,
            False,
            False,
            "external",
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
# Custom track from scratch
# --------------------------------------------------------------------------- #


def test_composer_from_scratch_hub_and_spoke_with_shared_keycloak(capsys) -> None:
    """Hub-and-spoke preset with edge spokes: attach keycloak to the hub, share
    it with an edge spoke (allowed: idp is not never_on_edge), reuse the
    auto-added postgres via the singleton-share path, and have a second
    database on the hub rejected with the state intact."""
    prompter = ScriptedPrompter(
        [
            "custom",  # track gate
            "hub-and-spoke",  # topology preset
            2,  # spokes
            "spoke",  # edge_role -> spokes run Edge
            False,  # network split
            False,  # redundancy
            # add keycloak to the hub (idp; no role prompt):
            "add",
            "hub",
            "keycloak",
            # share keycloak with the edge spoke (allowed):
            "share",
            "keycloak",
            "spoke-1",
            # attach the hub to the auto-added postgres (singleton -> share path):
            "add",
            "hub",
            "postgres",
            True,  # attach to the existing 'db' instance? -> yes
            # a second database on the hub must be rejected (error surfaced):
            "add",
            "hub",
            "mariadb",
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
    config = resolve(build_profile("hub-and-spoke", "demo", ProfileOptions(spokes=1, database_kind=None)))
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
# Per-gateway modules (#42 absorbed, per-gateway in Custom)
# --------------------------------------------------------------------------- #


def test_composer_per_gateway_modules_precheck_follows_that_gateways_db() -> None:
    """The per-gateway checkbox pre-checks the curated set plus the JDBC driver
    for the database THIS gateway attaches to, and the edit applies to that
    gateway only."""
    prompter = ScriptedPrompter(
        [
            "custom",  # track gate
            "standalone",  # topology preset
            "none",  # edge_role
            False,  # redundancy
            # give the gateway a mariadb:
            "add",
            "gateway",
            "mariadb",
            # set its modules:
            "modules",
            "gateway",
            ["perspective"],  # ENABLE only Perspective
            "done",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed

    # The checkbox offered the #42 curated default plus mariadb's JDBC driver.
    assert len(prompter.checkbox_choices) == 1
    prechecked = {value for value, _, checked in prompter.checkbox_choices[0] if checked}
    catalog = default_builtin_catalog()
    assert prechecked == catalog.default_enabled_slugs | {"mariadb-jdbc-driver"}

    gw = outcome.config.gateways[0]
    assert set(gw.disable_builtins) == catalog.slugs - {"perspective"}
    # Third-party modules (gw.modules) stay resolver-managed: mariadb has a
    # built-in driver, so nothing was added there.
    assert gw.modules == []


def test_module_choices_precheck_current_state_when_already_customized() -> None:
    """A gateway that already carries disable_builtins is pre-checked with its
    current enabled set, not the curated default."""
    config = resolve(build_profile("standalone", "demo", ProfileOptions(disable_builtins=("vision", "sfc"))))
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
            "custom",  # track gate
            "hub-and-spoke",  # topology preset
            2,  # spokes
            "spoke",  # spokes run Edge
            False,  # network split
            False,  # redundancy
            # gw2 = spoke-1, flipped to standard so it may hold a database:
            "edition",
            "spoke-1",
            "standard",
            # gw1 = hub: emqx as the central Engine side:
            "add",
            "hub",
            "emqx",
            "mqtt-engine",  # broker attachment role
            # edge spoke publishes through the same broker:
            "share",
            "emqx",
            "spoke-2",
            "mqtt-transmission",
            # gw1: keycloak,
            "add",
            "hub",
            "keycloak",
            # gw2 shares the SAME keycloak instance:
            "share",
            "keycloak",
            "spoke-1",
            # gw2 gets its own mongo:
            "add",
            "spoke-1",
            "mongo",
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
            "custom",
            "hub-and-spoke",
            2,
            "spoke",
            False,  # network split
            False,  # redundancy
            "edition",
            "spoke-1",
            "standard",
            "add",
            "hub",
            "emqx",
            "mqtt-engine",
            "share",
            "emqx",
            "spoke-2",
            "mqtt-transmission",
            "add",
            "hub",
            "keycloak",
            "share",
            "keycloak",
            "spoke-1",
            "add",
            "spoke-1",
            "mongo",
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
            "custom",
            "scaleout",  # preset
            1,  # frontends
            "none",  # edge_role
            True,  # network split
            False,  # redundancy
            # stack-level n8n (no attachment):
            "stack",
            "n8n",
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
            "backend",
            "postgres",
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
