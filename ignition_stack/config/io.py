"""Serialize and load a resolved :class:`ProjectConfig` as YAML or JSON.

The resolved config is the CLI's full build input - project name, gateways,
networks, database, services, env. ``ignition-stack init --dry-run`` dumps it;
``ignition-stack init -f <file>`` loads it back and builds from it. The same
artifact is what the lifecycle record (:mod:`ignition_stack.lifecycle.record`)
persists, so dump/edit/rebuild and reset/switch-profile share one schema.

JSON dumps reuse pydantic's ``model_dump_json`` so the on-disk lifecycle record
stays byte-identical to what it was before this module existed. YAML dumps go
through ruamel (already the compose engine's YAML library) in safe mode, which
emits block-style, alias-free output suitable for hand-editing by a person or
emission by an external architecture tool.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Literal

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from ignition_stack.config.schema import ProjectConfig

Format = Literal["yaml", "json"]


class ConfigIOError(Exception):
    """Raised when a config file can't be parsed or fails schema validation.

    Carries a human-readable message (no traceback) so the CLI can surface a
    clean non-zero exit for a malformed ``-f`` file.
    """


def _yaml() -> YAML:
    """A ruamel YAML configured for deterministic, wrap-free block output.

    The round-trip type (the default) preserves mapping insertion order, so the
    dump follows the schema's field declaration order (``name`` first) instead
    of the safe dumper's alphabetical sort - friendlier for a hand-edited file.
    """
    yaml = YAML()
    yaml.default_flow_style = False
    # Don't let ruamel hard-wrap long scalars (image tags, descriptions); a wrap
    # would change the bytes for no readability gain and complicate round-trips.
    yaml.width = 4096
    return yaml


def dump_config(config: ProjectConfig, fmt: Format = "yaml") -> str:
    """Serialize ``config`` to a string in ``fmt`` ('yaml' or 'json').

    The JSON form matches the lifecycle record exactly (2-space indent, trailing
    newline). The YAML form is the hand-authorable artifact: block style, keys in
    schema declaration order, no anchors.
    """
    if fmt == "json":
        return config.model_dump_json(indent=2) + "\n"
    if fmt == "yaml":
        data = config.model_dump(mode="json")
        stream = io.StringIO()
        _yaml().dump(data, stream)
        return stream.getvalue()
    raise ValueError(f"unsupported config format '{fmt}'; expected 'yaml' or 'json'")


def load_config(path: Path) -> ProjectConfig:
    """Load and validate a :class:`ProjectConfig` from ``path``.

    Format is chosen by suffix (``.json`` -> JSON, otherwise YAML). Parse errors
    and schema-validation failures are remapped to :class:`ConfigIOError` with a
    readable message, so a bad file never surfaces as a raw traceback.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigIOError(f"cannot read config file '{path}': {exc}") from exc

    data = _parse(text, path)
    if not isinstance(data, dict):
        raise ConfigIOError(
            f"config file '{path}' must contain a mapping at the top level, "
            f"got {type(data).__name__}"
        )

    try:
        return ProjectConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigIOError(
            f"invalid config in '{path}':\n{_format_validation_error(exc)}"
        ) from exc


def _parse(text: str, path: Path) -> object:
    """Parse ``text`` as JSON (``.json`` suffix) or YAML, remapping parse errors."""
    if path.suffix == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigIOError(f"cannot parse JSON config '{path}': {exc}") from exc
    try:
        return _yaml().load(text)
    except YAMLError as exc:
        raise ConfigIOError(f"cannot parse YAML config '{path}': {exc}") from exc


def _format_validation_error(exc: ValidationError) -> str:
    """Render a pydantic ValidationError as a compact, readable bullet list.

    pydantic prefixes messages from a validator's ``ValueError`` with
    ``Value error, ``; strip it so a schema's own message (e.g. "unsupported
    database kind ...") reads cleanly instead of leaking the wrapper.
    """
    lines: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        msg = err["msg"].removeprefix("Value error, ")
        lines.append(f"  - {loc}: {msg}")
    return "\n".join(lines)
