from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import click
from click.core import ParameterSource

from schemathesis.checks import load_all_checks
from schemathesis.cli.commands._options import apply_project_options, apply_report_options, build_filter_set
from schemathesis.cli.commands.run.filters import with_filters
from schemathesis.cli.constants import COLOR_OPTIONS_INVALID_USAGE_MESSAGE
from schemathesis.cli.core import ensure_color, resolve_color
from schemathesis.cli.ext.groups import group, grouped_option
from schemathesis.cli.ext.options import CsvChoice
from schemathesis.cli.options import (
    AUTH,
    AUTH_WFC,
    AUTH_WFC_USER,
    BASE_URL,
    BASELINE,
    BASELINE_PRUNE,
    BASELINE_UPDATE,
    CHECKS_OPTION,
    CONTINUE_ON_FAILURE,
    EXCLUDE_BY,
    EXCLUDE_CHECKS,
    EXCLUDE_DEPRECATED,
    FORCE_COLOR,
    GENERATION_ALLOW_X00,
    GENERATION_CODEC,
    GENERATION_DATABASE,
    GENERATION_DETERMINISTIC,
    GENERATION_GRAPHQL_ALLOW_NULL,
    GENERATION_MAX_EXAMPLES,
    GENERATION_MAXIMIZE,
    GENERATION_MODE,
    GENERATION_SEED,
    GENERATION_UNIQUE_INPUTS,
    GENERATION_WITH_SECURITY_PARAMETERS,
    HEADER,
    INCLUDE_BY,
    LOCATION,
    MAX_FAILURES,
    MAX_REDIRECTS,
    MAX_RESPONSE_TIME,
    MAX_TIME,
    NO_COLOR,
    ORIGIN,
    OUTPUT_SANITIZE,
    OUTPUT_TRUNCATE,
    PROXY,
    RATE_LIMIT,
    REPORT,
    REPORT_ALLURE_PATH,
    REPORT_DIR,
    REPORT_HAR_PATH,
    REPORT_HTML_PATH,
    REPORT_JSON_PATH,
    REPORT_JUNIT_PATH,
    REPORT_NDJSON_PATH,
    REPORT_PRESERVE_BYTES,
    REPORT_PYTEST_PATH,
    REPORT_VCR_PATH,
    REPORT_WFC_PATH,
    REQUEST_CERT,
    REQUEST_CERT_KEY,
    REQUEST_RETRIES,
    REQUEST_TIMEOUT,
    SUPPRESS_HEALTH_CHECK,
    TLS_VERIFY,
    WAIT_FOR_SCHEMA,
    WARNINGS,
    WORKERS,
)
from schemathesis.cli.validation import CONFLICTING_URL_MESSAGE, validate_auth_overlap, validate_wfc_user
from schemathesis.config import (
    HealthCheck,
    ProjectConfig,
    SchemathesisConfig,
    SchemathesisWarning,
)
from schemathesis.generation import GenerationMode
from schemathesis.generation.metrics import MetricFunction

load_all_checks()

DEFAULT_PHASES = ["examples", "coverage", "fuzzing", "stateful"]
# Timeouts this long are almost always milliseconds, the unit Schemathesis 3 took.
LIKELY_MILLISECONDS = 1000
OBJECT_LEVEL_AUTHORIZATION_GUIDE = "https://schemathesis.readthedocs.io/en/stable/guides/object-level-authorization/"


@click.argument(*LOCATION.args, **LOCATION.kwargs)
@group("Options")
@grouped_option(*BASE_URL.args, **BASE_URL.kwargs)
@grouped_option(*ORIGIN.args, **ORIGIN.kwargs)
@grouped_option(*WORKERS.args, **WORKERS.kwargs)
@grouped_option(*MAX_TIME.args, **MAX_TIME.kwargs)
@grouped_option(
    "--phases",
    help="A comma-separated list of test phases to run",
    type=CsvChoice(DEFAULT_PHASES),
    default=",".join(DEFAULT_PHASES),
    metavar="",
)
@grouped_option(*SUPPRESS_HEALTH_CHECK.args, **SUPPRESS_HEALTH_CHECK.kwargs)
@grouped_option(*WAIT_FOR_SCHEMA.args, **WAIT_FOR_SCHEMA.kwargs)
@grouped_option(*WARNINGS.args, **WARNINGS.kwargs)
@group("API validation options")
@grouped_option(*CHECKS_OPTION.args, **CHECKS_OPTION.kwargs)
@grouped_option(*EXCLUDE_CHECKS.args, **EXCLUDE_CHECKS.kwargs)
@grouped_option(*MAX_FAILURES.args, **MAX_FAILURES.kwargs)
@grouped_option(*CONTINUE_ON_FAILURE.args, **CONTINUE_ON_FAILURE.kwargs)
@grouped_option(*BASELINE.args, **BASELINE.kwargs)
@grouped_option(*BASELINE_UPDATE.args, **BASELINE_UPDATE.kwargs)
@grouped_option(*BASELINE_PRUNE.args, **BASELINE_PRUNE.kwargs)
@grouped_option(*MAX_RESPONSE_TIME.args, **MAX_RESPONSE_TIME.kwargs)
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
@grouped_option(*INCLUDE_BY.args, **INCLUDE_BY.kwargs)
@grouped_option(*EXCLUDE_BY.args, **EXCLUDE_BY.kwargs)
@grouped_option(*EXCLUDE_DEPRECATED.args, **EXCLUDE_DEPRECATED.kwargs)
@group("Network requests options")
@grouped_option(*HEADER.args, **HEADER.kwargs)
@grouped_option(*AUTH.args, **AUTH.kwargs)
@grouped_option(*AUTH_WFC.args, **AUTH_WFC.kwargs)
@grouped_option(*AUTH_WFC_USER.args, **AUTH_WFC_USER.kwargs)
@grouped_option(*PROXY.args, **PROXY.kwargs)
@grouped_option(*TLS_VERIFY.args, **TLS_VERIFY.kwargs)
@grouped_option(*RATE_LIMIT.args, **RATE_LIMIT.kwargs)
@grouped_option(*MAX_REDIRECTS.args, **MAX_REDIRECTS.kwargs)
@grouped_option(*REQUEST_TIMEOUT.args, **REQUEST_TIMEOUT.kwargs)
@grouped_option(*REQUEST_RETRIES.args, **REQUEST_RETRIES.kwargs)
@grouped_option(*REQUEST_CERT.args, **REQUEST_CERT.kwargs)
@grouped_option(*REQUEST_CERT_KEY.args, **REQUEST_CERT_KEY.kwargs)
@group("Output options")
@grouped_option(*REPORT.args, **REPORT.kwargs)
@grouped_option(*REPORT_DIR.args, **REPORT_DIR.kwargs)
@grouped_option(*REPORT_JUNIT_PATH.args, **REPORT_JUNIT_PATH.kwargs)
@grouped_option(*REPORT_VCR_PATH.args, **REPORT_VCR_PATH.kwargs)
@grouped_option(*REPORT_HAR_PATH.args, **REPORT_HAR_PATH.kwargs)
@grouped_option(*REPORT_NDJSON_PATH.args, **REPORT_NDJSON_PATH.kwargs)
@grouped_option(*REPORT_JSON_PATH.args, **REPORT_JSON_PATH.kwargs)
@grouped_option(*REPORT_WFC_PATH.args, **REPORT_WFC_PATH.kwargs)
@grouped_option(*REPORT_PYTEST_PATH.args, **REPORT_PYTEST_PATH.kwargs)
@grouped_option(*REPORT_ALLURE_PATH.args, **REPORT_ALLURE_PATH.kwargs)
@grouped_option(*REPORT_HTML_PATH.args, **REPORT_HTML_PATH.kwargs)
@grouped_option(*REPORT_PRESERVE_BYTES.args, **REPORT_PRESERVE_BYTES.kwargs)
@grouped_option(*OUTPUT_SANITIZE.args, **OUTPUT_SANITIZE.kwargs)
@grouped_option(*OUTPUT_TRUNCATE.args, **OUTPUT_TRUNCATE.kwargs)
@group("Data generation options")
@grouped_option(*GENERATION_MODE.args, **GENERATION_MODE.kwargs)
@grouped_option(*GENERATION_MAX_EXAMPLES.args, **GENERATION_MAX_EXAMPLES.kwargs)
@grouped_option(*GENERATION_SEED.args, **GENERATION_SEED.kwargs)
@grouped_option(
    "--no-shrink",
    "generation_no_shrink",
    help="Disable test case shrinking. Makes test failures harder to debug but improves performance",
    is_flag=True,
    default=None,
)
@grouped_option(*GENERATION_DETERMINISTIC.args, **GENERATION_DETERMINISTIC.kwargs)
@grouped_option(*GENERATION_ALLOW_X00.args, **GENERATION_ALLOW_X00.kwargs)
@grouped_option(*GENERATION_CODEC.args, **GENERATION_CODEC.kwargs)
@grouped_option(*GENERATION_MAXIMIZE.args, **GENERATION_MAXIMIZE.kwargs)
@grouped_option(*GENERATION_WITH_SECURITY_PARAMETERS.args, **GENERATION_WITH_SECURITY_PARAMETERS.kwargs)
@grouped_option(*GENERATION_GRAPHQL_ALLOW_NULL.args, **GENERATION_GRAPHQL_ALLOW_NULL.kwargs)
@grouped_option(*GENERATION_DATABASE.args, **GENERATION_DATABASE.kwargs)
@grouped_option(*GENERATION_UNIQUE_INPUTS.args, **GENERATION_UNIQUE_INPUTS.kwargs)
@group("Global options")
@grouped_option(*NO_COLOR.args, **NO_COLOR.kwargs)
@grouped_option(*FORCE_COLOR.args, **FORCE_COLOR.kwargs)
@click.pass_context
def run(
    ctx: click.Context,
    *,
    location: str,
    auth: tuple[str, str] | None,
    auth_wfc: str | None = None,
    auth_wfc_user: str | None = None,
    headers: dict[str, str],
    included_check_names: list[str] | None,
    excluded_check_names: list[str] | None,
    max_response_time: float | None = None,
    phases: list[str] = DEFAULT_PHASES,
    max_failures: int | None = None,
    max_time: int | None = None,
    continue_on_failure: bool | None = None,
    baseline: str | None = None,
    baseline_update: bool = False,
    baseline_prune: bool = False,
    workers: int | None = None,
    base_url: str | None,
    origin: str | None,
    wait_for_schema: float | None = None,
    suppress_health_check: list[HealthCheck] | None,
    warnings: bool | list[SchemathesisWarning] | None,
    rate_limit: str | None = None,
    max_redirects: int | None = None,
    request_timeout: int | None = None,
    request_retries: int | None = None,
    request_tls_verify: bool | None = None,
    request_cert: str | None = None,
    request_cert_key: str | None = None,
    request_proxy: str | None = None,
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

    Runs configured test phases in order and exits on completion.
    Use --phases to select which phases to run.

    \b
    LOCATION can be:
        - Local file: ./openapi.json, ./schema.yaml, ./schema.graphql
        - OpenAPI URL: https://api.example.com/openapi.json
        - GraphQL URL: https://api.example.com/graphql/
        - gRPC target: grpc://localhost:50051
    """  # noqa: D301
    if no_color and force_color:
        raise click.UsageError(COLOR_OPTIONS_INVALID_USAGE_MESSAGE)

    config: SchemathesisConfig = ctx.obj

    # First, set the right color
    color = resolve_color(force_color, no_color, config.color)
    ensure_color(ctx, color)

    validate_auth_overlap(auth, headers)
    validate_wfc_user(config, auth_wfc, auth_wfc_user)

    if base_url is not None and origin is not None:
        raise click.UsageError(CONFLICTING_URL_MESSAGE)

    # Then override the global config from CLI options
    config.update(
        color=color,
        suppress_health_check=suppress_health_check,
        seed=generation_seed,
        wait_for_schema=wait_for_schema,
        max_failures=max_failures,
        max_time=max_time,
    )
    config.output.sanitization.update(enabled=output_sanitize)
    config.output.truncation.update(enabled=output_truncate)
    apply_report_options(config.reports, ctx.params)
    # Other CLI options work as an override for all defined projects
    apply_project_options(config.projects.override, ctx.params, baseline=baseline, origin=origin)
    if (baseline_update or baseline_prune) and config.projects.get_default().baseline is None:
        flag = "--baseline-update" if baseline_update else "--baseline-prune"
        raise click.UsageError(
            f"{flag} needs a baseline file - pass `--baseline PATH` or set `baseline` in your configuration file"
        )

    # These are filters for what API operations should be tested
    filter_set = build_filter_set(ctx.params)
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

    from schemathesis.cli.commands.run import executor

    executor.execute(
        location=location,
        filter_set=filter_set,
        # We don't the project yet, so pass the default config
        config=config.projects.get_default(),
        args=ctx.args,
        params=ctx.params,
        startup_warnings=_startup_warnings(ctx, config, included_check_names),
    )


def _startup_warnings(
    ctx: click.Context, config: SchemathesisConfig, included_check_names: list[str] | None
) -> list[tuple[SchemathesisWarning | None, str]]:
    timeouts = _iter_timeouts_in_milliseconds(ctx, config.projects.get_default())
    return [
        *((SchemathesisWarning.TIMEOUT_UNITS, message) for message in timeouts),
        *((None, message) for message in _iter_unrunnable_checks(config, included_check_names)),
    ]


def _iter_timeouts_in_milliseconds(ctx: click.Context, config: ProjectConfig) -> Iterator[str]:
    if not config.warnings.should_display(SchemathesisWarning.TIMEOUT_UNITS):
        return
    for value, parameter, option, key in (
        (config.request_timeout, "request_timeout", "--request-timeout", "request-timeout"),
        (config.checks.max_response_time.limit, "max_response_time", "--max-response-time", "max_response_time"),
    ):
        if value is None or value < LIKELY_MILLISECONDS:
            continue
        if ctx.get_parameter_source(parameter) is ParameterSource.DEFAULT:
            given, suggested = f"{key} = {_format_number(value)}", f"{key} = {_format_number(value / 1000)}"
        else:
            given, suggested = f"{option}={_format_number(value)}", f"{option}={_format_number(value / 1000)}"
        yield f"`{given}` is {_describe_duration(value)}; Schemathesis takes seconds. Did you mean `{suggested}`?"


def _iter_unrunnable_checks(config: SchemathesisConfig, included_check_names: list[str] | None) -> Iterator[str]:
    # Only a check selected by name: one enabled by default or by `all` is not something the user asked for.
    if "object_level_authorization" not in (included_check_names or []):
        return
    projects = (config.projects.default, *config.projects.named.values())
    if any(project.auth.wfc is not None and project.auth.wfc.peers for project in projects):
        return
    yield (
        "`object_level_authorization` will not run: list at least 2 users as `peers` under `[auth.wfc]`. "
        f"See {OBJECT_LEVEL_AUTHORIZATION_GUIDE}"
    )


def _format_number(value: float) -> str:
    return f"{value:f}".rstrip("0").rstrip(".")


def _describe_duration(seconds: float) -> str:
    if seconds < 3600:
        return f"{round(seconds / 60)} minutes"
    hours = round(seconds / 3600)
    return f"{hours} hour" if hours == 1 else f"{hours} hours"
