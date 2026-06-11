"""Back-navigation in the Quick-track step machine (issue #59).

These drive ``wizard.walk`` with a scripted prompter that yields the
:data:`~ignition_stack.wizard.BACK` sentinel wherever a real user would pick the
Back affordance, then assert on the resulting config. The prompter records the
``default`` each select/confirm/integer was offered, so the "replay the prior
answer as the default" and "drop an answer the new profile no longer offers"
rules can be asserted directly, not just inferred from the outcome.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

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

    def _next(self):
        try:
            return next(self._answers)
        except StopIteration as exc:
            raise AssertionError("ScriptedPrompter ran out of answers") from exc

    def select(self, message: str, choices: Sequence[tuple[str, str]], default=None, allow_back: bool = False):
        self.select_defaults.append((message, default))
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
            "quick",  # track gate
            "standalone",  # profile
            "postgres",  # database
            BACK,  # at edge_role -> step back to database
            "mysql",  # database (re-asked) -> change it
            "none",  # edge_role
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",  # exposure
            "generate",  # summary
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    assert outcome.config.database is not None and outcome.config.database.kind == "mysql"

    # The database prompt was shown twice; the second time its default replayed
    # the prior answer (postgres) instead of resetting to the canonical default.
    db_defaults = _defaults_for(prompter.select_defaults, "Database?")
    assert db_defaults == ["postgres", "postgres"]


# --------------------------------------------------------------------------- #
# Back off the first step returns to the track gate
# --------------------------------------------------------------------------- #


def test_back_off_first_step_returns_to_track_gate() -> None:
    """Backing off the profile step re-shows the 'How do you want to build?'
    gate; re-picking quick continues the flow normally."""
    prompter = ScriptedPrompter(
        [
            "quick",  # track gate
            BACK,  # at profile -> back to the gate
            "quick",  # track gate (re-shown)
            "standalone",  # profile
            "postgres",
            "none",
            False,
            False,
            False,
            "ports",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    assert outcome.profile == "standalone"


def test_back_off_first_step_can_switch_track_to_custom() -> None:
    """Backing off profile to the gate and choosing Custom hands off to the
    composer (the nice-to-have from the issue)."""
    prompter = ScriptedPrompter(
        [
            "quick",  # gate
            BACK,  # profile -> back to gate
            "custom",  # gate -> custom track this time
            "standalone",  # topology preset
            "none",  # edge_role
            False,  # redundancy
            "done",  # composer: finish
            "generate",  # composer summary
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    assert outcome.profile == "standalone"


# --------------------------------------------------------------------------- #
# Back at the summary returns to the last question
# --------------------------------------------------------------------------- #


def test_back_at_summary_returns_to_last_question(monkeypatch) -> None:
    """Choosing Back at the summary drops the user on the exposure step (the
    last question), where re-answering with a reverse proxy changes the config
    instead of cancelling."""
    monkeypatch.setattr("ignition_stack.wizard._detect_proxy_network", lambda: [])
    prompter = ScriptedPrompter(
        [
            "quick",
            "standalone",
            "postgres",
            "none",
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",  # exposure -> host ports
            BACK,  # summary -> back to the last question (exposure)
            "proxy",  # exposure (re-asked) -> reverse proxy
            "named",  # name an existing network
            "edge-net",  # network name
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
    """For standalone the network-split step never applies; backing from the
    redundancy confirm lands on edge_role, jumping over network_split in the
    backward direction too (it is never prompted)."""
    prompter = ScriptedPrompter(
        [
            "quick",
            "standalone",
            "postgres",
            "none",  # edge_role
            BACK,  # at redundancy -> back, skipping network_split, to edge_role
            "none",  # edge_role (re-asked)
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    # network_split is never offered for a single-gateway profile, in either
    # walk direction.
    assert not _defaults_for(prompter.confirm_defaults, "Split frontend/backend")


def test_changing_profile_adds_spoke_count_and_drops_stale_edge_role() -> None:
    """Switch profile standalone -> hub-and-spoke via back. The spoke-count step
    becomes applicable (asked on the forward replay), and the standalone-only
    edge role 'gateway' is dropped: the re-asked edge prompt defaults to the
    hub-and-spoke proposal ('spoke'), not the stale 'gateway'."""
    prompter = ScriptedPrompter(
        [
            "quick",
            "standalone",  # profile (first pass)
            "postgres",  # database
            "gateway",  # edge_role -> Edge on the standalone gateway
            # now back all the way to profile (edge_role -> database -> profile):
            # (the previous answer was consumed; the next BACK is at the step we
            # re-enter)
            BACK,  # at network-split? no: standalone skips it; this BACK is at
            # the redundancy confirm -> back to edge_role
            BACK,  # at edge_role -> back to database
            BACK,  # at database -> back to profile
            "hub-and-spoke",  # profile (changed)
            2,  # spoke count (newly-applicable step)
            "postgres",  # database
            "spoke",  # edge_role (re-asked with hub-and-spoke choices)
            False,  # network split (now applies)
            False,  # redundancy
            False,  # iiot
            False,  # modules
            "ports",
            "generate",
        ]
    )
    outcome = walk("demo", prompter)
    assert outcome.confirmed
    assert outcome.profile == "hub-and-spoke"
    # 1 hub + 2 spokes; spokes run Edge (the new, valid edge role took effect).
    assert len(outcome.config.gateways) == 3
    spokes = [g for g in outcome.config.gateways if g.role == "spoke"]
    assert spokes and all(g.ignition_edition == "edge" for g in spokes)

    # The spoke-count integer step was prompted exactly once (forward replay).
    assert len(_defaults_for(prompter.integer_defaults, "Spoke gateway count?")) == 1

    # Invalidation: the re-asked edge prompt no longer defaults to the dropped
    # standalone role 'gateway'; it falls back to the hub-and-spoke proposal.
    edge_defaults = _defaults_for(prompter.select_defaults, "Run the Edge edition")
    assert edge_defaults[0] == "none"  # first pass: standalone canonical default
    assert edge_defaults[-1] == "spoke"  # re-ask dropped the invalid 'gateway'


# --------------------------------------------------------------------------- #
# Step list is introspectable (for the issue #60 breadcrumb)
# --------------------------------------------------------------------------- #


def test_applicable_steps_track_the_chosen_profile() -> None:
    """applicable_steps reflects profile-conditional steps appearing/vanishing,
    which the follow-up breadcrumb renders as 'step N of M'."""
    standalone = [s.name for s in applicable_steps({"profile": "standalone"})]
    assert "spokes" not in standalone and "frontends" not in standalone
    assert "network_split" not in standalone and "redundancy" in standalone

    hub = [s.name for s in applicable_steps({"profile": "hub-and-spoke"})]
    assert "spokes" in hub and "network_split" in hub and "redundancy" in hub

    scaleout = [s.name for s in applicable_steps({"profile": "scaleout"})]
    assert "frontends" in scaleout and "spokes" not in scaleout

    mcp = [s.name for s in applicable_steps({"profile": "mcp-n8n"})]
    assert "redundancy" not in mcp and "network_split" not in mcp


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
