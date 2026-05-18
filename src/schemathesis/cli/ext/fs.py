from pathlib import Path

import click

from schemathesis.core.fs import ensure_parent


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


def _describe(exc: OSError | ValueError) -> str:
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    message = str(exc)
    # Python <3.14 says "embedded null byte"; 3.14+ says "<syscall>: embedded null character in path".
    if "embedded null" in message:
        return "embedded null byte"
    return message
