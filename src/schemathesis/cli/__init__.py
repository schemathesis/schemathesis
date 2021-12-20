# pylint: disable=too-many-lines
import enum
import os
import sys
import traceback
from collections import defaultdict
from enum import Enum
from queue import Queue
from typing import Any, Callable, Dict, Generator, Iterable, List, Optional, Tuple, Union
from urllib.parse import urlparse

import attr
import click
import hypothesis
import yaml

from .. import checks as checks_module
from .. import fixups as _fixups
from .. import runner, service
from .. import targets as targets_module
from ..constants import (
    DEFAULT_DATA_GENERATION_METHODS,
    DEFAULT_RESPONSE_TIMEOUT,
    DEFAULT_STATEFUL_RECURSION_LIMIT,
    CodeSampleStyle,
    DataGenerationMethod,
)
from ..exceptions import HTTPError
from ..fixups import ALL_FIXUPS
from ..hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher, HookScope
from ..models import Case, CheckFunction
from ..runner import events, prepare_hypothesis_settings
from ..schemas import BaseSchema
from ..specs.graphql import loaders as gql_loaders
from ..specs.graphql.schemas import GraphQLSchema
from ..specs.openapi import loaders as oas_loaders
from ..stateful import Stateful
from ..targets import Target
from ..types import Filter, RequestCert
from ..utils import GenericResponse, file_exists, get_requests_auth, import_app
from . import callbacks, cassettes, output
from .constants import DEFAULT_WORKERS, MAX_WORKERS, MIN_WORKERS
from .context import ExecutionContext, ServiceContext
from .debug import DebugOutputHandler
from .handlers import EventHandler
from .junitxml import JunitXMLHandler
from .options import CSVOption, CustomHelpMessageChoice, NotSet, OptionalInt

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    # pylint: disable=unused-import
    from yaml import SafeLoader  # type: ignore


def _get_callable_names(items: Tuple[Callable, ...]) -> Tuple[str, ...]:
    return tuple(item.__name__ for item in items)


CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

DEFAULT_CHECKS_NAMES = _get_callable_names(checks_module.DEFAULT_CHECKS)
ALL_CHECKS_NAMES = _get_callable_names(checks_module.ALL_CHECKS)
CHECKS_TYPE = click.Choice((*ALL_CHECKS_NAMES, "all"))

DEFAULT_TARGETS_NAMES = _get_callable_names(targets_module.DEFAULT_TARGETS)
ALL_TARGETS_NAMES = _get_callable_names(targets_module.ALL_TARGETS)
TARGETS_TYPE = click.Choice((*ALL_TARGETS_NAMES, "all"))


def register_target(function: Target) -> Target:
    """Register a new testing target for schemathesis CLI.

    :param function: A function that will be called to calculate a metric passed to ``hypothesis.target``.
    """
    targets_module.ALL_TARGETS += (function,)
    TARGETS_TYPE.choices += (function.__name__,)  # type: ignore
    return function


def register_check(function: CheckFunction) -> CheckFunction:
    """Register a new check for schemathesis CLI.

    :param function: A function to validate API responses.

    .. code-block:: python

        @schemathesis.register_check
        def new_check(response, case):
            # some awesome assertions!
            ...
    """
    checks_module.ALL_CHECKS += (function,)
    CHECKS_TYPE.choices += (function.__name__,)  # type: ignore
    return function


def reset_checks() -> None:
    """Get checks list to their default state."""
    # Useful in tests
    checks_module.ALL_CHECKS = checks_module.DEFAULT_CHECKS + checks_module.OPTIONAL_CHECKS
    CHECKS_TYPE.choices = _get_callable_names(checks_module.ALL_CHECKS) + ("all",)


def reset_targets() -> None:
    """Get targets list to their default state."""
    # Useful in tests
    targets_module.ALL_TARGETS = targets_module.DEFAULT_TARGETS + targets_module.OPTIONAL_TARGETS
    TARGETS_TYPE.choices = _get_callable_names(targets_module.ALL_TARGETS) + ("all",)


class DeprecatedOption(click.Option):
    def __init__(self, *args: Any, removed_in: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.removed_in = removed_in

    def handle_parse_result(self, ctx: click.Context, opts: Dict[str, Any], args: List[str]) -> Tuple[Any, List[str]]:
        if self.name in opts:
            opt_names = "/".join(f"`{name}`" for name in self.opts)
            verb = "is" if len(self.opts) == 1 else "are"
            click.secho(
                f"\nWARNING: {opt_names} {verb} deprecated and will be removed in Schemathesis {self.removed_in}\n",
                fg="yellow",
            )
        return super().handle_parse_result(ctx, opts, args)


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option("--pre-run", help="A module to execute before the running the tests.", type=str)
@click.version_option()
def schemathesis(pre_run: Optional[str] = None) -> None:
    """Command line tool for testing your web application built with Open API / GraphQL specifications."""
    if pre_run:
        load_hook(pre_run)


class ParameterGroup(enum.Enum):
    filtering = "Filtering", "These options define what parts of the API will be tested."
    validation = "Validation", "Options, responsible for how responses & schemas will be checked."
    hypothesis = "Hypothesis", "Configuration of the underlying Hypothesis engine."
    generic = "Generic", None


class CommandWithCustomHelp(click.Command):
    def format_options(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        # Group options first
        groups = defaultdict(list)
        for param in self.get_params(ctx):
            rv = param.get_help_record(ctx)
            if rv is not None:
                if isinstance(param, GroupedOption):
                    group = param.group
                else:
                    group = ParameterGroup.generic
                groups[group].append(rv)
        # Then display groups separately with optional description
        for group in ParameterGroup:
            opts = groups[group]
            group_name, description = group.value
            with formatter.section(f"{group_name} options"):
                if description:
                    formatter.write_paragraph()
                    formatter.write_text(description)
                    formatter.write_paragraph()
                formatter.write_dl(opts)


class GroupedOption(click.Option):
    def __init__(self, *args: Any, group: ParameterGroup, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.group = group


@schemathesis.command(short_help="Perform schemathesis test.", cls=CommandWithCustomHelp)
@click.argument("schema", type=str, callback=callbacks.validate_schema)
@click.option(
    "--checks",
    "-c",
    multiple=True,
    help="List of checks to run.",
    type=CHECKS_TYPE,
    default=DEFAULT_CHECKS_NAMES,
    cls=GroupedOption,
    group=ParameterGroup.validation,
    show_default=True,
)
@click.option(
    "--data-generation-method",
    "-D",
    "data_generation_methods",
    help="Defines how Schemathesis generates data for tests.",
    type=click.Choice([item.name for item in DataGenerationMethod]),
    default=DataGenerationMethod.default(),
    callback=callbacks.convert_data_generation_method,
    show_default=True,
)
@click.option(
    "--max-response-time",
    help="A custom check that will fail if the response time is greater than the specified one in milliseconds.",
    type=click.IntRange(min=1),
    cls=GroupedOption,
    group=ParameterGroup.validation,
)
@click.option(
    "--target",
    "-t",
    "targets",
    multiple=True,
    help="Targets for input generation.",
    type=TARGETS_TYPE,
    default=DEFAULT_TARGETS_NAMES,
    show_default=True,
)
@click.option(
    "-x",
    "--exitfirst",
    "exit_first",
    is_flag=True,
    default=False,
    help="Exit instantly on first error or failed test.",
    show_default=True,
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Disable sending data to the application and checking responses. "
    "Helpful to verify whether data is generated at all.",
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
    show_default=True,
)
@click.option(
    "--header",
    "-H",
    "headers",
    help=r"Custom header that will be used in all requests to the server. Example: Authorization: Bearer\ 123",
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
    help=r"Filter schemathesis tests by API operation path pattern. Example: users/\d+",
    callback=callbacks.validate_regex,
    cls=GroupedOption,
    group=ParameterGroup.filtering,
)
@click.option(
    "--method",
    "-M",
    "methods",
    type=str,
    multiple=True,
    help="Filter schemathesis tests by HTTP method.",
    callback=callbacks.validate_regex,
    cls=GroupedOption,
    group=ParameterGroup.filtering,
)
@click.option(
    "--tag",
    "-T",
    "tags",
    type=str,
    multiple=True,
    help="Filter schemathesis tests by schema tag pattern.",
    callback=callbacks.validate_regex,
    cls=GroupedOption,
    group=ParameterGroup.filtering,
)
@click.option(
    "--operation-id",
    "-O",
    "operation_ids",
    type=str,
    multiple=True,
    help="Filter schemathesis tests by operationId pattern.",
    callback=callbacks.validate_regex,
    cls=GroupedOption,
    group=ParameterGroup.filtering,
)
@click.option(
    "--workers",
    "-w",
    "workers_num",
    help="Number of workers to run tests.",
    type=CustomHelpMessageChoice(
        ["auto"] + list(map(str, range(MIN_WORKERS, MAX_WORKERS + 1))),
        choices_repr=f"[auto|{MIN_WORKERS}-{MAX_WORKERS}]",
    ),
    default=str(DEFAULT_WORKERS),
    show_default=True,
    callback=callbacks.convert_workers,
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
    default=DEFAULT_RESPONSE_TIMEOUT,
)
@click.option(
    "--request-tls-verify",
    help="Controls whether Schemathesis verifies the server's TLS certificate. "
    "You can also pass the path to a CA_BUNDLE file for private certs.",
    type=str,
    default="true",
    show_default=True,
    callback=callbacks.convert_request_tls_verify,
)
@click.option(
    "--request-cert",
    help="File path of unencrypted client certificate for authentication. "
    "The certificate can be bundled with a private key (e.g. PEM) or the private "
    "key can be provided with the --request-cert-key argument.",
    type=click.Path(exists=True),
    default=None,
    show_default=False,
)
@click.option(
    "--request-cert-key",
    help="File path of the private key of the client certificate.",
    type=click.Path(exists=True),
    default=None,
    show_default=False,
    callback=callbacks.validate_request_cert_key,
)
@click.option(
    "--validate-schema",
    help="Enable or disable validation of input schema.",
    type=bool,
    default=True,
    show_default=True,
    cls=GroupedOption,
    group=ParameterGroup.validation,
)
@click.option(
    "--skip-deprecated-operations",
    help="Skip testing of deprecated API operations.",
    is_flag=True,
    is_eager=True,
    default=False,
    show_default=True,
    cls=GroupedOption,
    group=ParameterGroup.filtering,
)
@click.option(
    "--junit-xml", help="Create junit-xml style report file at given path.", type=click.File("w", encoding="utf-8")
)
@click.option(
    "--debug-output-file",
    help="Save debug output as JSON lines in the given file.",
    type=click.File("w", encoding="utf-8"),
)
@click.option(
    "--show-errors-tracebacks",
    help="Show full tracebacks for internal errors.",
    is_flag=True,
    is_eager=True,
    default=False,
    show_default=True,
)
@click.option(
    "--code-sample-style",
    help="Controls the style of code samples for failure reproduction.",
    type=click.Choice([item.name for item in CodeSampleStyle]),
    default=CodeSampleStyle.default().name,
    callback=callbacks.convert_code_sample_style,
)
@click.option(
    "--store-network-log", help="Store requests and responses into a file.", type=click.File("w", encoding="utf-8")
)
@click.option(
    "--fixups",
    help="Install specified compatibility fixups.",
    multiple=True,
    type=click.Choice(list(ALL_FIXUPS) + ["all"]),
)
@click.option(
    "--stateful",
    help="Utilize stateful testing capabilities.",
    type=click.Choice([item.name for item in Stateful]),
    callback=callbacks.convert_stateful,
)
@click.option(
    "--stateful-recursion-limit",
    help="Limit recursion depth for stateful testing.",
    default=DEFAULT_STATEFUL_RECURSION_LIMIT,
    show_default=True,
    type=click.IntRange(1, 100),
    cls=DeprecatedOption,
    removed_in="4.0",
)
@click.option(
    "--force-schema-version",
    help="Force Schemathesis to parse the input schema with the specified spec version.",
    type=click.Choice(["20", "30"]),
)
@click.option(
    "--hypothesis-deadline",
    help="Duration in milliseconds that each individual example with a test is not allowed to exceed.",
    # max value to avoid overflow. It is the maximum amount of days in milliseconds
    type=OptionalInt(1, 999999999 * 24 * 3600 * 1000),
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option(
    "--hypothesis-derandomize",
    help="Use Hypothesis's deterministic mode.",
    is_flag=True,
    default=None,
    show_default=True,
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option(
    "--hypothesis-max-examples",
    help="Maximum number of generated examples per each method/path combination.",
    type=click.IntRange(1),
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option(
    "--hypothesis-phases",
    help="Control which phases should be run.",
    type=CSVOption(hypothesis.Phase),
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option(
    "--hypothesis-report-multiple-bugs",
    help="Raise only the exception with the smallest minimal example.",
    type=bool,
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option(
    "--hypothesis-seed",
    help="Set a seed to use for all Hypothesis tests.",
    type=int,
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option(
    "--hypothesis-suppress-health-check",
    help="Comma-separated list of health checks to disable.",
    type=CSVOption(hypothesis.HealthCheck),
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option(
    "--hypothesis-verbosity",
    help="Verbosity level of Hypothesis messages.",
    type=click.Choice([item.name for item in hypothesis.Verbosity]),
    callback=callbacks.convert_verbosity,
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option("--no-color", help="Disable ANSI color escape codes.", type=bool, is_flag=True)
@click.option(
    "--schemathesis-io-token",
    help="Schemathesis.io authentication token. If present, test run results will be uploaded to Schemathesis.io",
    type=str,
)
@click.option(
    "--schemathesis-io-url",
    help="Schemathesis.io base URL.",
    default=service.DEFAULT_URL,
    type=str,
)
@click.option("--verbosity", "-v", help="Reduce verbosity of error output.", count=True)
@click.pass_context
def run(
    ctx: click.Context,
    schema: str,
    auth: Optional[Tuple[str, str]],
    auth_type: str,
    headers: Dict[str, str],
    checks: Iterable[str] = DEFAULT_CHECKS_NAMES,
    data_generation_methods: Tuple[DataGenerationMethod, ...] = DEFAULT_DATA_GENERATION_METHODS,
    max_response_time: Optional[int] = None,
    targets: Iterable[str] = DEFAULT_TARGETS_NAMES,
    exit_first: bool = False,
    dry_run: bool = False,
    endpoints: Optional[Filter] = None,
    methods: Optional[Filter] = None,
    tags: Optional[Filter] = None,
    operation_ids: Optional[Filter] = None,
    workers_num: int = DEFAULT_WORKERS,
    base_url: Optional[str] = None,
    app: Optional[str] = None,
    request_timeout: Optional[int] = None,
    request_tls_verify: bool = True,
    request_cert: Optional[str] = None,
    request_cert_key: Optional[str] = None,
    validate_schema: bool = True,
    skip_deprecated_operations: bool = False,
    junit_xml: Optional[click.utils.LazyFile] = None,
    debug_output_file: Optional[click.utils.LazyFile] = None,
    show_errors_tracebacks: bool = False,
    code_sample_style: CodeSampleStyle = CodeSampleStyle.default(),
    store_network_log: Optional[click.utils.LazyFile] = None,
    fixups: Tuple[str] = (),  # type: ignore
    stateful: Optional[Stateful] = None,
    stateful_recursion_limit: int = DEFAULT_STATEFUL_RECURSION_LIMIT,
    force_schema_version: Optional[str] = None,
    hypothesis_deadline: Optional[Union[int, NotSet]] = None,
    hypothesis_derandomize: Optional[bool] = None,
    hypothesis_max_examples: Optional[int] = None,
    hypothesis_phases: Optional[List[hypothesis.Phase]] = None,
    hypothesis_report_multiple_bugs: Optional[bool] = None,
    hypothesis_suppress_health_check: Optional[List[hypothesis.HealthCheck]] = None,
    hypothesis_seed: Optional[int] = None,
    hypothesis_verbosity: Optional[hypothesis.Verbosity] = None,
    verbosity: int = 0,
    no_color: bool = False,
    schemathesis_io_token: Optional[str] = None,
    schemathesis_io_url: str = service.DEFAULT_URL,
) -> None:
    """Perform schemathesis test against an API specified by SCHEMA.

    SCHEMA must be a valid URL or file path pointing to an Open API / GraphQL specification.
    """
    # pylint: disable=too-many-locals
    maybe_disable_color(ctx, no_color)
    check_auth(auth, headers)
    selected_targets = tuple(target for target in targets_module.ALL_TARGETS if target.__name__ in targets)

    if "all" in checks:
        selected_checks = checks_module.ALL_CHECKS
    else:
        selected_checks = tuple(check for check in checks_module.ALL_CHECKS if check.__name__ in checks)

    if fixups:
        if "all" in fixups:
            _fixups.install()
        else:
            _fixups.install(fixups)
    hypothesis_settings = prepare_hypothesis_settings(
        deadline=hypothesis_deadline,
        derandomize=hypothesis_derandomize,
        max_examples=hypothesis_max_examples,
        phases=hypothesis_phases,
        report_multiple_bugs=hypothesis_report_multiple_bugs,
        suppress_health_check=hypothesis_suppress_health_check,
        verbosity=hypothesis_verbosity,
    )
    event_stream = into_event_stream(
        schema,
        app=app,
        base_url=base_url,
        validate_schema=validate_schema,
        skip_deprecated_operations=skip_deprecated_operations,
        data_generation_methods=data_generation_methods,
        force_schema_version=force_schema_version,
        request_tls_verify=request_tls_verify,
        request_cert=prepare_request_cert(request_cert, request_cert_key),
        auth=auth,
        auth_type=auth_type,
        headers=headers,
        endpoint=endpoints or None,
        method=methods or None,
        tag=tags or None,
        operation_id=operation_ids or None,
        request_timeout=request_timeout,
        seed=hypothesis_seed,
        exit_first=exit_first,
        dry_run=dry_run,
        store_interactions=store_network_log is not None,
        checks=selected_checks,
        max_response_time=max_response_time,
        targets=selected_targets,
        workers_num=workers_num,
        stateful=stateful,
        stateful_recursion_limit=stateful_recursion_limit,
        hypothesis_settings=hypothesis_settings,
    )
    execute(
        event_stream,
        workers_num,
        show_errors_tracebacks,
        validate_schema,
        store_network_log,
        junit_xml,
        verbosity,
        code_sample_style,
        debug_output_file,
        schemathesis_io_token,
        schemathesis_io_url,
    )


def prepare_request_cert(cert: Optional[str], key: Optional[str]) -> Optional[RequestCert]:
    if cert is not None and key is not None:
        return cert, key
    return cert


@attr.s(slots=True)
class LoaderConfig:
    """Container for API loader parameters.

    The main goal is to avoid too many parameters in function signatures.
    """

    schema_location: str = attr.ib()  # pragma: no mutate
    app: Any = attr.ib()  # pragma: no mutate
    base_url: Optional[str] = attr.ib()  # pragma: no mutate
    validate_schema: bool = attr.ib()  # pragma: no mutate
    skip_deprecated_operations: bool = attr.ib()  # pragma: no mutate
    data_generation_methods: Tuple[DataGenerationMethod, ...] = attr.ib()  # pragma: no mutate
    force_schema_version: Optional[str] = attr.ib()  # pragma: no mutate
    request_tls_verify: Union[bool, str] = attr.ib()  # pragma: no mutate
    request_cert: Optional[RequestCert] = attr.ib()  # pragma: no mutate
    # Network request parameters
    auth: Optional[Tuple[str, str]] = attr.ib()  # pragma: no mutate
    auth_type: Optional[str] = attr.ib()  # pragma: no mutate
    headers: Optional[Dict[str, str]] = attr.ib()  # pragma: no mutate
    # Schema filters
    endpoint: Optional[Filter] = attr.ib()  # pragma: no mutate
    method: Optional[Filter] = attr.ib()  # pragma: no mutate
    tag: Optional[Filter] = attr.ib()  # pragma: no mutate
    operation_id: Optional[Filter] = attr.ib()  # pragma: no mutate


def into_event_stream(
    schema_location: str,
    *,
    app: Any,
    base_url: Optional[str],
    validate_schema: bool,
    skip_deprecated_operations: bool,
    data_generation_methods: Tuple[DataGenerationMethod, ...],
    force_schema_version: Optional[str],
    request_tls_verify: Union[bool, str],
    request_cert: Optional[RequestCert],
    # Network request parameters
    auth: Optional[Tuple[str, str]],
    auth_type: Optional[str],
    headers: Optional[Dict[str, str]],
    request_timeout: Optional[int],
    # Schema filters
    endpoint: Optional[Filter],
    method: Optional[Filter],
    tag: Optional[Filter],
    operation_id: Optional[Filter],
    # Runtime behavior
    checks: Iterable[CheckFunction],
    max_response_time: Optional[int],
    targets: Iterable[Target],
    workers_num: int,
    hypothesis_settings: Optional[hypothesis.settings],
    seed: Optional[int],
    exit_first: bool,
    dry_run: bool,
    store_interactions: bool,
    stateful: Optional[Stateful],
    stateful_recursion_limit: int,
) -> Generator[events.ExecutionEvent, None, None]:
    try:
        if app is not None:
            app = import_app(app)
        config = LoaderConfig(
            schema_location=schema_location,
            app=app,
            base_url=base_url,
            validate_schema=validate_schema,
            skip_deprecated_operations=skip_deprecated_operations,
            data_generation_methods=data_generation_methods,
            force_schema_version=force_schema_version,
            request_tls_verify=request_tls_verify,
            request_cert=request_cert,
            auth=auth,
            auth_type=auth_type,
            headers=headers,
            endpoint=endpoint or None,
            method=method or None,
            tag=tag or None,
            operation_id=operation_id or None,
        )
        loaded_schema = load_schema(config)
        yield from runner.from_schema(
            loaded_schema,
            auth=auth,
            auth_type=auth_type,
            headers=headers,
            request_timeout=request_timeout,
            request_tls_verify=request_tls_verify,
            request_cert=request_cert,
            seed=seed,
            exit_first=exit_first,
            dry_run=dry_run,
            store_interactions=store_interactions,
            checks=checks,
            max_response_time=max_response_time,
            targets=targets,
            workers_num=workers_num,
            stateful=stateful,
            stateful_recursion_limit=stateful_recursion_limit,
            hypothesis_settings=hypothesis_settings,
        ).execute()
    except Exception as exc:
        yield events.InternalError.from_exc(exc)


def load_schema(config: LoaderConfig) -> BaseSchema:
    """Automatically load API schema."""
    first: Callable[[LoaderConfig], BaseSchema]
    second: Callable[[LoaderConfig], BaseSchema]
    if is_probably_graphql(config.schema_location):
        # Try GraphQL first, then fallback to Open API
        first, second = (_load_graphql_schema, _load_openapi_schema)
    else:
        # Try Open API first, then fallback to GraphQL
        first, second = (_load_openapi_schema, _load_graphql_schema)
    return _try_load_schema(config, first, second)


def _try_load_schema(
    config: LoaderConfig, first: Callable[[LoaderConfig], BaseSchema], second: Callable[[LoaderConfig], BaseSchema]
) -> BaseSchema:
    try:
        return first(config)
    except HTTPError as exc:
        try:
            return second(config)
        except HTTPError:
            # Raise the first loader's error
            raise exc  # pylint: disable=raise-missing-from


def _load_graphql_schema(config: LoaderConfig) -> GraphQLSchema:
    loader = detect_loader(config.schema_location, config.app, is_openapi=False)
    kwargs = get_graphql_loader_kwargs(loader, config)
    return loader(config.schema_location, **kwargs)


def _load_openapi_schema(config: LoaderConfig) -> BaseSchema:
    loader = detect_loader(config.schema_location, config.app, is_openapi=True)
    kwargs = get_loader_kwargs(loader, config)
    return loader(config.schema_location, **kwargs)


def detect_loader(schema_location: str, app: Any, is_openapi: bool) -> Callable:
    """Detect API schema loader."""
    if file_exists(schema_location):
        # If there is an existing file with the given name,
        # then it is likely that the user wants to load API schema from there
        return oas_loaders.from_path if is_openapi else gql_loaders.from_path  # type: ignore
    if app is not None and not urlparse(schema_location).netloc:
        # App is passed & location is relative
        return oas_loaders.get_loader_for_app(app) if is_openapi else gql_loaders.get_loader_for_app(app)
    # Default behavior
    return oas_loaders.from_uri if is_openapi else gql_loaders.from_url  # type: ignore


def get_loader_kwargs(loader: Callable, config: LoaderConfig) -> Dict[str, Any]:
    """Detect the proper set of parameters for a loader."""
    # These kwargs are shared by all loaders
    kwargs = {
        "app": config.app,
        "base_url": config.base_url,
        "method": config.method,
        "endpoint": config.endpoint,
        "tag": config.tag,
        "operation_id": config.operation_id,
        "skip_deprecated_operations": config.skip_deprecated_operations,
        "validate_schema": config.validate_schema,
        "force_schema_version": config.force_schema_version,
        "data_generation_methods": config.data_generation_methods,
    }
    if loader is not oas_loaders.from_path:
        kwargs["headers"] = config.headers
    if loader in (oas_loaders.from_uri, oas_loaders.from_aiohttp):
        _add_requests_kwargs(kwargs, config)
    return kwargs


def get_graphql_loader_kwargs(
    loader: Callable,
    config: LoaderConfig,
) -> Dict[str, Any]:
    """Detect the proper set of parameters for a loader."""
    # These kwargs are shared by all loaders
    kwargs = {
        "app": config.app,
        "base_url": config.base_url,
        "data_generation_methods": config.data_generation_methods,
    }
    if loader is not gql_loaders.from_path:
        kwargs["headers"] = config.headers
    if loader is gql_loaders.from_url:
        _add_requests_kwargs(kwargs, config)
    return kwargs


def _add_requests_kwargs(kwargs: Dict[str, Any], config: LoaderConfig) -> None:
    kwargs["verify"] = config.request_tls_verify
    if config.request_cert is not None:
        kwargs["cert"] = config.request_cert
    if config.auth is not None:
        kwargs["auth"] = get_requests_auth(config.auth, config.auth_type)


def is_probably_graphql(location: str) -> bool:
    """Detect whether it is likely that the given location is a GraphQL endpoint."""
    return location.endswith(("/graphql", "/graphql/"))


def check_auth(auth: Optional[Tuple[str, str]], headers: Dict[str, str]) -> None:
    if auth is not None and "authorization" in {header.lower() for header in headers}:
        raise click.BadParameter("Passing `--auth` together with `--header` that sets `Authorization` is not allowed.")


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
    except Exception as exc:
        click.secho("An exception happened during the hook loading:\n", fg="red")
        message = traceback.format_exc()
        click.secho(message, fg="red")
        raise click.Abort() from exc


class OutputStyle(Enum):
    """Provide different output styles."""

    default = output.default.DefaultOutputStyleHandler
    short = output.short.ShortOutputStyleHandler


def execute(
    event_stream: Generator[events.ExecutionEvent, None, None],
    workers_num: int,
    show_errors_tracebacks: bool,
    validate_schema: bool,
    store_network_log: Optional[click.utils.LazyFile],
    junit_xml: Optional[click.utils.LazyFile],
    verbosity: int,
    code_sample_style: CodeSampleStyle,
    debug_output_file: Optional[click.utils.LazyFile],
    schemathesis_io_token: Optional[str],
    schemathesis_io_url: str,
) -> None:
    """Execute a prepared runner by drawing events from it and passing to a proper handler."""
    handlers: List[EventHandler] = []
    if junit_xml is not None:
        handlers.append(JunitXMLHandler(junit_xml))
    if debug_output_file is not None:
        handlers.append(DebugOutputHandler(debug_output_file))
    service_context = None
    if schemathesis_io_token is not None:
        service_queue: Queue = Queue()
        service_context = ServiceContext(url=schemathesis_io_url, queue=service_queue)
        handlers.append(service.ServiceReporter(service_queue, schemathesis_io_token, schemathesis_io_url))
    if store_network_log is not None:
        # This handler should be first to have logs writing completed when the output handler will display statistic
        handlers.append(cassettes.CassetteWriter(store_network_log))
    handlers.append(get_output_handler(workers_num))
    execution_context = ExecutionContext(
        workers_num=workers_num,
        show_errors_tracebacks=show_errors_tracebacks,
        validate_schema=validate_schema,
        cassette_file_name=store_network_log.name if store_network_log is not None else None,
        junit_xml_file=junit_xml.name if junit_xml is not None else None,
        verbosity=verbosity,
        code_sample_style=code_sample_style,
        service=service_context,
    )

    def shutdown() -> None:
        for _handler in handlers:
            _handler.shutdown()

    GLOBAL_HOOK_DISPATCHER.dispatch("after_init_cli_run_handlers", HookContext(), handlers, execution_context)
    event = None
    try:
        for event in event_stream:
            for handler in handlers:
                handler.handle_event(execution_context, event)
    except Exception as exc:
        if isinstance(exc, click.Abort):
            # To avoid showing "Aborted!" message, which is the default behavior in Click
            sys.exit(1)
        raise
    finally:
        shutdown()
    if event is not None and event.is_terminal:
        exit_code = get_exit_code(event)
        sys.exit(exit_code)
    # Event stream did not finish with a terminal event. Only possible if the handler is broken
    click.secho("Unexpected error", fg="red")
    sys.exit(1)


def get_exit_code(event: events.ExecutionEvent) -> int:
    if isinstance(event, events.Finished):
        if event.has_failures or event.has_errors:
            return 1
        return 0
    # Practically not possible. May occur only if the output handler is broken - in this case we still will have the
    # right exit code.
    return 1


@schemathesis.command(short_help="Replay requests from a saved cassette.")
@click.argument("cassette_path", type=click.Path(exists=True))
@click.option("--id", "id_", help="ID of interaction to replay.", type=str)
@click.option("--status", help="Status of interactions to replay.", type=str)
@click.option("--uri", help="A regexp that filters interactions by their request URI.", type=str)
@click.option("--method", help="A regexp that filters interactions by their request method.", type=str)
@click.option("--no-color", help="Disable ANSI color escape codes.", type=bool, is_flag=True)
@click.pass_context
def replay(
    ctx: click.Context,
    cassette_path: str,
    id_: Optional[str],
    status: Optional[str] = None,
    uri: Optional[str] = None,
    method: Optional[str] = None,
    no_color: bool = False,
) -> None:
    """Replay a cassette.

    Cassettes in VCR-compatible format can be replayed.
    For example, ones that are recorded with ``store-network-log`` option of `schemathesis run` command.
    """
    maybe_disable_color(ctx, no_color)
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


def maybe_disable_color(ctx: click.Context, no_color: bool) -> None:
    if no_color or "NO_COLOR" in os.environ:
        ctx.color = False


@HookDispatcher.register_spec([HookScope.GLOBAL])
def after_init_cli_run_handlers(
    context: HookContext, handlers: List[EventHandler], execution_context: ExecutionContext
) -> None:
    """Called after CLI hooks are initialized.

    Might be used to add extra event handlers.
    """


@HookDispatcher.register_spec([HookScope.GLOBAL])
def before_call(context: HookContext, case: Case) -> None:
    """Called before every network call in CLI tests.

    Use cases:
     - Modification of `case`. For example, adding some pre-determined value to its query string.
     - Logging
    """


@HookDispatcher.register_spec([HookScope.GLOBAL])
def after_call(context: HookContext, case: Case, response: GenericResponse) -> None:
    """Called after every network call in CLI tests.

    Note that you need to modify the response in-place.

    Use cases:
     - Response post-processing, like modifying its payload.
     - Logging
    """


@HookDispatcher.register_spec([HookScope.GLOBAL])
def process_call_kwargs(context: HookContext, case: Case, kwargs: Dict[str, Any]) -> None:
    """Called before every network call in CLI tests.

    Aims to modify the argument passed to `case.call` / `case.call_wsgi` / `case.call_asgi`.
    Note that you need to modify `kwargs` in-place.
    """
