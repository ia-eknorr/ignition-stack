"""Lifecycle primitives: SE-demo record, scoped cleanup, in-place regeneration.

Only the light modules (``record``, ``cleanup``) are re-exported here; both
depend on the config schema alone. ``regenerate`` pulls in the compose writer,
so import it from its submodule to keep this package free of an import cycle
(the writer imports :mod:`ignition_stack.lifecycle.record`).
"""

from ignition_stack.lifecycle.cleanup import CleanupError, project_name, wipe_command
from ignition_stack.lifecycle.record import (
    LIFECYCLE_DIR,
    RECORD_NAME,
    LifecycleError,
    has_record,
    read_record,
    record_path,
    write_record,
)

__all__ = [
    "LIFECYCLE_DIR",
    "RECORD_NAME",
    "CleanupError",
    "LifecycleError",
    "has_record",
    "project_name",
    "read_record",
    "record_path",
    "wipe_command",
    "write_record",
]
