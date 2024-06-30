from __future__ import annotations

import base64
import enum
import io
import os
import sys
import traceback
import warnings
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from queue import Queue
from typing import TYPE_CHECKING, Any, Callable, Generator, Iterable, NoReturn, cast
from urllib.parse import urlparse

import click

from .. import checks as checks_module
from .. import contrib, experimental, generation, runner, service
from .. import fixups as _fixups
from .. import targets as targets_module
from .._override import CaseOverride
from ..code_samples import CodeSampleStyle
from ..constants import (
    API_NAME_ENV_VAR,
    BASE_URL_ENV_VAR,
    DEFAULT_RESPONSE_TIMEOUT,
    DEFAULT_STATEFUL_RECURSION_LIMIT,
    EXTENSIONS_DOCUMENTATION_URL,
    HOOKS_MODULE_ENV_VAR,
    HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER,
    ISSUE_TRACKER_URL,
    WAIT_FOR_SCHEMA_ENV_VAR,
)
from ..exceptions import SchemaError, SchemaErrorType, extract_nth_traceback
from ..fixups import ALL_FIXUPS
from ..generation import DEFAULT_DATA_GENERATION_METHODS, DataGenerationMethod
from ..hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher, HookScope
from ..internal.datetime import current_datetime
from ..internal.output import OutputConfig
from ..internal.validation import file_exists
from ..loaders import load_app, load_yaml
from ..models import Case, CheckFunction
from ..runner import events, prepare_hypothesis_settings, probes
from ..specs.graphql import loaders as gql_loaders
from ..specs.openapi import loaders as oas_loaders
from ..stateful import Stateful
from ..targets import Target
from ..transports.auth import get_requests_auth
from ..types import Filter, PathLike, RequestCert
from . import callbacks, cassettes, output
from .constants import DEFAULT_WORKERS, MAX_WORKERS, MIN_WORKERS, HealthCheck, Phase, Verbosity
from .context import ExecutionContext, FileReportContext, ServiceReportContext
from .debug import DebugOutputHandler
from .junitxml import JunitXMLHandler
from .options import CsvChoice, CsvEnumChoice, CustomHelpMessageChoice, NotSet, OptionalInt
from .sanitization import SanitizationHandler

if TYPE_CHECKING:
    import hypothesis
    import requests

    from ..schemas import BaseSchema
    from ..service.client import ServiceClient
    from ..specs.graphql.schemas import GraphQLSchema
    from .handlers import EventHandler


def _get_callable_names(items: tuple[Callable, ...]) -> tuple[str, ...]:
    return tuple(item.__name__ for item in items)


CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

DEFAULT_CHECKS_NAMES = _get_callable_names(checks_module.DEFAULT_CHECKS)
ALL_CHECKS_NAMES = _get_callable_names(checks_module.ALL_CHECKS)
CHECKS_TYPE = CsvChoice((*ALL_CHECKS_NAMES, "all"))
EXCLUDE_CHECKS_TYPE = CsvChoice((*ALL_CHECKS_NAMES,))

DEFAULT_TARGETS_NAMES = _get_callable_names(targets_module.DEFAULT_TARGETS)
ALL_TARGETS_NAMES = _get_callable_names(targets_module.ALL_TARGETS)
TARGETS_TYPE = click.Choice((*ALL_TARGETS_NAMES, "all"))

DATA_GENERATION_METHOD_TYPE = click.Choice([item.name for item in DataGenerationMethod] + ["all"])

DEPRECATED_CASSETTE_PATH_OPTION_WARNING = (
    "Warning: Option `--store-network-log` is deprecated and will be removed in Schemathesis 4.0. "
    "Use `--cassette-path` instead."
)
DEPRECATED_PRE_RUN_OPTION_WARNING = (
    "Warning: Option `--pre-run` is deprecated and will be removed in Schemathesis 4.0. "
    f"Use the `{HOOKS_MODULE_ENV_VAR}` environment variable instead"
)
DEPRECATED_SHOW_ERROR_TRACEBACKS_OPTION_WARNING = (
    "Warning: Option `--show-errors-tracebacks` is deprecated and will be removed in Schemathesis 4.0. "
    "Use `--show-trace` instead"
)
DEPRECATED_CONTRIB_UNIQUE_DATA_OPTION_WARNING = (
    "The `--contrib-unique-data` CLI option and the corresponding `schemathesis.contrib.unique_data` hook "
    "are **DEPRECATED**. The concept of this feature does not fit the core principles of Hypothesis where "
    "strategies are configurable on a per-example basis but this feature implies uniqueness across examples. "
    "This leads to cryptic error messages about external state and flaky test runs, "
    "therefore it will be removed in Schemathesis 4.0"
)
CASSETTES_PATH_INVALID_USAGE_MESSAGE = "Can't use `--store-network-log` and `--cassette-path` simultaneously"
COLOR_OPTIONS_INVALID_USAGE_MESSAGE = "Can't use `--no-color` and `--force-color` simultaneously"
PHASES_INVALID_USAGE_MESSAGE = "Can't use `--hypothesis-phases` and `--hypothesis-no-phases` simultaneously"


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


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option("--pre-run", help="A module to execute before running the tests.", type=str, hidden=True)
@click.version_option()
def schemathesis(pre_run: str | None = None) -> None:
    """Automated API testing employing fuzzing techniques for OpenAPI and GraphQL."""
    # Don't use `envvar=HOOKS_MODULE_ENV_VAR` arg to raise a deprecation warning for hooks
    hooks: str | None
    if pre_run:
        click.secho(DEPRECATED_PRE_RUN_OPTION_WARNING, fg="yellow")
        hooks = pre_run
    else:
        hooks = os.getenv(HOOKS_MODULE_ENV_VAR)
    if hooks:
        load_hook(hooks)


class ParameterGroup(enum.Enum):
    filtering = "Testing scope", "Customize the scope of the API testing."
    validation = "Response & Schema validation", "These options specify how API responses and schemas are validated."
    hypothesis = "Hypothesis engine", "Configuration of the underlying Hypothesis engine."
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
            title, description = group.value
            with formatter.section(title):
                if description:
                    formatter.write_paragraph()
                    formatter.write_text(description)
                    formatter.write_paragraph()
                formatter.write_dl(opts)


class GroupedOption(click.Option):
    def __init__(self, *args: Any, group: ParameterGroup, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.group = group


with_request_proxy = click.option(
    "--request-proxy",
    help="Set the proxy for all network requests.",
    type=str,
)
with_request_tls_verify = click.option(
    "--request-tls-verify",
    help="Configures TLS certificate verification for server requests. Can specify path to CA_BUNDLE for custom certs.",
    type=str,
    default="true",
    show_default=True,
    callback=callbacks.convert_boolean_string,
)
with_request_cert = click.option(
    "--request-cert",
    help="File path of unencrypted client certificate for authentication. "
    "The certificate can be bundled with a private key (e.g. PEM) or the private "
    "key can be provided with the --request-cert-key argument.",
    type=click.Path(exists=True),
    default=None,
    show_default=False,
)
with_request_cert_key = click.option(
    "--request-cert-key",
    help="Specifies the file path of the private key for the client certificate.",
    type=click.Path(exists=True),
    default=None,
    show_default=False,
    callback=callbacks.validate_request_cert_key,
)
with_hosts_file = click.option(
    "--hosts-file",
    help="Path to a file to store the Schemathesis.io auth configuration.",
    type=click.Path(dir_okay=False, writable=True),
    default=service.DEFAULT_HOSTS_PATH,
    envvar=service.HOSTS_PATH_ENV_VAR,
    callback=callbacks.convert_hosts_file,
)


class ReportToService:
    pass


REPORT_TO_SERVICE = ReportToService()


@schemathesis.command(short_help="Execute automated tests based on API specifications.", cls=CommandWithCustomHelp)
@click.argument("schema", type=str)
@click.argument("api_name", type=str, required=False, envvar=API_NAME_ENV_VAR)
@click.option(
    "--checks",
    "-c",
    multiple=True,
    help="Specifies the validation checks to apply to API responses. "
    "Provide a comma-separated list of checks such as 'not_a_server_error,status_code_conformance', etc. "
    f"Default is '{','.join(DEFAULT_CHECKS_NAMES)}'.",
    type=CHECKS_TYPE,
    default=DEFAULT_CHECKS_NAMES,
    cls=GroupedOption,
    group=ParameterGroup.validation,
    callback=callbacks.convert_checks,
    show_default=True,
)
@click.option(
    "--exclude-checks",
    multiple=True,
    help="Specifies the validation checks to skip during testing. "
    "Provide a comma-separated list of checks you wish to bypass.",
    type=EXCLUDE_CHECKS_TYPE,
    default=[],
    cls=GroupedOption,
    group=ParameterGroup.validation,
    callback=callbacks.convert_checks,
    show_default=True,
)
@click.option(
    "--data-generation-method",
    "-D",
    "data_generation_methods",
    help="Specifies the approach Schemathesis uses to generate test data. "
    "Use 'positive' for valid data, 'negative' for invalid data, or 'all' for both. "
    "Default is 'positive'.",
    type=DATA_GENERATION_METHOD_TYPE,
    default=DataGenerationMethod.default().name,
    callback=callbacks.convert_data_generation_method,
    show_default=True,
)
@click.option(
    "--max-response-time",
    help="Sets a custom time limit for API response times. "
    "The test will fail if a response time exceeds this limit. "
    "Provide the time in milliseconds.",
    type=click.IntRange(min=1),
    cls=GroupedOption,
    group=ParameterGroup.validation,
)
@click.option(
    "--target",
    "-t",
    "targets",
    multiple=True,
    help="Guides input generation to values more likely to expose bugs via targeted property-based testing.",
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
    help="Terminates the test suite immediately upon the first failure or error encountered.",
    show_default=True,
)
@click.option(
    "--max-failures",
    "max_failures",
    type=click.IntRange(min=1),
    help="Terminates the test suite after reaching a specified number of failures or errors.",
    show_default=True,
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Simulates test execution without making any actual requests, useful for validating data generation.",
)
@click.option(
    "--auth",
    "-a",
    help="Provides the server authentication details in the 'USER:PASSWORD' format.",
    type=str,
    callback=callbacks.validate_auth,
)
@click.option(
    "--auth-type",
    "-A",
    type=click.Choice(["basic", "digest"], case_sensitive=False),
    default="basic",
    help="Specifies the authentication method. Default is 'basic'.",
    show_default=True,
)
@click.option(
    "--set-query",
    "set_query",
    help=r"OpenAPI: Override a specific query parameter by specifying 'parameter=value'",
    multiple=True,
    type=str,
    callback=callbacks.validate_set_query,
)
@click.option(
    "--set-header",
    "set_header",
    help=r"OpenAPI: Override a specific header parameter by specifying 'parameter=value'",
    multiple=True,
    type=str,
    callback=callbacks.validate_set_header,
)
@click.option(
    "--set-cookie",
    "set_cookie",
    help=r"OpenAPI: Override a specific cookie parameter by specifying 'parameter=value'",
    multiple=True,
    type=str,
    callback=callbacks.validate_set_cookie,
)
@click.option(
    "--set-path",
    "set_path",
    help=r"OpenAPI: Override a specific path parameter by specifying 'parameter=value'",
    multiple=True,
    type=str,
    callback=callbacks.validate_set_path,
)
@click.option(
    "--header",
    "-H",
    "headers",
    help=r"Adds a custom HTTP header to all API requests. Format: 'Header-Name: Value'.",
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
    help=r"API operation path pattern (e.g., users/\d+).",
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
    help="HTTP method (e.g., GET, POST).",
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
    help="Schema tag pattern.",
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
    help="OpenAPI operationId pattern.",
    callback=callbacks.validate_regex,
    cls=GroupedOption,
    group=ParameterGroup.filtering,
)
@click.option(
    "--workers",
    "-w",
    "workers_num",
    help="Sets the number of concurrent workers for testing. Auto-adjusts if 'auto' is specified.",
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
    help="Provides the base URL of the API, required when schema is provided as a file.",
    type=str,
    callback=callbacks.validate_base_url,
    envvar=BASE_URL_ENV_VAR,
)
@click.option(
    "--app",
    help="Specifies the WSGI/ASGI application under test, provided as an importable Python path.",
    type=str,
    callback=callbacks.validate_app,
)
@click.option(
    "--wait-for-schema",
    help="Maximum duration, in seconds, to wait for the API schema to become available.",
    type=click.FloatRange(1.0),
    default=None,
    envvar=WAIT_FOR_SCHEMA_ENV_VAR,
)
@click.option(
    "--request-timeout",
    help="Sets a timeout limit, in milliseconds, for each network request during tests.",
    type=click.IntRange(1),
    default=DEFAULT_RESPONSE_TIMEOUT,
)
@with_request_proxy
@with_request_tls_verify
@with_request_cert
@with_request_cert_key
@click.option(
    "--validate-schema",
    help="Toggles validation of incoming payloads against the defined API schema. "
    "Set to 'True' to enable or 'False' to disable. "
    "Default is 'False'.",
    type=bool,
    default=False,
    show_default=True,
    cls=GroupedOption,
    group=ParameterGroup.validation,
)
@click.option(
    "--skip-deprecated-operations",
    help="Exclude deprecated API operations from testing.",
    is_flag=True,
    is_eager=True,
    default=False,
    show_default=True,
    cls=GroupedOption,
    group=ParameterGroup.filtering,
)
@click.option(
    "--junit-xml",
    help="Outputs a JUnit-XML style report at the specified file path.",
    type=click.File("w", encoding="utf-8"),
)
@click.option(
    "--report",
    "report_value",
    help="""Specifies how the generated report should be handled.
If used without an argument, the report data will automatically be uploaded to Schemathesis.io.
If a file name is provided, the report will be stored in that file.
The report data, consisting of a tar gz file with multiple JSON files, is subject to change.""",
    is_flag=False,
    flag_value="",
    envvar=service.REPORT_ENV_VAR,
    callback=callbacks.convert_report,  # type: ignore
)
@click.option(
    "--debug-output-file",
    help="Saves debugging information in a JSONL format at the specified file path.",
    type=click.File("w", encoding="utf-8"),
)
@click.option(
    "--show-errors-tracebacks",
    help="Displays complete traceback information for internal errors.",
    is_flag=True,
    is_eager=True,
    default=False,
    hidden=True,
    show_default=True,
)
@click.option(
    "--show-trace",
    help="Displays complete traceback information for internal errors.",
    is_flag=True,
    is_eager=True,
    default=False,
    show_default=True,
)
@click.option(
    "--code-sample-style",
    help="Selects the code sample style for reproducing failures.",
    type=click.Choice([item.name for item in CodeSampleStyle]),
    default=CodeSampleStyle.default().name,
    callback=callbacks.convert_code_sample_style,
)
@click.option(
    "--cassette-path",
    help="Saves the test outcomes in a VCR-compatible format.",
    type=click.File("w", encoding="utf-8"),
    is_eager=True,
)
@click.option(
    "--cassette-format",
    help="Format of the saved cassettes.",
    type=click.Choice([item.name.lower() for item in cassettes.CassetteFormat]),
    default=cassettes.CassetteFormat.VCR.name.lower(),
    callback=callbacks.convert_cassette_format,
)
@click.option(
    "--cassette-preserve-exact-body-bytes",
    help="Retains exact byte sequence of payloads in cassettes, encoded as base64.",
    is_flag=True,
    callback=callbacks.validate_preserve_exact_body_bytes,
)
@click.option(
    "--store-network-log",
    help="Saves the test outcomes in a VCR-compatible format.",
    type=click.File("w", encoding="utf-8"),
    hidden=True,
)
@click.option(
    "--fixups",
    help="Applies compatibility adjustments like 'fast_api', 'utf8_bom'.",
    multiple=True,
    type=click.Choice(list(ALL_FIXUPS) + ["all"]),
)
@click.option(
    "--rate-limit",
    help="Specifies a rate limit for test requests in '<limit>/<duration>' format. "
    "Example - `100/m` for 100 requests per minute.",
    type=str,
    callback=callbacks.validate_rate_limit,
)
@click.option(
    "--stateful",
    help="Enables or disables stateful testing features.",
    type=click.Choice([item.name for item in Stateful]),
    default=Stateful.links.name,
    callback=callbacks.convert_stateful,
)
@click.option(
    "--stateful-recursion-limit",
    help="Sets the recursion depth limit for stateful testing.",
    default=DEFAULT_STATEFUL_RECURSION_LIMIT,
    show_default=True,
    type=click.IntRange(1, 100),
    hidden=True,
)
@click.option(
    "--force-schema-version",
    help="Forces the schema to be interpreted as a particular OpenAPI version.",
    type=click.Choice(["20", "30"]),
)
@click.option(
    "--sanitize-output",
    type=bool,
    default=True,
    show_default=True,
    help="Enable or disable automatic output sanitization to obscure sensitive data.",
)
@click.option(
    "--contrib-unique-data",
    "contrib_unique_data",
    help="Forces the generation of unique test cases.",
    is_flag=True,
    default=False,
    show_default=True,
)
@click.option(
    "--contrib-openapi-formats-uuid",
    "contrib_openapi_formats_uuid",
    help="Enables support for the 'uuid' string format in OpenAPI.",
    is_flag=True,
    default=False,
    show_default=True,
)
@click.option(
    "--contrib-openapi-fill-missing-examples",
    "contrib_openapi_fill_missing_examples",
    help="Enables generation of random examples for API operations that do not have explicit examples defined.",
    is_flag=True,
    default=False,
    show_default=True,
)
@click.option(
    "--hypothesis-database",
    help="Configures storage for examples discovered by Hypothesis. "
    f"Use 'none' to disable, '{HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER}' for temporary storage, "
    f"or specify a file path for persistent storage.",
    type=str,
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
    callback=callbacks.validate_hypothesis_database,
)
@click.option(
    "--hypothesis-deadline",
    help="Sets a time limit for each test case generated by Hypothesis, in milliseconds. "
    "Exceeding this limit will cause the test to fail.",
    # max value to avoid overflow. It is the maximum amount of days in milliseconds
    type=OptionalInt(1, 999999999 * 24 * 3600 * 1000),
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option(
    "--hypothesis-derandomize",
    help="Enables deterministic mode in Hypothesis, which eliminates random variation between test runs.",
    is_flag=True,
    is_eager=True,
    default=None,
    show_default=True,
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option(
    "--hypothesis-max-examples",
    help="Sets the cap on the number of examples generated by Hypothesis for each API method/path pair.",
    type=click.IntRange(1),
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option(
    "--hypothesis-phases",
    help="Specifies which testing phases to execute.",
    type=CsvEnumChoice(Phase),
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option(
    "--hypothesis-no-phases",
    help="Specifies which testing phases to exclude from execution.",
    type=CsvEnumChoice(Phase),
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option(
    "--hypothesis-report-multiple-bugs",
    help="If set, only the most easily reproducible exception will be reported when multiple issues are found.",
    type=bool,
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option(
    "--hypothesis-seed",
    help="Sets a seed value for Hypothesis, ensuring reproducibility across test runs.",
    type=int,
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option(
    "--hypothesis-suppress-health-check",
    help="Disables specified health checks from Hypothesis like 'data_too_large', 'filter_too_much', etc. "
    "Provide a comma-separated list",
    type=CsvEnumChoice(HealthCheck),
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option(
    "--hypothesis-verbosity",
    help="Controls the verbosity level of Hypothesis output.",
    type=click.Choice([item.name for item in Verbosity]),
    callback=callbacks.convert_verbosity,
    cls=GroupedOption,
    group=ParameterGroup.hypothesis,
)
@click.option("--no-color", help="Disable ANSI color escape codes.", type=bool, is_flag=True)
@click.option("--force-color", help="Explicitly tells to enable ANSI color escape codes.", type=bool, is_flag=True)
@click.option(
    "--experimental",
    "experiments",
    help="Enable experimental support for specific features.",
    type=click.Choice(
        [
            experimental.OPEN_API_3_1.name,
            experimental.SCHEMA_ANALYSIS.name,
            experimental.STATEFUL_TEST_RUNNER.name,
            experimental.STATEFUL_ONLY.name,
        ]
    ),
    callback=callbacks.convert_experimental,
    multiple=True,
)
@click.option(
    "--output-truncate",
    help="Specifies whether to truncate schemas and responses in error messages.",
    type=str,
    default="true",
    show_default=True,
    callback=callbacks.convert_boolean_string,
)
@click.option(
    "--generation-allow-x00",
    help="Determines whether to allow the generation of `\x00` bytes within strings.",
    type=str,
    default="true",
    show_default=True,
    callback=callbacks.convert_boolean_string,
)
@click.option(
    "--generation-codec",
    help="Specifies the codec used for generating strings.",
    type=str,
    default="utf-8",
    callback=callbacks.validate_generation_codec,
)
@click.option(
    "--generation-with-security-parameters",
    help="Whether to generate security parameters.",
    type=str,
    default="true",
    show_default=True,
    callback=callbacks.convert_boolean_string,
)
@click.option(
    "--schemathesis-io-token",
    help="Schemathesis.io authentication token.",
    type=str,
    envvar=service.TOKEN_ENV_VAR,
)
@click.option(
    "--schemathesis-io-url",
    help="Schemathesis.io base URL.",
    default=service.DEFAULT_URL,
    type=str,
    envvar=service.URL_ENV_VAR,
)
@click.option(
    "--schemathesis-io-telemetry",
    help="Controls whether you send anonymized CLI usage data to Schemathesis.io along with your report.",
    type=str,
    default="true",
    show_default=True,
    callback=callbacks.convert_boolean_string,
    envvar=service.TELEMETRY_ENV_VAR,
)
@with_hosts_file
@click.option("--verbosity", "-v", help="Increase verbosity of the output.", count=True)
@click.pass_context
def run(
    ctx: click.Context,
    schema: str,
    api_name: str | None,
    auth: tuple[str, str] | None,
    auth_type: str,
    headers: dict[str, str],
    set_query: dict[str, str],
    set_header: dict[str, str],
    set_cookie: dict[str, str],
    set_path: dict[str, str],
    experiments: list,
    checks: Iterable[str] = DEFAULT_CHECKS_NAMES,
    exclude_checks: Iterable[str] = (),
    data_generation_methods: tuple[DataGenerationMethod, ...] = DEFAULT_DATA_GENERATION_METHODS,
    max_response_time: int | None = None,
    targets: Iterable[str] = DEFAULT_TARGETS_NAMES,
    exit_first: bool = False,
    max_failures: int | None = None,
    dry_run: bool = False,
    endpoints: Filter | None = None,
    methods: Filter | None = None,
    tags: Filter | None = None,
    operation_ids: Filter | None = None,
    workers_num: int = DEFAULT_WORKERS,
    base_url: str | None = None,
    app: str | None = None,
    request_timeout: int | None = None,
    request_tls_verify: bool = True,
    request_cert: str | None = None,
    request_cert_key: str | None = None,
    request_proxy: str | None = None,
    validate_schema: bool = True,
    skip_deprecated_operations: bool = False,
    junit_xml: click.utils.LazyFile | None = None,
    debug_output_file: click.utils.LazyFile | None = None,
    show_errors_tracebacks: bool = False,
    show_trace: bool = False,
    code_sample_style: CodeSampleStyle = CodeSampleStyle.default(),
    cassette_path: click.utils.LazyFile | None = None,
    cassette_format: cassettes.CassetteFormat = cassettes.CassetteFormat.VCR,
    cassette_preserve_exact_body_bytes: bool = False,
    store_network_log: click.utils.LazyFile | None = None,
    wait_for_schema: float | None = None,
    fixups: tuple[str] = (),  # type: ignore
    rate_limit: str | None = None,
    stateful: Stateful | None = None,
    stateful_recursion_limit: int = DEFAULT_STATEFUL_RECURSION_LIMIT,
    force_schema_version: str | None = None,
    sanitize_output: bool = True,
    output_truncate: bool = True,
    contrib_unique_data: bool = False,
    contrib_openapi_formats_uuid: bool = False,
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
    verbosity: int = 0,
    no_color: bool = False,
    report_value: str | None = None,
    generation_allow_x00: bool = True,
    generation_with_security_parameters: bool = True,
    generation_codec: str = "utf-8",
    schemathesis_io_token: str | None = None,
    schemathesis_io_url: str = service.DEFAULT_URL,
    schemathesis_io_telemetry: bool = True,
    hosts_file: PathLike = service.DEFAULT_HOSTS_PATH,
    force_color: bool = False,
) -> None:
    """Run tests against an API using a specified SCHEMA.

    [Required] SCHEMA: Path to an OpenAPI (`.json`, `.yml`) or GraphQL SDL file, or a URL pointing to such specifications.

    [Optional] API_NAME: Identifier for uploading test data to Schemathesis.io.
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
            health_check.as_hypothesis() for health_check in hypothesis_suppress_health_check
        ]

    if contrib_unique_data:
        click.secho(DEPRECATED_CONTRIB_UNIQUE_DATA_OPTION_WARNING, fg="yellow")

    if show_errors_tracebacks:
        click.secho(DEPRECATED_SHOW_ERROR_TRACEBACKS_OPTION_WARNING, fg="yellow")
        show_trace = show_errors_tracebacks

    # Enable selected experiments
    for experiment in experiments:
        experiment.enable()

    override = CaseOverride(query=set_query, headers=set_header, cookies=set_cookie, path_parameters=set_path)

    generation_config = generation.GenerationConfig(
        allow_x00=generation_allow_x00,
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
    started_at = current_datetime()

    if no_color and force_color:
        raise click.UsageError(COLOR_OPTIONS_INVALID_USAGE_MESSAGE)
    decide_color_output(ctx, no_color, force_color)

    check_auth(auth, headers, override)
    selected_targets = tuple(target for target in targets_module.ALL_TARGETS if target.__name__ in targets)

    if store_network_log and cassette_path:
        raise click.UsageError(CASSETTES_PATH_INVALID_USAGE_MESSAGE)
    if store_network_log is not None:
        click.secho(DEPRECATED_CASSETTE_PATH_OPTION_WARNING, fg="yellow")
        cassette_path = store_network_log

    output_config = OutputConfig(truncate=output_truncate)

    schemathesis_io_hostname = urlparse(schemathesis_io_url).netloc
    token = schemathesis_io_token or service.hosts.get_token(hostname=schemathesis_io_hostname, hosts_file=hosts_file)
    schema_kind = callbacks.parse_schema_kind(schema, app)
    callbacks.validate_schema(schema, schema_kind, base_url=base_url, dry_run=dry_run, app=app, api_name=api_name)
    client = None
    schema_or_location: str | dict[str, Any] = schema
    if schema_kind == callbacks.SchemaInputKind.NAME:
        api_name = schema
    if (
        not isinstance(report, click.utils.LazyFile)
        and api_name is not None
        and schema_kind == callbacks.SchemaInputKind.NAME
    ):
        from ..service.client import ServiceClient

        client = ServiceClient(base_url=schemathesis_io_url, token=token)
        # It is assigned above
        if token is not None or schema_kind == callbacks.SchemaInputKind.NAME:
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

    if "all" in checks:
        selected_checks = checks_module.ALL_CHECKS
    else:
        selected_checks = tuple(check for check in checks_module.ALL_CHECKS if check.__name__ in checks)

    selected_checks = tuple(check for check in selected_checks if check.__name__ not in exclude_checks)

    if fixups:
        if "all" in fixups:
            _fixups.install()
        else:
            _fixups.install(fixups)

    if contrib_unique_data:
        contrib.unique_data.install()
    if contrib_openapi_formats_uuid:
        contrib.openapi.formats.uuid.install()
    if contrib_openapi_fill_missing_examples:
        contrib.openapi.fill_missing_examples.install()

    hypothesis_settings = prepare_hypothesis_settings(
        database=hypothesis_database,
        deadline=hypothesis_deadline,
        derandomize=hypothesis_derandomize,
        max_examples=hypothesis_max_examples,
        phases=_hypothesis_phases,
        report_multiple_bugs=hypothesis_report_multiple_bugs,
        suppress_health_check=_hypothesis_suppress_health_check,
        verbosity=hypothesis_verbosity,
    )
    event_stream = into_event_stream(
        schema_or_location,
        app=app,
        base_url=base_url,
        started_at=started_at,
        validate_schema=validate_schema,
        skip_deprecated_operations=skip_deprecated_operations,
        data_generation_methods=data_generation_methods,
        force_schema_version=force_schema_version,
        request_tls_verify=request_tls_verify,
        request_proxy=request_proxy,
        request_cert=prepare_request_cert(request_cert, request_cert_key),
        wait_for_schema=wait_for_schema,
        auth=auth,
        auth_type=auth_type,
        override=override,
        headers=headers,
        endpoint=endpoints or None,
        method=methods or None,
        tag=tags or None,
        operation_id=operation_ids or None,
        request_timeout=request_timeout,
        seed=hypothesis_seed,
        exit_first=exit_first,
        max_failures=max_failures,
        dry_run=dry_run,
        store_interactions=cassette_path is not None,
        checks=selected_checks,
        max_response_time=max_response_time,
        targets=selected_targets,
        workers_num=workers_num,
        rate_limit=rate_limit,
        stateful=stateful,
        stateful_recursion_limit=stateful_recursion_limit,
        hypothesis_settings=hypothesis_settings,
        generation_config=generation_config,
        output_config=output_config,
        service_client=client,
    )
    execute(
        event_stream,
        hypothesis_settings=hypothesis_settings,
        workers_num=workers_num,
        rate_limit=rate_limit,
        show_trace=show_trace,
        wait_for_schema=wait_for_schema,
        validate_schema=validate_schema,
        cassette_path=cassette_path,
        cassette_format=cassette_format,
        cassette_preserve_exact_body_bytes=cassette_preserve_exact_body_bytes,
        junit_xml=junit_xml,
        verbosity=verbosity,
        code_sample_style=code_sample_style,
        data_generation_methods=data_generation_methods,
        debug_output_file=debug_output_file,
        sanitize_output=sanitize_output,
        host_data=host_data,
        client=client,
        report=report,
        telemetry=schemathesis_io_telemetry,
        api_name=api_name,
        location=schema,
        base_url=base_url,
        started_at=started_at,
        output_config=output_config,
    )


def prepare_request_cert(cert: str | None, key: str | None) -> RequestCert | None:
    if cert is not None and key is not None:
        return cert, key
    return cert


@dataclass
class LoaderConfig:
    """Container for API loader parameters.

    The main goal is to avoid too many parameters in function signatures.
    """

    schema_or_location: str | dict[str, Any]
    app: Any
    base_url: str | None
    validate_schema: bool
    skip_deprecated_operations: bool
    data_generation_methods: tuple[DataGenerationMethod, ...]
    force_schema_version: str | None
    request_tls_verify: bool | str
    request_proxy: str | None
    request_cert: RequestCert | None
    wait_for_schema: float | None
    rate_limit: str | None
    output_config: OutputConfig
    # Network request parameters
    auth: tuple[str, str] | None
    auth_type: str | None
    headers: dict[str, str] | None
    # Schema filters
    endpoint: Filter | None
    method: Filter | None
    tag: Filter | None
    operation_id: Filter | None


def into_event_stream(
    schema_or_location: str | dict[str, Any],
    *,
    app: Any,
    base_url: str | None,
    started_at: str,
    validate_schema: bool,
    skip_deprecated_operations: bool,
    data_generation_methods: tuple[DataGenerationMethod, ...],
    force_schema_version: str | None,
    request_tls_verify: bool | str,
    request_proxy: str | None,
    request_cert: RequestCert | None,
    # Network request parameters
    auth: tuple[str, str] | None,
    auth_type: str | None,
    override: CaseOverride,
    headers: dict[str, str] | None,
    request_timeout: int | None,
    wait_for_schema: float | None,
    # Schema filters
    endpoint: Filter | None,
    method: Filter | None,
    tag: Filter | None,
    operation_id: Filter | None,
    # Runtime behavior
    checks: Iterable[CheckFunction],
    max_response_time: int | None,
    targets: Iterable[Target],
    workers_num: int,
    hypothesis_settings: hypothesis.settings | None,
    generation_config: generation.GenerationConfig,
    output_config: OutputConfig,
    seed: int | None,
    exit_first: bool,
    max_failures: int | None,
    rate_limit: str | None,
    dry_run: bool,
    store_interactions: bool,
    stateful: Stateful | None,
    stateful_recursion_limit: int,
    service_client: ServiceClient | None,
) -> Generator[events.ExecutionEvent, None, None]:
    try:
        if app is not None:
            app = load_app(app)
        config = LoaderConfig(
            schema_or_location=schema_or_location,
            app=app,
            base_url=base_url,
            validate_schema=validate_schema,
            skip_deprecated_operations=skip_deprecated_operations,
            data_generation_methods=data_generation_methods,
            force_schema_version=force_schema_version,
            request_proxy=request_proxy,
            request_tls_verify=request_tls_verify,
            request_cert=request_cert,
            wait_for_schema=wait_for_schema,
            rate_limit=rate_limit,
            auth=auth,
            auth_type=auth_type,
            headers=headers,
            endpoint=endpoint or None,
            method=method or None,
            tag=tag or None,
            operation_id=operation_id or None,
            output_config=output_config,
        )
        schema = load_schema(config)
        yield from runner.from_schema(
            schema,
            auth=auth,
            auth_type=auth_type,
            override=override,
            headers=headers,
            request_timeout=request_timeout,
            request_tls_verify=request_tls_verify,
            request_proxy=request_proxy,
            request_cert=request_cert,
            seed=seed,
            exit_first=exit_first,
            max_failures=max_failures,
            started_at=started_at,
            dry_run=dry_run,
            store_interactions=store_interactions,
            checks=checks,
            max_response_time=max_response_time,
            targets=targets,
            workers_num=workers_num,
            stateful=stateful,
            stateful_recursion_limit=stateful_recursion_limit,
            hypothesis_settings=hypothesis_settings,
            generation_config=generation_config,
            probe_config=probes.ProbeConfig(
                base_url=config.base_url,
                request_tls_verify=config.request_tls_verify,
                request_proxy=config.request_proxy,
                request_cert=config.request_cert,
                auth=config.auth,
                auth_type=config.auth_type,
                headers=config.headers,
            ),
            service_client=service_client,
        ).execute()
    except SchemaError as error:
        yield events.InternalError.from_schema_error(error)
    except Exception as exc:
        yield events.InternalError.from_exc(exc)


def load_schema(config: LoaderConfig) -> BaseSchema:
    """Automatically load API schema."""
    first: Callable[[LoaderConfig], BaseSchema]
    second: Callable[[LoaderConfig], BaseSchema]
    if is_probably_graphql(config.schema_or_location):
        # Try GraphQL first, then fallback to Open API
        first, second = (_load_graphql_schema, _load_openapi_schema)
    else:
        # Try Open API first, then fallback to GraphQL
        first, second = (_load_openapi_schema, _load_graphql_schema)
    return _try_load_schema(config, first, second)


def should_try_more(exc: SchemaError) -> bool:
    import requests
    from yaml.reader import ReaderError

    if isinstance(exc.__cause__, ReaderError) and "characters are not allowed" in str(exc.__cause__):
        return False

    # We should not try other loaders for cases when we can't even establish connection
    return not isinstance(exc.__cause__, requests.exceptions.ConnectionError) and exc.type not in (
        SchemaErrorType.OPEN_API_INVALID_SCHEMA,
        SchemaErrorType.OPEN_API_UNSPECIFIED_VERSION,
        SchemaErrorType.OPEN_API_UNSUPPORTED_VERSION,
        SchemaErrorType.OPEN_API_EXPERIMENTAL_VERSION,
    )


Loader = Callable[[LoaderConfig], "BaseSchema"]


def _try_load_schema(config: LoaderConfig, first: Loader, second: Loader) -> BaseSchema:
    from urllib3.exceptions import InsecureRequestWarning

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureRequestWarning)
        try:
            return first(config)
        except SchemaError as exc:
            if config.force_schema_version is None and should_try_more(exc):
                try:
                    return second(config)
                except Exception as second_exc:
                    if is_specific_exception(second, second_exc):
                        raise second_exc
            # Re-raise the original error
            raise exc


def is_specific_exception(loader: Loader, exc: Exception) -> bool:
    return (
        loader is _load_graphql_schema
        and isinstance(exc, SchemaError)
        and exc.type == SchemaErrorType.GRAPHQL_INVALID_SCHEMA
        # In some cases it is not clear that the schema is even supposed to be GraphQL, e.g. an empty input
        and "Syntax Error: Unexpected <EOF>." not in exc.extras
    )


def _load_graphql_schema(config: LoaderConfig) -> GraphQLSchema:
    loader = detect_loader(config.schema_or_location, config.app, is_openapi=False)
    kwargs = get_graphql_loader_kwargs(loader, config)
    return loader(config.schema_or_location, **kwargs)


def _load_openapi_schema(config: LoaderConfig) -> BaseSchema:
    loader = detect_loader(config.schema_or_location, config.app, is_openapi=True)
    kwargs = get_loader_kwargs(loader, config)
    return loader(config.schema_or_location, **kwargs)


def detect_loader(schema_or_location: str | dict[str, Any], app: Any, is_openapi: bool) -> Callable:
    """Detect API schema loader."""
    if isinstance(schema_or_location, str):
        if file_exists(schema_or_location):
            # If there is an existing file with the given name,
            # then it is likely that the user wants to load API schema from there
            return oas_loaders.from_path if is_openapi else gql_loaders.from_path  # type: ignore
        if app is not None and not urlparse(schema_or_location).netloc:
            # App is passed & location is relative
            return oas_loaders.get_loader_for_app(app) if is_openapi else gql_loaders.get_loader_for_app(app)
        # Default behavior
        return oas_loaders.from_uri if is_openapi else gql_loaders.from_url  # type: ignore
    return oas_loaders.from_dict if is_openapi else gql_loaders.from_dict  # type: ignore


def get_loader_kwargs(loader: Callable, config: LoaderConfig) -> dict[str, Any]:
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
        "rate_limit": config.rate_limit,
        "output_config": config.output_config,
    }
    if loader not in (oas_loaders.from_path, oas_loaders.from_dict):
        kwargs["headers"] = config.headers
    if loader in (oas_loaders.from_uri, oas_loaders.from_aiohttp):
        _add_requests_kwargs(kwargs, config)
    return kwargs


def get_graphql_loader_kwargs(
    loader: Callable,
    config: LoaderConfig,
) -> dict[str, Any]:
    """Detect the proper set of parameters for a loader."""
    # These kwargs are shared by all loaders
    kwargs = {
        "app": config.app,
        "base_url": config.base_url,
        "data_generation_methods": config.data_generation_methods,
        "rate_limit": config.rate_limit,
    }
    if loader not in (gql_loaders.from_path, gql_loaders.from_dict):
        kwargs["headers"] = config.headers
    if loader is gql_loaders.from_url:
        _add_requests_kwargs(kwargs, config)
    return kwargs


def _add_requests_kwargs(kwargs: dict[str, Any], config: LoaderConfig) -> None:
    kwargs["verify"] = config.request_tls_verify
    if config.request_cert is not None:
        kwargs["cert"] = config.request_cert
    if config.auth is not None:
        kwargs["auth"] = get_requests_auth(config.auth, config.auth_type)
    if config.wait_for_schema is not None:
        kwargs["wait_for_schema"] = config.wait_for_schema


def is_probably_graphql(schema_or_location: str | dict[str, Any]) -> bool:
    """Detect whether it is likely that the given location is a GraphQL endpoint."""
    if isinstance(schema_or_location, str):
        return schema_or_location.endswith(("/graphql", "/graphql/", ".graphql", ".gql"))
    return "__schema" in schema_or_location or (
        "data" in schema_or_location and "__schema" in schema_or_location["data"]
    )


def check_auth(auth: tuple[str, str] | None, headers: dict[str, str], override: CaseOverride) -> None:
    auth_is_set = auth is not None
    header_is_set = "authorization" in {header.lower() for header in headers}
    override_is_set = "authorization" in {header.lower() for header in override.headers}
    if len([is_set for is_set in (auth_is_set, header_is_set, override_is_set) if is_set]) > 1:
        message = "The "
        used = []
        if auth_is_set:
            used.append("`--auth`")
        if header_is_set:
            used.append("`--header`")
        if override_is_set:
            used.append("`--set-header`")
        message += " and ".join(used)
        message += " options were both used to set the 'Authorization' header, which is not permitted."
        raise click.BadParameter(message)


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
            trace = extract_nth_traceback(exc.__traceback__, 1)
            lines = traceback.format_exception(type(exc), exc, trace)
            message = "".join(lines).strip()
            click.secho(f"\n{message}", fg="red")
        click.echo(f"\nFor more information on how to work with hooks, visit {EXTENSIONS_DOCUMENTATION_URL}")
        raise click.exceptions.Exit(1) from None


class OutputStyle(Enum):
    """Provide different output styles."""

    default = output.default.DefaultOutputStyleHandler
    short = output.short.ShortOutputStyleHandler


def execute(
    event_stream: Generator[events.ExecutionEvent, None, None],
    *,
    hypothesis_settings: hypothesis.settings,
    workers_num: int,
    rate_limit: str | None,
    show_trace: bool,
    wait_for_schema: float | None,
    validate_schema: bool,
    cassette_path: click.utils.LazyFile | None,
    cassette_format: cassettes.CassetteFormat,
    cassette_preserve_exact_body_bytes: bool,
    junit_xml: click.utils.LazyFile | None,
    verbosity: int,
    code_sample_style: CodeSampleStyle,
    data_generation_methods: tuple[DataGenerationMethod, ...],
    debug_output_file: click.utils.LazyFile | None,
    sanitize_output: bool,
    host_data: service.hosts.HostData,
    client: ServiceClient | None,
    report: ReportToService | click.utils.LazyFile | None,
    telemetry: bool,
    api_name: str | None,
    location: str,
    base_url: str | None,
    started_at: str,
    output_config: OutputConfig,
) -> None:
    """Execute a prepared runner by drawing events from it and passing to a proper handler."""
    handlers: list[EventHandler] = []
    report_context: ServiceReportContext | FileReportContext | None = None
    report_queue: Queue
    if client:
        # If API name is specified, validate it
        report_queue = Queue()
        report_context = ServiceReportContext(queue=report_queue, service_base_url=client.base_url)
        handlers.append(
            service.ServiceReportHandler(
                client=client,
                host_data=host_data,
                api_name=api_name,
                location=location,
                base_url=base_url,
                started_at=started_at,
                out_queue=report_queue,
                telemetry=telemetry,
            )
        )
    elif isinstance(report, click.utils.LazyFile):
        _open_file(report)
        report_queue = Queue()
        report_context = FileReportContext(queue=report_queue, filename=report.name)
        handlers.append(
            service.FileReportHandler(
                file_handle=report,
                api_name=api_name,
                location=location,
                base_url=base_url,
                started_at=started_at,
                out_queue=report_queue,
                telemetry=telemetry,
            )
        )
    if junit_xml is not None:
        _open_file(junit_xml)
        handlers.append(JunitXMLHandler(junit_xml))
    if debug_output_file is not None:
        _open_file(debug_output_file)
        handlers.append(DebugOutputHandler(debug_output_file))
    if cassette_path is not None:
        # This handler should be first to have logs writing completed when the output handler will display statistic
        _open_file(cassette_path)
        handlers.append(
            cassettes.CassetteWriter(
                cassette_path, format=cassette_format, preserve_exact_body_bytes=cassette_preserve_exact_body_bytes
            )
        )
    handlers.append(get_output_handler(workers_num))
    if sanitize_output:
        handlers.insert(0, SanitizationHandler())
    execution_context = ExecutionContext(
        hypothesis_settings=hypothesis_settings,
        workers_num=workers_num,
        rate_limit=rate_limit,
        show_trace=show_trace,
        wait_for_schema=wait_for_schema,
        validate_schema=validate_schema,
        cassette_path=cassette_path.name if cassette_path is not None else None,
        junit_xml_file=junit_xml.name if junit_xml is not None else None,
        verbosity=verbosity,
        code_sample_style=code_sample_style,
        report=report_context,
        output_config=output_config,
    )

    def shutdown() -> None:
        for _handler in handlers:
            _handler.shutdown()

    GLOBAL_HOOK_DISPATCHER.dispatch("after_init_cli_run_handlers", HookContext(), handlers, execution_context)
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
    from ..utils import _ensure_parent

    try:
        _ensure_parent(file.name, fail_silently=False)
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
            SanitizationHandler,
        )
    )


def display_handler_error(handler: EventHandler, exc: Exception) -> None:
    """Display error that happened within."""
    is_built_in = is_built_in_handler(handler)
    if is_built_in:
        click.secho("Internal Error", fg="red", bold=True)
        click.secho("\nSchemathesis encountered an unexpected issue.")
        trace = exc.__traceback__
    else:
        click.secho("CLI Handler Error", fg="red", bold=True)
        click.echo(f"\nAn error occurred within your custom CLI handler `{bold(handler.__class__.__name__)}`.")
        trace = extract_nth_traceback(exc.__traceback__, 1)
    lines = traceback.format_exception(type(exc), exc, trace)
    message = "".join(lines).strip()
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
@click.option("--force-color", help="Explicitly tells to enable ANSI color escape codes.", type=bool, is_flag=True)
@click.option("--verbosity", "-v", help="Increase verbosity of the output.", count=True)
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
        cassette = load_yaml(fd)
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
    help="Schemathesis.io authentication token.",
    type=str,
    envvar=service.TOKEN_ENV_VAR,
)
@click.option(
    "--schemathesis-io-url",
    help="Schemathesis.io base URL.",
    default=service.DEFAULT_URL,
    type=str,
    envvar=service.URL_ENV_VAR,
)
@with_request_tls_verify
@with_hosts_file
def upload(
    report: io.BufferedReader,
    hosts_file: str,
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
def login(token: str, hostname: str, hosts_file: str, protocol: str, request_tls_verify: bool = True) -> None:
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
def logout(hostname: str, hosts_file: str) -> None:
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


@HookDispatcher.register_spec([HookScope.GLOBAL])
def after_init_cli_run_handlers(
    context: HookContext, handlers: list[EventHandler], execution_context: ExecutionContext
) -> None:
    """Called after CLI hooks are initialized.

    Might be used to add extra event handlers.
    """


@HookDispatcher.register_spec([HookScope.GLOBAL])
def process_call_kwargs(context: HookContext, case: Case, kwargs: dict[str, Any]) -> None:
    """Called before every network call in CLI tests.

    Aims to modify the argument passed to `case.call`.
    Note that you need to modify `kwargs` in-place.
    """
