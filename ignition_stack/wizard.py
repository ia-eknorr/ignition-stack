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

The wizard is **architecture-first**: the very first prompt is the architecture
select (Basic / Scale Out / Hub and Spoke, mirroring Ignition's documented
system architectures). There is no preliminary "how do you want to build?"
gate; the per-gateway composer is reached one way only, through the summary's
*tweak* action.

Step order:

1. **Architecture** - which of the system architectures the user wants.
2. **Architecture-specific count** - spoke count for hub-and-spoke, frontend
   count for scale-out (skipped for single-gateway architectures).
3. **Database** - SQL flavor for the stack, or "none".
4. **Edition per role** - which role (if any) runs Edge. The default is
   architecture-driven: basic/scale-out propose "none", hub-and-spoke "spoke".
5. **Network split** - for the multi-gateway architectures (scale-out +
   hub-and-spoke), whether to split frontend/backend onto separate
   networks. Defaults on for scale-out, off for hub-and-spoke.
6. **Redundancy** - for architectures with a single workhorse role (basic
   gateway, scale-out backend, hub-and-spoke hub), whether to add a backup
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
    *tweak* (hand the built+resolved config to the composer pre-filled),
    or *cancel* (abort).

Per-gateway env-var overrides (``memory_mb`` etc.) are deferred to a future
phase; the gateway model already accepts them, so adding a wizard step on top
is a non-breaking follow-up.

Back-navigation (issue #59)
---------------------------

The flow is a **step machine, steps as data**: :data:`WIZARD_STEPS` is an
ordered list of :class:`Step` objects, each with a ``name``, a human ``label``
(used by the issue #60 progress sign-posting), an ``applies(answers)``
predicate, and an ``ask(prompter, answers, allow_back)`` callable that prompts
and returns the step's answer (or the :data:`BACK` sentinel). Walking advances
through the applicable steps recording answers; the summary is reached when the
cursor runs off the end of the list. The list is introspectable - given an
``answers`` dict you can recover the names, the applicable subset, and the
current position - which is what :func:`_print_plan` and :func:`_step_counter`
read.

Progress sign-posting (issue #60)
---------------------------------

The wizard keeps questionary's natural scrolling Q&A (every answered question
stays on screen) rather than redrawing a fixed header, matching how comparable
scaffolding CLIs read. Orientation comes from two cheap, scrolling sign-posts:
:func:`_print_plan` prints the numbered road ahead once, right after the
architecture is chosen (the answer that decides which steps apply); and
:func:`_step_counter` folds an ``"[N/M] "`` prefix into every later prompt via
the prompter, so position and the finish line ride along with each question.
The architecture prompt itself carries no counter - its total is not yet known.

*Going back re-asks from that step forward.* Backing pops to the previous
**applicable** step (skipped steps are jumped in both directions); each
subsequent step is then re-asked, with the previously stored answer replayed as
its default (select default = prior choice, confirm default = prior bool,
integer default = prior value). This "replay forward, re-asking each" rule is
deliberately the simpler of the two candidates: every later answer is
re-confirmed rather than silently kept, so **invalidation is automatic** - an
answer that is no longer a legal choice for the changed earlier answer (e.g. the
edge-role after switching architecture from hub-and-spoke to basic) is dropped
because its step re-asks and its stale value, not being in the new choice set,
falls back to the step's canonical default. Newly-applicable steps (a spoke
count that only exists for hub-and-spoke) are asked; newly-inapplicable ones are
skipped and their stored answers ignored at build time.

*Back affordance.* Back is offered on **select** prompts (a dim "Back" choice
appended last, mapped to :data:`BACK`) and on **confirm** prompts (rendered as a
Yes/No/Back select when ``allow_back`` is set). **Integer** and **text** prompts
have no Back affordance in v1: the only integer steps are the spoke/frontend
counts, which sit directly after the architecture select, and text prompts
appear only as reverse-proxy sub-questions. You can still step *into* those
steps from a later select (their prior value replays as the default); you just
cannot *initiate* a back from them. The architecture select is the first prompt,
so it offers no Back affordance - there is nowhere earlier to go. The summary
select carries a Back option, returning the user to the last question instead of
forcing a cancel. The :data:`BACK` sentinel keeps the ``Prompter`` protocol
test-mockable: ``ScriptedPrompter`` drives back-navigation by yielding ``BACK``
in its answer script, exactly where ``QuestionaryPrompter`` would map the Back
choice.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from rich.console import Console
from rich.text import Text

from ignition_stack.architectures import (
    ArchitectureError,
    ArchOptions,
    build_architecture,
    list_architectures,
)
from ignition_stack.config import ProjectConfig, ReverseProxyConfig, dump_config
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

# Database options shown in the wizard, in the order they appear on screen.
_DB_CHOICES: list[tuple[str, str]] = [
    ("postgres", "PostgreSQL"),
    ("mysql", "MySQL"),
    ("mariadb", "MariaDB"),
    ("mongo", "MongoDB"),
    ("none", "None"),
]

# Per-architecture default edge-role proposal. The wizard offers this as the
# default selection in the edition prompt; the user can override.
_DEFAULT_EDGE_ROLE: dict[str, str] = {
    "basic": "none",
    "scale-out": "none",
    "hub-and-spoke": "spoke",
}

# Architectures that produce more than one gateway, so the frontend/backend
# network split is a meaningful choice. Single-gateway architectures skip it.
_MULTI_GATEWAY_ARCHITECTURES = frozenset({"scale-out", "hub-and-spoke"})

# Default network-split proposal per multi-gateway architecture. Scale-out
# splits (the point of the demo); hub-and-spoke stays on one shared network.
_DEFAULT_NETWORK_SPLIT: dict[str, bool] = {
    "scale-out": True,
    "hub-and-spoke": False,
}

# The single workhorse role each architecture can make redundant (master +
# backup). Replicated tiers (frontends, spokes) are deliberately absent - they
# scale out, they don't fail over. Architectures not listed here skip the prompt.
_REDUNDANCY_ROLE: dict[str, str] = {
    "basic": "gateway",
    "scale-out": "backend",
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
    architecture: str
    options: ArchOptions
    summary_lines: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Step:
    """One node of the wizard step machine.

    ``applies`` decides whether the step is shown for the current ``answers``
    (so architecture-specific steps appear/vanish as earlier answers change).
    ``ask`` runs the prompt(s) and returns the step's answer, or :data:`BACK`
    to request stepping back. ``label`` is the human name the issue #60 plan and
    step counter render. The objects are pure data: the closures take the
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
    architecture select - there is no preliminary track gate. Drive
    :data:`WIZARD_STEPS`, then the summary, honouring Back throughout.

    A single cursor walks the step list. ``history`` is the stack of applicable
    step indices already answered, so a Back pops to the previous *applicable*
    step (non-applicable steps are skipped in both directions). Running off the
    end of the list is "show the summary"; Back at the summary pops to the last
    question. The architecture step is first, so it offers no Back affordance and
    the cursor never runs before it. Returns a :class:`WizardOutcome` on
    completion.
    """
    # Stash the project name under a reserved key (not a step name) so the
    # services stage can build the architecture config to mutate. Ignored by
    # _options_from_answers, which only reads the step-named answers.
    answers: dict[str, Any] = {"_name": name}
    steps = WIZARD_STEPS
    i = 0
    history: list[int] = []
    while True:
        if i >= len(steps):
            result = _summary_phase(name, prompter, answers)
            if result is not BACK:
                return result
            i = history.pop()
            continue
        step = steps[i]
        if not step.applies(answers):
            i += 1
            continue
        # Fold an "[N/M] " counter into this step's prompt so the user always
        # knows where they are and how many remain. Recomputed each iteration so
        # it tracks the applicable set after a back-and-change. The architecture
        # step gets no counter (the total isn't known until it's answered).
        _set_progress(prompter, _step_counter(answers, step.name))
        # The first step (architecture) has no earlier prompt to return to, so it
        # carries no Back affordance.
        answer = step.ask(prompter, answers, bool(history))
        if answer is BACK:
            i = history.pop()
            continue
        answers[step.name] = answer
        history.append(i)
        i += 1
        # The architecture decides which steps apply, so once it is answered the
        # full plan is known. Print it as a one-time sign-post of the road ahead
        # (reprinted if the user backs up and changes the architecture).
        if step.name == "architecture":
            _print_plan(answers)


def _options_from_answers(answers: Mapping[str, Any]) -> ArchOptions:
    """Assemble :class:`ArchOptions` from the recorded step answers.

    Answers for steps that no longer apply to the chosen architecture are
    ignored here (the spoke count after switching away from hub-and-spoke, etc.),
    which is where stale-but-stored answers get dropped at build time.
    """
    arch_slug = answers["architecture"]
    spokes = answers.get("spokes", 3) if arch_slug == "hub-and-spoke" else 3
    frontends = answers.get("frontends", 1) if arch_slug == "scale-out" else 1
    network_split = answers.get("network_split") if arch_slug in _MULTI_GATEWAY_ARCHITECTURES else None
    redundant_role = answers.get("redundancy") if arch_slug in _REDUNDANCY_ROLE else None
    iiot, iiot_broker = answers.get("iiot", (False, None))
    return ArchOptions(
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


def _build_base_config(name: str, prompter: Prompter, answers: Mapping[str, Any]) -> tuple[ProjectConfig | None, ArchOptions, list[str]]:
    """Build the resolved architecture config the services stage + summary share.

    Returns ``(resolved_config, options, advisory_lines)``; ``resolved_config`` is
    ``None`` only when the hub-and-spoke red-tier acknowledgement was declined
    (the caller turns that into a cancel). The advisory prompt lives here, ahead
    of the services stage, so the spoke count the services stage builds against is
    the same one the summary shows - no divergence between the two.
    """
    arch_slug = answers["architecture"]
    options = _options_from_answers(answers)
    if arch_slug == "hub-and-spoke":
        options = _confirm_advisory_if_needed(prompter, options)
    try:
        config = build_architecture(arch_slug, name, options)
    except ArchitectureError as exc:
        return None, options, [f"advisory: {exc}"]
    return resolve(config), options, []


def _summary_phase(name: str, prompter: Prompter, answers: Mapping[str, Any]) -> Any:
    """Build the config from ``answers`` and run the summary gate.

    Returns a :class:`WizardOutcome` for generate/tweak/cancel, or :data:`BACK`
    when the user chooses to return to the last question. The config is taken
    from the services stage's recorded answer when present (already built,
    resolved, and mutated by the add-loop); otherwise it is built fresh here. The
    preview loop lives here so it re-runs each time the summary is (re)entered.
    """
    # The summary is the final screen; show it as the last step in the counter.
    total = len(applicable_steps(answers)) + 1
    _set_progress(prompter, f"[{total}/{total}] ")
    arch_slug = answers["architecture"]
    recorded = answers.get("services")
    if recorded is not None and len(recorded) == 2:
        config, options = recorded
    elif recorded is not None:
        # The services stage recorded a declined red-tier advisory (3-tuple with a
        # None config); surface it as a cancel, same as the fresh-build path.
        _config, options, advisory_lines = recorded
        return WizardOutcome(config=ProjectConfig(name=name), confirmed=False, architecture=arch_slug, options=options, summary_lines=advisory_lines)
    else:
        config, options, advisory_lines = _build_base_config(name, prompter, answers)
        if config is None:
            return WizardOutcome(
                config=ProjectConfig(name=name),
                confirmed=False,
                architecture=arch_slug,
                options=options,
                summary_lines=advisory_lines,
            )

    summary = _summarize(config, arch_slug, options)
    while True:
        action = _ask_summary_action(prompter, summary)
        if action != "preview":
            break
        console.print(dump_config(resolve(config), "yaml"), end="", markup=False)
    if action is BACK:
        return BACK
    if action == "tweak":
        # Hand the built, resolved config to the composer pre-filled. Its summary
        # loop takes over and produces the final config. Clear the counter first:
        # the composer's add-flow prompts are not part of the numbered plan.
        from ignition_stack import wizard_composer

        _set_progress(prompter, "")
        result = wizard_composer.edit_loop(prompter, resolve(config), arch_slug, options)
        return _outcome_from_composer(result)
    return WizardOutcome(
        config=config,
        confirmed=(action == "generate"),
        architecture=arch_slug,
        options=options,
        summary_lines=summary,
    )


def _outcome_from_composer(result) -> WizardOutcome:
    """Adapt a :class:`~ignition_stack.wizard_composer.ComposerResult`."""
    return WizardOutcome(
        config=result.config,
        confirmed=result.confirmed,
        architecture=result.architecture,
        options=result.options,
        summary_lines=result.summary_lines,
    )


# --------------------------------------------------------------------------- #
# Step implementations
# --------------------------------------------------------------------------- #


# Terse architecture labels for the wizard select, in IA's vocabulary. The
# longer ``summary`` attributes drive ``--help`` and docs; the wizard keeps the
# menu scannable with one structural phrase per choice.
_ARCH_LABELS: dict[str, str] = {
    "basic": "one gateway",
    "scale-out": "frontend/backend tiers",
    "hub-and-spoke": "central hub, edge spokes",
}


def _ask_architecture(prompter: Prompter, default: str = "basic", allow_back: bool = False) -> Any:
    choices = [(a.slug, f"{a.slug} — {_ARCH_LABELS.get(a.slug, a.summary)}") for a in list_architectures()]
    return prompter.select("Architecture?", choices, default=default, allow_back=allow_back)


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


def _ask_network_split(prompter: Prompter, arch_slug: str, default: bool | None = None, allow_back: bool = False) -> Any:
    """Whether to split frontend/backend onto separate networks.

    Only meaningful for multi-gateway architectures; single-gateway architectures
    return ``None`` (no prompt) and let the architecture keep its default. ``default``
    of ``None`` means "use the per-architecture proposal"; a stored bool replays it.
    """
    if arch_slug not in _MULTI_GATEWAY_ARCHITECTURES:
        return None
    if default is None:
        default = _DEFAULT_NETWORK_SPLIT.get(arch_slug, False)
    return prompter.confirm("Split frontend/backend onto separate Docker networks?", default=default, allow_back=allow_back)


def _ask_redundancy(prompter: Prompter, arch_slug: str, default: bool = False, allow_back: bool = False) -> Any:
    """Offer to make the architecture's workhorse role redundant (master + backup).

    Only architectures with a single pairable role prompt; the rest return ``None``.
    Defaults off - redundancy doubles the gateway count and needs two licenses,
    so it is opt-in. Returns the role slug when accepted, ``None`` when declined,
    or :data:`BACK` on a back request.
    """
    role = _REDUNDANCY_ROLE.get(arch_slug)
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
    config model, the writer, and the non-interactive architecture path unchanged.
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


def _ask_edge_role(prompter: Prompter, arch_slug: str, default: Any = _UNSET, allow_back: bool = False) -> Any:
    choices = _edition_choices_for(arch_slug)
    if not choices:
        return None
    valid = {value for value, _ in choices}
    # Replay the stored raw value only when it is still a legal choice for the
    # current architecture; otherwise fall back to the architecture's canonical
    # default. This is what drops an edge-role the new architecture no longer offers.
    if default is _UNSET or default not in valid:
        default = _DEFAULT_EDGE_ROLE.get(arch_slug, "none")
    raw = prompter.select("Run the Edge edition on which role?", choices, default=default, allow_back=allow_back)
    if raw is BACK:
        return BACK
    return None if raw == "none" else raw


def _edition_choices_for(arch_slug: str) -> list[tuple[str, str]]:
    """The set of roles that can be Edge-ified per architecture, plus 'none'."""
    if arch_slug == "scale-out":
        return [
            ("none", "All gateways run standard"),
            ("frontend", "Frontends run Edge"),
            ("backend", "Backend runs Edge"),
        ]
    if arch_slug == "hub-and-spoke":
        return [
            ("none", "All gateways run standard"),
            ("spoke", "All spokes run Edge"),
            ("hub", "Hub runs Edge"),
        ]
    # basic: single gateway
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


def _confirm_advisory_if_needed(prompter: Prompter, options: ArchOptions) -> ArchOptions:
    """Surface yellow/red advisories during the wizard run.

    Green tier proceeds silently. Yellow asks for confirmation; declining
    rolls back to ``spokes=4`` (still green) so the wizard doesn't strand
    the user on a config they didn't want. Red asks the user to explicitly
    acknowledge the cost; on confirmation, we set ``force=True`` so the
    architecture builder lets the config through.
    """
    from ignition_stack.architectures import spoke_advisory

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


def _with(options: ArchOptions, **changes: Any) -> ArchOptions:
    """Return a new ArchOptions with ``changes`` applied (frozen dataclass)."""
    from dataclasses import replace

    return replace(options, **changes)


def _summarize(config: ProjectConfig, arch_slug: str, options: ArchOptions) -> list[str]:
    # The config reaching here is resolved (registry populated, legacy shims
    # cleared), so read the database + services off the registry rather than the
    # emptied ``database`` / ``services`` shims. ``database_instances`` keeps the
    # auto-added Keycloak store visible; ``non_database_instances`` lists every
    # service including the deliberately-flat ones the services stage added.
    dbs = [inst.service for inst in config.database_instances()]
    svcs = [_service_summary_label(config, inst) for inst in config.non_database_instances()]
    lines = [
        f"architecture : {arch_slug}",
        f"project name : {config.name}",
        f"gateways     : {len(config.gateways)} ({', '.join(f'{g.name}={g.ignition_edition}' for g in config.gateways)})",
        f"database     : {', '.join(dbs) if dbs else 'none'}",
        f"services     : {', '.join(svcs) if svcs else '(none)'}",
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


def _service_summary_label(config: ProjectConfig, inst: ProjectConfig) -> str:
    """A service's summary label, tagging the deliberately-flat (unattached) ones."""
    attached = any(att.instance == inst.id for gw in config.gateways for att in gw.services)
    return inst.id if attached else f"{inst.id} (flat)"


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
    the built config to the composer pre-filled; *cancel* aborts (the CLI maps
    an unconfirmed outcome to exit 130). The Back affordance returns
    :data:`BACK`, sending the user to the last question instead of cancelling.
    """
    block = "\n".join(summary)
    return prompter.select(
        f"Ready to generate?\n\n{block}\n",
        [
            ("generate", "Generate the project"),
            ("preview", "Preview the resolved config (dry-run)"),
            ("tweak", "Tweak per-gateway services in the composer"),
            ("cancel", "Cancel"),
        ],
        default="generate",
        allow_back=True,
    )


# --------------------------------------------------------------------------- #
# Step machine (issue #59): steps as data, with back-navigation
# --------------------------------------------------------------------------- #


def _step_architecture(prompter: Prompter, answers: dict[str, Any], allow_back: bool) -> Any:
    return _ask_architecture(prompter, default=answers.get("architecture", "basic"), allow_back=allow_back)


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
    return _ask_edge_role(prompter, answers["architecture"], default=raw_default, allow_back=allow_back)


def _step_network_split(prompter: Prompter, answers: dict[str, Any], allow_back: bool) -> Any:
    return _ask_network_split(prompter, answers["architecture"], default=answers.get("network_split"), allow_back=allow_back)


def _step_redundancy(prompter: Prompter, answers: dict[str, Any], allow_back: bool) -> Any:
    prior = answers.get("redundancy", _UNSET)
    default = False if prior is _UNSET else (prior is not None)
    return _ask_redundancy(prompter, answers["architecture"], default=default, allow_back=allow_back)


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


def _step_services(prompter: Prompter, answers: dict[str, Any], allow_back: bool) -> Any:
    """Loop offering "Add a service?" over the built architecture config.

    Builds the resolved base config from the answers so far (the same one the
    summary would), then loops: confirm "Add a service?" -> run the shared
    add-flow (pick / placement / per-service follow-ups) -> repeat. The recorded
    answer is the ``(config, options)`` pair the summary consumes directly, so
    flat instances and attachments the user composed here flow straight into the
    build. Returns :data:`BACK` when the user backs out of the *first* "Add a
    service?" before adding anything (nothing recorded yet); once a service is
    added the loop only offers stop, never back, so the additions are not lost
    silently.
    """
    from ignition_stack import wizard_composer

    config, options, advisory_lines = _build_base_config(answers["_name"], prompter, answers)
    if config is None:
        # Red-tier acknowledgement declined: record the (None-config) so the
        # summary surfaces the advisory as a cancel; carry the lines through.
        return (None, options, advisory_lines)

    added = False
    while True:
        want = prompter.confirm("Add a service?", default=False, allow_back=allow_back and not added)
        if want is BACK:
            return BACK
        if not want:
            return (config, options)
        config = wizard_composer.dispatch_action("add", prompter, config)
        added = True


#: The wizard as an ordered, introspectable list of steps. ``applies`` reads the
#: chosen architecture so count/split/redundancy steps appear only where they
#: mean something (and are skipped in both walk directions otherwise). The
#: summary is reached when the cursor runs past the end of this list. The
#: plan and step counter (#60) render ``label`` for the applicable subset.
WIZARD_STEPS: list[Step] = [
    Step("architecture", "Architecture", lambda a: True, _step_architecture),
    Step("spokes", "Spoke count", lambda a: a.get("architecture") == "hub-and-spoke", _step_spokes),
    Step("frontends", "Frontend count", lambda a: a.get("architecture") == "scale-out", _step_frontends),
    Step("database", "Database", lambda a: True, _step_database),
    Step("edge_role", "Edge edition", lambda a: True, _step_edge_role),
    Step("network_split", "Network split", lambda a: a.get("architecture") in _MULTI_GATEWAY_ARCHITECTURES, _step_network_split),
    Step("redundancy", "Redundancy", lambda a: a.get("architecture") in _REDUNDANCY_ROLE, _step_redundancy),
    Step("iiot", "IIoT", lambda a: True, _step_iiot),
    Step("modules", "Modules", lambda a: True, _step_modules),
    Step("exposure", "Exposure", lambda a: True, _step_exposure),
    Step("services", "Services", lambda a: True, _step_services),
]


def applicable_steps(answers: Mapping[str, Any]) -> list[Step]:
    """The wizard steps that apply for the given ``answers``.

    Introspection seam for the plan + counter: the position of the current step
    in this list, and its length, give "step N of M".
    """
    return [step for step in WIZARD_STEPS if step.applies(answers)]


def _step_counter(answers: Mapping[str, Any], step_name: str) -> str:
    """The ``"[N/M] "`` prompt prefix for a step, or ``""`` for architecture.

    ``M`` is the count of applicable steps plus one for the closing Review
    screen, so the user can see the finish line. ``N`` is the step's 1-based
    position in the applicable list. The architecture step returns ``""``: its
    answer is what *decides* which steps apply, so before it is answered the
    total is unknown - and a counter that jumped (e.g. 7→8) the moment the first
    question was answered is exactly the confusion this replaces.
    """
    if step_name == "architecture":
        return ""
    names = [step.name for step in applicable_steps(answers)]
    if step_name not in names:
        return ""
    total = len(names) + 1  # +1 for the Review summary screen
    return f"[{names.index(step_name) + 1}/{total}] "


def _print_plan(answers: Mapping[str, Any]) -> None:
    """Print the numbered step plan once, after the architecture is chosen.

    Lists every step that applies to the chosen architecture, then the closing
    Review screen, so the user sees the whole road ahead and where it ends. It
    scrolls with the rest of the Q&A - a one-time sign-post, not a pinned header
    - and is reprinted if the architecture changes under back-navigation (which
    changes the applicable set). A no-op renderable cost on non-TTY output.
    """
    app = applicable_steps(answers)
    total = len(app) + 1
    body = Text()
    body.append("\nPlan ", style="bold")
    body.append(f"· {total} steps\n", style="dim")
    for n, step in enumerate(app, start=1):
        # Only the architecture (the step just answered when this prints) is done.
        done = step.name == "architecture"
        body.append("  ✓ " if done else "    ", style="green")
        body.append(f"{n}. {step.label}\n", style="dim" if done else None)
    body.append(f"    {total}. Review\n", style="dim")
    console.print(body)


# --------------------------------------------------------------------------- #
# Concrete prompter backed by Questionary
# --------------------------------------------------------------------------- #


# Title shown on the appended Back choice. Advertising the 'b' shortcut here is
# the only place the keybinding is surfaced to the user, so keep them in sync.
_BACK_TITLE = "← Back  (b)"


def _set_progress(prompter: Prompter, prefix: str) -> None:
    """Tell the prompter the current step's ``"[N/M] "`` prefix, if it supports it.

    Only :class:`QuestionaryPrompter` renders the counter; scripted test
    prompters ignore prompt messages entirely, so this is routed through
    ``getattr`` and silently skipped when the method is absent. Keeping
    ``set_progress`` off the :class:`Prompter` protocol means the test doubles
    need not implement it.
    """
    setter = getattr(prompter, "set_progress", None)
    if setter is not None:
        setter(prefix)


def _bind_back_key(question: Any) -> None:
    """Bind the ``b`` key to Back on a questionary ``select`` Question.

    questionary builds its own prompt_toolkit ``Application`` with a private
    ``KeyBindings``; we reach in and register an eager ``b`` handler that exits
    the prompt with the :data:`BACK` sentinel - the same result as moving the
    cursor to the appended "← Back" row and pressing enter. Registering before
    ``.unsafe_ask()`` runs is safe: the key processor is built lazily at run
    time. The handler is added only when a Back row is present, so ``b`` is
    inert on prompts that have no back affordance. ``b`` is otherwise unbound on
    these selects (search-filtering is off), so nothing is shadowed.
    """
    try:
        bindings = question.application.key_bindings
    except AttributeError:  # pragma: no cover - defensive, shape is stable
        return

    @bindings.add("b", eager=True)
    def _(event: Any) -> None:
        event.app.exit(result=BACK)


class QuestionaryPrompter:
    """Real-CLI prompter that delegates to ``questionary``.

    Each method translates the Prompter contract into the equivalent
    Questionary call. Imported lazily so unit tests don't require a TTY.

    Holds the wizard's current ``"[N/M] "`` progress prefix (set by the walk via
    :func:`_set_progress`) and folds it into every prompt message, so the
    counter rides along with the question instead of printing as a separate line
    that would stack up in the scrollback.
    """

    def __init__(self) -> None:
        self._progress = ""

    def set_progress(self, prefix: str) -> None:
        """Record the prefix prepended to the next prompt(s); ``""`` clears it."""
        self._progress = prefix

    def _msg(self, message: str) -> str:
        """Prepend the current progress prefix to a prompt message."""
        return f"{self._progress}{message}" if self._progress else message

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
            # The title advertises the 'b' shortcut bound below.
            q_choices.append(questionary.Choice(title=_BACK_TITLE, value=BACK))
        # Questionary matches `default` against choice values, not titles, so
        # pass the slug straight through (or None when it isn't a real choice).
        default_value = default if any(value == default for value, _ in choices) else None
        # instruction=" " (single space) is truthy so questionary uses it
        # verbatim instead of falling back to "(Use arrow keys)". A space
        # renders as nothing visible. Arrow keys, j/k, and emacs keys remain
        # active via their defaults (use_jk_keys=True, use_emacs_keys=True).
        # use_search_filter is intentionally left False: questionary raises
        # ValueError when use_jk_keys and use_search_filter are both True.
        question = questionary.select(self._msg(message), choices=q_choices, default=default_value, instruction=" ")
        if allow_back:
            _bind_back_key(question)
        answer = question.unsafe_ask()
        if answer is BACK:
            return BACK
        return str(answer)

    def text(self, message: str, default: str = "") -> str:
        import questionary

        answer = questionary.text(self._msg(message), default=default).unsafe_ask()
        return str(answer)

    def confirm(self, message: str, default: bool = False, allow_back: bool = False) -> Any:
        import questionary

        if allow_back:
            # Render a back-able confirm as a 3-way select so the Back affordance
            # is uniform with the other steps; the y/n choices preserve the bool
            # contract. Plain confirms (allow_back=False) keep the native y/n UX.
            question = questionary.select(
                self._msg(message),
                choices=[
                    questionary.Choice(title="Yes", value=True),
                    questionary.Choice(title="No", value=False),
                    questionary.Choice(title=_BACK_TITLE, value=BACK),
                ],
                default=bool(default),
                instruction=" ",
            )
            _bind_back_key(question)
            answer = question.unsafe_ask()
            return answer if answer is BACK else bool(answer)
        return bool(questionary.confirm(self._msg(message), default=default).unsafe_ask())

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

        answer = questionary.text(self._msg(message), default=str(default), validate=_validate).unsafe_ask()
        return int(answer)

    def checkbox(self, message: str, choices: Sequence[tuple[str, str, bool]]) -> list[str]:
        import questionary

        q_choices = [questionary.Choice(title=label, value=value, checked=checked) for value, label, checked in choices]
        # Replace the long default instruction with a single terse hint.
        # Space-to-toggle is the only non-obvious binding; enter-to-confirm
        # and arrow navigation need no explanation.
        # checkbox uses `if instruction is not None` so instruction="" also
        # works, but an explicit descriptive string is more helpful.
        answer = questionary.checkbox(self._msg(message), choices=q_choices, instruction="(space to toggle)").unsafe_ask()
        # Questionary returns None on Ctrl-C and a list otherwise; normalize.
        return [str(a) for a in (answer or [])]
