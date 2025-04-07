from __future__ import annotations

from pathlib import Path
from random import Random
from typing import Any, Sequence

import click
from click.utils import LazyFile

from schemathesis import contrib, experimental
from schemathesis.checks import CHECKS
from schemathesis.cli.commands.run import executor, validation
from schemathesis.cli.commands.run.checks import CheckArguments
from schemathesis.cli.commands.run.filters import FilterArguments, with_filters
from schemathesis.cli.commands.run.hypothesis import (
    HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER,
    prepare_phases,
    prepare_settings,
)
from schemathesis.cli.constants import DEFAULT_WORKERS, MAX_WORKERS, MIN_WORKERS
from schemathesis.cli.core import ensure_color
from schemathesis.cli.ext.groups import group, grouped_option
from schemathesis.cli.ext.options import (
    CsvChoice,
    CsvEnumChoice,
    CustomHelpMessageChoice,
    RegistryChoice,
)
from schemathesis.config import DEFAULT_REPORT_DIRECTORY, HealthCheck, ReportFormat, SchemathesisConfig
from schemathesis.core.output import OutputConfig
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT
from schemathesis.engine.config import EngineConfig, ExecutionConfig, NetworkConfig
from schemathesis.engine.phases import PhaseName
from schemathesis.generation import DEFAULT_GENERATOR_MODES, GenerationConfig, GenerationMode
from schemathesis.generation.targets import TARGETS

# NOTE: Need to explicitly import all registered checks
from schemathesis.specs.openapi.checks import *  # noqa: F401, F403

COLOR_OPTIONS_INVALID_USAGE_MESSAGE = "Can't use `--no-color` and `--force-color` simultaneously"

DEFAULT_PHASES = ("examples", "coverage", "fuzzing", "stateful")


@click.argument("schema", type=str)  # type: ignore[misc]
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
@group("API validation options")
@grouped_option(
    "--checks",
    "-c",
    "included_check_names",
    multiple=True,
    help="Comma-separated list of checks to run against API responses",
    type=RegistryChoice(CHECKS, with_all=True),
    default=("not_a_server_error",),
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
    default=(),
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
    help="Include using custom expression",
)
@grouped_option(
    "--exclude-by",
    "exclude_by",
    type=str,
    metavar="EXPR",
    help="Exclude using custom expression",
)
@grouped_option(
    "--exclude-deprecated",
    help="Skip deprecated operations",
    is_flag=True,
    is_eager=True,
    default=False,
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
    default="true",
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
    default=False,
    callback=validation.validate_preserve_bytes,
)
@grouped_option(
    "--output-sanitize",
    type=str,
    default="true",
    show_default=True,
    help="Enable or disable automatic output sanitization to obscure sensitive data",
    metavar="BOOLEAN",
    callback=validation.convert_boolean_string,
)
@grouped_option(
    "--output-truncate",
    help="Truncate schemas and responses in error messages",
    type=str,
    default="true",
    show_default=True,
    metavar="BOOLEAN",
    callback=validation.convert_boolean_string,
)
@group("Experimental options")
@grouped_option(
    "--experimental",
    "experiments",
    help="Enable experimental features",
    type=click.Choice(sorted([experiment.label for experiment in experimental.GLOBAL_EXPERIMENTS.available])),
    callback=validation.convert_experimental,
    multiple=True,
    metavar="FEATURES",
)
@grouped_option(
    "--experimental-coverage-unexpected-methods",
    "coverage_unexpected_methods",
    help="HTTP methods to use when generating test cases with methods not specified in the API during the coverage phase.",
    type=CsvChoice(["get", "put", "post", "delete", "options", "head", "patch", "trace"], case_sensitive=False),
    callback=validation.convert_http_methods,
    metavar="",
    default=None,
    envvar="SCHEMATHESIS_EXPERIMENTAL_COVERAGE_UNEXPECTED_METHODS",
)
@group("Data generation options")
@grouped_option(
    "--mode",
    "-m",
    "generation_modes",
    help="Test data generation mode",
    type=click.Choice([item.value for item in GenerationMode] + ["all"]),
    default=GenerationMode.default().value,
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
    default="true",
    show_default=True,
    metavar="BOOLEAN",
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
    "--generation-maximize",
    "generation_maximize",
    multiple=True,
    help="Guide input generation to values more likely to expose bugs via targeted property-based testing",
    type=RegistryChoice(TARGETS),
    default=None,
    callback=validation.reduce_list,
    show_default=True,
    metavar="METRIC",
)
@grouped_option(
    "--generation-with-security-parameters",
    help="Whether to generate security parameters",
    type=str,
    default="true",
    show_default=True,
    callback=validation.convert_boolean_string,
    metavar="BOOLEAN",
)
@grouped_option(
    "--generation-graphql-allow-null",
    help="Whether to use `null` values for optional arguments in GraphQL queries",
    type=str,
    default="true",
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
    default=False,
    show_default=True,
    metavar="BOOLEAN",
)
@grouped_option(
    "--contrib-openapi-fill-missing-examples",
    "contrib_openapi_fill_missing_examples",
    help="Enable generation of random examples for API operations that do not have explicit examples",
    is_flag=True,
    default=False,
    show_default=True,
    metavar="BOOLEAN",
)
@group("Global options")
@grouped_option("--no-color", help="Disable ANSI color escape codes", type=bool, is_flag=True)
@grouped_option("--force-color", help="Explicitly tells to enable ANSI color escape codes", type=bool, is_flag=True)
@click.pass_context  # type: ignore[misc]
def run(
    ctx: click.Context,
    *,
    schema: str,
    auth: tuple[str, str] | None,
    headers: dict[str, str],
    experiments: list,
    coverage_unexpected_methods: set[str] | None,
    missing_required_header_allowed_statuses: list[str],
    positive_data_acceptance_allowed_statuses: list[str],
    negative_data_rejection_allowed_statuses: list[str],
    included_check_names: Sequence[str],
    excluded_check_names: Sequence[str],
    max_response_time: float | None = None,
    phases: Sequence[str] = DEFAULT_PHASES,
    max_failures: int | None = None,
    continue_on_failure: bool = False,
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
    base_url: str | None,
    wait_for_schema: float | None = None,
    rate_limit: str | None = None,
    suppress_health_check: list[HealthCheck] | None,
    request_timeout: int | None = None,
    request_tls_verify: bool = True,
    request_cert: str | None = None,
    request_cert_key: str | None = None,
    request_proxy: str | None = None,
    report_formats: list[ReportFormat] | None,
    report_directory: Path | str = DEFAULT_REPORT_DIRECTORY,
    report_junit_path: LazyFile | None = None,
    report_vcr_path: LazyFile | None = None,
    report_har_path: LazyFile | None = None,
    report_preserve_bytes: bool = False,
    output_sanitize: bool = True,
    output_truncate: bool = True,
    contrib_openapi_fill_missing_examples: bool = False,
    generation_modes: tuple[GenerationMode, ...] = DEFAULT_GENERATOR_MODES,
    generation_seed: int | None = None,
    generation_max_examples: int | None = None,
    generation_maximize: Sequence[str] | None = None,
    generation_deterministic: bool | None = None,
    generation_database: str | None = None,
    generation_unique_inputs: bool = False,
    generation_allow_x00: bool = True,
    generation_graphql_allow_null: bool = True,
    generation_with_security_parameters: bool = True,
    generation_codec: str = "utf-8",
    generation_no_shrink: bool = False,
    force_color: bool = False,
    no_color: bool = False,
    **__kwargs: Any,
) -> None:
    """Run tests against an API using a specified SCHEMA.

    [Required] SCHEMA: Path to an OpenAPI (`.json`, `.yml`) or GraphQL SDL file, or a URL pointing to such specifications
    """
    if no_color and force_color:
        raise click.UsageError(COLOR_OPTIONS_INVALID_USAGE_MESSAGE)

    cfg: SchemathesisConfig = ctx.obj.config

    if force_color:
        color = True
    elif no_color:
        color = False
    else:
        color = None
    cfg.override(
        color=color,
        suppress_health_check=suppress_health_check,
        max_failures=max_failures,
    )
    cfg.projects.default.override(
        base_url=base_url,
    )
    cfg.reports.override(
        formats=report_formats,
        junit_path=report_junit_path.name if report_junit_path else None,
        vcr_path=report_vcr_path.name if report_vcr_path else None,
        har_path=report_har_path.name if report_har_path else None,
        directory=Path(report_directory),
        preserve_bytes=report_preserve_bytes,
    )

    ensure_color(ctx, cfg)

    validation.validate_schema(schema, cfg.projects.default.base_url)

    _hypothesis_phases = prepare_phases(generation_no_shrink)

    for experiment in experiments:
        experiment.enable()
    if contrib_openapi_fill_missing_examples:
        contrib.openapi.fill_missing_examples.install()

    validation.validate_auth_overlap(auth, headers)

    filter_set = FilterArguments(
        include_path=include_path,
        include_method=include_method,
        include_name=include_name,
        include_tag=include_tag,
        include_operation_id=include_operation_id,
        include_path_regex=include_path_regex,
        include_method_regex=include_method_regex,
        include_name_regex=include_name_regex,
        include_tag_regex=include_tag_regex,
        include_operation_id_regex=include_operation_id_regex,
        exclude_path=exclude_path,
        exclude_method=exclude_method,
        exclude_name=exclude_name,
        exclude_tag=exclude_tag,
        exclude_operation_id=exclude_operation_id,
        exclude_path_regex=exclude_path_regex,
        exclude_method_regex=exclude_method_regex,
        exclude_name_regex=exclude_name_regex,
        exclude_tag_regex=exclude_tag_regex,
        exclude_operation_id_regex=exclude_operation_id_regex,
        include_by=include_by,
        exclude_by=exclude_by,
        exclude_deprecated=exclude_deprecated,
    ).into()

    selected_checks, _ = CheckArguments(
        included_check_names=included_check_names,
        excluded_check_names=excluded_check_names,
        max_response_time=max_response_time,
    ).into()

    # Use the same seed for all tests unless `derandomize=True` is used
    seed: int | None
    if generation_seed is None and not generation_deterministic:
        seed = Random().getrandbits(128)
    else:
        seed = generation_seed

    phases_ = [PhaseName.PROBING] + [PhaseName.from_str(phase) for phase in phases]

    config = executor.RunConfig(
        location=schema,
        config=cfg,
        engine=EngineConfig(
            execution=ExecutionConfig(
                phases=phases_,
                checks=selected_checks,
                targets=TARGETS.get_by_names(generation_maximize or []),
                hypothesis_settings=prepare_settings(
                    database=generation_database,
                    derandomize=generation_deterministic,
                    max_examples=generation_max_examples,
                    phases=_hypothesis_phases,
                    suppress_health_check=cfg.suppress_health_check,
                ),
                generation=GenerationConfig(
                    modes=list(generation_modes),
                    allow_x00=generation_allow_x00,
                    graphql_allow_null=generation_graphql_allow_null,
                    codec=generation_codec,
                    with_security_parameters=generation_with_security_parameters,
                    unexpected_methods=coverage_unexpected_methods,
                ),
                max_failures=cfg.max_failures,
                continue_on_failure=continue_on_failure,
                unique_inputs=generation_unique_inputs,
                seed=seed,
                workers_num=workers_num,
            ),
            network=NetworkConfig(
                auth=auth,
                headers=headers,
                timeout=request_timeout,
                tls_verify=request_tls_verify,
                proxy=request_proxy,
                cert=(request_cert, request_cert_key)
                if request_cert is not None and request_cert_key is not None
                else request_cert,
            ),
        ),
        filter_set=filter_set,
        wait_for_schema=wait_for_schema,
        rate_limit=rate_limit,
        output=OutputConfig(sanitize=output_sanitize, truncate=output_truncate),
        args=ctx.args,
        params=ctx.params,
    )
    executor.execute(config)
