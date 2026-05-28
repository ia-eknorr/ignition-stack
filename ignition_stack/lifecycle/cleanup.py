"""Scoped cleanup: build the one ``docker compose`` command that removes a
project's resources and nothing else.

Docker Compose labels every container, network, and named volume it creates
with ``com.docker.compose.project=<project>``. ``down -v`` only touches
resources carrying the project's label, so naming the project explicitly
(``-p <name>``) is what makes ``wipe`` provably scoped: unrelated containers
and volumes on the same host are never matched.
"""

from __future__ import annotations

from pathlib import Path

from ignition_stack.lifecycle.record import LIFECYCLE_DIR, has_record, read_record

_ENV_PROJECT_KEY = "COMPOSE_PROJECT_NAME"


class CleanupError(Exception):
    """Raised when the project name to scope the wipe to can't be determined."""


def wipe_command(project_name: str) -> list[str]:
    """The scoped teardown for ``project_name``.

    ``-p`` pins the compose project so ``down -v`` removes only that project's
    containers, networks, and named volumes; ``--remove-orphans`` clears any of
    its containers no longer in the compose file. Nothing global (no
    ``system prune``, no unfiltered ``volume rm``) is ever issued.
    """
    return [
        "docker",
        "compose",
        "-p",
        project_name,
        "down",
        "-v",
        "--remove-orphans",
    ]


def project_name(project_dir: Path) -> str:
    """Resolve the compose project name for a generated project.

    Prefers the SE-demo record (authoritative), then ``COMPOSE_PROJECT_NAME``
    from the generated ``.env`` so one-shot projects can still be wiped.
    """
    project_dir = Path(project_dir)
    if has_record(project_dir):
        return read_record(project_dir).name

    name = _project_name_from_env(project_dir / ".env")
    if name is not None:
        return name

    raise CleanupError(
        f"could not determine the compose project name in {project_dir}: no "
        f"{LIFECYCLE_DIR} record and no {_ENV_PROJECT_KEY} in .env. Run from a "
        "generated project directory."
    )


def _project_name_from_env(env_file: Path) -> str | None:
    if not env_file.is_file():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{_ENV_PROJECT_KEY}="):
            return stripped.split("=", 1)[1].strip() or None
    return None
