from __future__ import annotations

import base64
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from queue import Queue
from typing import TYPE_CHECKING, Any, Callable, Literal, NoReturn, Sequence, cast
from urllib.parse import urlparse

import click

import schemathesis.specs.openapi.checks as checks
from schemathesis.checks import CHECKS, ChecksConfig
from schemathesis.cli import env
from schemathesis.core.deserialization import deserialize_yaml
from schemathesis.core.errors import LoaderError, format_exception
from schemathesis.core.fs import ensure_parent
from schemathesis.core.output import OutputConfig
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT
from schemathesis.generation.hypothesis import HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER, settings
from schemathesis.generation.targets import TARGETS

from .. import contrib, experimental, generation, runner, service
from .._override import CaseOverride
from ..filters import FilterSet, expression_to_filter_function, is_deprecated
from ..generation import DEFAULT_DATA_GENERATION_METHODS, DataGenerationMethod
from ..runner import events
from ..runner.config import NetworkConfig
from ..stateful import Stateful
from . import cassettes, loaders, output, validation
from .constants import DEFAULT_WORKERS, ISSUE_TRACKER_URL, MAX_WORKERS, MIN_WORKERS, HealthCheck, Phase, Verbosity
from .context import ExecutionContext, FileReportContext, ServiceReportContext
from .debug import DebugOutputHandler
from .handlers import EventHandler
from .junitxml import JunitXMLHandler
from .options import CsvEnumChoice, CsvListChoice, CustomHelpMessageChoice, OptionalInt, RegistryChoice

if TYPE_CHECKING:
    import io

    import hypothesis
    import requests

    from schemathesis.core import NotSet
    from schemathesis.generation.targets import TargetFunction

    from ..models import CheckFunction
    from ..service.client import ServiceClient


__all__ = [
    "EventHandler",
]

del checks

CUSTOM_HANDLERS: list[type[EventHandler]] = []
CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

DATA_GENERATION_METHOD_TYPE = click.Choice([item.name for item in DataGenerationMethod] + ["all"])

COLOR_OPTIONS_INVALID_USAGE_MESSAGE = "Can't use `--no-color` and `--force-color` simultaneously"
PHASES_INVALID_USAGE_MESSAGE = "Can't use `--hypothesis-phases` and `--hypothesis-no-phases` simultaneously"
EXTENSIONS_DOCUMENTATION_URL = "https://schemathesis.readthedocs.io/en/stable/extending.html"


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option()
def schemathesis() -> None:
    """Property-based API testing for OpenAPI and GraphQL."""
    hooks = os.getenv(env.HOOKS_MODULE_ENV_VAR)
    if hooks:
        load_hook(hooks)


GROUPS: list[str] = []


class CommandWithGroupedOptions(click.Command):
    def format_options(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        groups = defaultdict(list)
        for param in self.get_params(ctx):
            rv = param.get_help_record(ctx)
            if rv is not None:
                (option_repr, message) = rv
                if isinstance(param.type, click.Choice):
                    message += (
                        getattr(param.type, "choices_repr", None)
                        or f" [possible values: {', '.join(param.type.choices)}]"
                    )

                if isinstance(param, GroupedOption):
                    group = param.group
                else:
                    group = "Global options"
                groups[group].append((option_repr, message))
        for group in GROUPS:
            with formatter.section(group or "Options"):
                formatter.write_dl(groups[group], col_max=40)


class GroupedOption(click.Option):
    def __init__(self, *args: Any, group: str | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.group = group


def group(name: str) -> Callable:
    GROUPS.append(name)

    def _inner(cmd: Callable) -> Callable:
        for param in reversed(cmd.__click_params__):  # type: ignore[attr-defined]
            if not isinstance(param, GroupedOption) or param.group is not None:
                break
            param.group = name
        return cmd

    return _inner


def grouped_option(*args: Any, **kwargs: Any) -> Callable:
    kwargs.setdefault("cls", GroupedOption)
    return click.option(*args, **kwargs)


with_request_proxy = grouped_option(
    "--request-proxy",
    help="Set the proxy for all network requests",
    type=str,
)
with_request_tls_verify = grouped_option(
    "--request-tls-verify",
    help="Configures TLS certificate verification for server requests. Can specify path to CA_BUNDLE for custom certs",
    type=str,
    default="true",
    show_default=True,
    callback=validation.convert_boolean_string,
)
with_request_cert = grouped_option(
    "--request-cert",
    help="File path of unencrypted client certificate for authentication. "
    "The certificate can be bundled with a private key (e.g. PEM) or the private "
    "key can be provided with the --request-cert-key argument",
    type=click.Path(exists=True),
    default=None,
    show_default=False,
)
with_request_cert_key = grouped_option(
    "--request-cert-key",
    help="Specify the file path of the private key for the client certificate",
    type=click.Path(exists=True),
    default=None,
    show_default=False,
    callback=validation.validate_request_cert_key,
)
with_hosts_file = grouped_option(
    "--hosts-file",
    help="Path to a file to store the Schemathesis.io auth configuration",
    type=click.Path(dir_okay=False, writable=True),
    default=service.DEFAULT_HOSTS_PATH,
    envvar=service.HOSTS_PATH_ENV_VAR,
    callback=validation.convert_hosts_file,
)


def _with_filter(*, by: str, mode: Literal["include", "exclude"], modifier: Literal["regex"] | None = None) -> Callable:
    """Generate a CLI option for filtering API operations."""
    param = f"--{mode}-{by}"
    action = "include in" if mode == "include" else "exclude from"
    prop = {
        "operation-id": "ID",
        "name": "Operation name",
    }.get(by, by.capitalize())
    if modifier:
        param += f"-{modifier}"
        prop += " pattern"
    help_text = f"{prop} to {action} testing."
    return grouped_option(
        param,
        help=help_text,
        type=str,
        multiple=modifier is None,
    )


_BY_VALUES = ("operation-id", "tag", "name", "method", "path")


def with_filters(command: Callable) -> Callable:
    for by in _BY_VALUES:
        for mode in ("exclude", "include"):
            for modifier in ("regex", None):
                command = _with_filter(by=by, mode=mode, modifier=modifier)(command)  # type: ignore[arg-type]
    return command


class ReportToService:
    pass


REPORT_TO_SERVICE = ReportToService()


@schemathesis.command(
    short_help="Execute automated tests based on API specifications",
    cls=CommandWithGroupedOptions,
    context_settings={"terminal_width": output.default.get_terminal_width(), **CONTEXT_SETTINGS},
)
@click.argument("schema", type=str)
@click.argument("api_name", type=str, required=False, envvar=env.API_NAME_ENV_VAR)
@group("Options")
@grouped_option(
    "--workers",
    "-w",
    "workers_num",
    help="Number of concurrent workers for testing. Auto-adjusts if 'auto' is specified",
    type=CustomHelpMessageChoice(
        ["auto", *list(map(str, range(MIN_WORKERS, MAX_WORKERS + 1)))],
        choices_repr=f"[auto, {MIN_WORKERS}-{MAX_WORKERS}]",
    ),
    default=str(DEFAULT_WORKERS),
    show_default=True,
    callback=validation.convert_workers,
    metavar="",
)
@grouped_option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Simulate test execution without making any actual requests, useful for validating data generation",
)
@group("Experimental options")
@grouped_option(
    "--experimental",
    "experiments",
    help="Enable experimental features",
    type=click.Choice(
        [
            experimental.SCHEMA_ANALYSIS.name,
            experimental.STATEFUL_ONLY.name,
            experimental.COVERAGE_PHASE.name,
            experimental.POSITIVE_DATA_ACCEPTANCE.name,
        ]
    ),
    callback=validation.convert_experimental,
    multiple=True,
    metavar="",
)
@grouped_option(
    "--experimental-missing-required-header-allowed-statuses",
    "missing_required_header_allowed_statuses",
    help="Comma-separated list of status codes expected for test cases with a missing required header",
    type=CsvListChoice(),
    callback=validation.convert_status_codes,
    metavar="",
    envvar="SCHEMATHESIS_EXPERIMENTAL_MISSING_REQUIRED_HEADER_ALLOWED_STATUSES",
)
@grouped_option(
    "--experimental-positive-data-acceptance-allowed-statuses",
    "positive_data_acceptance_allowed_statuses",
    help="Comma-separated list of status codes considered as successful responses",
    type=CsvListChoice(),
    callback=validation.convert_status_codes,
    metavar="",
    envvar="SCHEMATHESIS_EXPERIMENTAL_POSITIVE_DATA_ACCEPTANCE_ALLOWED_STATUSES",
)
@grouped_option(
    "--experimental-negative-data-rejection-allowed-statuses",
    "negative_data_rejection_allowed_statuses",
    help="Comma-separated list of status codes expected for rejected negative data",
    type=CsvListChoice(),
    callback=validation.convert_status_codes,
    metavar="",
    envvar="SCHEMATHESIS_EXPERIMENTAL_NEGATIVE_DATA_REJECTION_ALLOWED_STATUSES",
)
@group("API validation options")
@grouped_option(
    "--checks",
    "-c",
    "included_check_names",
    multiple=True,
    help="Comma-separated list of checks to run against API responses",
    type=RegistryChoice(CHECKS, with_all=True),
    default=("not_a_server_error",),
    callback=validation.convert_checks,
    show_default=True,
    metavar="",
)
@grouped_option(
    "--exclude-checks",
    "excluded_check_names",
    multiple=True,
    help="Comma-separated list of checks to skip during testing",
    type=RegistryChoice(CHECKS, with_all=True),
    default=(),
    callback=validation.convert_checks,
    show_default=True,
    metavar="",
)
@grouped_option(
    "--max-response-time",
    help="Time limit in seconds for API response times. The test will fail if a response time exceeds this limit",
    type=click.FloatRange(min=0.0, min_open=True),
)
@grouped_option(
    "-x",
    "--exitfirst",
    "exit_first",
    is_flag=True,
    default=False,
    help="Terminate the test suite immediately upon the first failure or error encountered",
    show_default=True,
)
@grouped_option(
    "--max-failures",
    "max_failures",
    type=click.IntRange(min=1),
    help="Terminate the test suite after reaching a specified number of failures or errors",
    show_default=True,
)
@group("Loader options")
@grouped_option(
    "--wait-for-schema",
    help="Maximum duration, in seconds, to wait for the API schema to become available. Disabled by default",
    type=click.FloatRange(1.0),
    default=None,
    envvar=env.WAIT_FOR_SCHEMA_ENV_VAR,
)
@group("Network requests options")
@grouped_option(
    "--base-url",
    "-b",
    help="Base URL of the API, required when schema is provided as a file",
    type=str,
    callback=validation.validate_base_url,
    envvar=env.BASE_URL_ENV_VAR,
)
@grouped_option(
    "--request-timeout",
    help="Timeout limit, in seconds, for each network request during tests",
    type=click.FloatRange(min=0.0, min_open=True),
    default=DEFAULT_RESPONSE_TIMEOUT,
)
@with_request_proxy
@with_request_tls_verify
@with_request_cert
@with_request_cert_key
@grouped_option(
    "--rate-limit",
    help="Specify a rate limit for test requests in '<limit>/<duration>' format. "
    "Example - `100/m` for 100 requests per minute",
    type=str,
    callback=validation.validate_rate_limit,
)
@grouped_option(
    "--header",
    "-H",
    "headers",
    help=r"Add a custom HTTP header to all API requests. Format: 'Header-Name: Value'",
    multiple=True,
    type=str,
    callback=validation.validate_headers,
)
@grouped_option(
    "--auth",
    "-a",
    help="Provide the server authentication details in the 'USER:PASSWORD' format",
    type=str,
    callback=validation.validate_auth,
)
@group("Filtering options")
@with_filters
@grouped_option(
    "--include-by",
    "include_by",
    type=str,
    help="Include API operations by expression",
)
@grouped_option(
    "--exclude-by",
    "exclude_by",
    type=str,
    help="Exclude API operations by expression",
)
@grouped_option(
    "--exclude-deprecated",
    help="Exclude deprecated API operations from testing",
    is_flag=True,
    is_eager=True,
    default=False,
    show_default=True,
)
@group("Output options")
@grouped_option(
    "--junit-xml",
    help="Output a JUnit-XML style report at the specified file path",
    type=click.File("w", encoding="utf-8"),
)
@grouped_option(
    "--cassette-path",
    help="Save the test outcomes in a VCR-compatible format",
    type=click.File("w", encoding="utf-8"),
    is_eager=True,
)
@grouped_option(
    "--cassette-format",
    help="Format of the saved cassettes",
    type=click.Choice([item.name.lower() for item in cassettes.CassetteFormat]),
    default=cassettes.CassetteFormat.VCR.name.lower(),
    callback=validation.convert_cassette_format,
    metavar="",
)
@grouped_option(
    "--cassette-preserve-exact-body-bytes",
    help="Retain exact byte sequence of payloads in cassettes, encoded as base64",
    is_flag=True,
    callback=validation.validate_preserve_exact_body_bytes,
)
@grouped_option(
    "--sanitize-output",
    type=bool,
    default=True,
    show_default=True,
    help="Enable or disable automatic output sanitization to obscure sensitive data",
)
@grouped_option(
    "--output-truncate",
    help="Truncate schemas and responses in error messages",
    type=str,
    default="true",
    show_default=True,
    callback=validation.convert_boolean_string,
)
@grouped_option(
    "--debug-output-file",
    help="Save debugging information in a JSONL format at the specified file path",
    type=click.File("w", encoding="utf-8"),
)
@group("Data generation options")
@grouped_option(
    "--data-generation-method",
    "-D",
    "data_generation_methods",
    help="Specify the approach Schemathesis uses to generate test data. "
    "Use 'positive' for valid data, 'negative' for invalid data, or 'all' for both",
    type=DATA_GENERATION_METHOD_TYPE,
    default=DataGenerationMethod.default().name,
    callback=validation.convert_data_generation_method,
    show_default=True,
    metavar="",
)
@grouped_option(
    "--stateful",
    help="Enable or disable stateful testing",
    type=click.Choice([item.name for item in Stateful]),
    default=Stateful.links.name,
    callback=validation.convert_stateful,
    metavar="",
)
@grouped_option(
    "--generation-allow-x00",
    help="Whether to allow the generation of `\x00` bytes within strings",
    type=str,
    default="true",
    show_default=True,
    callback=validation.convert_boolean_string,
)
@grouped_option(
    "--generation-codec",
    help="The codec used for generating strings",
    type=str,
    default="utf-8",
    callback=validation.validate_generation_codec,
)
@grouped_option(
    "--generation-with-security-parameters",
    help="Whether to generate security parameters",
    type=str,
    default="true",
    show_default=True,
    callback=validation.convert_boolean_string,
)
@grouped_option(
    "--generation-graphql-allow-null",
    help="Whether to use `null` values for optional arguments in GraphQL queries",
    type=str,
    default="true",
    show_default=True,
    callback=validation.convert_boolean_string,
)
@grouped_option(
    "--contrib-unique-data",
    "contrib_unique_data",
    help="Force the generation of unique test cases",
    is_flag=True,
    default=False,
    show_default=True,
)
@grouped_option(
    "--contrib-openapi-fill-missing-examples",
    "contrib_openapi_fill_missing_examples",
    help="Enable generation of random examples for API operations that do not have explicit examples",
    is_flag=True,
    default=False,
    show_default=True,
)
@grouped_option(
    "--target",
    "-t",
    "included_target_names",
    multiple=True,
    help="Guide input generation to values more likely to expose bugs via targeted property-based testing",
    type=RegistryChoice(TARGETS),
    default=None,
    callback=validation.convert_checks,
    show_default=True,
    metavar="",
)
@group("Open API options")
@grouped_option(
    "--set-query",
    "set_query",
    help=r"OpenAPI: Override a specific query parameter by specifying 'parameter=value'",
    multiple=True,
    type=str,
    callback=validation.validate_set_query,
)
@grouped_option(
    "--set-header",
    "set_header",
    help=r"OpenAPI: Override a specific header parameter by specifying 'parameter=value'",
    multiple=True,
    type=str,
    callback=validation.validate_set_header,
)
@grouped_option(
    "--set-cookie",
    "set_cookie",
    help=r"OpenAPI: Override a specific cookie parameter by specifying 'parameter=value'",
    multiple=True,
    type=str,
    callback=validation.validate_set_cookie,
)
@grouped_option(
    "--set-path",
    "set_path",
    help=r"OpenAPI: Override a specific path parameter by specifying 'parameter=value'",
    multiple=True,
    type=str,
    callback=validation.validate_set_path,
)
@group("Hypothesis engine options")
@grouped_option(
    "--hypothesis-database",
    help="Storage for examples discovered by Hypothesis. "
    f"Use 'none' to disable, '{HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER}' for temporary storage, "
    f"or specify a file path for persistent storage",
    type=str,
    callback=validation.validate_hypothesis_database,
)
@grouped_option(
    "--hypothesis-deadline",
    help="Time limit for each test case generated by Hypothesis, in milliseconds. "
    "Exceeding this limit will cause the test to fail",
    type=OptionalInt(1, 5 * 60 * 1000),
)
@grouped_option(
    "--hypothesis-derandomize",
    help="Enables deterministic mode in Hypothesis, which eliminates random variation between tests",
    is_flag=True,
    is_eager=True,
    default=None,
    show_default=True,
)
@grouped_option(
    "--hypothesis-max-examples",
    help="The cap on the number of examples generated by Hypothesis for each API operation",
    type=click.IntRange(1),
)
@grouped_option(
    "--hypothesis-phases",
    help="Testing phases to execute",
    type=CsvEnumChoice(Phase),
    metavar="",
)
@grouped_option(
    "--hypothesis-no-phases",
    help="Testing phases to exclude from execution",
    type=CsvEnumChoice(Phase),
    metavar="",
)
@grouped_option(
    "--hypothesis-report-multiple-bugs",
    help="Report only the most easily reproducible error when multiple issues are found",
    type=bool,
)
@grouped_option(
    "--hypothesis-seed",
    help="Seed value for Hypothesis, ensuring reproducibility across test runs",
    type=int,
)
@grouped_option(
    "--hypothesis-suppress-health-check",
    help="A comma-separated list of Hypothesis health checks to disable",
    type=CsvEnumChoice(HealthCheck),
    metavar="",
)
@grouped_option(
    "--hypothesis-verbosity",
    help="Verbosity level of Hypothesis output",
    type=click.Choice([item.name for item in Verbosity]),
    callback=validation.convert_verbosity,
    metavar="",
)
@group("Schemathesis.io options")
@grouped_option(
    "--report",
    "report_value",
    help="""Specify how the generated report should be handled.
If used without an argument, the report data will automatically be uploaded to Schemathesis.io.
If a file name is provided, the report will be stored in that file.
The report data, consisting of a tar gz file with multiple JSON files, is subject to change""",
    is_flag=False,
    flag_value="",
    envvar=service.REPORT_ENV_VAR,
    callback=validation.convert_report,  # type: ignore
)
@grouped_option(
    "--schemathesis-io-token",
    help="Schemathesis.io authentication token",
    type=str,
    envvar=service.TOKEN_ENV_VAR,
)
@grouped_option(
    "--schemathesis-io-url",
    help="Schemathesis.io base URL",
    default=service.DEFAULT_URL,
    type=str,
    envvar=service.URL_ENV_VAR,
)
@grouped_option(
    "--schemathesis-io-telemetry",
    help="Whether to send anonymized usage data to Schemathesis.io along with your report",
    type=str,
    default="true",
    show_default=True,
    callback=validation.convert_boolean_string,
    envvar=service.TELEMETRY_ENV_VAR,
)
@with_hosts_file
@group("Global options")
@grouped_option("--no-color", help="Disable ANSI color escape codes", type=bool, is_flag=True)
@grouped_option("--force-color", help="Explicitly tells to enable ANSI color escape codes", type=bool, is_flag=True)
@click.pass_context
def run(
    ctx: click.Context,
    schema: str,
    api_name: str | None,
    auth: tuple[str, str] | None,
    headers: dict[str, str],
    set_query: dict[str, str],
    set_header: dict[str, str],
    set_cookie: dict[str, str],
    set_path: dict[str, str],
    experiments: list,
    missing_required_header_allowed_statuses: list[str],
    positive_data_acceptance_allowed_statuses: list[str],
    negative_data_rejection_allowed_statuses: list[str],
    included_check_names: Sequence[str],
    excluded_check_names: Sequence[str],
    data_generation_methods: tuple[DataGenerationMethod, ...] = DEFAULT_DATA_GENERATION_METHODS,
    max_response_time: float | None = None,
    included_target_names: Sequence[str] | None = None,
    exit_first: bool = False,
    max_failures: int | None = None,
    dry_run: bool = False,
    include_path: Sequence[str] = (),
    include_path_regex: str | None = None,
    include_method: Sequence[str] = (),
    include_method_regex: str | None = None,
    include_name: Sequence[str] = (),
    include_name_regex: str | None = None,
    include_tag: Sequence[str] = (),
    include_tag_regex: str | None = None,
    include_operation_id: Sequence[str] = (),
    include_operation_id_regex: str | None = None,
    exclude_path: Sequence[str] = (),
    exclude_path_regex: str | None = None,
    exclude_method: Sequence[str] = (),
    exclude_method_regex: str | None = None,
    exclude_name: Sequence[str] = (),
    exclude_name_regex: str | None = None,
    exclude_tag: Sequence[str] = (),
    exclude_tag_regex: str | None = None,
    exclude_operation_id: Sequence[str] = (),
    exclude_operation_id_regex: str | None = None,
    include_by: str | None = None,
    exclude_by: str | None = None,
    exclude_deprecated: bool = False,
    workers_num: int = DEFAULT_WORKERS,
    base_url: str | None = None,
    request_timeout: int | None = None,
    request_tls_verify: bool = True,
    request_cert: str | None = None,
    request_cert_key: str | None = None,
    request_proxy: str | None = None,
    junit_xml: click.utils.LazyFile | None = None,
    debug_output_file: click.utils.LazyFile | None = None,
    cassette_path: click.utils.LazyFile | None = None,
    cassette_format: cassettes.CassetteFormat = cassettes.CassetteFormat.VCR,
    cassette_preserve_exact_body_bytes: bool = False,
    wait_for_schema: float | None = None,
    rate_limit: str | None = None,
    stateful: Stateful | None = None,
    sanitize_output: bool = True,
    output_truncate: bool = True,
    contrib_unique_data: bool = False,
    contrib_openapi_fill_missing_examples: bool = False,
    hypothesis_database: str | None = None,
    hypothesis_deadline: int | NotSet | None = None,
    hypothesis_derandomize: bool | None = None,
    hypothesis_max_examples: int | None = None,
    hypothesis_phases: list[Phase] | None = None,
    hypothesis_no_phases: list[Phase] | None = None,
    hypothesis_report_multiple_bugs: bool | None = None,
    hypothesis_suppress_health_check: list[HealthCheck] | None = None,
    hypothesis_seed: int | None = None,
    hypothesis_verbosity: hypothesis.Verbosity | None = None,
    no_color: bool = False,
    report_value: str | None = None,
    generation_allow_x00: bool = True,
    generation_graphql_allow_null: bool = True,
    generation_with_security_parameters: bool = True,
    generation_codec: str = "utf-8",
    schemathesis_io_token: str | None = None,
    schemathesis_io_url: str = service.DEFAULT_URL,
    schemathesis_io_telemetry: bool = True,
    hosts_file: os.PathLike = service.DEFAULT_HOSTS_PATH,
    force_color: bool = False,
    **__kwargs,
) -> None:
    """Run tests against an API using a specified SCHEMA.

    [Required] SCHEMA: Path to an OpenAPI (`.json`, `.yml`) or GraphQL SDL file, or a URL pointing to such specifications

    [Optional] API_NAME: Identifier for uploading test data to Schemathesis.io
    """
    _hypothesis_phases: list[hypothesis.Phase] | None = None
    if hypothesis_phases is not None:
        _hypothesis_phases = [phase.as_hypothesis() for phase in hypothesis_phases]
        if hypothesis_no_phases is not None:
            raise click.UsageError(PHASES_INVALID_USAGE_MESSAGE)
    if hypothesis_no_phases is not None:
        _hypothesis_phases = Phase.filter_from_all(hypothesis_no_phases)
    _hypothesis_suppress_health_check: list[hypothesis.HealthCheck] | None = None
    if hypothesis_suppress_health_check is not None:
        _hypothesis_suppress_health_check = [
            entry for health_check in hypothesis_suppress_health_check for entry in health_check.as_hypothesis()
        ]

    # Enable selected experiments
    for experiment in experiments:
        experiment.enable()

    cassette_config = None
    if cassette_path is not None:
        cassette_config = cassettes.CassetteConfig(
            path=cassette_path,
            format=cassette_format,
            sanitize_output=sanitize_output,
            preserve_exact_body_bytes=cassette_preserve_exact_body_bytes,
        )
    override = CaseOverride(query=set_query, headers=set_header, cookies=set_cookie, path_parameters=set_path)

    generation_config = generation.GenerationConfig(
        methods=list(data_generation_methods),
        allow_x00=generation_allow_x00,
        graphql_allow_null=generation_graphql_allow_null,
        codec=generation_codec,
        with_security_parameters=generation_with_security_parameters,
    )

    report: ReportToService | click.utils.LazyFile | None
    if report_value is None:
        report = None
    elif report_value:
        report = click.utils.LazyFile(report_value, mode="wb")
    else:
        report = REPORT_TO_SERVICE
    started_at = datetime.now(timezone.utc).astimezone().isoformat()

    if no_color and force_color:
        raise click.UsageError(COLOR_OPTIONS_INVALID_USAGE_MESSAGE)
    decide_color_output(ctx, no_color, force_color)

    validation.validate_auth_overlap(auth, headers, override)
    selected_targets = TARGETS.get_by_names(included_target_names or [])

    for values, arg_name in (
        (include_path, "--include-path"),
        (include_method, "--include-method"),
        (include_name, "--include-name"),
        (include_tag, "--include-tag"),
        (include_operation_id, "--include-operation-id"),
        (exclude_path, "--exclude-path"),
        (exclude_method, "--exclude-method"),
        (exclude_name, "--exclude-name"),
        (exclude_tag, "--exclude-tag"),
        (exclude_operation_id, "--exclude-operation-id"),
    ):
        validation.validate_unique_filter(values, arg_name)
    include_by_function = _filter_by_expression_to_func(include_by, "--include-by")
    exclude_by_function = _filter_by_expression_to_func(exclude_by, "--exclude-by")

    filter_set = FilterSet()
    if include_by_function:
        filter_set.include(include_by_function)
    for name_ in include_name:
        filter_set.include(name=name_)
    for method in include_method:
        filter_set.include(method=method)
    for path in include_path:
        filter_set.include(path=path)
    for tag in include_tag:
        filter_set.include(tag=tag)
    for operation_id in include_operation_id:
        filter_set.include(operation_id=operation_id)
    if (
        include_name_regex
        or include_method_regex
        or include_path_regex
        or include_tag_regex
        or include_operation_id_regex
    ):
        filter_set.include(
            name_regex=include_name_regex,
            method_regex=include_method_regex,
            path_regex=include_path_regex,
            tag_regex=include_tag_regex,
            operation_id_regex=include_operation_id_regex,
        )
    if exclude_by_function:
        filter_set.exclude(exclude_by_function)
    for name_ in exclude_name:
        filter_set.exclude(name=name_)
    for method in exclude_method:
        filter_set.exclude(method=method)
    for path in exclude_path:
        filter_set.exclude(path=path)
    for tag in exclude_tag:
        filter_set.exclude(tag=tag)
    for operation_id in exclude_operation_id:
        filter_set.exclude(operation_id=operation_id)
    if (
        exclude_name_regex
        or exclude_method_regex
        or exclude_path_regex
        or exclude_tag_regex
        or exclude_operation_id_regex
    ):
        filter_set.exclude(
            name_regex=exclude_name_regex,
            method_regex=exclude_method_regex,
            path_regex=exclude_path_regex,
            tag_regex=exclude_tag_regex,
            operation_id_regex=exclude_operation_id_regex,
        )
    if exclude_deprecated:
        filter_set.exclude(is_deprecated)

    schemathesis_io_hostname = urlparse(schemathesis_io_url).netloc
    token = schemathesis_io_token or service.hosts.get_token(hostname=schemathesis_io_hostname, hosts_file=hosts_file)
    schema_kind = validation.parse_schema_kind(schema)
    validation.validate_schema(schema, schema_kind, base_url=base_url, dry_run=dry_run, api_name=api_name)
    client = None
    schema_or_location: str | dict[str, Any] = schema
    if schema_kind == validation.SchemaInputKind.NAME:
        api_name = schema
    if (
        not isinstance(report, click.utils.LazyFile)
        and api_name is not None
        and schema_kind == validation.SchemaInputKind.NAME
    ):
        from ..service.client import ServiceClient

        client = ServiceClient(base_url=schemathesis_io_url, token=token)
        # It is assigned above
        if token is not None or schema_kind == validation.SchemaInputKind.NAME:
            if token is None:
                hostname = (
                    "Schemathesis.io"
                    if schemathesis_io_hostname == service.DEFAULT_HOSTNAME
                    else schemathesis_io_hostname
                )
                click.secho(f"Missing authentication for {hostname} upload", bold=True, fg="red")
                click.echo(
                    f"\nYou've specified an API name, suggesting you want to upload data to {bold(hostname)}. "
                    "However, your CLI is not currently authenticated."
                )
                output.default.display_service_unauthorized(hostname)
                raise click.exceptions.Exit(1) from None
            name: str = cast(str, api_name)
            import requests

            try:
                details = client.get_api_details(name)
                # Replace config values with ones loaded from the service
                schema_or_location = details.specification.schema
                default_environment = details.default_environment
                base_url = base_url or (default_environment.url if default_environment else None)
            except requests.HTTPError as exc:
                handle_service_error(exc, name)
    if report is REPORT_TO_SERVICE and not client:
        from ..service.client import ServiceClient

        # Upload without connecting data to a certain API
        client = ServiceClient(base_url=schemathesis_io_url, token=token)
    if experimental.SCHEMA_ANALYSIS.is_enabled and not client:
        from ..service.client import ServiceClient

        client = ServiceClient(base_url=schemathesis_io_url, token=token)
    host_data = service.hosts.HostData(schemathesis_io_hostname, hosts_file)
    report_config = service.ReportConfig(
        api_name=api_name,
        location=schema,
        base_url=base_url,
        started_at=started_at,
        telemetry=schemathesis_io_telemetry,
    )

    if "all" in included_check_names:
        selected_checks = CHECKS.get_all()
    else:
        selected_checks = CHECKS.get_by_names(included_check_names or [])

    checks_config: ChecksConfig = {}
    if experimental.POSITIVE_DATA_ACCEPTANCE.is_enabled:
        from schemathesis.openapi.checks import PositiveDataAcceptanceConfig
        from schemathesis.specs.openapi.checks import positive_data_acceptance

        selected_checks.append(positive_data_acceptance)
        if positive_data_acceptance_allowed_statuses:
            checks_config[positive_data_acceptance] = PositiveDataAcceptanceConfig(
                allowed_statuses=positive_data_acceptance_allowed_statuses
            )
    if missing_required_header_allowed_statuses:
        from schemathesis.openapi.checks import MissingRequiredHeaderConfig
        from schemathesis.specs.openapi.checks import missing_required_header

        selected_checks.append(missing_required_header)
        checks_config[missing_required_header] = MissingRequiredHeaderConfig(
            allowed_statuses=missing_required_header_allowed_statuses
        )
    if negative_data_rejection_allowed_statuses:
        from schemathesis.openapi.checks import NegativeDataRejectionConfig
        from schemathesis.specs.openapi.checks import negative_data_rejection

        checks_config[negative_data_rejection] = NegativeDataRejectionConfig(
            allowed_statuses=negative_data_rejection_allowed_statuses
        )
    if max_response_time is not None:
        from schemathesis.checks import max_response_time as _max_response_time
        from schemathesis.core.failures import MaxResponseTimeConfig

        checks_config[_max_response_time] = MaxResponseTimeConfig(max_response_time)
        selected_checks.append(_max_response_time)

    selected_checks = [check for check in selected_checks if check.__name__ not in excluded_check_names]

    if contrib_openapi_fill_missing_examples:
        contrib.openapi.fill_missing_examples.install()

    hypothesis_settings = settings.prepare(
        database=hypothesis_database,
        deadline=hypothesis_deadline,
        derandomize=hypothesis_derandomize,
        max_examples=hypothesis_max_examples,
        phases=_hypothesis_phases,
        report_multiple_bugs=hypothesis_report_multiple_bugs,
        suppress_health_check=_hypothesis_suppress_health_check,
        verbosity=hypothesis_verbosity,
    )
    if exit_first:
        max_failures = 1
    network_config = NetworkConfig(
        auth=auth,
        headers=headers,
        timeout=request_timeout,
        tls_verify=request_tls_verify,
        proxy=request_proxy,
        cert=prepare_request_cert(request_cert, request_cert_key),
    )
    output_config = OutputConfig(sanitize=sanitize_output, truncate=output_truncate)
    loader_config = loaders.AutodetectConfig(
        schema_or_location=schema_or_location,
        network=network_config,
        wait_for_schema=wait_for_schema,
        base_url=base_url,
        rate_limit=rate_limit,
        output=output_config,
        generation=generation_config,
    )
    event_stream = into_event_stream(
        network_config=network_config,
        override=override,
        seed=hypothesis_seed,
        max_failures=max_failures,
        unique_data=contrib_unique_data,
        dry_run=dry_run,
        checks=selected_checks,
        targets=selected_targets,
        workers_num=workers_num,
        stateful=stateful,
        hypothesis_settings=hypothesis_settings,
        generation_config=generation_config,
        checks_config=checks_config,
        loader_config=loader_config,
        service_client=client,
        filter_set=filter_set,
    )
    execute(
        event_stream,
        ctx=ctx,
        hypothesis_settings=hypothesis_settings,
        workers_num=workers_num,
        rate_limit=rate_limit,
        wait_for_schema=wait_for_schema,
        cassette_config=cassette_config,
        junit_xml=junit_xml,
        debug_output_file=debug_output_file,
        client=client,
        report=report,
        host_data=host_data,
        report_config=report_config,
        output_config=output_config,
    )


def _filter_by_expression_to_func(value: str | None, arg_name: str) -> Callable | None:
    if value:
        try:
            return expression_to_filter_function(value)
        except ValueError:
            raise click.UsageError(f"Invalid expression for {arg_name}: {value}") from None
    return None


def prepare_request_cert(cert: str | None, key: str | None) -> str | tuple[str, str] | None:
    if cert is not None and key is not None:
        return cert, key
    return cert


def into_event_stream(
    *,
    network_config: NetworkConfig,
    override: CaseOverride,
    filter_set: FilterSet,
    checks: list[CheckFunction],
    checks_config: ChecksConfig,
    targets: list[TargetFunction],
    workers_num: int,
    hypothesis_settings: hypothesis.settings | None,
    generation_config: generation.GenerationConfig,
    seed: int | None,
    max_failures: int | None,
    unique_data: bool,
    dry_run: bool,
    stateful: Stateful | None,
    service_client: ServiceClient | None,
    loader_config: loaders.AutodetectConfig,
) -> events.EventGenerator:
    try:
        schema = loaders.load_schema(loader_config)
        schema.filter_set = filter_set
    except LoaderError as error:
        yield events.InternalError.from_schema_error(error)
        return

    try:
        yield from runner.from_schema(
            schema,
            override=override,
            seed=seed,
            max_failures=max_failures,
            unique_data=unique_data,
            dry_run=dry_run,
            checks=checks,
            checks_config=checks_config,
            targets=targets,
            workers_num=workers_num,
            stateful=stateful,
            hypothesis_settings=hypothesis_settings,
            generation_config=generation_config,
            network=network_config,
            service_client=service_client,
        ).execute()
    except Exception as exc:
        yield events.InternalError.from_exc(exc)


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
        click.secho("Unable to load Schemathesis extension hooks", fg="red", bold=True)
        formatted_module_name = bold(f"'{module_name}'")
        if isinstance(exc, ModuleNotFoundError) and exc.name == module_name:
            click.echo(
                f"\nAn attempt to import the module {formatted_module_name} failed because it could not be found."
            )
            click.echo("\nEnsure the module name is correctly spelled and reachable from the current directory.")
        else:
            click.echo(f"\nAn error occurred while importing the module {formatted_module_name}. Traceback:")
            message = format_exception(exc, with_traceback=True, skip_frames=1)
            click.secho(f"\n{message}", fg="red")
        click.echo(f"\nFor more information on how to work with hooks, visit {EXTENSIONS_DOCUMENTATION_URL}")
        raise click.exceptions.Exit(1) from None


class OutputStyle(Enum):
    """Provide different output styles."""

    default = output.default.DefaultOutputStyleHandler
    short = output.short.ShortOutputStyleHandler


def execute(
    event_stream: events.EventGenerator,
    *,
    ctx: click.Context,
    hypothesis_settings: hypothesis.settings,
    workers_num: int,
    rate_limit: str | None,
    wait_for_schema: float | None,
    cassette_config: cassettes.CassetteConfig | None,
    junit_xml: click.utils.LazyFile | None,
    debug_output_file: click.utils.LazyFile | None,
    client: ServiceClient | None,
    report: ReportToService | click.utils.LazyFile | None,
    host_data: service.hosts.HostData,
    report_config: service.ReportConfig,
    output_config: OutputConfig,
) -> None:
    """Execute a prepared runner by drawing events from it and passing to a proper handler."""
    handlers: list[EventHandler] = []
    report_context: ServiceReportContext | FileReportContext | None = None
    report_queue: Queue
    if client:
        report_queue = Queue()
        report_context = ServiceReportContext(queue=report_queue, service_base_url=client.base_url)
        handlers.append(
            service.ServiceReportHandler(
                client=client, host_data=host_data, config=report_config, out_queue=report_queue
            )
        )
    elif isinstance(report, click.utils.LazyFile):
        _open_file(report)
        report_queue = Queue()
        report_context = FileReportContext(queue=report_queue, filename=report.name)
        handlers.append(service.FileReportHandler(file_handle=report, config=report_config, out_queue=report_queue))
    if junit_xml is not None:
        _open_file(junit_xml)
        handlers.append(JunitXMLHandler(junit_xml))
    if debug_output_file is not None:
        _open_file(debug_output_file)
        handlers.append(DebugOutputHandler(debug_output_file))
    if cassette_config is not None:
        # This handler should be first to have logs writing completed when the output handler will display statistic
        _open_file(cassette_config.path)
        handlers.append(cassettes.CassetteWriter(config=cassette_config))
    for custom_handler in CUSTOM_HANDLERS:
        handlers.append(custom_handler(*ctx.args, **ctx.params))
    handlers.append(get_output_handler(workers_num))
    execution_context = ExecutionContext(
        hypothesis_settings=hypothesis_settings,
        workers_num=workers_num,
        rate_limit=rate_limit,
        wait_for_schema=wait_for_schema,
        cassette_path=cassette_config.path.name if cassette_config is not None else None,
        junit_xml_file=junit_xml.name if junit_xml is not None else None,
        report=report_context,
        output_config=output_config,
    )

    def shutdown() -> None:
        for _handler in handlers:
            _handler.shutdown()

    event = None
    try:
        for event in event_stream:
            for handler in handlers:
                try:
                    handler.handle_event(execution_context, event)
                except Exception as exc:
                    # `Abort` is used for handled errors
                    if not isinstance(exc, click.Abort):
                        display_handler_error(handler, exc)
                    raise
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


def _open_file(file: click.utils.LazyFile) -> None:
    try:
        ensure_parent(file.name, fail_silently=False)
    except OSError as exc:
        raise click.BadParameter(f"'{file.name}': {exc.strerror}") from exc
    try:
        file.open()
    except click.FileError as exc:
        raise click.BadParameter(exc.format_message()) from exc


def is_built_in_handler(handler: EventHandler) -> bool:
    # Look for exact instances, not subclasses
    return any(
        type(handler) is class_
        for class_ in (
            output.default.DefaultOutputStyleHandler,
            output.short.ShortOutputStyleHandler,
            service.FileReportHandler,
            service.ServiceReportHandler,
            DebugOutputHandler,
            cassettes.CassetteWriter,
            JunitXMLHandler,
        )
    )


def display_handler_error(handler: EventHandler, exc: Exception) -> None:
    """Display error that happened within."""
    is_built_in = is_built_in_handler(handler)
    if is_built_in:
        click.secho("Internal Error", fg="red", bold=True)
        click.secho("\nSchemathesis encountered an unexpected issue.")
        message = format_exception(exc, with_traceback=True)
    else:
        click.secho("CLI Handler Error", fg="red", bold=True)
        click.echo(f"\nAn error occurred within your custom CLI handler `{bold(handler.__class__.__name__)}`.")
        message = format_exception(exc, with_traceback=True, skip_frames=1)
    click.secho(f"\n{message}", fg="red")
    if is_built_in:
        click.echo(
            f"\nWe apologize for the inconvenience. This appears to be an internal issue.\n"
            f"Please consider reporting this error to our issue tracker:\n\n  {ISSUE_TRACKER_URL}."
        )
    else:
        click.echo(
            f"\nFor more information on implementing extensions for Schemathesis CLI, visit {EXTENSIONS_DOCUMENTATION_URL}"
        )


def handle_service_error(exc: requests.HTTPError, api_name: str) -> NoReturn:
    import requests

    response = cast(requests.Response, exc.response)
    if response.status_code == 403:
        error_message(response.json()["detail"])
    elif response.status_code == 404:
        error_message(f"API with name `{api_name}` not found!")
    else:
        output.default.display_service_error(service.Error(exc), message_prefix="❌ ")
    sys.exit(1)


def get_exit_code(event: events.ExecutionEvent) -> int:
    if isinstance(event, events.Finished):
        if event.results.has_failures or event.results.has_errors:
            return 1
        return 0
    # Practically not possible. May occur only if the output handler is broken - in this case we still will have the
    # right exit code.
    return 1


@schemathesis.command(short_help="Replay requests from a saved cassette.")
@click.argument("cassette_path", type=click.Path(exists=True))
@click.option("--id", "id_", help="ID of interaction to replay", type=str)
@click.option("--status", help="Status of interactions to replay", type=str)
@click.option("--uri", help="A regexp that filters interactions by their request URI", type=str)
@click.option("--method", help="A regexp that filters interactions by their request method", type=str)
@click.option("--no-color", help="Disable ANSI color escape codes", type=bool, is_flag=True)
@click.option("--force-color", help="Explicitly tells to enable ANSI color escape codes", type=bool, is_flag=True)
@click.option("--verbosity", "-v", help="Increase verbosity of the output", count=True)
@with_request_tls_verify
@with_request_proxy
@with_request_cert
@with_request_cert_key
@click.pass_context
def replay(
    ctx: click.Context,
    cassette_path: str,
    id_: str | None,
    status: str | None = None,
    uri: str | None = None,
    method: str | None = None,
    no_color: bool = False,
    verbosity: int = 0,
    request_tls_verify: bool = True,
    request_cert: str | None = None,
    request_cert_key: str | None = None,
    request_proxy: str | None = None,
    force_color: bool = False,
) -> None:
    """Replay a cassette.

    Cassettes in VCR-compatible format can be replayed.
    For example, ones that are recorded with the ``--cassette-path`` option of the `st run` command.
    """
    if no_color and force_color:
        raise click.UsageError(COLOR_OPTIONS_INVALID_USAGE_MESSAGE)
    decide_color_output(ctx, no_color, force_color)

    click.secho(f"{bold('Replaying cassette')}: {cassette_path}")
    with open(cassette_path, "rb") as fd:
        cassette = deserialize_yaml(fd)
    click.secho(f"{bold('Total interactions')}: {len(cassette['http_interactions'])}\n")
    for replayed in cassettes.replay(
        cassette,
        id_=id_,
        status=status,
        uri=uri,
        method=method,
        request_tls_verify=request_tls_verify,
        request_cert=prepare_request_cert(request_cert, request_cert_key),
        request_proxy=request_proxy,
    ):
        click.secho(f"  {bold('ID')}              : {replayed.interaction['id']}")
        click.secho(f"  {bold('URI')}             : {replayed.interaction['request']['uri']}")
        click.secho(f"  {bold('Old status code')} : {replayed.interaction['response']['status']['code']}")
        click.secho(f"  {bold('New status code')} : {replayed.response.status_code}")
        if verbosity > 0:
            data = replayed.interaction["response"]
            old_body = ""
            # Body may be missing for 204 responses
            if "body" in data:
                if "base64_string" in data["body"]:
                    content = data["body"]["base64_string"]
                    if content:
                        old_body = base64.b64decode(content).decode(errors="replace")
                else:
                    old_body = data["body"]["string"]
            click.secho(f"  {bold('Old payload')} : {old_body}")
            click.secho(f"  {bold('New payload')} : {replayed.response.text}")
        click.echo()


@schemathesis.command(short_help="Upload report to Schemathesis.io.")
@click.argument("report", type=click.File(mode="rb"))
@click.option(
    "--schemathesis-io-token",
    help="Schemathesis.io authentication token",
    type=str,
    envvar=service.TOKEN_ENV_VAR,
)
@click.option(
    "--schemathesis-io-url",
    help="Schemathesis.io base URL",
    default=service.DEFAULT_URL,
    type=str,
    envvar=service.URL_ENV_VAR,
)
@with_request_tls_verify
@with_hosts_file
def upload(
    report: io.BufferedReader,
    hosts_file: os.PathLike,
    request_tls_verify: bool = True,
    schemathesis_io_url: str = service.DEFAULT_URL,
    schemathesis_io_token: str | None = None,
) -> None:
    """Upload report to Schemathesis.io."""
    from ..service.client import ServiceClient
    from ..service.models import UploadResponse, UploadSource

    schemathesis_io_hostname = urlparse(schemathesis_io_url).netloc
    host_data = service.hosts.HostData(schemathesis_io_hostname, hosts_file)
    token = schemathesis_io_token or service.hosts.get_token(hostname=schemathesis_io_hostname, hosts_file=hosts_file)
    client = ServiceClient(base_url=schemathesis_io_url, token=token, verify=request_tls_verify)
    ci_environment = service.ci.environment()
    provider = ci_environment.provider if ci_environment is not None else None
    response = client.upload_report(
        report=report.read(),
        correlation_id=host_data.correlation_id,
        ci_provider=provider,
        source=UploadSource.UPLOAD_COMMAND,
    )
    if isinstance(response, UploadResponse):
        host_data.store_correlation_id(response.correlation_id)
        click.echo(f"{response.message}\n{response.next_url}")
    else:
        error_message(f"Failed to upload report to {schemathesis_io_hostname}: " + bold(response.detail))
        sys.exit(1)


@schemathesis.group(short_help="Authenticate with Schemathesis.io.")
def auth() -> None:
    pass


@auth.command(short_help="Authenticate with a Schemathesis.io host.")
@click.argument("token", type=str, envvar=service.TOKEN_ENV_VAR)
@click.option(
    "--hostname",
    help="The hostname of the Schemathesis.io instance to authenticate with",
    type=str,
    default=service.DEFAULT_HOSTNAME,
    envvar=service.HOSTNAME_ENV_VAR,
)
@click.option(
    "--protocol",
    type=click.Choice(["https", "http"]),
    default=service.DEFAULT_PROTOCOL,
    envvar=service.PROTOCOL_ENV_VAR,
)
@with_request_tls_verify
@with_hosts_file
def login(token: str, hostname: str, hosts_file: os.PathLike, protocol: str, request_tls_verify: bool = True) -> None:
    """Authenticate with a Schemathesis.io host."""
    import requests

    try:
        username = service.auth.login(token, hostname, protocol, request_tls_verify)
        service.hosts.store(token, hostname, hosts_file)
        success_message(f"Logged in into {hostname} as " + bold(username))
    except requests.HTTPError as exc:
        response = cast(requests.Response, exc.response)
        detail = response.json()["detail"]
        error_message(f"Failed to login into {hostname}: " + bold(detail))
        sys.exit(1)


@auth.command(short_help="Remove authentication for a Schemathesis.io host.")
@click.option(
    "--hostname",
    help="The hostname of the Schemathesis.io instance to authenticate with",
    type=str,
    default=service.DEFAULT_HOSTNAME,
    envvar=service.HOSTNAME_ENV_VAR,
)
@with_hosts_file
def logout(hostname: str, hosts_file: os.PathLike) -> None:
    """Remove authentication for a Schemathesis.io host."""
    result = service.hosts.remove(hostname, hosts_file)
    if result == service.hosts.RemoveAuth.success:
        success_message(f"Logged out of {hostname} account")
    else:
        if result == service.hosts.RemoveAuth.no_match:
            warning_message(f"Not logged in to {hostname}")
        if result == service.hosts.RemoveAuth.no_hosts:
            warning_message("Not logged in to any hosts")
        if result == service.hosts.RemoveAuth.error:
            error_message(f"Failed to read the hosts file. Try to remove {hosts_file}")
        sys.exit(1)


def success_message(message: str) -> None:
    click.secho(click.style("✔️", fg="green") + f" {message}")


def warning_message(message: str) -> None:
    click.secho(click.style("🟡️", fg="yellow") + f" {message}")


def error_message(message: str) -> None:
    click.secho(f"❌ {message}")


def bold(message: str) -> str:
    return click.style(message, bold=True)


def decide_color_output(ctx: click.Context, no_color: bool, force_color: bool) -> None:
    if force_color:
        ctx.color = True
    elif no_color or "NO_COLOR" in os.environ:
        ctx.color = False


def add_option(*args: Any, cls: type = click.Option, **kwargs: Any) -> None:
    """Add a new CLI option to `st run`."""
    run.params.append(cls(args, **kwargs))


@dataclass
class Group:
    name: str

    def add_option(self, *args: Any, **kwargs: Any) -> None:
        kwargs["cls"] = GroupedOption
        kwargs["group"] = self.name
        add_option(*args, **kwargs)


def add_group(name: str, *, index: int | None = None) -> Group:
    """Add a custom options group to `st run`."""
    if index is not None:
        GROUPS.insert(index, name)
    else:
        GROUPS.append(name)
    return Group(name)


def handler() -> Callable[[type], None]:
    """Register a new CLI event handler."""

    def _wrapper(cls: type) -> None:
        CUSTOM_HANDLERS.append(cls)

    return _wrapper
