"""Back-navigation in the wizard step machine (issue #59).

These drive ``wizard.walk`` with a scripted prompter that yields the
:data:`~ignition_stack.wizard.BACK` sentinel wherever a real user would pick the
Back affordance, then assert on the resulting config. The prompter records the
``default`` each select/confirm/integer was offered, so the "replay the prior
answer as the default" and "drop an answer the new architecture no longer offers"
rules can be asserted directly, not just inferred from the outcome.
"""

from __future__ import annotations

from collections.abc import Sequence

from ignition_stack.wizard import (
    BACK,
    QuestionaryPrompter,
    applicable_steps,
    walk,
)


class ScriptedPrompter:
    """Pre-recorded answers in order, recording the defaults each prompt offered.

    ``BACK`` may appear anywhere in the answer list to simulate the user picking
    the Back affordance on a select/confirm step.
    """

    def __init__(self, answers: list) -> None:
        self._answers = iter(answers)
        self.select_defaults: list[tuple[str, object]] = []
        self.confirm_defaults: list[tuple[str, object]] = []
        self.integer_defaults: list[tuple[str, object]] = []
        self.select_allow_back: list[tuple[str, bool]] = []

    def _next(self):
        try:
            return next(self._answers)
        except StopIteration as exc:
            raise AssertionError("ScriptedPrompter ran out of answers") from exc

    def select(self, message: str, choices: Sequence[tuple[str, str]], default=None, allow_back: bool = False):
        self.select_defaults.append((message, default))
        self.select_allow_back.append((message, allow_back))
        return self._next()

    def text(self, message: str, default: str = "") -> str:
        return self._next()

    def confirm(self, message: str, default: bool = False, allow_back: bool = False):
        self.confirm_defaults.append((message, default))
        return self._next()

    def integer(self, message: str, default: int, minimum: int = 0, allow_back: bool = False):
        self.integer_defaults.append((message, default))
        return self._next()

    def checkbox(self, message: str, choices):
        return self._next()


def _defaults_for(recorded: list[tuple[str, object]], needle: str) -> list[object]:
    return [default for message, default in recorded if needle in message]


# --------------------------------------------------------------------------- #
# Back -> change -> different config; prior answer replayed as default
# --------------------------------------------------------------------------- #


def test_back_changes_an_earlier_answer_and_replays_prior_default() -> None:
    """Answer database=postgres, step forward, back to it, change to mysql: the
    final config carries mysql, and the re-asked database prompt was offered
    postgres (the prior answer) as its default."""
    prompter = ScriptedPrompter(
        [
            "basic",  # architecture
            "postgres",  # database
            BACK,  # at edge_role -> step back to database
            "mysql",  # database (re-asked) -> change it
            "none",  # edge_role
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",  # exposure
            False,  # services stage: add a service? -> no
            "generate",  # summary
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    # The wizard returns a resolved config (the services stage resolves it), so
    # the database lives in the registry, not the legacy ``database`` shim.
    db = outcome.config.database_instance()
    assert db is not None and db.service == "mysql"

    # The database prompt was shown twice; the second time its default replayed
    # the prior answer (postgres) instead of resetting to the canonical default.
    db_defaults = _defaults_for(prompter.select_defaults, "Database?")
    assert db_defaults == ["postgres", "postgres"]


# --------------------------------------------------------------------------- #
# The architecture step is first, so it offers no Back affordance
# --------------------------------------------------------------------------- #


def test_architecture_step_offers_no_back() -> None:
    """The architecture select is the wizard's first prompt - there is no
    earlier prompt to return to - so it must be asked with allow_back False."""
    prompter = ScriptedPrompter(
        [
            "basic",  # architecture (first prompt)
            "postgres",
            "none",
            False,
            False,
            False,
            "ports",
            False,  # services stage: add a service? -> no
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    assert outcome.architecture == "basic"
    # The very first select is the architecture prompt, offered without Back.
    first_message, first_allow_back = prompter.select_allow_back[0]
    assert "Architecture?" in first_message
    assert first_allow_back is False


# --------------------------------------------------------------------------- #
# Back at the summary returns to the last question
# --------------------------------------------------------------------------- #


def test_back_at_summary_returns_to_last_question(monkeypatch) -> None:
    """Choosing Back at the summary drops the user on the services stage (the
    last step); backing again from its "Add a service?" reaches exposure, where
    re-answering with a reverse proxy changes the config instead of cancelling."""
    monkeypatch.setattr("ignition_stack.wizard._detect_proxy_network", lambda: [])
    prompter = ScriptedPrompter(
        [
            "basic",
            "postgres",
            "none",
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",  # exposure -> host ports
            False,  # services stage: add a service? -> no
            BACK,  # summary -> back to the services stage "Add a service?"
            BACK,  # services stage "Add a service?" -> back to exposure
            "proxy",  # exposure (re-asked) -> reverse proxy
            "named",  # name an existing network
            "edge-net",  # network name
            False,  # services stage: add a service? -> no
            "generate",  # summary
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    proxy = outcome.config.reverse_proxy
    assert proxy is not None and proxy.mode == "external" and proxy.network == "edge-net"


# --------------------------------------------------------------------------- #
# Skipped steps are skipped in both directions
# --------------------------------------------------------------------------- #


def test_skipped_step_is_skipped_when_backing() -> None:
    """For basic the network-split step never applies; backing from the
    redundancy confirm lands on edge_role, jumping over network_split in the
    backward direction too (it is never prompted)."""
    prompter = ScriptedPrompter(
        [
            "basic",
            "postgres",
            "none",  # edge_role
            BACK,  # at redundancy -> back, skipping network_split, to edge_role
            "none",  # edge_role (re-asked)
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",
            False,  # services stage: add a service? -> no
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    # network_split is never offered for a single-gateway architecture, in either
    # walk direction.
    assert not _defaults_for(prompter.confirm_defaults, "Split frontend/backend")


def test_changing_architecture_adds_spoke_count_and_drops_stale_edge_role() -> None:
    """Switch architecture basic -> hub-and-spoke via back. The spoke-count step
    becomes applicable (asked on the forward replay), and the basic-only
    edge role 'gateway' is dropped: the re-asked edge prompt defaults to the
    hub-and-spoke proposal ('spoke'), not the stale 'gateway'."""
    prompter = ScriptedPrompter(
        [
            "basic",  # architecture (first pass)
            "postgres",  # database
            "gateway",  # edge_role -> Edge on the basic gateway
            # now back all the way to architecture (edge_role -> database ->
            # architecture). The previous answer was consumed; the next BACK is at
            # the step we re-enter.
            BACK,  # at network-split? no: basic skips it; this BACK is at
            # the redundancy confirm -> back to edge_role
            BACK,  # at edge_role -> back to database
            BACK,  # at database -> back to architecture
            "hub-and-spoke",  # architecture (changed)
            2,  # spoke count (newly-applicable step)
            "postgres",  # database
            "spoke",  # edge_role (re-asked with hub-and-spoke choices)
            False,  # network split (now applies)
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",
            False,  # services stage: add a service? -> no
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    assert outcome.architecture == "hub-and-spoke"
    # 1 hub + 2 spokes; spokes run Edge (the new, valid edge role took effect).
    assert len(outcome.config.gateways) == 3
    spokes = [g for g in outcome.config.gateways if g.role == "spoke"]
    assert spokes and all(g.ignition_edition == "edge" for g in spokes)

    # The spoke-count integer step was prompted exactly once (forward replay).
    assert len(_defaults_for(prompter.integer_defaults, "Spoke gateway count?")) == 1

    # Invalidation: the re-asked edge prompt no longer defaults to the dropped
    # basic role 'gateway'; it falls back to the hub-and-spoke proposal.
    edge_defaults = _defaults_for(prompter.select_defaults, "Run the Edge edition")
    assert edge_defaults[0] == "none"  # first pass: basic canonical default
    assert edge_defaults[-1] == "spoke"  # re-ask dropped the invalid 'gateway'


# --------------------------------------------------------------------------- #
# Step list is introspectable (for the issue #60 breadcrumb)
# --------------------------------------------------------------------------- #


def test_applicable_steps_track_the_chosen_architecture() -> None:
    """applicable_steps reflects architecture-conditional steps appearing/
    vanishing, which the follow-up breadcrumb renders as 'step N of M'."""
    basic = [s.name for s in applicable_steps({"architecture": "basic"})]
    assert "spokes" not in basic and "frontends" not in basic
    assert "network_split" not in basic and "redundancy" in basic

    hub = [s.name for s in applicable_steps({"architecture": "hub-and-spoke"})]
    assert "spokes" in hub and "network_split" in hub and "redundancy" in hub

    scale_out = [s.name for s in applicable_steps({"architecture": "scale-out"})]
    assert "frontends" in scale_out and "spokes" not in scale_out


# --------------------------------------------------------------------------- #
# QuestionaryPrompter adapter: the Back affordance maps to the BACK sentinel
# --------------------------------------------------------------------------- #


class _StubQuestion:
    def __init__(self, answer: object) -> None:
        self._answer = answer

    def unsafe_ask(self) -> object:
        return self._answer


def test_questionary_select_appends_back_choice_and_maps_sentinel(monkeypatch) -> None:
    """allow_back appends a Back choice whose value is the BACK sentinel, and the
    adapter returns BACK unchanged (not stringified) when it is chosen."""
    import questionary

    captured: dict = {}

    def spy_select(message, *, choices, default=None, **kwargs):
        captured["choices"] = choices
        return _StubQuestion(BACK)

    monkeypatch.setattr(questionary, "select", spy_select)

    result = QuestionaryPrompter().select("Pick", [("a", "A"), ("b", "B")], default="a", allow_back=True)
    assert result is BACK
    # The Back row was appended last with the sentinel as its value.
    assert captured["choices"][-1].value is BACK


def test_questionary_select_without_allow_back_has_no_back_choice(monkeypatch) -> None:
    import questionary

    captured: dict = {}

    def spy_select(message, *, choices, default=None, **kwargs):
        captured["choices"] = choices
        return _StubQuestion("a")

    monkeypatch.setattr(questionary, "select", spy_select)

    result = QuestionaryPrompter().select("Pick", [("a", "A"), ("b", "B")], default="a")
    assert result == "a"
    assert all(c.value is not BACK for c in captured["choices"])


def test_questionary_confirm_allow_back_renders_select_with_back(monkeypatch) -> None:
    """A back-able confirm renders as a Yes/No/Back select; Yes/No still return
    bools and Back returns the sentinel."""
    import questionary

    captured: dict = {}

    def spy_select(message, *, choices, default=None, **kwargs):
        captured["values"] = [c.value for c in choices]
        return _StubQuestion(BACK)

    monkeypatch.setattr(questionary, "select", spy_select)
    assert QuestionaryPrompter().confirm("OK?", default=True, allow_back=True) is BACK
    assert captured["values"] == [True, False, BACK]

    monkeypatch.setattr(questionary, "select", lambda *a, **k: _StubQuestion(True))
    assert QuestionaryPrompter().confirm("OK?", allow_back=True) is True


def test_questionary_confirm_without_allow_back_uses_native_confirm(monkeypatch) -> None:
    import questionary

    monkeypatch.setattr(questionary, "confirm", lambda *a, **k: _StubQuestion(False))
    assert QuestionaryPrompter().confirm("OK?", default=True) is False
