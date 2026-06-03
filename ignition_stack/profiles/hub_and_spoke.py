"""Hub-and-spoke profile: 1 central hub + N spoke gateways.

Edge gateways typically run as the spokes (lightweight, deployed close to
the data); the hub aggregates them. Per the design's tiered-advisory
decision, the spoke count drives an advisory: green ≤4, yellow 5-8, red
≥9 (refuses without ``--force``). The advisory itself lives in
:mod:`ignition_stack.profiles.advisory`; this profile wires it into the
build path so a bare ``build()`` raises ``ProfileError`` on the red tier
unless ``options.force`` is set.

Reverse-proxy / database / services flow through the standard
``ProfileOptions`` contract; a hub-and-spoke stack is just a multi-gateway
arrangement with a specific role layout and the advisory gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from ignition_stack.config import DatabaseConfig, GatewayConfig, ProjectConfig
from ignition_stack.profiles.advisory import Advisory, spoke_advisory
from ignition_stack.profiles.base import Profile, ProfileOptions, register


class ProfileError(Exception):
    """Raised when a profile's preconditions are not met.

    The CLI catches this and exits non-zero with the message. Carries the
    advisory (for hub-and-spoke red-tier) so callers can surface tier +
    counts alongside the message.
    """

    def __init__(self, message: str, advisory: Advisory | None = None) -> None:
        super().__init__(message)
        self.advisory = advisory


@dataclass(frozen=True)
class HubAndSpokeProfile:
    slug: str = "hub-and-spoke"
    summary: str = "Central hub gateway + N Edge spoke gateways. Spoke count > 8 needs --force."

    def build(self, name: str, options: ProfileOptions) -> ProjectConfig:
        advisory = spoke_advisory(options.spokes)
        if advisory.tier == "red" and not options.force:
            raise ProfileError(advisory.message, advisory=advisory)

        # Hub is a standard gateway; spokes default to Edge unless the user
        # explicitly opted them out via edge_role.
        spokes_run_edge = options.edge_role != "hub" and options.edge_role != "none"
        gateways: list[GatewayConfig] = [
            GatewayConfig(
                name="hub",
                role="hub",
                ignition_edition="standard",
                http_port=9088,
            )
        ]
        # Spoke ports start at 9089 and step up - one host port per spoke so
        # the SE can hit any of them directly from the laptop.
        for i in range(1, options.spokes + 1):
            gateways.append(
                GatewayConfig(
                    name=f"spoke-{i}",
                    role="spoke",
                    ignition_edition="edge" if spokes_run_edge else "standard",
                    http_port=9088 + i,
                )
            )

        # Hub-and-spoke runs on a single shared network by default; callers
        # (wizard / CLI) can opt into the frontend/backend split.
        network_split = options.network_split if options.network_split is not None else False

        return ProjectConfig(
            name=name,
            profile=self.slug,
            network_split=network_split,
            gateways=gateways,
            database=_database(options),
            services=list(options.services),
            reverse_proxy=options.reverse_proxy,
        )


def _database(options: ProfileOptions) -> DatabaseConfig | None:
    if options.database_kind is None:
        return None
    return DatabaseConfig(kind=options.database_kind)


profile: Profile = register(HubAndSpokeProfile())
