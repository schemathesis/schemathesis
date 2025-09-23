from pathlib import Path

import click

from schemathesis.core.fs import ensure_parent


def open_file(file: Path) -> None:
    try:
        ensure_parent(file, fail_silently=False)
    except OSError as exc:
        raise click.BadParameter(f"'{file.name}': {exc.strerror}") from exc
    try:
        file.open("w", encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise click.BadParameter(f"Could not open file {file.name}: {exc}") from exc
