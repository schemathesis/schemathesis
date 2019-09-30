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
@click.option(
    "--checks",
    "-c",
    multiple=True,
    help="List of checks to run.",
    type=click.Choice(DEFAULT_CHECKS_NAMES),
    default=DEFAULT_CHECKS_NAMES,
)
def run(schema: str, checks: Iterable[str] = DEFAULT_CHECKS_NAMES) -> None:
    """Perform schemathesis test against an API specified by SCHEMA.

    SCHEMA must be a valid URL pointing to an Open API / Swagger specification.
    """
    if not urlparse(schema).netloc:
        raise click.UsageError("Invalid SCHEMA, must be a valid URL.")

    selected_checks = tuple(check for check in runner.DEFAULT_CHECKS if check.__name__ in checks)

    click.echo("Running schemathesis test cases ...")

    runner.execute(schema, checks=selected_checks)

    click.echo("Done.")
