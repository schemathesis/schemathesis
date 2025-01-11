import click

from schemathesis.core.fs import ensure_parent


def open_file(file: click.utils.LazyFile) -> None:
    try:
        ensure_parent(file.name, fail_silently=False)
    except OSError as exc:
        raise click.BadParameter(f"'{file.name}': {exc.strerror}") from exc
    try:
        file.open()
    except click.FileError as exc:
        raise click.BadParameter(exc.format_message()) from exc
