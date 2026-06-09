"""Scaleout profile: N frontend gateways + 1 backend, networked together.

Two roles: ``frontend`` (user-facing UI / OPC-UA aggregation) and
``backend`` (the database-connected workhorse). Every gateway runs the
standard edition by default - the all-standard shape is the common case;
opt a role into Edge with ``edge_role``. With one frontend the gateway is
named ``frontend``; with N>1 they are ``frontend-1``..``frontend-N``. Ports
step up from 9088, the backend taking the next free port. Every gateway
joins the frontend AND backend networks so a frontend can reach the DB the
backend owns. The network split is on by default - that's the whole point
of the scaleout demo - but ``options.network_split`` can override it.

The gateway-network link auto-forms with no UI approval: each frontend gets
``gan_outgoing=["backend"]``, which the compose engine renders as a plain
(non-SSL, port 8088) outgoing Gateway Network connection, and every participant
runs an ``Unrestricted`` incoming policy so the link is accepted on sight - the
same proven pattern the redundancy link rides. POST-SETUP carries a *verify*
step (not a manual procedure). Today the ``services`` list is empty by default;
users add brokers/IDPs on top.
"""

from __future__ import annotations

from dataclasses import dataclass

from ignition_stack.config import DatabaseConfig, GatewayConfig, ProjectConfig
from ignition_stack.profiles.base import Profile, ProfileOptions, register


@dataclass(frozen=True)
class ScaleoutProfile:
    slug: str = "scaleout"
    summary: str = "Frontend + backend Ignition gateways via gateway network + Postgres."

    def build(self, name: str, options: ProfileOptions) -> ProjectConfig:
        # All gateways run standard unless the caller opts a role into Edge.
        edge_role = options.edge_role if options.edge_role is not None else "none"
        frontends = max(options.frontends, 1)

        gateways: list[GatewayConfig] = []
        # Frontend ports start at 9088 and step up; one host port each so the
        # SE can hit any frontend directly from the laptop. A single frontend
        # keeps the bare ``frontend`` name for golden stability.
        for i in range(1, frontends + 1):
            gateways.append(
                GatewayConfig(
                    name="frontend" if frontends == 1 else f"frontend-{i}",
                    role="frontend",
                    ignition_edition="edge" if edge_role == "frontend" else "standard",
                    http_port=9088 + (i - 1),
                    # Each frontend opens a plain Gateway Network link to the
                    # backend so the GAN auto-forms with no UI approval.
                    gan_outgoing=["backend"],
                )
            )
        gateways.append(
            GatewayConfig(
                name="backend",
                role="backend",
                ignition_edition="edge" if edge_role == "backend" else "standard",
                http_port=9088 + frontends,
            )
        )

        network_split = options.network_split if options.network_split is not None else True

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


profile: Profile = register(ScaleoutProfile())
