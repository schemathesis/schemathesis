import pathlib
from typing import Iterable
from urllib.parse import urlparse

import click

from . import runner

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

DEFAULT_CHECKS_NAMES = tuple(check.__name__ for check in runner.DEFAULT_CHECKS)


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option()
def main() -> None:
    """Command line tool for testing your web application built with Open API / Swagger specifications."""


@main.command(short_help="Perform schemathesis test.")
@click.argument("schema", type=str)
@click.option("--url", help="URL address of the API, required for SCHEMA if specified by file.", type=str)
@click.option(
    "--checks",
    "-c",
    multiple=True,
    help="List of checks to run.",
    type=click.Choice(DEFAULT_CHECKS_NAMES),
    default=DEFAULT_CHECKS_NAMES,
)
def run(schema: str, url: str = "", checks: Iterable[str] = DEFAULT_CHECKS_NAMES) -> None:
    """Perform schemathesis test against an API specified by SCHEMA.

    SCHEMA must be a valid URL or file path pointing to an Open API / Swagger specification.
    """
    is_file_path = False

    if not urlparse(schema).netloc:
        is_file_path = pathlib.Path(schema).is_file()

        if not is_file_path:
            raise click.UsageError("Invalid SCHEMA, must be a valid URL or file path.")
        if not url:
            raise click.UsageError('Missing argument, "--url" is required for SCHEMA specified by file.')

    selected_checks = tuple(check for check in runner.DEFAULT_CHECKS if check.__name__ in checks)

    click.echo("Running schemathesis test cases ...")

    if is_file_path:
        runner.execute_from_path(schema, base_url=url, checks=selected_checks)
    else:
        runner.execute(schema, base_url=url, checks=selected_checks)

    click.echo("Done.")
