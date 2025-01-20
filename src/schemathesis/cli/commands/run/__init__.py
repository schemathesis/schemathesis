from __future__ import annotations

from random import Random
from typing import Any, Sequence

import click

from schemathesis import contrib, experimental
from schemathesis.checks import CHECKS
from schemathesis.cli.commands.run import executor, validation
from schemathesis.cli.commands.run.checks import CheckArguments
from schemathesis.cli.commands.run.filters import FilterArguments, with_filters
from schemathesis.cli.commands.run.handlers.cassettes import CassetteConfig, CassetteFormat
from schemathesis.cli.commands.run.hypothesis import (
    HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER,
    HealthCheck,
    Phase,
    prepare_health_checks,
    prepare_phases,
    prepare_settings,
)
from schemathesis.cli.constants import DEFAULT_WORKERS, MAX_WORKERS, MIN_WORKERS
from schemathesis.cli.core import ensure_color
from schemathesis.cli.ext.groups import group, grouped_option
from schemathesis.cli.ext.options import (
    CsvChoice,
    CsvEnumChoice,
    CsvListChoice,
    CustomHelpMessageChoice,
    RegistryChoice,
)
from schemathesis.core.output import OutputConfig
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT
from schemathesis.engine.config import EngineConfig, ExecutionConfig, NetworkConfig
from schemathesis.engine.phases import PhaseName
from schemathesis.generation import DEFAULT_GENERATOR_MODES, GenerationConfig, GenerationMode
from schemathesis.generation.overrides import Override
from schemathesis.generation.targets import TARGETS

# NOTE: Need to explicitly import all registered checks
from schemathesis.specs.openapi.checks import *  # noqa: F401, F403

COLOR_OPTIONS_INVALID_USAGE_MESSAGE = "Can't use `--no-color` and `--force-color` simultaneously"

DEFAULT_PHASES = ("unit", "stateful")


@click.argument("schema", type=str)  # type: ignore[misc]
@group("Options")
@grouped_option(
    "--phases",
    help="A comma-separated list of test phases to run",
    type=CsvChoice(["unit", "stateful"]),
    default=",".join(DEFAULT_PHASES),
    metavar="",
)
@grouped_option(
    "--base-url",
    "-b",
    help="Base URL of the API, required when schema is provided as a file",
    type=str,
    callback=validation.validate_base_url,
    envvar="SCHEMATHESIS_BASE_URL",
)
@grouped_option(
    "--suppress-health-check",
    help="A comma-separated list of Schemathesis health checks to disable",
    type=CsvEnumChoice(HealthCheck),
    metavar="",
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
@group("Loader options")
@grouped_option(
    "--wait-for-schema",
    help="Maximum duration, in seconds, to wait for the API schema to become available. Disabled by default",
    type=click.FloatRange(1.0),
    default=None,
    envvar="SCHEMATHESIS_WAIT_FOR_SCHEMA",
)
@group("Network requests options")
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
@grouped_option(
    "--request-timeout",
    help="Timeout limit, in seconds, for each network request during tests",
    type=click.FloatRange(min=0.0, min_open=True),
    default=DEFAULT_RESPONSE_TIMEOUT,
)
@grouped_option(
    "--request-proxy",
    help="Set the proxy for all network requests",
    type=str,
)
@grouped_option(
    "--request-tls-verify",
    help="Configures TLS certificate verification for server requests. Can specify path to CA_BUNDLE for custom certs",
    type=str,
    default="true",
    show_default=True,
    callback=validation.convert_boolean_string,
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
@grouped_option(
    "--rate-limit",
    help="Specify a rate limit for test requests in '<limit>/<duration>' format. "
    "Example - `100/m` for 100 requests per minute",
    type=str,
    callback=validation.validate_rate_limit,
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
    type=click.Choice([item.name.lower() for item in CassetteFormat]),
    default=CassetteFormat.VCR.name.lower(),
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
    "--output-sanitize",
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
    "--experimental-no-failfast",
    "no_failfast",
    help="Continue testing an API operation after a failure is found",
    is_flag=True,
    default=False,
    metavar="",
    envvar="SCHEMATHESIS_EXPERIMENTAL_NO_FAILFAST",
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
@group("Data generation options")
@grouped_option(
    "--generation-mode",
    "generation_modes",
    help="Specify the approach Schemathesis uses to generate test data. "
    "Use 'positive' for valid data, 'negative' for invalid data, or 'all' for both",
    type=click.Choice([item.value for item in GenerationMode] + ["all"]),
    default=GenerationMode.default().value,
    callback=validation.convert_generation_mode,
    show_default=True,
    metavar="",
)
@grouped_option(
    "--generation-seed",
    help="Seed value for Schemathesis, ensuring reproducibility across test runs",
    type=int,
)
@grouped_option(
    "--generation-max-examples",
    help="The cap on the number of examples generated by Schemathesis for each API operation",
    type=click.IntRange(1),
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
    "--generation-optimize",
    "generation_optimize",
    multiple=True,
    help="Guide input generation to values more likely to expose bugs via targeted property-based testing",
    type=RegistryChoice(TARGETS),
    default=None,
    callback=validation.reduce_list,
    show_default=True,
    metavar="TARGET",
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
@group("Global options")
@grouped_option("--no-color", help="Disable ANSI color escape codes", type=bool, is_flag=True)
@grouped_option("--force-color", help="Explicitly tells to enable ANSI color escape codes", type=bool, is_flag=True)
@click.pass_context  # type: ignore[misc]
def run(
    ctx: click.Context,
    schema: str,
    auth: tuple[str, str] | None,
    headers: dict[str, str],
    set_query: dict[str, str],
    set_header: dict[str, str],
    set_cookie: dict[str, str],
    set_path: dict[str, str],
    experiments: list,
    no_failfast: bool,
    missing_required_header_allowed_statuses: list[str],
    positive_data_acceptance_allowed_statuses: list[str],
    negative_data_rejection_allowed_statuses: list[str],
    included_check_names: Sequence[str],
    excluded_check_names: Sequence[str],
    phases: Sequence[str] = DEFAULT_PHASES,
    max_response_time: float | None = None,
    exit_first: bool = False,
    max_failures: int | None = None,
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
    suppress_health_check: list[HealthCheck] | None = None,
    request_timeout: int | None = None,
    request_tls_verify: bool = True,
    request_cert: str | None = None,
    request_cert_key: str | None = None,
    request_proxy: str | None = None,
    junit_xml: click.utils.LazyFile | None = None,
    cassette_path: click.utils.LazyFile | None = None,
    cassette_format: CassetteFormat = CassetteFormat.VCR,
    cassette_preserve_exact_body_bytes: bool = False,
    wait_for_schema: float | None = None,
    rate_limit: str | None = None,
    output_sanitize: bool = True,
    output_truncate: bool = True,
    contrib_openapi_fill_missing_examples: bool = False,
    hypothesis_phases: list[Phase] | None = None,
    hypothesis_no_phases: list[Phase] | None = None,
    generation_modes: tuple[GenerationMode, ...] = DEFAULT_GENERATOR_MODES,
    generation_seed: int | None = None,
    generation_max_examples: int | None = None,
    generation_optimize: Sequence[str] | None = None,
    generation_deterministic: bool | None = None,
    generation_database: str | None = None,
    generation_unique_inputs: bool = False,
    generation_allow_x00: bool = True,
    generation_graphql_allow_null: bool = True,
    generation_with_security_parameters: bool = True,
    generation_codec: str = "utf-8",
    force_color: bool = False,
    no_color: bool = False,
    **__kwargs: Any,
) -> None:
    """Run tests against an API using a specified SCHEMA.

    [Required] SCHEMA: Path to an OpenAPI (`.json`, `.yml`) or GraphQL SDL file, or a URL pointing to such specifications
    """
    if no_color and force_color:
        raise click.UsageError(COLOR_OPTIONS_INVALID_USAGE_MESSAGE)
    ensure_color(ctx, no_color, force_color)

    validation.validate_schema(schema, base_url)

    _hypothesis_phases = prepare_phases(hypothesis_phases, hypothesis_no_phases)
    _hypothesis_suppress_health_check = prepare_health_checks(suppress_health_check)

    for experiment in experiments:
        experiment.enable()
    if contrib_openapi_fill_missing_examples:
        contrib.openapi.fill_missing_examples.install()

    override = Override(query=set_query, headers=set_header, cookies=set_cookie, path_parameters=set_path)

    validation.validate_auth_overlap(auth, headers, override)

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

    selected_checks, checks_config = CheckArguments(
        included_check_names=included_check_names,
        excluded_check_names=excluded_check_names,
        positive_data_acceptance_allowed_statuses=positive_data_acceptance_allowed_statuses,
        missing_required_header_allowed_statuses=missing_required_header_allowed_statuses,
        negative_data_rejection_allowed_statuses=negative_data_rejection_allowed_statuses,
        max_response_time=max_response_time,
    ).into()

    if exit_first and max_failures is None:
        max_failures = 1

    cassette_config = None
    if cassette_path is not None:
        cassette_config = CassetteConfig(
            path=cassette_path,
            format=cassette_format,
            sanitize_output=output_sanitize,
            preserve_exact_body_bytes=cassette_preserve_exact_body_bytes,
        )

    # Use the same seed for all tests unless `derandomize=True` is used
    seed: int | None
    if generation_seed is None and not generation_deterministic:
        seed = Random().getrandbits(128)
    else:
        seed = generation_seed

    phases_ = [PhaseName.PROBING] + [PhaseName.from_str(phase) for phase in phases]

    config = executor.RunConfig(
        location=schema,
        base_url=base_url,
        engine=EngineConfig(
            execution=ExecutionConfig(
                phases=phases_,
                checks=selected_checks,
                targets=TARGETS.get_by_names(generation_optimize or []),
                hypothesis_settings=prepare_settings(
                    database=generation_database,
                    derandomize=generation_deterministic,
                    max_examples=generation_max_examples,
                    phases=_hypothesis_phases,
                    suppress_health_check=_hypothesis_suppress_health_check,
                ),
                generation=GenerationConfig(
                    modes=list(generation_modes),
                    allow_x00=generation_allow_x00,
                    graphql_allow_null=generation_graphql_allow_null,
                    codec=generation_codec,
                    with_security_parameters=generation_with_security_parameters,
                ),
                max_failures=max_failures,
                no_failfast=no_failfast,
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
            override=override,
            checks_config=checks_config,
        ),
        filter_set=filter_set,
        wait_for_schema=wait_for_schema,
        rate_limit=rate_limit,
        output=OutputConfig(sanitize=output_sanitize, truncate=output_truncate),
        cassette=cassette_config,
        junit_xml=junit_xml,
        args=ctx.args,
        params=ctx.params,
    )
    executor.execute(config)
