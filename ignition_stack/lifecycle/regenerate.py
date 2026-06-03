"""Regenerate a project in place from its recorded config.

``reset`` and ``switch-profile`` both clear the previously-generated tree and
re-run the writer. The lifecycle record (``.ignition-stack/``) and the modules
cache are preserved across the rewrite: the record because it is the input to
the regeneration, the cache because re-downloading pinned ``.modl`` files on
every reset would be wasteful and offline-hostile.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ignition_stack.compose import write_project
from ignition_stack.config.schema import ProjectConfig
from ignition_stack.lifecycle.record import LIFECYCLE_DIR

# Generated subtrees kept across a regenerate. Everything else under the project
# root is removed before the writer re-runs.
_PRESERVE = frozenset({LIFECYCLE_DIR})
_PRESERVE_NESTED = (("modules", "cache"),)


def regenerate(project_dir: Path, config: ProjectConfig) -> list[Path]:
    """Clear the generated tree (keeping primitives) and re-run the writer."""
    project_dir = Path(project_dir).resolve()
    _clear_generated(project_dir)
    return write_project(config, project_dir, overwrite=True)


def _clear_generated(project_dir: Path) -> None:
    preserved_cache = _stash_cache(project_dir)
    for entry in project_dir.iterdir():
        if entry.name in _PRESERVE:
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    _restore_cache(project_dir, preserved_cache)


def _stash_cache(project_dir: Path) -> Path | None:
    """Move modules/cache aside so it survives the rmtree, returning its temp path."""
    cache = project_dir / Path(*_PRESERVE_NESTED[0])
    if not cache.is_dir():
        return None
    stash = project_dir / LIFECYCLE_DIR / "_cache-stash"
    if stash.exists():
        shutil.rmtree(stash)
    stash.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(cache), str(stash))
    return stash


def _restore_cache(project_dir: Path, stash: Path | None) -> None:
    if stash is None:
        return
    dst = project_dir / Path(*_PRESERVE_NESTED[0])
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(stash), str(dst))
