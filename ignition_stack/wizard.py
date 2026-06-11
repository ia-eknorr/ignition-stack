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
9. **Exposure** - host ports (default) or a reverse proxy. Choosing the proxy
   detects an existing ``proxy`` Docker network and offers to join it, else asks
   for the network name or scaffolds ``ia-eknorr/traefik-reverse-proxy``.
10. **Summary** - a three-way select: *generate* (write the project),
    *tweak* (hand the built+resolved config to the Custom composer pre-filled),
    or *cancel* (abort).

Per-gateway env-var overrides (``memory_mb`` etc.) are deferred to a future
phase; the gateway model already accepts them, so adding a wizard step on top
is a non-breaking follow-up.

Back-navigation (issue #59)
---------------------------

The Quick track is a **step machine, steps as data**: :data:`QUICK_STEPS` is an
ordered list of :class:`Step` objects, each with a ``name``, a human ``label``
(for the issue #60 breadcrumb), an ``applies(answers)`` predicate, and an
``ask(prompter, answers, allow_back)`` callable that prompts and returns the
step's answer (or the :data:`BACK` sentinel). Walking advances through the
applicable steps recording answers; the summary is reached when the cursor runs
off the end of the list. The list is introspectable - given an ``answers`` dict
you can recover the names, the applicable subset, and the current position -
which is what the follow-up breadcrumb will render.

*Going back re-asks from that step forward.* Backing pops to the previous
**applicable** step (skipped steps are jumped in both directions); each
subsequent step is then re-asked, with the previously stored answer replayed as
its default (select default = prior choice, confirm default = prior bool,
integer default = prior value). This "replay forward, re-asking each" rule is
deliberately the simpler of the two candidates: every later answer is
re-confirmed rather than silently kept, so **invalidation is automatic** - an
answer that is no longer a legal choice for the changed earlier answer (e.g. the
edge-role after switching profile from hub-and-spoke to standalone) is dropped
because its step re-asks and its stale value, not being in the new choice set,
falls back to the step's canonical default. Newly-applicable steps (a spoke
count that only exists for hub-and-spoke) are asked; newly-inapplicable ones are
skipped and their stored answers ignored at build time.

*Back affordance.* Back is offered on **select** prompts (a dim "Back" choice
appended last, mapped to :data:`BACK`) and on **confirm** prompts (rendered as a
Yes/No/Back select when ``allow_back`` is set). **Integer** and **text** prompts
have no Back affordance in v1: the only integer steps are the spoke/frontend
counts, which sit directly after the profile select, and text prompts appear
only as reverse-proxy sub-questions. You can still step *into* those steps from a
later select (their prior value replays as the default); you just cannot
*initiate* a back from them. The track gate is step 0's predecessor: backing off
the profile step returns to the "How do you want to build?" prompt. The summary
select also carries a Back option, returning the user to the last question
instead of forcing a cancel. The :data:`BACK` sentinel keeps the ``Prompter``
protocol test-mockable: ``ScriptedPrompter`` drives back-navigation by yielding
``BACK`` in its answer script, exactly where ``QuestionaryPrompter`` would map
the Back choice.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from rich.console import Console

from ignition_stack.config import ProjectConfig, ReverseProxyConfig, dump_config
from ignition_stack.profiles import (
    ProfileError,
    ProfileOptions,
    build_profile,
    list_profiles,
)
from ignition_stack.services.resolver import resolve

console = Console()


class _Back:
    """Singleton sentinel a prompter returns to mean "go back one step".

    A distinct type (not ``None``/``False``, both legitimate answers) so the
    step machine can tell a back request apart from a real selection. Both
    prompters return this same instance: ``QuestionaryPrompter`` maps its Back
    choice to it, ``ScriptedPrompter`` yields it straight from the answer script.
    """

    _instance: _Back | None = None

    def __new__(cls) -> _Back:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "BACK"


#: Sentinel returned by a prompter to request stepping back to the prior prompt.
BACK = _Back()

# Internal sentinel for "no stored answer yet, use the step's canonical default".
# Distinct from ``None``, which is a real stored answer (e.g. database "none").
_UNSET = object()

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
        allow_back: bool = False,
    ) -> Any:
        """Single-choice prompt. ``choices`` is a list of ``(value, label)``
        pairs; the chosen ``value`` is returned. ``default`` is one of the
        values (or None for first-choice default). When ``allow_back`` is set a
        Back affordance is offered and selecting it returns :data:`BACK`."""

    def text(
        self,
        message: str,
        default: str = "",
    ) -> str:
        """Free-text prompt; returns the user's string (or ``default``)."""

    def confirm(self, message: str, default: bool = False, allow_back: bool = False) -> Any:
        """Yes/no prompt; returns the user's choice (or ``default``). When
        ``allow_back`` is set a Back affordance is offered and choosing it
        returns :data:`BACK`."""

    def integer(self, message: str, default: int, minimum: int = 0, allow_back: bool = False) -> Any:
        """Integer prompt; validates ``>= minimum`` and returns the parsed value.

        ``allow_back`` is accepted for signature parity with the other prompts;
        integer steps have no Back affordance in v1 (see the module docstring)."""

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


@dataclass(frozen=True)
class Step:
    """One node of the Quick-track step machine.

    ``applies`` decides whether the step is shown for the current ``answers``
    (so profile-specific steps appear/vanish as earlier answers change).
    ``ask`` runs the prompt(s) and returns the step's answer, or :data:`BACK`
    to request stepping back. ``label`` is the human name the issue #60
    breadcrumb will render. The objects are pure data: the closures take the
    prompter and the answers dict, holding no state of their own.
    """

    name: str
    label: str
    applies: Callable[[Mapping[str, Any]], bool]
    ask: Callable[[Prompter, dict[str, Any], bool], Any]


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

    The loop re-shows the track gate when the Quick track backs out of its first
    step, so the gate behaves like step 0 of the machine without entangling the
    Custom branch.
    """
    answers: dict[str, Any] = {}
    while True:
        track = _ask_track(prompter, default=answers.get("track", _TRACK_QUICK))
        answers["track"] = track
        if track == _TRACK_CUSTOM:
            return _run_custom_track(name, prompter)
        outcome = _run_quick_track(name, prompter, answers)
        if outcome is BACK:
            continue  # backed off the first profile step -> re-ask the gate
        return outcome


def _run_quick_track(name: str, prompter: Prompter, answers: dict[str, Any]) -> Any:
    """Drive :data:`QUICK_STEPS`, then the summary; honour Back throughout.

    A single cursor walks the step list. ``history`` is the stack of applicable
    step indices already answered, so a Back pops to the previous *applicable*
    step (non-applicable steps are skipped in both directions). Running off the
    end of the list is "show the summary"; Back at the summary pops to the last
    question, Back before the first step returns :data:`BACK` so :func:`walk`
    re-shows the track gate. Returns a :class:`WizardOutcome` on completion.
    """
    steps = QUICK_STEPS
    i = 0
    history: list[int] = []
    while True:
        if i >= len(steps):
            result = _summary_phase(name, prompter, answers)
            if result is not BACK:
                return result
            if not history:
                return BACK
            i = history.pop()
            continue
        step = steps[i]
        if not step.applies(answers):
            i += 1
            continue
        answer = step.ask(prompter, answers, True)
        if answer is BACK:
            if not history:
                return BACK
            i = history.pop()
            continue
        answers[step.name] = answer
        history.append(i)
        i += 1


def _options_from_answers(answers: Mapping[str, Any]) -> ProfileOptions:
    """Assemble :class:`ProfileOptions` from the recorded step answers.

    Answers for steps that no longer apply to the chosen profile are ignored
    here (the spoke count after switching away from hub-and-spoke, etc.), which
    is where stale-but-stored answers get dropped at build time.
    """
    profile_slug = answers["profile"]
    spokes = answers.get("spokes", 3) if profile_slug == "hub-and-spoke" else 3
    frontends = answers.get("frontends", 1) if profile_slug == "scaleout" else 1
    network_split = answers.get("network_split") if profile_slug in _MULTI_GATEWAY_PROFILES else None
    redundant_role = answers.get("redundancy") if profile_slug in _REDUNDANCY_ROLE else None
    iiot, iiot_broker = answers.get("iiot", (False, None))
    return ProfileOptions(
        spokes=spokes,
        frontends=frontends,
        force=False,  # the wizard prompts on yellow/red instead of using --force.
        edge_role=answers.get("edge_role"),
        network_split=network_split,
        reverse_proxy=answers.get("exposure"),
        database_kind=answers.get("database"),
        redundant_role=redundant_role,
        disable_builtins=answers.get("modules", ()),
        iiot=iiot,
        iiot_broker=iiot_broker,
    )


def _summary_phase(name: str, prompter: Prompter, answers: Mapping[str, Any]) -> Any:
    """Build the config from ``answers`` and run the summary gate.

    Returns a :class:`WizardOutcome` for generate/tweak/cancel, or :data:`BACK`
    when the user chooses to return to the last question. The hub-and-spoke
    advisory and the preview loop live here so they re-run each time the summary
    is (re)entered after a back.
    """
    profile_slug = answers["profile"]
    options = _options_from_answers(answers)

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
    while True:
        action = _ask_summary_action(prompter, summary)
        if action != "preview":
            break
        console.print(dump_config(resolve(config), "yaml"), end="", markup=False)
    if action is BACK:
        return BACK
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


def _ask_track(prompter: Prompter, default: str = _TRACK_QUICK) -> str:
    """The two-track gate (issue #43 phase 7): quick profile flow or custom composer.

    The wizard's very first prompt, so it carries no Back affordance.
    """
    return prompter.select(
        "How do you want to build?",
        [
            (_TRACK_QUICK, "Quick — profile flow"),
            (_TRACK_CUSTOM, "Custom — compose services per gateway"),
        ],
        default=default,
    )


def _ask_profile(prompter: Prompter, default: str = "standalone", allow_back: bool = False) -> Any:
    choices = [(p.slug, f"{p.slug:<14} - {p.summary}") for p in list_profiles()]
    return prompter.select("Architecture profile?", choices, default=default, allow_back=allow_back)


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


def _ask_iiot(prompter: Prompter, default: tuple[bool, str | None] = (False, None), allow_back: bool = False) -> Any:
    """Whether to overlay the MQTT/Sparkplug pipeline, and which broker.

    Default off. On "yes" a broker select defaults to chariot (Cirrus Link's own
    broker, the most official pairing with Transmission/Engine) and lists every
    ``mqtt-broker`` catalog kind. Returns ``(False, None)`` when declined, or
    :data:`BACK` if the user backs out of the leading confirm. ``default`` is the
    previously stored ``(enabled, broker)`` pair, replayed on re-entry.
    """
    want = prompter.confirm("Add IIoT (MQTT/Sparkplug)?", default=default[0], allow_back=allow_back)
    if want is BACK:
        return BACK
    if not want:
        return False, None
    from ignition_stack.wizard_composer import mqtt_broker_choices

    broker = prompter.select("MQTT broker?", mqtt_broker_choices(), default=default[1] or "chariot")
    return True, broker


def _ask_spokes(prompter: Prompter, default: int = 3, allow_back: bool = False) -> Any:
    return prompter.integer("Spoke gateway count?", default=default, minimum=0, allow_back=allow_back)


def _ask_frontends(prompter: Prompter, default: int = 1, allow_back: bool = False) -> Any:
    return prompter.integer("Frontend gateway count?", default=default, minimum=1, allow_back=allow_back)


def _ask_network_split(prompter: Prompter, profile_slug: str, default: bool | None = None, allow_back: bool = False) -> Any:
    """Whether to split frontend/backend onto separate networks.

    Only meaningful for multi-gateway profiles; single-gateway profiles
    return ``None`` (no prompt) and let the profile keep its default. ``default``
    of ``None`` means "use the per-profile proposal"; a stored bool replays it.
    """
    if profile_slug not in _MULTI_GATEWAY_PROFILES:
        return None
    if default is None:
        default = _DEFAULT_NETWORK_SPLIT.get(profile_slug, False)
    return prompter.confirm("Split frontend/backend onto separate Docker networks?", default=default, allow_back=allow_back)


def _ask_redundancy(prompter: Prompter, profile_slug: str, default: bool = False, allow_back: bool = False) -> Any:
    """Offer to make the profile's workhorse role redundant (master + backup).

    Only profiles with a single pairable role prompt; the rest return ``None``.
    Defaults off - redundancy doubles the gateway count and needs two licenses,
    so it is opt-in. Returns the role slug when accepted, ``None`` when declined,
    or :data:`BACK` on a back request.
    """
    role = _REDUNDANCY_ROLE.get(profile_slug)
    if role is None:
        return None
    make = prompter.confirm(
        f"Enable redundancy for the {role} gateway?",
        default=default,
        allow_back=allow_back,
    )
    if make is BACK:
        return BACK
    return role if make else None


def _ask_disable_builtins(
    prompter: Prompter,
    db_kind: str | None,
    prior_disable: tuple[str, ...] | None = None,
    allow_back: bool = False,
) -> Any:
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

    customize = prompter.confirm("Customize the enabled gateway modules?", default=False, allow_back=allow_back)
    if customize is BACK:
        return BACK
    if not customize:
        # One-keystroke common path: accept the curated default set as-is. The
        # disabled set is everything outside it, including the non-matching JDBC
        # drivers - which is exactly the lean gateway the opt-in model promises.
        return tuple(sorted(all_slugs - prechecked))

    # On re-entry after a back, replay the user's prior custom selection as the
    # pre-checked set rather than resetting them to the curated baseline.
    if prior_disable is not None:
        prechecked = all_slugs - set(prior_disable)
    choices = [(m.slug, m.name, m.slug in prechecked) for m in sorted(catalog.modules, key=lambda m: m.name.lower())]
    chosen = set(prompter.checkbox("Modules to enable:", choices))
    return tuple(sorted(all_slugs - chosen))


def _ask_database(prompter: Prompter, default: str = "postgres", allow_back: bool = False) -> Any:
    raw = prompter.select("Database?", _DB_CHOICES, default=default, allow_back=allow_back)
    if raw is BACK:
        return BACK
    return None if raw == "none" else raw


def _ask_edge_role(prompter: Prompter, profile_slug: str, default: Any = _UNSET, allow_back: bool = False) -> Any:
    choices = _edition_choices_for(profile_slug)
    if not choices:
        return None
    valid = {value for value, _ in choices}
    # Replay the stored raw value only when it is still a legal choice for the
    # current profile; otherwise fall back to the profile's canonical default.
    # This is what drops an edge-role that the new profile no longer offers.
    if default is _UNSET or default not in valid:
        default = _DEFAULT_EDGE_ROLE.get(profile_slug, "none")
    raw = prompter.select("Run the Edge edition on which role?", choices, default=default, allow_back=allow_back)
    if raw is BACK:
        return BACK
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


# The network ia-eknorr/traefik-reverse-proxy creates by default. Detecting it
# lets the wizard offer "join the proxy you already run" before asking anything.
_DEFAULT_PROXY_NETWORK = "proxy"


def _detect_proxy_network() -> list[str]:
    """Docker network names on this host, or [] when Docker is unreachable.

    Kept as a module-level seam (the wizard's pure ``walk`` never shells out
    directly) so tests monkeypatch this one function and stay TTY/Docker-free.
    Degrades silently: a missing ``docker`` binary, a dead daemon, or a non-zero
    exit all return [] so the wizard falls through to the manual question.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["docker", "network", "ls", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _ask_reverse_proxy(prompter: Prompter, default: ReverseProxyConfig | None = None, allow_back: bool = False) -> Any:
    """Proxy vs. host ports, with detection of an existing proxy network.

    Host ports (the default, least-surprising answer) returns ``None`` - today's
    plain ``localhost:<port>`` mapping. Choosing the proxy detects a ``proxy``
    network (``ia-eknorr/traefik-reverse-proxy``'s default) and offers to join
    it; otherwise the user names their proxy's network or scaffolds the repo. The
    leading mode select carries the Back affordance; the proxy sub-questions do
    not. ``default`` (a prior :class:`ReverseProxyConfig` or ``None``) only
    replays the top-level host-ports-vs-proxy choice.
    """
    mode = prompter.select(
        "Expose gateways via",
        [
            ("ports", "Host ports"),
            ("proxy", "Reverse proxy"),
        ],
        default="ports" if default is None else "proxy",
        allow_back=allow_back,
    )
    if mode is BACK:
        return BACK
    if mode == "ports":
        return None

    networks = _detect_proxy_network()
    if _DEFAULT_PROXY_NETWORK in networks and prompter.confirm(
        f"Join the existing '{_DEFAULT_PROXY_NETWORK}' network?",
        default=True,
    ):
        return ReverseProxyConfig(mode="external", network=_DEFAULT_PROXY_NETWORK)

    source = prompter.select(
        "Proxy network",
        [
            ("named", "Name an existing network"),
            ("scaffold", "Scaffold ia-eknorr/traefik-reverse-proxy"),
        ],
        default="named",
    )
    if source == "scaffold":
        path = prompter.text("Scaffold path", default="reverse-proxy")
        return ReverseProxyConfig(mode="scaffold", network=_DEFAULT_PROXY_NETWORK, path=path)
    network = prompter.text("Network name", default=_DEFAULT_PROXY_NETWORK)
    return ReverseProxyConfig(mode="external", network=network)


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
        "exposure     : " + _proxy_label(config.reverse_proxy),
    ]
    if config.mcp_dropin:
        lines.append("MCP dropin   : modules/dropin/ (EA-gated; see POST-SETUP.md)")
    if options.force:
        lines.append("advisory     : --force acknowledged")
    return lines


def _proxy_label(proxy: ReverseProxyConfig | None) -> str:
    """Summary-line rendering of the exposure choice."""
    if proxy is None:
        return "host ports"
    if proxy.mode == "scaffold":
        return f"reverse proxy — scaffold Traefik at './{proxy.path}', network '{proxy.network}'"
    return f"reverse proxy — join network '{proxy.network}'"


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


def _ask_summary_action(prompter: Prompter, summary: list[str]) -> Any:
    """The summary gate: generate / preview / tweak / cancel, plus Back.

    *generate* writes the project as-is (today's confirmed path); *tweak* hands
    the built config to the Custom composer pre-filled; *cancel* aborts (the
    CLI maps an unconfirmed outcome to exit 130). The Back affordance returns
    :data:`BACK`, sending the user to the last question instead of cancelling.
    """
    block = "\n".join(summary)
    return prompter.select(
        f"Ready to generate?\n\n{block}\n",
        [
            ("generate", "Generate the project"),
            ("preview", "Preview the resolved config (dry-run)"),
            ("tweak", "Tweak per-gateway services in the custom composer"),
            ("cancel", "Cancel"),
        ],
        default="generate",
        allow_back=True,
    )


# --------------------------------------------------------------------------- #
# Quick-track step machine (issue #59): steps as data, with back-navigation
# --------------------------------------------------------------------------- #


def _step_profile(prompter: Prompter, answers: dict[str, Any], allow_back: bool) -> Any:
    return _ask_profile(prompter, default=answers.get("profile", "standalone"), allow_back=allow_back)


def _step_spokes(prompter: Prompter, answers: dict[str, Any], allow_back: bool) -> Any:
    return _ask_spokes(prompter, default=answers.get("spokes", 3), allow_back=allow_back)


def _step_frontends(prompter: Prompter, answers: dict[str, Any], allow_back: bool) -> Any:
    return _ask_frontends(prompter, default=answers.get("frontends", 1), allow_back=allow_back)


def _step_database(prompter: Prompter, answers: dict[str, Any], allow_back: bool) -> Any:
    prior = answers.get("database", _UNSET)
    default = "postgres" if prior is _UNSET else ("none" if prior is None else prior)
    return _ask_database(prompter, default=default, allow_back=allow_back)


def _step_edge_role(prompter: Prompter, answers: dict[str, Any], allow_back: bool) -> Any:
    prior = answers.get("edge_role", _UNSET)
    raw_default = _UNSET if prior is _UNSET else ("none" if prior is None else prior)
    return _ask_edge_role(prompter, answers["profile"], default=raw_default, allow_back=allow_back)


def _step_network_split(prompter: Prompter, answers: dict[str, Any], allow_back: bool) -> Any:
    return _ask_network_split(prompter, answers["profile"], default=answers.get("network_split"), allow_back=allow_back)


def _step_redundancy(prompter: Prompter, answers: dict[str, Any], allow_back: bool) -> Any:
    prior = answers.get("redundancy", _UNSET)
    default = False if prior is _UNSET else (prior is not None)
    return _ask_redundancy(prompter, answers["profile"], default=default, allow_back=allow_back)


def _step_iiot(prompter: Prompter, answers: dict[str, Any], allow_back: bool) -> Any:
    return _ask_iiot(prompter, default=answers.get("iiot", (False, None)), allow_back=allow_back)


def _step_modules(prompter: Prompter, answers: dict[str, Any], allow_back: bool) -> Any:
    prior = answers.get("modules", _UNSET)
    return _ask_disable_builtins(
        prompter,
        answers.get("database"),
        prior_disable=None if prior is _UNSET else prior,
        allow_back=allow_back,
    )


def _step_exposure(prompter: Prompter, answers: dict[str, Any], allow_back: bool) -> Any:
    return _ask_reverse_proxy(prompter, default=answers.get("exposure"), allow_back=allow_back)


#: The Quick track as an ordered, introspectable list of steps. ``applies``
#: reads the chosen profile so count/split/redundancy steps appear only where
#: they mean something (and are skipped in both walk directions otherwise). The
#: summary is reached when the cursor runs past the end of this list. The
#: follow-up breadcrumb (#60) renders ``label`` for the applicable subset.
QUICK_STEPS: list[Step] = [
    Step("profile", "Profile", lambda a: True, _step_profile),
    Step("spokes", "Spoke count", lambda a: a.get("profile") == "hub-and-spoke", _step_spokes),
    Step("frontends", "Frontend count", lambda a: a.get("profile") == "scaleout", _step_frontends),
    Step("database", "Database", lambda a: True, _step_database),
    Step("edge_role", "Edge edition", lambda a: True, _step_edge_role),
    Step("network_split", "Network split", lambda a: a.get("profile") in _MULTI_GATEWAY_PROFILES, _step_network_split),
    Step("redundancy", "Redundancy", lambda a: a.get("profile") in _REDUNDANCY_ROLE, _step_redundancy),
    Step("iiot", "IIoT", lambda a: True, _step_iiot),
    Step("modules", "Modules", lambda a: True, _step_modules),
    Step("exposure", "Exposure", lambda a: True, _step_exposure),
]


def applicable_steps(answers: Mapping[str, Any]) -> list[Step]:
    """The Quick-track steps that apply for the given ``answers``.

    Introspection seam for the issue #60 breadcrumb: the position of the current
    step in this list, and its length, give "step N of M".
    """
    return [step for step in QUICK_STEPS if step.applies(answers)]


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
        allow_back: bool = False,
    ) -> Any:
        import questionary

        # Questionary's `select` takes a list of `Choice` objects with a
        # title (what the user sees) and a value (what we receive). Build
        # the map so we can resolve the answer back to its slug.
        q_choices = [questionary.Choice(title=label, value=value) for value, label in choices]
        if allow_back:
            # Append a dim Back row last; its value is the BACK sentinel, so the
            # answer round-trips straight into the step machine's back handler.
            q_choices.append(questionary.Choice(title="← Back", value=BACK))
        # Questionary matches `default` against choice values, not titles, so
        # pass the slug straight through (or None when it isn't a real choice).
        default_value = default if any(value == default for value, _ in choices) else None
        # instruction=" " (single space) is truthy so questionary uses it
        # verbatim instead of falling back to "(Use arrow keys)". A space
        # renders as nothing visible. Arrow keys, j/k, and emacs keys remain
        # active via their defaults (use_jk_keys=True, use_emacs_keys=True).
        # use_search_filter is intentionally left False: questionary raises
        # ValueError when use_jk_keys and use_search_filter are both True.
        answer = questionary.select(message, choices=q_choices, default=default_value, instruction=" ").unsafe_ask()
        if answer is BACK:
            return BACK
        return str(answer)

    def text(self, message: str, default: str = "") -> str:
        import questionary

        answer = questionary.text(message, default=default).unsafe_ask()
        return str(answer)

    def confirm(self, message: str, default: bool = False, allow_back: bool = False) -> Any:
        import questionary

        if allow_back:
            # Render a back-able confirm as a 3-way select so the Back affordance
            # is uniform with the other steps; the y/n choices preserve the bool
            # contract. Plain confirms (allow_back=False) keep the native y/n UX.
            answer = questionary.select(
                message,
                choices=[
                    questionary.Choice(title="Yes", value=True),
                    questionary.Choice(title="No", value=False),
                    questionary.Choice(title="← Back", value=BACK),
                ],
                default=bool(default),
                instruction=" ",
            ).unsafe_ask()
            return answer if answer is BACK else bool(answer)
        return bool(questionary.confirm(message, default=default).unsafe_ask())

    def integer(self, message: str, default: int, minimum: int = 0, allow_back: bool = False) -> Any:
        import questionary

        # allow_back is accepted for protocol parity but integer prompts have no
        # Back affordance in v1 (see the module docstring); the flag is a no-op.
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
        # Replace the long default instruction with a single terse hint.
        # Space-to-toggle is the only non-obvious binding; enter-to-confirm
        # and arrow navigation need no explanation.
        # checkbox uses `if instruction is not None` so instruction="" also
        # works, but an explicit descriptive string is more helpful.
        answer = questionary.checkbox(message, choices=q_choices, instruction="(space to toggle)").unsafe_ask()
        # Questionary returns None on Ctrl-C and a list otherwise; normalize.
        return [str(a) for a in (answer or [])]
