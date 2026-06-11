"""Host-side cache writer for catalog entries.

The cache lives at ``<project>/modules/cache/`` inside generated projects
and at any user-supplied path for the standalone ``modules download``
command. Each artifact is named by its in-container filename so the
compose-layer mount line is trivial.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx

from ignition_stack.catalog.schema import SHA256_UNPINNED, CatalogEntry
from ignition_stack.catalog.verify import sha256_of_file

DOWNLOAD_TIMEOUT_SECONDS = 60.0
DEFAULT_CACHE_DIR = Path("modules/cache")


class DownloadOutcome(StrEnum):
    DOWNLOADED = "downloaded"
    COPIED_FROM_LOCAL = "copied-from-local"
    SKIPPED_MANUAL = "skipped-manual"
    SKIPPED_CACHED = "skipped-cached"


@dataclass(frozen=True, slots=True)
class DownloadResult:
    entry_name: str
    outcome: DownloadOutcome
    path: Path | None
    message: str


class DownloadError(Exception):
    """Raised when a network download cannot be completed or fails sha256."""


def download_entry(
    entry: CatalogEntry,
    cache_dir: Path,
    *,
    client: httpx.Client,
    offline: bool = False,
) -> DownloadResult:
    """Materialise ``entry`` into ``cache_dir``.

    Behaviour matrix:
      - manual + local_source_path exists  -> copy from local
      - manual + local_source_path missing -> warn-and-skip (config drift)
      - manual + no local_source_path      -> skip with explanation
      - already cached + sha matches       -> skip (idempotent)
      - offline                            -> error if not already cached
      - normal                             -> http download + sha verify
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / entry.cache_filename()

    if entry.requires_manual_download:
        return _handle_manual(entry, target)

    if target.exists() and entry.sha256 != SHA256_UNPINNED and sha256_of_file(target) == entry.sha256:
        return DownloadResult(
            entry.name,
            DownloadOutcome.SKIPPED_CACHED,
            target,
            f"already cached at {target}",
        )

    if offline:
        raise DownloadError(
            f"{entry.name}: --offline set but artifact not in cache " f"({target}). Pre-populate the cache or drop --offline.",
        )

    if entry.download_url is None:
        raise DownloadError(
            f"{entry.name}: no download_url and not marked manual-download.",
        )

    _http_download(str(entry.download_url), target, client=client)

    if entry.sha256 != SHA256_UNPINNED:
        actual = sha256_of_file(target)
        if actual != entry.sha256:
            target.unlink(missing_ok=True)
            raise DownloadError(
                f"{entry.name}: sha256 mismatch after download " f"(expected {entry.sha256}, got {actual}). Cached file removed.",
            )

    return DownloadResult(
        entry.name,
        DownloadOutcome.DOWNLOADED,
        target,
        f"downloaded {entry.download_url} -> {target}",
    )


def _handle_manual(entry: CatalogEntry, target: Path) -> DownloadResult:
    if entry.local_source_path is None:
        return DownloadResult(
            entry.name,
            DownloadOutcome.SKIPPED_MANUAL,
            None,
            f"{entry.name} requires manual download (see POST-SETUP.md).",
        )

    source = Path(entry.local_source_path)
    if not source.is_file():
        return DownloadResult(
            entry.name,
            DownloadOutcome.SKIPPED_MANUAL,
            None,
            (f"WARN: {entry.name} local_source_path missing ({source}). " "Skipping: requires manual download. " "See POST-SETUP.md for instructions."),
        )

    shutil.copy2(source, target)
    return DownloadResult(
        entry.name,
        DownloadOutcome.COPIED_FROM_LOCAL,
        target,
        f"copied local source {source} -> {target}",
    )


def _http_download(url: str, target: Path, *, client: httpx.Client) -> None:
    with client.stream("GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_SECONDS) as r:
        r.raise_for_status()
        with target.open("wb") as fp:
            for chunk in r.iter_bytes(chunk_size=1024 * 1024):
                fp.write(chunk)
