"""Interactive wizard that walks the architecture decision tree.

The wizard's *core* (``walk()``) is a pure function over a small
:class:`Prompter` protocol. Real CLI invocations pass a
:class:`QuestionaryPrompter` that delegates to ``questionary``; tests pass
a :class:`ScriptedPrompter` with a pre-recorded sequence of answers and
assert on the resulting :class:`ProjectConfig`. Wizard logic stays
testable without a TTY this way.

The UX shape borrows from Create T3 App: select -> defaults -> summary ->
generate. Each step is one Questionary prompt; the summary screen
recaps the resolved choices and gates the write.

The first prompt is a **two-track gate** (issue #43 phase 7). *Quick* walks
the linear profile flow below; *Custom* hands off to
:mod:`ignition_stack.wizard_composer` for per-gateway service composition on a
topology preset. Both tracks produce the same :class:`ProjectConfig`.

Quick-track step order:

0. **Track** - quick (linear profile flow) or custom (composer). Default quick.
1. **Profile** - which of the four canned shapes the user wants.
2. **Profile-specific count** - spoke count for hub-and-spoke, frontend
   count for scaleout (skipped for single-gateway profiles).
3. **Database** - SQL flavor for the stack, or "none".
4. **Edition per role** - which role (if any) runs Edge. The default is
   profile-driven: all profiles now propose "none" (all standard).
5. **Network split** - for the multi-gateway profiles (scaleout +
   hub-and-spoke), whether to split frontend/backend onto separate
   networks. Defaults on for scaleout, off for hub-and-spoke.
6. **Redundancy** - for profiles with a single workhorse role (standalone
   gateway, scaleout backend, hub-and-spoke hub), whether to add a backup
   node and form a master/backup pair. Defaults off.
7. **IIoT** - whether to add an IIoT (MQTT/Sparkplug) pipeline. Default no;
   on "yes" a broker select defaults to chariot. Wires Cirrus Transmission to
   edge-side roles and Engine to central roles via ``apply_iiot``.
8. **Modules** - opt-in selection of built-in IA modules. A curated default
   set (Perspective, OPC-UA, SQL Bridge, the historian pair, Alarm
   Notification, Reporting) plus the JDBC driver matching the chosen database
   are pre-checked; the un-selected remainder becomes ``disable_builtins``,
   which the engine inverts into the GATEWAY_MODULES_ENABLED whitelist. Gated
   behind a "Customize?" confirm; declining accepts the lean default.
9. **Reverse proxy** - existing/install-Traefik/skip.
10. **Summary** - a three-way select: *generate* (write the project),
    *tweak* (hand the built+resolved config to the Custom composer pre-filled),
    or *cancel* (abort).

Per-gateway env-var overrides (``memory_mb`` etc.) are deferred to a future
phase; the gateway model already accepts them, so adding a wizard step on top
is a non-breaking follow-up.
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
from ignition_stack.services.resolver import resolve

# Track-gate values (the wizard's first prompt). Quick keeps the linear profile
# flow; Custom hands off to the per-gateway composer.
_TRACK_QUICK = "quick"
_TRACK_CUSTOM = "custom"

# Database options shown in the wizard, in the order they appear on screen.
_DB_CHOICES: list[tuple[str, str]] = [
    ("postgres", "PostgreSQL"),
    ("mysql", "MySQL"),
    ("mariadb", "MariaDB"),
    ("mongo", "MongoDB"),
    ("none", "None"),
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

# The single workhorse role each profile can make redundant (master + backup).
# Replicated tiers (frontends, spokes) are deliberately absent - they scale
# out, they don't fail over. Profiles not listed here skip the prompt.
_REDUNDANCY_ROLE: dict[str, str] = {
    "standalone": "gateway",
    "scaleout": "backend",
    "hub-and-spoke": "hub",
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

    def checkbox(self, message: str, choices: Sequence[tuple[str, str, bool]]) -> list[str]:
        """Multi-select prompt. ``choices`` is ``(value, label, checked)`` triples
        where ``checked`` pre-selects the row; returns the list of chosen
        ``value``\\ s (the pre-checked rows the user left on, plus any they added,
        minus any they toggled off). Possibly empty."""


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

    Pure modulo the prompter; no global state. The first prompt is the
    two-track gate: *quick* runs the linear profile flow, *custom* hands off to
    the per-gateway composer. Both return a :class:`WizardOutcome` whose
    ``config`` is what the CLI writes.
    """
    track = _ask_track(prompter)
    if track == _TRACK_CUSTOM:
        return _run_custom_track(name, prompter)
    return _run_quick_track(name, prompter)


def _run_quick_track(name: str, prompter: Prompter) -> WizardOutcome:
    """The linear profile flow (unchanged shape, plus the IIoT confirm and the
    three-way summary that can hand off to the composer)."""
    profile_slug = _ask_profile(prompter)
    spokes = _ask_spokes(prompter) if profile_slug == "hub-and-spoke" else 3
    frontends = _ask_frontends(prompter) if profile_slug == "scaleout" else 1
    db_kind = _ask_database(prompter)
    edge_role = _ask_edge_role(prompter, profile_slug)
    network_split = _ask_network_split(prompter, profile_slug)
    redundant_role = _ask_redundancy(prompter, profile_slug)
    iiot, iiot_broker = _ask_iiot(prompter)
    disable_builtins = _ask_disable_builtins(prompter, db_kind)
    reverse_proxy = _ask_reverse_proxy(prompter)

    options = ProfileOptions(
        spokes=spokes,
        frontends=frontends,
        force=False,  # the wizard prompts on yellow/red instead of using --force.
        edge_role=edge_role,
        network_split=network_split,
        reverse_proxy=reverse_proxy,
        database_kind=db_kind,
        redundant_role=redundant_role,
        disable_builtins=disable_builtins,
        iiot=iiot,
        iiot_broker=iiot_broker,
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
    action = _ask_summary_action(prompter, summary)
    if action == "tweak":
        # Hand the built, resolved config to the composer pre-filled. Its summary
        # loop takes over and produces the final config.
        from ignition_stack import wizard_composer

        result = wizard_composer.edit_loop(prompter, resolve(config), profile_slug, options)
        return _outcome_from_composer(result)
    return WizardOutcome(
        config=config,
        confirmed=(action == "generate"),
        profile=profile_slug,
        options=options,
        summary_lines=summary,
    )


def _run_custom_track(name: str, prompter: Prompter) -> WizardOutcome:
    """Build a bare topology preset, resolve it, and enter the composer."""
    from ignition_stack import wizard_composer

    profile_slug, options = _ask_topology_preset(prompter)
    if profile_slug == "hub-and-spoke":
        options = _confirm_advisory_if_needed(prompter, options)

    try:
        config = build_profile(profile_slug, name, options)
    except ProfileError as exc:
        return WizardOutcome(
            config=ProjectConfig(name=name),
            confirmed=False,
            profile=profile_slug,
            options=options,
            summary_lines=[f"advisory: {exc}"],
        )

    result = wizard_composer.edit_loop(prompter, resolve(config), profile_slug, options)
    return _outcome_from_composer(result)


def _outcome_from_composer(result) -> WizardOutcome:
    """Adapt a :class:`~ignition_stack.wizard_composer.ComposerResult`."""
    return WizardOutcome(
        config=result.config,
        confirmed=result.confirmed,
        profile=result.profile,
        options=result.options,
        summary_lines=result.summary_lines,
    )


# --------------------------------------------------------------------------- #
# Step implementations
# --------------------------------------------------------------------------- #


def _ask_track(prompter: Prompter) -> str:
    """The two-track gate (issue #43 phase 7): quick profile flow or custom composer."""
    return prompter.select(
        "How do you want to build?",
        [
            (_TRACK_QUICK, "Quick — profile flow"),
            (_TRACK_CUSTOM, "Custom — compose services per gateway"),
        ],
        default=_TRACK_QUICK,
    )


def _ask_profile(prompter: Prompter) -> str:
    choices = [(p.slug, f"{p.slug:<14} - {p.summary}") for p in list_profiles()]
    return prompter.select("Architecture profile?", choices, default="standalone")


def _ask_topology_preset(prompter: Prompter) -> tuple[str, ProfileOptions]:
    """Pick the Custom track's starting topology + its count/edge/split/redundancy.

    Reuses the Quick-track ``_ask_*`` helpers so the two tracks never drift on
    what a profile's topology questions are. ``mcp-n8n`` is skipped - it is just
    "standalone + n8n + MCP dropin", expressible by attaching n8n in the
    composer. The preset is built with **no database and no services**
    (``database_kind=None``): the composer is where the registry gets populated,
    so it starts from a bare gateway skeleton.
    """
    choices = [(p.slug, f"{p.slug:<14} - {p.summary}") for p in list_profiles() if p.slug != "mcp-n8n"]
    profile_slug = prompter.select("Starting topology (preset)?", choices, default="standalone")
    spokes = _ask_spokes(prompter) if profile_slug == "hub-and-spoke" else 3
    frontends = _ask_frontends(prompter) if profile_slug == "scaleout" else 1
    edge_role = _ask_edge_role(prompter, profile_slug)
    network_split = _ask_network_split(prompter, profile_slug)
    redundant_role = _ask_redundancy(prompter, profile_slug)
    options = ProfileOptions(
        spokes=spokes,
        frontends=frontends,
        force=False,
        edge_role=edge_role,
        network_split=network_split,
        database_kind=None,
        redundant_role=redundant_role,
    )
    return profile_slug, options


def _ask_iiot(prompter: Prompter) -> tuple[bool, str | None]:
    """Whether to overlay the MQTT/Sparkplug pipeline, and which broker.

    Default off. On "yes" a broker select defaults to chariot (Cirrus Link's own
    broker, the most official pairing with Transmission/Engine) and lists every
    ``mqtt-broker`` catalog kind. Returns ``(False, None)`` when declined.
    """
    if not prompter.confirm("Add IIoT (MQTT/Sparkplug)?", default=False):
        return False, None
    from ignition_stack.wizard_composer import mqtt_broker_choices

    broker = prompter.select("MQTT broker?", mqtt_broker_choices(), default="chariot")
    return True, broker


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
    return prompter.confirm("Split frontend/backend onto separate Docker networks?", default=default)


def _ask_redundancy(prompter: Prompter, profile_slug: str) -> str | None:
    """Offer to make the profile's workhorse role redundant (master + backup).

    Only profiles with a single pairable role prompt; the rest return ``None``.
    Defaults off - redundancy doubles the gateway count and needs two licenses,
    so it is opt-in.
    """
    role = _REDUNDANCY_ROLE.get(profile_slug)
    if role is None:
        return None
    make = prompter.confirm(
        f"Enable redundancy for the {role} gateway?",
        default=False,
    )
    return role if make else None


def _ask_disable_builtins(prompter: Prompter, db_kind: str | None) -> tuple[str, ...]:
    """Pick the built-in modules to run, opt-in from a curated default set.

    The catalog marks a lean "typical demo" set as default-enabled (Perspective,
    OPC-UA, SQL Bridge, the historian pair, Alarm Notification, Reporting); the
    wizard pre-checks those plus the JDBC driver matching the chosen database.
    The common path is one keystroke - declining "Customize?" accepts that set
    as-is. Customizing opens a checkbox with the default set pre-checked, so the
    user adds or removes from a sensible baseline instead of scrolling a 29-item
    list. Whatever ends up *un*selected becomes the stored ``disable_builtins``;
    the engine emits the inverse whitelist. Returning the inverse keeps the
    config model, the writer, and the non-interactive profile path unchanged.
    """
    from ignition_stack.catalog.builtins import default_builtin_catalog, jdbc_driver_for

    catalog = default_builtin_catalog()
    all_slugs = catalog.slugs
    prechecked = set(catalog.default_enabled_slugs)
    driver = jdbc_driver_for(db_kind)
    if driver is not None:
        prechecked.add(driver)

    if not prompter.confirm("Customize the enabled gateway modules?", default=False):
        # One-keystroke common path: accept the curated default set as-is. The
        # disabled set is everything outside it, including the non-matching JDBC
        # drivers - which is exactly the lean gateway the opt-in model promises.
        return tuple(sorted(all_slugs - prechecked))

    choices = [(m.slug, m.name, m.slug in prechecked) for m in sorted(catalog.modules, key=lambda m: m.name.lower())]
    chosen = set(prompter.checkbox("Modules to enable:", choices))
    return tuple(sorted(all_slugs - chosen))


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
            ("spoke", "All spokes run Edge"),
            ("hub", "Hub runs Edge"),
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
            ("external", "Use an existing reverse proxy"),
            ("install", "Install ia-eknorr/traefik-reverse-proxy"),
            ("skip", "Skip — exposed directly on a host port"),
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
    confirmed = prompter.confirm(f"{advisory.message}\nAcknowledge and continue anyway?", default=False)
    return _with(options, force=True) if confirmed else _with(options, spokes=4)


def _with(options: ProfileOptions, **changes: Any) -> ProfileOptions:
    """Return a new ProfileOptions with ``changes`` applied (frozen dataclass)."""
    from dataclasses import replace

    return replace(options, **changes)


def _summarize(config: ProjectConfig, profile_slug: str, options: ProfileOptions) -> list[str]:
    lines = [
        f"profile      : {profile_slug}",
        f"project name : {config.name}",
        f"gateways     : {len(config.gateways)} ({', '.join(f'{g.name}={g.ignition_edition}' for g in config.gateways)})",
        f"database     : {config.database.kind if config.database else 'none'}",
        f"services     : {', '.join(config.services) if config.services else '(none)'}",
        f"network split: {'on' if config.network_split else 'off'}",
        "redundancy   : " + (f"{options.redundant_role} (master + backup)" if options.redundant_role else "none"),
        "iiot         : " + (f"{options.iiot_broker or 'chariot'} (Transmission/Engine overlay)" if options.iiot else "off"),
        "modules      : " + _enabled_modules_label(options.disable_builtins),
        "reverse proxy: " + (f"install Traefik at './{config.reverse_proxy.path}'" if config.reverse_proxy else "external (plain host-port mapping)"),
    ]
    if config.mcp_dropin:
        lines.append("MCP dropin   : modules/dropin/ (EA-gated; see POST-SETUP.md)")
    if options.force:
        lines.append("advisory     : --force acknowledged")
    return lines


def _enabled_modules_label(disable_builtins: tuple[str, ...]) -> str:
    """Render the kept (enabled) built-ins by display name for the summary.

    The opt-in model usually disables more than it keeps, so the summary shows
    the lean *enabled* set - the few modules the gateway will actually run - by
    display name, which is the choice the user just made. ``disable_builtins``
    is the stored inverse; enabled is the catalog minus it.
    """
    from ignition_stack.catalog.builtins import default_builtin_catalog

    disabled = set(disable_builtins)
    kept = [m.name for m in sorted(default_builtin_catalog().modules, key=lambda m: m.name.lower()) if m.slug not in disabled]
    return ", ".join(kept) if kept else "(none - all built-ins disabled)"


def _ask_summary_action(prompter: Prompter, summary: list[str]) -> str:
    """The three-way summary gate: generate / tweak / cancel.

    *generate* writes the project as-is (today's confirmed path); *tweak* hands
    the built config to the Custom composer pre-filled; *cancel* aborts (the
    CLI maps an unconfirmed outcome to exit 130).
    """
    block = "\n".join(summary)
    return prompter.select(
        f"Ready to generate?\n\n{block}\n",
        [
            ("generate", "Generate the project"),
            ("tweak", "Tweak per-gateway services in the custom composer"),
            ("cancel", "Cancel"),
        ],
        default="generate",
    )


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

    def checkbox(self, message: str, choices: Sequence[tuple[str, str, bool]]) -> list[str]:
        import questionary

        q_choices = [questionary.Choice(title=label, value=value, checked=checked) for value, label, checked in choices]
        answer = questionary.checkbox(message, choices=q_choices).unsafe_ask()
        # Questionary returns None on Ctrl-C and a list otherwise; normalize.
        return [str(a) for a in (answer or [])]
