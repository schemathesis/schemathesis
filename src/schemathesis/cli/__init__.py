import os
import sys
import traceback
from enum import Enum
from typing import Callable, Dict, Generator, Iterable, List, Optional, Tuple, Union

import click
import hypothesis
import requests
import yaml

from .. import checks as checks_module
from .. import models, runner
from ..fixups import ALL_FIXUPS
from ..hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher, HookScope
from ..runner import DEFAULT_STATEFUL_RECURSION_LIMIT, events
from ..runner.targeted import DEFAULT_TARGETS_NAMES, Target
from ..types import Filter
from ..utils import WSGIResponse
from . import callbacks, cassettes, output
from .context import ExecutionContext
from .handlers import EventHandler
from .junitxml import JunitXMLHandler
from .options import CSVOption, NotSet, OptionalInt

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    # pylint: disable=unused-import
    from yaml import SafeLoader  # type: ignore


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


def reset_checks() -> None:
    """Get checks list to their default state."""
    # Useful in tests
    checks_module.ALL_CHECKS = checks_module.DEFAULT_CHECKS + checks_module.OPTIONAL_CHECKS
    CHECKS_TYPE.choices = tuple(check.__name__ for check in checks_module.ALL_CHECKS) + ("all",)


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
    "--target",
    "-t",
    "targets",
    multiple=True,
    help="Targets for input generation.",
    type=click.Choice([target.name for target in Target]),
    default=DEFAULT_TARGETS_NAMES,
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
    "--operation-id",
    "-O",
    "operation_ids",
    type=str,
    multiple=True,
    help="Filter schemathesis test by operationId pattern.",
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
@click.option("--app", help="WSGI/ASGI application to test.", type=str, callback=callbacks.validate_app)
@click.option(
    "--request-timeout",
    help="Timeout in milliseconds for network requests during the test run.",
    type=click.IntRange(1),
)
@click.option("--validate-schema", help="Enable or disable validation of input schema.", type=bool, default=True)
@click.option("--junit-xml", help="Create junit-xml style report file at given path.", type=click.File("w"))
@click.option(
    "--show-errors-tracebacks",
    help="Show full tracebacks for internal errors.",
    is_flag=True,
    is_eager=True,
    default=False,
)
@click.option("--store-network-log", help="Store requests and responses into a file.", type=click.File("w"))
@click.option(
    "--fixups",
    help="Install specified compatibility fixups.",
    multiple=True,
    type=click.Choice(list(ALL_FIXUPS) + ["all"]),
)
@click.option("--stateful", help="Utilize stateful testing capabilities.", type=click.Choice(["links"]))
@click.option(
    "--stateful-recursion-limit",
    help="Limit recursion depth for stateful testing.",
    default=DEFAULT_STATEFUL_RECURSION_LIMIT,
    type=click.IntRange(1, 100),
)
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
@click.option("--verbosity", "-v", help="Reduce verbosity of error output.", count=True)
def run(  # pylint: disable=too-many-arguments
    schema: str,
    auth: Optional[Tuple[str, str]],
    auth_type: str,
    headers: Dict[str, str],
    checks: Iterable[str] = DEFAULT_CHECKS_NAMES,
    targets: Iterable[str] = DEFAULT_TARGETS_NAMES,
    exit_first: bool = False,
    endpoints: Optional[Filter] = None,
    methods: Optional[Filter] = None,
    tags: Optional[Filter] = None,
    operation_ids: Optional[Filter] = None,
    workers_num: int = DEFAULT_WORKERS,
    base_url: Optional[str] = None,
    app: Optional[str] = None,
    request_timeout: Optional[int] = None,
    validate_schema: bool = True,
    junit_xml: Optional[click.utils.LazyFile] = None,
    show_errors_tracebacks: bool = False,
    store_network_log: Optional[click.utils.LazyFile] = None,
    fixups: Tuple[str] = (),  # type: ignore
    stateful: Optional[str] = None,
    stateful_recursion_limit: int = DEFAULT_STATEFUL_RECURSION_LIMIT,
    hypothesis_deadline: Optional[Union[int, NotSet]] = None,
    hypothesis_derandomize: Optional[bool] = None,
    hypothesis_max_examples: Optional[int] = None,
    hypothesis_phases: Optional[List[hypothesis.Phase]] = None,
    hypothesis_report_multiple_bugs: Optional[bool] = None,
    hypothesis_suppress_health_check: Optional[List[hypothesis.HealthCheck]] = None,
    hypothesis_seed: Optional[int] = None,
    hypothesis_verbosity: Optional[hypothesis.Verbosity] = None,
    verbosity: int = 0,
) -> None:
    """Perform schemathesis test against an API specified by SCHEMA.

    SCHEMA must be a valid URL or file path pointing to an Open API / Swagger specification.
    """
    # pylint: disable=too-many-locals
    selected_targets = tuple(target for target in Target if target.name in targets)

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
        operation_id=operation_ids,
        app=app,
        seed=hypothesis_seed,
        exit_first=exit_first,
        store_interactions=store_network_log is not None,
        checks=selected_checks,
        targets=selected_targets,
        workers_num=workers_num,
        validate_schema=validate_schema,
        fixups=fixups,
        stateful=stateful,
        stateful_recursion_limit=stateful_recursion_limit,
        hypothesis_deadline=hypothesis_deadline,
        hypothesis_derandomize=hypothesis_derandomize,
        hypothesis_max_examples=hypothesis_max_examples,
        hypothesis_phases=hypothesis_phases,
        hypothesis_report_multiple_bugs=hypothesis_report_multiple_bugs,
        hypothesis_suppress_health_check=hypothesis_suppress_health_check,
        hypothesis_verbosity=hypothesis_verbosity,
    )
    execute(prepared_runner, workers_num, show_errors_tracebacks, store_network_log, junit_xml, verbosity)


def get_output_handler(workers_num: int) -> EventHandler:
    if workers_num > 1:
        output_style = OutputStyle.short
    else:
        output_style = OutputStyle.default
    return output_style.value()


def load_hook(module_name: str) -> None:
    """Load the given hook by importing it."""
    try:
        sys.path.append(os.getcwd())  # fix ModuleNotFoundError module in cwd
        __import__(module_name)
    except Exception:
        click.secho("An exception happened during the hook loading:\n", fg="red")
        message = traceback.format_exc()
        click.secho(message, fg="red")
        raise click.Abort()


class OutputStyle(Enum):
    """Provide different output styles."""

    default = output.default.DefaultOutputStyleHandler
    short = output.short.ShortOutputStyleHandler


def execute(  # pylint: disable=too-many-arguments
    prepared_runner: Generator[events.ExecutionEvent, None, None],
    workers_num: int,
    show_errors_tracebacks: bool,
    store_network_log: Optional[click.utils.LazyFile],
    junit_xml: Optional[click.utils.LazyFile],
    verbosity: int,
) -> None:
    """Execute a prepared runner by drawing events from it and passing to a proper handler."""
    handlers: List[EventHandler] = []
    if junit_xml is not None:
        handlers.append(JunitXMLHandler(junit_xml))
    if store_network_log is not None:
        # This handler should be first to have logs writing completed when the output handler will display statistic
        handlers.append(cassettes.CassetteWriter(store_network_log))
    handlers.append(get_output_handler(workers_num))
    execution_context = ExecutionContext(
        workers_num=workers_num,
        show_errors_tracebacks=show_errors_tracebacks,
        cassette_file_name=store_network_log.name if store_network_log is not None else None,
        junit_xml_file=junit_xml.name if junit_xml is not None else None,
        verbosity=verbosity,
    )
    GLOBAL_HOOK_DISPATCHER.dispatch("after_init_cli_run_handlers", HookContext(), handlers, execution_context)
    try:
        for event in prepared_runner:
            for handler in handlers:
                handler.handle_event(execution_context, event)
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        for handler in handlers:
            handler.shutdown()
        if isinstance(exc, click.Abort):
            # To avoid showing "Aborted!" message, which is the default behavior in Click
            sys.exit(1)
        raise


@schemathesis.command(short_help="Replay requests from a saved cassette.")
@click.argument("cassette_path", type=click.Path(exists=True))
@click.option("--id", "id_", help="ID of interaction to replay.", type=str)
@click.option("--status", help="Status of interactions to replay.", type=str)
@click.option("--uri", help="A regexp that filters interactions by their request URI.", type=str)
@click.option("--method", help="A regexp that filters interactions by their request method.", type=str)
def replay(
    cassette_path: str,
    id_: Optional[str],
    status: Optional[str] = None,
    uri: Optional[str] = None,
    method: Optional[str] = None,
) -> None:
    """Replay a cassette.

    Cassettes in VCR-compatible format can be replayed.
    For example, ones that are recorded with ``store-network-log`` option of `schemathesis run` command.
    """
    click.secho(f"{bold('Replaying cassette')}: {cassette_path}")
    with open(cassette_path) as fd:
        cassette = yaml.load(fd, Loader=SafeLoader)
    click.secho(f"{bold('Total interactions')}: {len(cassette['http_interactions'])}\n")
    for replayed in cassettes.replay(cassette, id_=id_, status=status, uri=uri, method=method):
        click.secho(f"  {bold('ID')}              : {replayed.interaction['id']}")
        click.secho(f"  {bold('URI')}             : {replayed.interaction['request']['uri']}")
        click.secho(f"  {bold('Old status code')} : {replayed.interaction['response']['status']['code']}")
        click.secho(f"  {bold('New status code')} : {replayed.response.status_code}\n")


def bold(message: str) -> str:
    return click.style(message, bold=True)


@HookDispatcher.register_spec([HookScope.GLOBAL])
def after_init_cli_run_handlers(
    context: HookContext, handlers: List[EventHandler], execution_context: ExecutionContext
) -> None:
    """Called after CLI hooks are initialized.

    Might be used to add extra event handlers.
    """
