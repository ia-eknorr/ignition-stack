"""Interactive wizard that walks the architecture decision tree.

The wizard's *core* (``walk()``) is a pure function over a small
:class:`Prompter` protocol. Real CLI invocations pass a
:class:`QuestionaryPrompter` that delegates to ``questionary``; tests pass
a :class:`ScriptedPrompter` with a pre-recorded sequence of answers and
assert on the resulting :class:`ProjectConfig`. Wizard logic stays
testable without a TTY this way.

The UX shape borrows from Create T3 App: select -> defaults -> summary ->
generate. Each step is one Questionary prompt; the summary screen
recaps the resolved choices and a single confirm gates the write.

Step order:

1. **Profile** - which of the four canned shapes the user wants.
2. **Profile-specific count** - spoke count for hub-and-spoke, frontend
   count for scaleout (skipped for single-gateway profiles).
3. **Database** - SQL flavor for the stack, or "none".
4. **Edition per role** - which role (if any) runs Edge. The default is
   profile-driven: all profiles now propose "none" (all standard).
5. **Network split** - for the multi-gateway profiles (scaleout +
   hub-and-spoke), whether to split frontend/backend onto separate
   networks. Defaults on for scaleout, off for hub-and-spoke.
6. **Reverse proxy** - existing/install-Traefik/skip.
7. **Summary + confirm**.

Per-gateway env-var overrides (``memory_mb`` etc.) are deferred to Phase 7
when the lifecycle/reset commands need them; the gateway model already
accepts them, so adding a wizard step on top is a non-breaking follow-up.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ignition_stack.config import ProjectConfig, ReverseProxyConfig
from ignition_stack.profiles import (
    ProfileError,
    ProfileOptions,
    build_profile,
    list_profiles,
)

# Database options shown in the wizard, in the order they appear on screen.
_DB_CHOICES: list[tuple[str, str]] = [
    ("postgres", "Postgres (recommended)"),
    ("mysql", "MySQL"),
    ("mariadb", "MariaDB"),
    ("mongo", "MongoDB"),
    ("none", "No database (gateway-only stack)"),
]

# Per-profile default edge-role proposal. The wizard offers this as the
# default selection in the edition prompt; the user can override.
_DEFAULT_EDGE_ROLE: dict[str, str] = {
    "standalone": "none",
    "scaleout": "none",
    "hub-and-spoke": "spoke",
    "mcp-n8n": "none",
}

# Profiles that produce more than one gateway, so the frontend/backend
# network split is a meaningful choice. Single-gateway profiles skip it.
_MULTI_GATEWAY_PROFILES = frozenset({"scaleout", "hub-and-spoke"})

# Default network-split proposal per multi-gateway profile. Scaleout splits
# (the point of the demo); hub-and-spoke stays on one shared network.
_DEFAULT_NETWORK_SPLIT: dict[str, bool] = {
    "scaleout": True,
    "hub-and-spoke": False,
}


class Prompter(Protocol):
    """Minimal prompt surface the wizard uses.

    Implementations: :class:`QuestionaryPrompter` (real CLI) and the test
    harness's ``ScriptedPrompter``. Keeping the surface this small keeps
    the wizard easy to mock and easy to reason about.
    """

    def select(
        self,
        message: str,
        choices: Sequence[tuple[str, str]],
        default: str | None = None,
    ) -> str:
        """Single-choice prompt. ``choices`` is a list of ``(value, label)``
        pairs; the chosen ``value`` is returned. ``default`` is one of the
        values (or None for first-choice default)."""

    def text(
        self,
        message: str,
        default: str = "",
    ) -> str:
        """Free-text prompt; returns the user's string (or ``default``)."""

    def confirm(self, message: str, default: bool = False) -> bool:
        """Yes/no prompt; returns the user's choice (or ``default``)."""

    def integer(self, message: str, default: int, minimum: int = 0) -> int:
        """Integer prompt; validates ``>= minimum`` and returns the parsed value."""


@dataclass
class WizardOutcome:
    """What the wizard produces.

    The :class:`ProjectConfig` is the headline output; ``confirmed`` lets
    the caller distinguish "user reviewed the summary and said yes" from
    "user bailed at the summary" so the CLI can exit non-zero cleanly.
    """

    config: ProjectConfig
    confirmed: bool
    profile: str
    options: ProfileOptions
    summary_lines: list[str] = field(default_factory=list)


def run_wizard(name: str, prompter: Prompter | None = None) -> ProjectConfig:
    """Run the wizard. Used by the CLI; raises if the user cancels at the summary.

    ``prompter`` defaults to a :class:`QuestionaryPrompter`. Tests pass a
    scripted prompter to drive the wizard without a TTY.
    """
    if prompter is None:
        prompter = QuestionaryPrompter()
    outcome = walk(name, prompter)
    if not outcome.confirmed:
        raise KeyboardInterrupt("wizard cancelled at summary")
    return outcome.config


def walk(name: str, prompter: Prompter) -> WizardOutcome:
    """Walk the decision tree and return the resolved config + summary.

    Pure modulo the prompter; no I/O, no global state. Profile validation
    happens once, at the end - red-tier hub-and-spoke advisories surface as
    :class:`ProfileError`, which the CLI catches.
    """
    profile_slug = _ask_profile(prompter)
    spokes = _ask_spokes(prompter) if profile_slug == "hub-and-spoke" else 3
    frontends = _ask_frontends(prompter) if profile_slug == "scaleout" else 1
    db_kind = _ask_database(prompter)
    edge_role = _ask_edge_role(prompter, profile_slug)
    network_split = _ask_network_split(prompter, profile_slug)
    reverse_proxy = _ask_reverse_proxy(prompter)

    options = ProfileOptions(
        spokes=spokes,
        frontends=frontends,
        force=False,  # the wizard prompts on yellow/red instead of using --force.
        edge_role=edge_role,
        network_split=network_split,
        reverse_proxy=reverse_proxy,
        database_kind=db_kind,
    )

    # Hub-and-spoke advisory: ask the user inside the wizard rather than
    # demanding --force. Yellow asks for confirmation, red asks for the
    # acknowledgement first and then proceeds via the ``force=True`` path so
    # the profile's red-tier guard doesn't block them.
    if profile_slug == "hub-and-spoke":
        options = _confirm_advisory_if_needed(prompter, options)

    try:
        config = build_profile(profile_slug, name, options)
    except ProfileError as exc:
        # Only happens if the user declined the red-tier confirmation;
        # treat as an explicit cancel.
        return WizardOutcome(
            config=ProjectConfig(name=name),
            confirmed=False,
            profile=profile_slug,
            options=options,
            summary_lines=[f"advisory: {exc}"],
        )

    summary = _summarize(config, profile_slug, options)
    confirmed = _ask_summary_confirm(prompter, summary)
    return WizardOutcome(
        config=config,
        confirmed=confirmed,
        profile=profile_slug,
        options=options,
        summary_lines=summary,
    )


# --------------------------------------------------------------------------- #
# Step implementations
# --------------------------------------------------------------------------- #


def _ask_profile(prompter: Prompter) -> str:
    choices = [(p.slug, f"{p.slug:<14} - {p.summary}") for p in list_profiles()]
    return prompter.select("Architecture profile?", choices, default="standalone")


def _ask_spokes(prompter: Prompter) -> int:
    return prompter.integer("Spoke gateway count?", default=3, minimum=0)


def _ask_frontends(prompter: Prompter) -> int:
    return prompter.integer("Frontend gateway count?", default=1, minimum=1)


def _ask_network_split(prompter: Prompter, profile_slug: str) -> bool | None:
    """Whether to split frontend/backend onto separate networks.

    Only meaningful for multi-gateway profiles; single-gateway profiles
    return ``None`` (no prompt) and let the profile keep its default.
    """
    if profile_slug not in _MULTI_GATEWAY_PROFILES:
        return None
    default = _DEFAULT_NETWORK_SPLIT.get(profile_slug, False)
    return prompter.confirm(
        "Split frontend/backend onto separate Docker networks?", default=default
    )


def _ask_database(prompter: Prompter) -> str | None:
    raw = prompter.select("Database?", _DB_CHOICES, default="postgres")
    return None if raw == "none" else raw


def _ask_edge_role(prompter: Prompter, profile_slug: str) -> str | None:
    choices = _edition_choices_for(profile_slug)
    if not choices:
        return None
    default = _DEFAULT_EDGE_ROLE.get(profile_slug, "none")
    raw = prompter.select("Run the Edge edition on which role?", choices, default=default)
    return None if raw == "none" else raw


def _edition_choices_for(profile_slug: str) -> list[tuple[str, str]]:
    """The set of roles that can be Edge-ified per profile, plus 'none'."""
    if profile_slug == "scaleout":
        return [
            ("none", "All gateways run standard"),
            ("frontend", "Frontends run Edge"),
            ("backend", "Backend runs Edge"),
        ]
    if profile_slug == "hub-and-spoke":
        return [
            ("none", "All gateways run standard"),
            ("spoke", "All spokes run Edge (recommended for hub-and-spoke)"),
            ("hub", "Hub runs Edge (unusual; spokes stay standard)"),
        ]
    # standalone / mcp-n8n: single gateway
    return [
        ("none", "Standard edition"),
        ("gateway", "Edge edition"),
    ]


def _ask_reverse_proxy(prompter: Prompter) -> ReverseProxyConfig | None:
    choice = prompter.select(
        "Reverse proxy?",
        [
            ("external", "I already run one (Traefik, nginx, ...): plain host-port mapping"),
            ("install", "Install ia-eknorr/traefik-reverse-proxy"),
            ("skip", "Skip - the gateway is exposed directly on a host port"),
        ],
        default="external",
    )
    if choice != "install":
        return None
    path = prompter.text(
        "Where should the proxy live? (relative path under the project)",
        default="reverse-proxy",
    )
    return ReverseProxyConfig(kind="traefik", path=path)


def _confirm_advisory_if_needed(prompter: Prompter, options: ProfileOptions) -> ProfileOptions:
    """Surface yellow/red advisories during the wizard run.

    Green tier proceeds silently. Yellow asks for confirmation; declining
    rolls back to ``spokes=4`` (still green) so the wizard doesn't strand
    the user on a config they didn't want. Red asks the user to explicitly
    acknowledge the cost; on confirmation, we set ``force=True`` so the
    profile builder lets the config through.
    """
    from ignition_stack.profiles import spoke_advisory

    advisory = spoke_advisory(options.spokes)
    if advisory.tier == "green":
        return options
    if advisory.tier == "yellow":
        confirmed = prompter.confirm(f"{advisory.message}\nProceed?", default=False)
        if confirmed:
            return options
        # Step down to the largest still-green count (4) so the user lands
        # on a usable stack instead of bailing the whole wizard.
        return _with(options, spokes=4)
    # red
    confirmed = prompter.confirm(
        f"{advisory.message}\nAcknowledge and continue anyway?", default=False
    )
    return _with(options, force=True) if confirmed else _with(options, spokes=4)


def _with(options: ProfileOptions, **changes: Any) -> ProfileOptions:
    """Return a new ProfileOptions with ``changes`` applied (frozen dataclass)."""
    from dataclasses import replace

    return replace(options, **changes)


def _summarize(config: ProjectConfig, profile_slug: str, options: ProfileOptions) -> list[str]:
    lines = [
        f"profile      : {profile_slug}",
        f"project name : {config.name}",
        f"gateways     : {len(config.gateways)} "
        f"({', '.join(f'{g.name}={g.ignition_edition}' for g in config.gateways)})",
        f"database     : {config.database.kind if config.database else 'none'}",
        f"services     : {', '.join(config.services) if config.services else '(none)'}",
        f"network split: {'on' if config.network_split else 'off'}",
        "reverse proxy: "
        + (
            f"install Traefik at './{config.reverse_proxy.path}'"
            if config.reverse_proxy
            else "external (plain host-port mapping)"
        ),
    ]
    if config.mcp_dropin:
        lines.append("MCP dropin   : modules/dropin/ (EA-gated; see POST-SETUP.md)")
    if options.force:
        lines.append("advisory     : --force acknowledged")
    return lines


def _ask_summary_confirm(prompter: Prompter, summary: list[str]) -> bool:
    block = "\n".join(summary)
    return prompter.confirm(f"Ready to generate?\n\n{block}\n", default=True)


# --------------------------------------------------------------------------- #
# Concrete prompter backed by Questionary
# --------------------------------------------------------------------------- #


class QuestionaryPrompter:
    """Real-CLI prompter that delegates to ``questionary``.

    Each method translates the Prompter contract into the equivalent
    Questionary call. Imported lazily so unit tests don't require a TTY.
    """

    def select(
        self,
        message: str,
        choices: Sequence[tuple[str, str]],
        default: str | None = None,
    ) -> str:
        import questionary

        # Questionary's `select` takes a list of `Choice` objects with a
        # title (what the user sees) and a value (what we receive). Build
        # the map so we can resolve the answer back to its slug.
        q_choices = [questionary.Choice(title=label, value=value) for value, label in choices]
        # Questionary matches `default` against choice values, not titles, so
        # pass the slug straight through (or None when it isn't a real choice).
        default_value = default if any(value == default for value, _ in choices) else None
        answer = questionary.select(message, choices=q_choices, default=default_value).unsafe_ask()
        return str(answer)

    def text(self, message: str, default: str = "") -> str:
        import questionary

        answer = questionary.text(message, default=default).unsafe_ask()
        return str(answer)

    def confirm(self, message: str, default: bool = False) -> bool:
        import questionary

        return bool(questionary.confirm(message, default=default).unsafe_ask())

    def integer(self, message: str, default: int, minimum: int = 0) -> int:
        import questionary

        def _validate(text: str) -> bool | str:
            try:
                value = int(text)
            except ValueError:
                return "Enter an integer."
            if value < minimum:
                return f"Must be >= {minimum}."
            return True

        answer = questionary.text(message, default=str(default), validate=_validate).unsafe_ask()
        return int(answer)
