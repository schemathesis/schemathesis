from typing import Dict, Iterable, Optional, Tuple

import click

from .. import runner
from ..types import Filter
from ..utils import dict_true_values
from . import validators

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

DEFAULT_CHECKS_NAMES = tuple(check.__name__ for check in runner.DEFAULT_CHECKS)


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option()
def main() -> None:
    """Command line tool for testing your web application built with Open API / Swagger specifications."""


@main.command(short_help="Perform schemathesis test.")
@click.argument("schema", type=str, callback=validators.validate_schema)  # type: ignore
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
    callback=validators.validate_auth,  # type: ignore
)
@click.option(  # type: ignore
    "--header",
    "-H",
    "headers",
    help=r"Custom header in a that will be used in all requests to the server. Example: Authorization: Bearer\ 123",
    multiple=True,
    type=str,
    callback=validators.validate_headers,  # type: ignore
)
@click.option(
    "--endpoint",
    "-E",
    "endpoints",
    type=str,
    multiple=True,
    help=r"Filter schemathesis test by endpoint pattern. Example: users/\d+",
)
@click.option("--method", "-M", "methods", type=str, multiple=True, help="Filter schemathesis test by HTTP method.")
def run(  # pylint: disable=too-many-arguments
    schema: str,
    auth: Optional[Tuple[str, str]],
    headers: Dict[str, str],
    checks: Iterable[str] = DEFAULT_CHECKS_NAMES,
    endpoints: Optional[Filter] = None,
    methods: Optional[Filter] = None,
) -> None:
    """Perform schemathesis test against an API specified by SCHEMA.

    SCHEMA must be a valid URL pointing to an Open API / Swagger specification.
    """
    selected_checks = tuple(check for check in runner.DEFAULT_CHECKS if check.__name__ in checks)

    click.echo("Running schemathesis test cases ...")

    options = dict_true_values(
        api_options=dict_true_values(auth=auth, headers=headers),
        loader_options=dict_true_values(endpoint=endpoints, method=methods),
    )
    runner.execute(schema, checks=selected_checks, **options)

    click.echo("Done.")
