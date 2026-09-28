from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

from schemathesis.core.fs import ensure_parent

if TYPE_CHECKING:
    from schemathesis.config import ProjectConfig


def open_file(file: Path) -> None:
    try:
        ensure_parent(file, fail_silently=False)
    except (OSError, ValueError) as exc:
        raise click.BadParameter(f"Could not create parent directory for {file.name!r}: {_describe(exc)}") from exc
    try:
        file.open("w", encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise click.BadParameter(f"Could not open file {file.name!r}: {_describe(exc)}") from exc


def prepare_directory(directory: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        raise click.BadParameter(f"Could not create directory {directory.name!r}: {_describe(exc)}") from exc


def load_baseline(config: ProjectConfig) -> None:
    # Opening a directory fails with "Permission denied" on Windows, which hides the real problem.
    if config.baseline is not None and Path(config.baseline).is_dir():
        raise click.BadParameter(f"Could not load baseline file {config.baseline!r}: Is a directory")
    try:
        config.load_baseline()
    except (OSError, ValueError) as exc:
        raise click.BadParameter(f"Could not load baseline file {config.baseline!r}: {_describe(exc)}") from exc


def _describe(exc: OSError | ValueError) -> str:
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    message = str(exc)
    # Python <3.14 says "embedded null byte"; 3.14+ says "<syscall>: embedded null character in path".
    if "embedded null" in message:
        return "embedded null byte"
    return message
