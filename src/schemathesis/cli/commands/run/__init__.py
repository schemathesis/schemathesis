from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import click
from click.utils import LazyFile

from schemathesis.checks import CHECKS, load_all_checks
from schemathesis.cli.commands.run import executor, validation
from schemathesis.cli.commands.run.filters import with_filters
from schemathesis.cli.constants import MAX_WORKERS, MIN_WORKERS
from schemathesis.cli.core import ensure_color
from schemathesis.cli.ext.groups import group, grouped_option
from schemathesis.cli.ext.options import (
    CsvChoice,
    CsvEnumChoice,
    CustomHelpMessageChoice,
    RegistryChoice,
)
from schemathesis.config import (
    DEFAULT_REPORT_DIRECTORY,
    HealthCheck,
    ReportFormat,
    SchemathesisConfig,
    SchemathesisWarning,
    WarningsConfig,
)
from schemathesis.core import HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT
from schemathesis.generation import GenerationMode
from schemathesis.generation.metrics import METRICS, MetricFunction

load_all_checks()

COLOR_OPTIONS_INVALID_USAGE_MESSAGE = "Can't use `--no-color` and `--force-color` simultaneously"

DEFAULT_PHASES = ["examples", "coverage", "fuzzing", "stateful"]


@click.argument(  # type: ignore[untyped-decorator]
    "location",
    type=str,
    callback=validation.validate_schema_location,
)
@group("Options")
@grouped_option(
    "--url",
    "-u",
    "base_url",
    help="API base URL (required for file-based schemas)",
    metavar="URL",
    type=str,
    callback=validation.validate_base_url,
    envvar="SCHEMATHESIS_BASE_URL",
)
@grouped_option(
    "--workers",
    "-w",
    "workers",
    help="Number of concurrent workers for testing. Auto-adjusts if 'auto' is specified",
    type=CustomHelpMessageChoice(
        ["auto", *list(map(str, range(MIN_WORKERS, MAX_WORKERS + 1)))],
        choices_repr=f"[auto, {MIN_WORKERS}-{MAX_WORKERS}]",
    ),
    default=None,
    show_default=True,
    callback=validation.convert_workers,
    metavar="",
)
@grouped_option(
    "--phases",
    help="A comma-separated list of test phases to run",
    type=CsvChoice(DEFAULT_PHASES),
    default=",".join(DEFAULT_PHASES),
    metavar="",
)
@grouped_option(
    "--suppress-health-check",
    help="A comma-separated list of Schemathesis health checks to disable",
    type=CsvEnumChoice(HealthCheck),
    metavar="",
)
@grouped_option(
    "--wait-for-schema",
    help="Maximum duration, in seconds, to wait for the API schema to become available. Disabled by default",
    type=click.FloatRange(1.0),
    default=None,
    envvar="SCHEMATHESIS_WAIT_FOR_SCHEMA",
)
@grouped_option(
    "--warnings",
    help="Control warning display: 'off' to disable all, or comma-separated list of warning types to enable",
    type=str,
    default=None,
    callback=validation.validate_warnings,
    metavar="WARNINGS",
)
@group("API validation options")
@grouped_option(
    "--checks",
    "-c",
    "included_check_names",
    multiple=True,
    help="Comma-separated list of checks to run against API responses",
    type=RegistryChoice(CHECKS, with_all=True),
    default=None,
    callback=validation.reduce_list,
    show_default=True,
    metavar="",
)
@grouped_option(
    "--exclude-checks",
    "excluded_check_names",
    multiple=True,
    help="Comma-separated list of checks to skip during testing",
    type=RegistryChoice(CHECKS, with_all=True),
    default=None,
    callback=validation.reduce_list,
    show_default=True,
    metavar="",
)
@grouped_option(
    "--max-failures",
    "max_failures",
    type=click.IntRange(min=1),
    help="Terminate the test suite after reaching a specified number of failures or errors",
    show_default=True,
)
@grouped_option(
    "--continue-on-failure",
    "continue_on_failure",
    help="Continue executing all test cases within a scenario, even after encountering failures",
    is_flag=True,
    default=False,
    metavar="",
)
@grouped_option(
    "--max-response-time",
    help="Maximum allowed API response time in seconds",
    type=click.FloatRange(min=0.0, min_open=True),
    metavar="SECONDS",
)
@group(
    "Filtering options",
    description=(
        "Filter operations by path, method, name, tag, or operation-id using:\n\n"
        "--include-TYPE VALUE          Match operations with exact VALUE\n"
        "--include-TYPE-regex PATTERN  Match operations using regular expression\n"
        "--exclude-TYPE VALUE          Exclude operations with exact VALUE\n"
        "--exclude-TYPE-regex PATTERN  Exclude operations using regular expression"
    ),
)
@with_filters
@grouped_option(
    "--include-by",
    "include_by",
    type=str,
    metavar="EXPR",
    callback=validation.validate_filter_expression,
    help="Include using custom expression",
)
@grouped_option(
    "--exclude-by",
    "exclude_by",
    type=str,
    callback=validation.validate_filter_expression,
    metavar="EXPR",
    help="Exclude using custom expression",
)
@grouped_option(
    "--exclude-deprecated",
    help="Skip deprecated operations",
    is_flag=True,
    is_eager=True,
    default=None,
    show_default=True,
)
@group("Network requests options")
@grouped_option(
    "--header",
    "-H",
    "headers",
    help=r"Add a custom HTTP header to all API requests",
    metavar="NAME:VALUE",
    multiple=True,
    type=str,
    callback=validation.validate_headers,
)
@grouped_option(
    "--auth",
    "-a",
    help="Authenticate all API requests with basic authentication",
    metavar="USER:PASS",
    type=str,
    callback=validation.validate_auth,
)
@grouped_option(
    "--proxy",
    "request_proxy",
    help="Set the proxy for all network requests",
    metavar="URL",
    type=str,
)
@grouped_option(
    "--tls-verify",
    "request_tls_verify",
    help="Path to CA bundle for TLS verification, or 'false' to disable",
    type=str,
    default=None,
    show_default=True,
    callback=validation.convert_boolean_string,
)
@grouped_option(
    "--rate-limit",
    help="Specify a rate limit for test requests in '<limit>/<duration>' format. "
    "Example - `100/m` for 100 requests per minute",
    type=str,
    callback=validation.validate_rate_limit,
)
@grouped_option(
    "--max-redirects",
    help="Maximum number of redirects to follow for each request",
    type=click.IntRange(min=0),
    show_default=True,
)
@grouped_option(
    "--request-timeout",
    help="Timeout limit, in seconds, for each network request during tests",
    type=click.FloatRange(min=0.0, min_open=True),
    default=DEFAULT_RESPONSE_TIMEOUT,
)
@grouped_option(
    "--request-cert",
    help="File path of unencrypted client certificate for authentication. "
    "The certificate can be bundled with a private key (e.g. PEM) or the private "
    "key can be provided with the --request-cert-key argument",
    type=click.Path(exists=True),
    default=None,
    show_default=False,
)
@grouped_option(
    "--request-cert-key",
    help="Specify the file path of the private key for the client certificate",
    type=click.Path(exists=True),
    default=None,
    show_default=False,
    callback=validation.validate_request_cert_key,
)
@group("Output options")
@grouped_option(
    "--report",
    "report_formats",
    help="Generate test reports in formats specified as a comma-separated list",
    type=CsvEnumChoice(ReportFormat),
    is_eager=True,
    metavar="FORMAT",
)
@grouped_option(
    "--report-dir",
    "report_directory",
    help="Directory to store all report files",
    type=click.Path(file_okay=False, dir_okay=True),
    default=DEFAULT_REPORT_DIRECTORY,
    show_default=True,
)
@grouped_option(
    "--report-junit-path",
    help="Custom path for JUnit XML report",
    type=click.File("w", encoding="utf-8"),
    is_eager=True,
)
@grouped_option(
    "--report-vcr-path",
    help="Custom path for VCR cassette",
    type=click.File("w", encoding="utf-8"),
    is_eager=True,
)
@grouped_option(
    "--report-har-path",
    help="Custom path for HAR file",
    type=click.File("w", encoding="utf-8"),
    is_eager=True,
)
@grouped_option(
    "--report-preserve-bytes",
    help="Retain exact byte sequence of payloads in cassettes, encoded as base64",
    type=bool,
    is_flag=True,
    default=None,
    callback=validation.validate_preserve_bytes,
)
@grouped_option(
    "--output-sanitize",
    type=str,
    default=None,
    show_default=True,
    help="Enable or disable automatic output sanitization to obscure sensitive data",
    metavar="BOOLEAN",
    callback=validation.convert_boolean_string,
)
@grouped_option(
    "--output-truncate",
    help="Truncate schemas and responses in error messages",
    type=str,
    default=None,
    show_default=True,
    metavar="BOOLEAN",
    callback=validation.convert_boolean_string,
)
@group("Data generation options")
@grouped_option(
    "--mode",
    "-m",
    "generation_modes",
    help="Test data generation mode",
    type=click.Choice([item.value for item in GenerationMode] + ["all"]),
    default="all",
    callback=validation.convert_generation_mode,
    show_default=True,
    metavar="",
)
@grouped_option(
    "--max-examples",
    "-n",
    "generation_max_examples",
    help="Maximum number of test cases per API operation",
    type=click.IntRange(1),
)
@grouped_option(
    "--seed",
    "generation_seed",
    help="Random seed for reproducible test runs",
    type=int,
)
@grouped_option(
    "--no-shrink",
    "generation_no_shrink",
    help="Disable test case shrinking. Makes test failures harder to debug but improves performance",
    is_flag=True,
    default=None,
)
@grouped_option(
    "--generation-deterministic",
    help="Enables deterministic mode, which eliminates random variation between tests",
    is_flag=True,
    is_eager=True,
    default=None,
    show_default=True,
)
@grouped_option(
    "--generation-allow-x00",
    help="Whether to allow the generation of 'NULL' bytes within strings",
    type=str,
    default=None,
    show_default=True,
    metavar="BOOLEAN",
    callback=validation.convert_boolean_string,
)
@grouped_option(
    "--generation-codec",
    help="The codec used for generating strings",
    type=str,
    default=None,
    callback=validation.validate_generation_codec,
)
@grouped_option(
    "--generation-maximize",
    "generation_maximize",
    multiple=True,
    help="Guide input generation to values more likely to expose bugs via targeted property-based testing",
    type=RegistryChoice(METRICS),
    default=None,
    callback=validation.convert_maximize,
    show_default=True,
    metavar="METRIC",
)
@grouped_option(
    "--generation-with-security-parameters",
    help="Whether to generate security parameters",
    type=str,
    default=None,
    show_default=True,
    callback=validation.convert_boolean_string,
    metavar="BOOLEAN",
)
@grouped_option(
    "--generation-graphql-allow-null",
    help="Whether to use `null` values for optional arguments in GraphQL queries",
    type=str,
    default=None,
    show_default=True,
    callback=validation.convert_boolean_string,
    metavar="BOOLEAN",
)
@grouped_option(
    "--generation-database",
    help="Storage for examples discovered by Schemathesis. "
    f"Use 'none' to disable, '{HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER}' for temporary storage, "
    f"or specify a file path for persistent storage",
    type=str,
    callback=validation.validate_hypothesis_database,
)
@grouped_option(
    "--generation-unique-inputs",
    "generation_unique_inputs",
    help="Force the generation of unique test cases",
    is_flag=True,
    default=None,
    show_default=True,
    metavar="BOOLEAN",
)
@group("Global options")
@grouped_option("--no-color", help="Disable ANSI color escape codes", type=bool, is_flag=True)
@grouped_option("--force-color", help="Explicitly tells to enable ANSI color escape codes", type=bool, is_flag=True)
@click.pass_context  # type: ignore[untyped-decorator]
def run(
    ctx: click.Context,
    *,
    location: str,
    auth: tuple[str, str] | None,
    headers: dict[str, str],
    included_check_names: list[str] | None,
    excluded_check_names: list[str] | None,
    max_response_time: float | None = None,
    phases: list[str] = DEFAULT_PHASES,
    max_failures: int | None = None,
    continue_on_failure: bool | None = None,
    include_path: tuple[str, ...],
    include_path_regex: str | None,
    include_method: tuple[str, ...],
    include_method_regex: str | None,
    include_name: tuple[str, ...],
    include_name_regex: str | None,
    include_tag: tuple[str, ...],
    include_tag_regex: str | None,
    include_operation_id: tuple[str, ...],
    include_operation_id_regex: str | None,
    exclude_path: tuple[str, ...],
    exclude_path_regex: str | None,
    exclude_method: tuple[str, ...],
    exclude_method_regex: str | None,
    exclude_name: tuple[str, ...],
    exclude_name_regex: str | None,
    exclude_tag: tuple[str, ...],
    exclude_tag_regex: str | None,
    exclude_operation_id: tuple[str, ...],
    exclude_operation_id_regex: str | None,
    include_by: Callable | None = None,
    exclude_by: Callable | None = None,
    exclude_deprecated: bool | None = None,
    workers: int | None = None,
    base_url: str | None,
    wait_for_schema: float | None = None,
    suppress_health_check: list[HealthCheck] | None,
    warnings: bool | list[SchemathesisWarning] | None,
    rate_limit: str | None = None,
    max_redirects: int | None = None,
    request_timeout: int | None = None,
    request_tls_verify: bool | None = None,
    request_cert: str | None = None,
    request_cert_key: str | None = None,
    request_proxy: str | None = None,
    report_formats: list[ReportFormat] | None,
    report_directory: Path | str = DEFAULT_REPORT_DIRECTORY,
    report_junit_path: LazyFile | None = None,
    report_vcr_path: LazyFile | None = None,
    report_har_path: LazyFile | None = None,
    report_preserve_bytes: bool | None = None,
    output_sanitize: bool | None = None,
    output_truncate: bool | None = None,
    generation_modes: list[GenerationMode],
    generation_seed: int | None = None,
    generation_max_examples: int | None = None,
    generation_maximize: list[MetricFunction] | None,
    generation_deterministic: bool | None = None,
    generation_database: str | None = None,
    generation_unique_inputs: bool | None = None,
    generation_allow_x00: bool | None = None,
    generation_graphql_allow_null: bool | None = None,
    generation_with_security_parameters: bool | None = None,
    generation_codec: str | None = None,
    generation_no_shrink: bool | None = None,
    force_color: bool = False,
    no_color: bool = False,
    **__kwargs: Any,
) -> None:
    """Generate and run property-based tests against your API.

    \b
    LOCATION can be:
        - Local file: ./openapi.json, ./schema.yaml, ./schema.graphql
        - OpenAPI URL: https://api.example.com/openapi.json
        - GraphQL URL: https://api.example.com/graphql/
    """  # noqa: D301
    if no_color and force_color:
        raise click.UsageError(COLOR_OPTIONS_INVALID_USAGE_MESSAGE)

    config: SchemathesisConfig = ctx.obj.config

    # First, set the right color
    color: bool | None
    if force_color:
        color = True
    elif no_color:
        color = False
    else:
        color = config.color
    ensure_color(ctx, color)

    validation.validate_auth_overlap(auth, headers)

    # Then override the global config from CLI options
    config.update(
        color=color,
        suppress_health_check=suppress_health_check,
        seed=generation_seed,
        wait_for_schema=wait_for_schema,
        max_failures=max_failures,
    )
    config.output.sanitization.update(enabled=output_sanitize)
    config.output.truncation.update(enabled=output_truncate)
    config.reports.update(
        formats=report_formats,
        junit_path=report_junit_path.name if report_junit_path else None,
        vcr_path=report_vcr_path.name if report_vcr_path else None,
        har_path=report_har_path.name if report_har_path else None,
        directory=Path(report_directory),
        preserve_bytes=report_preserve_bytes,
    )
    # Other CLI options work as an override for all defined projects
    config.projects.override.update(
        base_url=base_url,
        headers=headers if headers else None,
        basic_auth=auth,
        workers=workers,
        continue_on_failure=continue_on_failure,
        rate_limit=rate_limit,
        max_redirects=max_redirects,
        request_timeout=request_timeout,
        tls_verify=request_tls_verify,
        request_cert=request_cert,
        request_cert_key=request_cert_key,
        proxy=request_proxy,
        warnings=WarningsConfig.from_value([w.value for w in warnings] if isinstance(warnings, list) else warnings)
        if warnings is not None
        else None,
    )
    # These are filters for what API operations should be tested
    filter_set = {
        "include_path": include_path,
        "include_method": include_method,
        "include_name": include_name,
        "include_tag": include_tag,
        "include_operation_id": include_operation_id,
        "include_path_regex": include_path_regex,
        "include_method_regex": include_method_regex,
        "include_name_regex": include_name_regex,
        "include_tag_regex": include_tag_regex,
        "include_operation_id_regex": include_operation_id_regex,
        "exclude_path": exclude_path,
        "exclude_method": exclude_method,
        "exclude_name": exclude_name,
        "exclude_tag": exclude_tag,
        "exclude_operation_id": exclude_operation_id,
        "exclude_path_regex": exclude_path_regex,
        "exclude_method_regex": exclude_method_regex,
        "exclude_name_regex": exclude_name_regex,
        "exclude_tag_regex": exclude_tag_regex,
        "exclude_operation_id_regex": exclude_operation_id_regex,
        "include_by": include_by,
        "exclude_by": exclude_by,
        "exclude_deprecated": exclude_deprecated,
    }
    config.projects.override.phases.update(phases=phases)
    config.projects.override.checks.update(
        included_check_names=included_check_names,
        excluded_check_names=excluded_check_names,
        max_response_time=max_response_time,
    )
    config.projects.override.generation.update(
        modes=generation_modes,
        max_examples=generation_max_examples,
        no_shrink=generation_no_shrink,
        maximize=generation_maximize,
        deterministic=generation_deterministic,
        database=generation_database,
        unique_inputs=generation_unique_inputs,
        allow_x00=generation_allow_x00,
        graphql_allow_null=generation_graphql_allow_null,
        with_security_parameters=generation_with_security_parameters,
        codec=generation_codec,
    )

    executor.execute(
        location=location,
        filter_set=filter_set,
        # We don't the project yet, so pass the default config
        config=config.projects.get_default(),
        args=ctx.args,
        params=ctx.params,
    )
