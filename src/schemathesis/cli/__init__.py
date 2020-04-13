import traceback
from enum import Enum
from typing import Callable, Dict, Generator, Iterable, List, Optional, Tuple, Union, cast

import click
import hypothesis
import requests

from .. import checks as checks_module
from .. import models, runner
from ..runner import events
from ..types import Filter
from ..utils import WSGIResponse
from . import callbacks, output
from .context import ExecutionContext
from .options import CSVOption, NotSet, OptionalInt

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

DEFAULT_CHECKS_NAMES = tuple(check.__name__ for check in checks_module.DEFAULT_CHECKS)
ALL_CHECKS_NAMES = tuple(check.__name__ for check in checks_module.ALL_CHECKS)
CHECKS_TYPE = click.Choice((*ALL_CHECKS_NAMES, "all"))
DEFAULT_WORKERS = 1
MAX_WORKERS = 64


def register_check(function: Callable[[Union[requests.Response, WSGIResponse], models.Case], None]) -> None:
    """Register a new check for schemathesis CLI."""
    checks_module.ALL_CHECKS += (function,)
    CHECKS_TYPE.choices += (function.__name__,)  # type: ignore


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option("--pre-run", help="A module to execute before the running the tests.", type=str)
@click.version_option()
def schemathesis(pre_run: Optional[str] = None) -> None:
    """Command line tool for testing your web application built with Open API / Swagger specifications."""
    if pre_run:
        load_hook(pre_run)


@schemathesis.command(short_help="Perform schemathesis test.")
@click.argument("schema", type=str, callback=callbacks.validate_schema)
@click.option(
    "--checks", "-c", multiple=True, help="List of checks to run.", type=CHECKS_TYPE, default=DEFAULT_CHECKS_NAMES
)
@click.option(
    "-x", "--exitfirst", "exit_first", is_flag=True, default=False, help="Exit instantly on first error or failed test."
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
    callback=callbacks.validate_regex,
)
@click.option(
    "--method",
    "-M",
    "methods",
    type=str,
    multiple=True,
    help="Filter schemathesis test by HTTP method.",
    callback=callbacks.validate_regex,
)
@click.option(
    "--tag",
    "-T",
    "tags",
    type=str,
    multiple=True,
    help="Filter schemathesis test by schema tag pattern.",
    callback=callbacks.validate_regex,
)
@click.option(
    "--workers",
    "-w",
    "workers_num",
    help="Number of workers to run tests.",
    type=click.IntRange(1, MAX_WORKERS),
    default=DEFAULT_WORKERS,
)
@click.option(
    "--base-url",
    "-b",
    help="Base URL address of the API, required for SCHEMA if specified by file.",
    type=str,
    callback=callbacks.validate_base_url,
)
@click.option("--app", help="WSGI application to test.", type=str, callback=callbacks.validate_app)
@click.option(
    "--request-timeout",
    help="Timeout in milliseconds for network requests during the test run.",
    type=click.IntRange(1),
)
@click.option("--validate-schema", help="Enable or disable validation of input schema.", type=bool, default=True)
@click.option("--show-errors-tracebacks", help="Show full tracebacks for internal errors.", is_flag=True, default=False)
@click.option(
    "--hypothesis-deadline",
    help="Duration in milliseconds that each individual example with a test is not allowed to exceed.",
    # max value to avoid overflow. It is maximum amount of days in milliseconds
    type=OptionalInt(1, 999999999 * 24 * 3600 * 1000),
)
@click.option("--hypothesis-derandomize", help="Use Hypothesis's deterministic mode.", is_flag=True, default=None)
@click.option(
    "--hypothesis-max-examples",
    help="Maximum number of generated examples per each method/endpoint combination.",
    type=click.IntRange(1),
)
@click.option("--hypothesis-phases", help="Control which phases should be run.", type=CSVOption(hypothesis.Phase))
@click.option(
    "--hypothesis-report-multiple-bugs", help="Raise only the exception with the smallest minimal example.", type=bool
)
@click.option("--hypothesis-seed", help="Set a seed to use for all Hypothesis tests.", type=int)
@click.option(
    "--hypothesis-suppress-health-check",
    help="Comma-separated list of health checks to disable.",
    type=CSVOption(hypothesis.HealthCheck),
)
@click.option(
    "--hypothesis-verbosity",
    help="Verbosity level of Hypothesis messages.",
    type=click.Choice([item.name for item in hypothesis.Verbosity]),
    callback=callbacks.convert_verbosity,
)
@click.option(
    "--stateful", help="Create tests chaining endpoint's dependencies.", type=bool, default=False, is_flag=True,
)
def run(  # pylint: disable=too-many-arguments
    schema: str,
    auth: Optional[Tuple[str, str]],
    auth_type: str,
    headers: Dict[str, str],
    checks: Iterable[str] = DEFAULT_CHECKS_NAMES,
    exit_first: bool = False,
    endpoints: Optional[Filter] = None,
    methods: Optional[Filter] = None,
    tags: Optional[Filter] = None,
    workers_num: int = DEFAULT_WORKERS,
    base_url: Optional[str] = None,
    app: Optional[str] = None,
    request_timeout: Optional[int] = None,
    validate_schema: bool = True,
    show_errors_tracebacks: bool = False,
    hypothesis_deadline: Optional[Union[int, NotSet]] = None,
    hypothesis_derandomize: Optional[bool] = None,
    hypothesis_max_examples: Optional[int] = None,
    hypothesis_phases: Optional[List[hypothesis.Phase]] = None,
    hypothesis_report_multiple_bugs: Optional[bool] = None,
    hypothesis_suppress_health_check: Optional[List[hypothesis.HealthCheck]] = None,
    hypothesis_seed: Optional[int] = None,
    hypothesis_verbosity: Optional[hypothesis.Verbosity] = None,
    stateful: bool = False,
) -> None:
    """Perform schemathesis test against an API specified by SCHEMA.

    SCHEMA must be a valid URL or file path pointing to an Open API / Swagger specification.
    """
    # pylint: disable=too-many-locals

    if "all" in checks:
        selected_checks = checks_module.ALL_CHECKS
    else:
        selected_checks = tuple(check for check in checks_module.ALL_CHECKS if check.__name__ in checks)

    prepared_runner = runner.prepare(
        schema,
        auth=auth,
        auth_type=auth_type,
        headers=headers,
        request_timeout=request_timeout,
        base_url=base_url,
        endpoint=endpoints,
        method=methods,
        tag=tags,
        app=app,
        seed=hypothesis_seed,
        exit_first=exit_first,
        checks=selected_checks,
        workers_num=workers_num,
        validate_schema=validate_schema,
        stateful=stateful,
        hypothesis_deadline=hypothesis_deadline,
        hypothesis_derandomize=hypothesis_derandomize,
        hypothesis_max_examples=hypothesis_max_examples,
        hypothesis_phases=hypothesis_phases,
        hypothesis_report_multiple_bugs=hypothesis_report_multiple_bugs,
        hypothesis_suppress_health_check=hypothesis_suppress_health_check,
        hypothesis_verbosity=hypothesis_verbosity,
    )
    execute(prepared_runner, workers_num, show_errors_tracebacks)


def get_output_handler(workers_num: int) -> Callable[[ExecutionContext, events.ExecutionEvent], None]:
    if workers_num > 1:
        output_style = OutputStyle.short
    else:
        output_style = OutputStyle.default
    return cast(Callable[[ExecutionContext, events.ExecutionEvent], None], output_style)


def load_hook(module_name: str) -> None:
    """Load the given hook by importing it."""
    try:
        __import__(module_name)
    except Exception:
        click.secho("An exception happened during the hook loading:\n", fg="red")
        message = traceback.format_exc()
        click.secho(message, fg="red")
        raise click.Abort()


class OutputStyle(Enum):
    """Provide different output styles."""

    default = output.default.handle_event
    short = output.short.handle_event


def execute(
    prepared_runner: Generator[events.ExecutionEvent, None, None], workers_num: int, show_errors_tracebacks: bool
) -> None:
    """Execute a prepared runner by drawing events from it and passing to a proper handler."""
    handler = get_output_handler(workers_num)
    context = ExecutionContext(workers_num=workers_num, show_errors_tracebacks=show_errors_tracebacks)
    for event in prepared_runner:
        handler(context, event)
