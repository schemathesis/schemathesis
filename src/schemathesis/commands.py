from contextlib import contextmanager
from typing import Dict, Generator, Iterable, Optional, Tuple
from urllib.parse import urlparse

import click

from . import runner

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

DEFAULT_CHECKS_NAMES = tuple(check.__name__ for check in runner.DEFAULT_CHECKS)


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option()
def main() -> None:
    """Command line tool for testing your web application built with Open API / Swagger specifications."""


def validate_auth(
    ctx: click.core.Context, param: click.core.Option, raw_value: Optional[str]
) -> Optional[Tuple[str, str]]:
    if raw_value is not None:
        with reraise_format_error(raw_value):
            user, password = tuple(raw_value.split(":"))
        return user, password
    return None


def validate_headers(ctx: click.core.Context, param: click.core.Option, raw_value: Tuple[str, ...]) -> Dict[str, str]:
    headers = {}
    for header in raw_value:
        with reraise_format_error(header):
            key, value = header.split(":")
        headers[key] = value.lstrip()
    return headers


@contextmanager
def reraise_format_error(raw_value: str) -> Generator:
    try:
        yield
    except ValueError:
        raise click.BadParameter(f"Should be in KEY:VALUE format. Got: {raw_value}")


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
@click.option(  # type: ignore
    "--auth",
    "-a",
    help="Server user and password. Example: USER:PASSWORD",
    type=str,
    callback=validate_auth,  # type: ignore
)
@click.option(  # type: ignore
    "--header",
    "-H",
    "headers",
    help=r"Custom header in a that will be used in all requests to the server. Example: Authorization: Bearer\ 123",
    multiple=True,
    type=str,
    callback=validate_headers,  # type: ignore
)
def run(
    schema: str, auth: Optional[Tuple[str, str]], headers: Dict[str, str], checks: Iterable[str] = DEFAULT_CHECKS_NAMES
) -> None:
    """Perform schemathesis test against an API specified by SCHEMA.

    SCHEMA must be a valid URL pointing to an Open API / Swagger specification.
    """
    if not urlparse(schema).netloc:
        raise click.UsageError("Invalid SCHEMA, must be a valid URL.")

    selected_checks = tuple(check for check in runner.DEFAULT_CHECKS if check.__name__ in checks)

    click.echo("Running schemathesis test cases ...")

    runner.execute(schema, checks=selected_checks, auth=auth, headers=headers)

    click.echo("Done.")
