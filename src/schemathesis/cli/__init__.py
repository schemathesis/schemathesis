from contextlib import contextmanager
from typing import Dict, Generator, Iterable, List, Optional, Tuple

import click
import hypothesis
from requests.auth import HTTPDigestAuth
from requests.exceptions import HTTPError

from .. import runner, utils
from ..runner import events
from ..types import Filter
from ..utils import dict_not_none_values, dict_true_values
from . import callbacks, output
from .options import CSVOption

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

DEFAULT_CHECKS_NAMES = tuple(check.__name__ for check in runner.DEFAULT_CHECKS)


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option()
def main() -> None:
    """Command line tool for testing your web application built with Open API / Swagger specifications."""


@main.command(short_help="Perform schemathesis test.")
@click.argument("schema", type=str, callback=callbacks.validate_schema)
@click.option(
    "--checks",
    "-c",
    multiple=True,
    help="List of checks to run.",
    type=click.Choice(DEFAULT_CHECKS_NAMES),
    default=DEFAULT_CHECKS_NAMES,
)
@click.option(
    "--auth", "-a", help="Server user and password. Example: USER:PASSWORD", type=str, callback=callbacks.validate_auth
)
@click.option(
    "--auth-type",
    "-A",
    type=click.Choice(["basic", "digest"], case_sensitive=False),
    default="basic",
    help="The authentication mechanism to be used. Defaults to 'basic'.",
)
@click.option(
    "--header",
    "-H",
    "headers",
    help=r"Custom header in a that will be used in all requests to the server. Example: Authorization: Bearer\ 123",
    multiple=True,
    type=str,
    callback=callbacks.validate_headers,
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
@click.option("--tag", "-T", "tags", type=str, multiple=True, help="Filter schemathesis test by schema tag pattern.")
@click.option("--base-url", "-b", help="Base URL address of the API.", type=str)
@click.option(
    "--hypothesis-deadline",
    help="Duration in milliseconds that each individual example with a test is not allowed to exceed.",
    type=int,
)
@click.option("--hypothesis-derandomize", help="Use Hypothesis's deterministic mode.", is_flag=True, default=None)
@click.option(
    "--hypothesis-max-examples",
    help="Maximum number of generated examples per each method/endpoint combination.",
    type=int,
)
@click.option("--hypothesis-phases", help="Control which phases should be run.", type=CSVOption(hypothesis.Phase))
@click.option(
    "--hypothesis-report-multiple-bugs", help="Raise only the exception with the smallest minimal example.", type=bool
)
@click.option(
    "--hypothesis-suppress-health-check",
    help="Comma-separated list of health checks to disable.",
    type=CSVOption(hypothesis.HealthCheck),
)
@click.option(
    "--hypothesis-verbosity",
    help="Verbosity level of Hypothesis messages",
    type=click.Choice([item.name for item in hypothesis.Verbosity]),
    callback=callbacks.convert_verbosity,
)
def run(  # pylint: disable=too-many-arguments
    schema: str,
    auth: Optional[Tuple[str, str]],
    auth_type: str,
    headers: Dict[str, str],
    checks: Iterable[str] = DEFAULT_CHECKS_NAMES,
    endpoints: Optional[Filter] = None,
    methods: Optional[Filter] = None,
    tags: Optional[Filter] = None,
    base_url: Optional[str] = None,
    hypothesis_deadline: Optional[int] = None,
    hypothesis_derandomize: Optional[bool] = None,
    hypothesis_max_examples: Optional[int] = None,
    hypothesis_phases: Optional[List[hypothesis.Phase]] = None,
    hypothesis_report_multiple_bugs: Optional[bool] = None,
    hypothesis_suppress_health_check: Optional[List[hypothesis.HealthCheck]] = None,
    hypothesis_verbosity: Optional[hypothesis.Verbosity] = None,
) -> None:
    """Perform schemathesis test against an API specified by SCHEMA.

    SCHEMA must be a valid URL pointing to an Open API / Swagger specification.
    """
    # pylint: disable=too-many-locals
    selected_checks = tuple(check for check in runner.DEFAULT_CHECKS if check.__name__ in checks)

    if auth and auth_type == "digest":
        auth = HTTPDigestAuth(*auth)  # type: ignore

    options = dict_true_values(
        api_options=dict_true_values(base_url=base_url, auth=auth, headers=headers),
        loader_options=dict_true_values(endpoint=endpoints, method=methods, tag=tags),
        hypothesis_options=dict_not_none_values(
            deadline=hypothesis_deadline,
            derandomize=hypothesis_derandomize,
            max_examples=hypothesis_max_examples,
            phases=hypothesis_phases,
            report_multiple_bugs=hypothesis_report_multiple_bugs,
            suppress_health_check=hypothesis_suppress_health_check,
            verbosity=hypothesis_verbosity,
        ),
    )

    with abort_on_network_errors():
        prepared_runner = runner.prepare(schema, checks=selected_checks, **options)
    execute(prepared_runner)


@contextmanager
def abort_on_network_errors() -> Generator[None, None, None]:
    """Abort on network errors during the schema loading."""
    try:
        yield
    except HTTPError as exc:
        if exc.response.status_code == 404:
            click.secho(f"Schema was not found at {exc.request.url}", fg="red")
            raise click.Abort
        click.secho(
            f"Failed to load schema, code {exc.response.status_code} was returned via {exc.request.url}", fg="red"
        )
        raise click.Abort


def execute(prepared_runner: Generator[events.ExecutionEvent, None, None]) -> None:
    """Execute a prepared runner by drawing events from it and passing to a proper handler."""
    with utils.capture_hypothesis_output() as hypothesis_output:
        context = events.ExecutionContext(hypothesis_output)
        for event in prepared_runner:
            output.handle_event(context, event)
