"""Progress sign-posting for the wizard step machine (issue #60).

The wizard keeps questionary's natural scrolling Q&A (every answered question
stays on screen) and orients the user two ways:

- ``_print_plan``: a one-time numbered plan of the road ahead, printed once the
  architecture (which decides the applicable steps) is chosen.
- ``_step_counter``: an ``"[N/M] "`` prefix folded into each subsequent prompt
  so the user sees position and the finish line.

Tests cover the counter rules (architecture has none; the total counts the
Review screen; it is stable once the architecture is set and grows for
multi-gateway architectures), the plan rendering, the message folding, and the
walk/back-nav integration that the counter recompute rides on.
"""

from __future__ import annotations

import io

from rich.console import Console

import ignition_stack.wizard as wizard
from ignition_stack.wizard import (
    BACK,
    QuestionaryPrompter,
    _print_plan,
    _step_counter,
    applicable_steps,
    walk,
)

# --------------------------------------------------------------------------- #
# _step_counter: position/total prefix
# --------------------------------------------------------------------------- #


def test_counter_architecture_has_no_prefix() -> None:
    """The architecture step shows no counter: its answer decides the total, so
    before it is answered the total is unknown (and would otherwise jump)."""
    assert _step_counter({"architecture": "basic"}, "architecture") == ""


def test_counter_total_includes_review_screen() -> None:
    """M counts every applicable step plus the closing Review screen, so the
    last real step is N-1 of M and the finish line is visible."""
    answers = {"architecture": "basic"}
    total = len(applicable_steps(answers)) + 1
    # The last step before Review is at position total-1.
    last_step = applicable_steps(answers)[-1].name
    assert _step_counter(answers, last_step) == f"[{total - 1}/{total}] "


def test_counter_database_position_basic() -> None:
    """Basic: architecture(1), database(2) of 9 (8 steps + Review)."""
    assert _step_counter({"architecture": "basic"}, "database") == "[2/9] "


def test_counter_grows_for_hub_and_spoke() -> None:
    """Hub-and-spoke inserts spokes + network_split, so database slides to 3
    and the total grows past basic's."""
    assert _step_counter({"architecture": "hub-and-spoke"}, "database") == "[3/11] "


def test_counter_empty_for_inapplicable_step() -> None:
    """A step that does not apply to the chosen architecture has no counter
    (e.g. spokes under basic)."""
    assert _step_counter({"architecture": "basic"}, "spokes") == ""


# --------------------------------------------------------------------------- #
# _print_plan: the one-time road map
# --------------------------------------------------------------------------- #


def _render_plan_text(answers: dict) -> str:
    """Capture _print_plan's output as plain text via a non-terminal console."""
    buf = io.StringIO()
    original = wizard.console
    wizard.console = Console(file=buf, force_terminal=False, width=80)
    try:
        _print_plan(answers)
    finally:
        wizard.console = original
    return buf.getvalue()


def test_plan_lists_every_applicable_step_and_review() -> None:
    """The plan numbers every applicable step and ends on Review, with a count
    header that matches (steps + Review)."""
    answers = {"_name": "test", "architecture": "basic"}
    text = _render_plan_text(answers)
    total = len(applicable_steps(answers)) + 1
    assert f"{total} steps" in text
    for step in applicable_steps(answers):
        assert step.label in text
    assert f"{total}. Review" in text


def test_plan_grows_for_multi_gateway_architecture() -> None:
    """Hub-and-spoke's plan includes the steps basic's omits."""
    text = _render_plan_text({"_name": "test", "architecture": "hub-and-spoke"})
    assert "Spoke count" in text
    assert "Network split" in text


# --------------------------------------------------------------------------- #
# QuestionaryPrompter: counter folded into the message
# --------------------------------------------------------------------------- #


def test_prompter_folds_progress_into_message() -> None:
    """set_progress prefixes subsequent prompt messages; clearing removes it."""
    p = QuestionaryPrompter()
    assert p._msg("Database?") == "Database?"
    p.set_progress("[2/9] ")
    assert p._msg("Database?") == "[2/9] Database?"
    p.set_progress("")
    assert p._msg("Database?") == "Database?"


# --------------------------------------------------------------------------- #
# applicable_steps: count per architecture
# --------------------------------------------------------------------------- #


def test_applicable_count_basic() -> None:
    """Basic: no spokes, no frontends, no network_split; has redundancy."""
    names = [s.name for s in applicable_steps({"architecture": "basic"})]
    assert "spokes" not in names
    assert "frontends" not in names
    assert "network_split" not in names
    assert "redundancy" in names


def test_applicable_count_hub_and_spoke_is_larger() -> None:
    """Hub-and-spoke adds spokes + network_split vs basic — total is larger."""
    basic_count = len(applicable_steps({"architecture": "basic"}))
    hub_count = len(applicable_steps({"architecture": "hub-and-spoke"}))
    assert hub_count > basic_count
    hub_names = [s.name for s in applicable_steps({"architecture": "hub-and-spoke"})]
    assert "spokes" in hub_names
    assert "network_split" in hub_names


def test_applicable_count_scale_out() -> None:
    """Scale-out has frontends and network_split but not spokes."""
    names = [s.name for s in applicable_steps({"architecture": "scale-out"})]
    assert "frontends" in names
    assert "network_split" in names
    assert "spokes" not in names


# --------------------------------------------------------------------------- #
# Recount after back + architecture change (integration)
# --------------------------------------------------------------------------- #


class _ScriptedPrompter:
    """Minimal scripted prompter. Progress prefixes and plan prints go to the
    console, not the prompter, so this only replays the recorded answers — and
    deliberately omits ``set_progress`` to prove the walk tolerates a prompter
    without it (the call is getattr-guarded)."""

    def __init__(self, answers: list) -> None:
        self._answers = iter(answers)

    def _next(self):
        try:
            return next(self._answers)
        except StopIteration as exc:
            raise AssertionError("_ScriptedPrompter ran out of answers") from exc

    def select(self, message, choices, default=None, allow_back=False):
        return self._next()

    def text(self, message, default=""):
        return self._next()

    def confirm(self, message, default=False, allow_back=False):
        return self._next()

    def integer(self, message, default, minimum=0, allow_back=False):
        return self._next()

    def checkbox(self, message, choices):
        return self._next()


def test_applicable_count_changes_after_arch_switch() -> None:
    """basic → hub-and-spoke grows the applicable count by two (spokes +
    network_split), which is what the counter/plan recompute relies on."""
    basic_count = len(applicable_steps({"architecture": "basic"}))
    hub_count = len(applicable_steps({"architecture": "hub-and-spoke"}))
    assert hub_count == basic_count + 2


def test_walk_with_arch_change_still_produces_correct_config() -> None:
    """Back to architecture, switch basic → hub-and-spoke, complete: the walk
    completes and produces a hub-and-spoke config. Exercises the recount path
    (the plan reprints and the counter recomputes after the change) and proves a
    prompter without set_progress runs cleanly."""
    prompter = _ScriptedPrompter(
        [
            "basic",  # architecture (first pass)
            "postgres",  # database
            BACK,  # at edge_role → back to database
            BACK,  # at database → back to architecture
            "hub-and-spoke",  # architecture (changed)
            3,  # spoke count (newly applicable)
            "postgres",  # database (re-asked)
            "spoke",  # edge_role
            False,  # network split
            False,  # redundancy
            False,  # iiot
            False,  # modules (decline customize)
            "ports",  # exposure
            False,  # services: add a service? → no
            "generate",  # summary
        ]
    )
    outcome = walk("test-project", prompter)
    assert outcome.confirmed
    assert outcome.architecture == "hub-and-spoke"
    # 1 hub + 3 spokes
    assert len(outcome.config.gateways) == 4
