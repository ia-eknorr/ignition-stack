"""Service catalog: per-service manifests, loader, and dependency resolver.

Phase 5 turns every supported service (databases, MQTT brokers, Keycloak,
simulators, Kafka, n8n) into a self-contained template directory under
``ignition_stack/templates/services/<name>/`` holding a ``manifest.yaml``
(metadata + capability declarations), a ``compose.yaml.j2`` fragment, and a
``seed/`` tree. This package reads those manifests and resolves the implicit
dependencies between services (Keycloak needs a SQL database; MySQL needs its
JDBC driver) into a fully-expanded :class:`ProjectConfig` before the compose
engine renders anything.
"""

from ignition_stack.services.loader import load_all_services, load_service, service_dir
from ignition_stack.services.manifest import PostSetupItem, ServiceManifest
from ignition_stack.services.resolver import ResolveError, resolve

__all__ = [
    "PostSetupItem",
    "ResolveError",
    "ServiceManifest",
    "load_all_services",
    "load_service",
    "resolve",
    "service_dir",
]
