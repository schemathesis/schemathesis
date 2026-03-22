from __future__ import annotations

from collections.abc import Callable
from typing import Any

import click

from schemathesis.checks import load_all_checks
from schemathesis.cli.commands.fuzz import executor
from schemathesis.cli.commands.run.filters import with_filters
from schemathesis.cli.constants import COLOR_OPTIONS_INVALID_USAGE_MESSAGE
from schemathesis.cli.core import ensure_color
from schemathesis.cli.ext.groups import group, grouped_option
from schemathesis.cli.options import (
    AUTH,
    BASE_URL,
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
    NO_COLOR,
    OUTPUT_SANITIZE,
    OUTPUT_TRUNCATE,
    PROXY,
    RATE_LIMIT,
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
from schemathesis.cli.validation import validate_auth_overlap
from schemathesis.config import (
    FuzzConfig,
    HealthCheck,
    SchemathesisConfig,
    SchemathesisWarning,
    WarningsConfig,
)
from schemathesis.generation import GenerationMode
from schemathesis.generation.metrics import MetricFunction

load_all_checks()


@click.argument(*LOCATION.args, **LOCATION.kwargs)  # type: ignore[untyped-decorator]
@group("Options")
@grouped_option(*BASE_URL.args, **BASE_URL.kwargs)
@grouped_option(*WORKERS.args, **WORKERS.kwargs)
@grouped_option(
    "--max-time",
    "max_time",
    help="Stop fuzzing after this many seconds",
    type=int,
    default=None,
    metavar="SECONDS",
)
@grouped_option(
    "--max-steps",
    "max_steps",
    help="Maximum number of steps per fuzzing scenario",
    type=int,
    default=None,
    metavar="STEPS",
)
@grouped_option(*SUPPRESS_HEALTH_CHECK.args, **SUPPRESS_HEALTH_CHECK.kwargs)
@grouped_option(*WAIT_FOR_SCHEMA.args, **WAIT_FOR_SCHEMA.kwargs)
@grouped_option(*WARNINGS.args, **WARNINGS.kwargs)
@group("API validation options")
@grouped_option(*CHECKS_OPTION.args, **CHECKS_OPTION.kwargs)
@grouped_option(*EXCLUDE_CHECKS.args, **EXCLUDE_CHECKS.kwargs)
@grouped_option(*MAX_FAILURES.args, **MAX_FAILURES.kwargs)
@grouped_option(*CONTINUE_ON_FAILURE.args, **CONTINUE_ON_FAILURE.kwargs)
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
@grouped_option(*PROXY.args, **PROXY.kwargs)
@grouped_option(*TLS_VERIFY.args, **TLS_VERIFY.kwargs)
@grouped_option(*RATE_LIMIT.args, **RATE_LIMIT.kwargs)
@grouped_option(*MAX_REDIRECTS.args, **MAX_REDIRECTS.kwargs)
@grouped_option(*REQUEST_TIMEOUT.args, **REQUEST_TIMEOUT.kwargs)
@grouped_option(*REQUEST_RETRIES.args, **REQUEST_RETRIES.kwargs)
@grouped_option(*REQUEST_CERT.args, **REQUEST_CERT.kwargs)
@grouped_option(*REQUEST_CERT_KEY.args, **REQUEST_CERT_KEY.kwargs)
@group("Output options")
@grouped_option(*OUTPUT_SANITIZE.args, **OUTPUT_SANITIZE.kwargs)
@grouped_option(*OUTPUT_TRUNCATE.args, **OUTPUT_TRUNCATE.kwargs)
@group("Data generation options")
@grouped_option(*GENERATION_MODE.args, **GENERATION_MODE.kwargs)
@grouped_option(*GENERATION_MAX_EXAMPLES.args, **GENERATION_MAX_EXAMPLES.kwargs)
@grouped_option(*GENERATION_SEED.args, **GENERATION_SEED.kwargs)
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
@click.pass_context  # type: ignore[untyped-decorator]
def fuzz(
    ctx: click.Context,
    *,
    location: str,
    auth: tuple[str, str] | None,
    headers: dict[str, str],
    included_check_names: list[str] | None,
    excluded_check_names: list[str] | None,
    max_response_time: float | None = None,
    max_time: int | None = None,
    max_steps: int | None = None,
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
    force_color: bool = False,
    no_color: bool = False,
    **__kwargs: Any,
) -> None:
    """Run continuous fuzzing tests against your API.

    \b
    LOCATION can be:
        - Local file: ./openapi.json, ./schema.yaml, ./schema.graphql
        - OpenAPI URL: https://api.example.com/openapi.json
        - GraphQL URL: https://api.example.com/graphql/
    """  # noqa: D301
    if no_color and force_color:
        raise click.UsageError(COLOR_OPTIONS_INVALID_USAGE_MESSAGE)

    config: SchemathesisConfig = ctx.obj.config

    color: bool | None
    if force_color:
        color = True
    elif no_color:
        color = False
    else:
        color = config.color
    ensure_color(ctx, color)

    validate_auth_overlap(auth, headers)

    config.update(
        color=color,
        suppress_health_check=suppress_health_check,
        seed=generation_seed,
        wait_for_schema=wait_for_schema,
        max_failures=max_failures,
    )
    config.output.sanitization.update(enabled=output_sanitize)
    config.output.truncation.update(enabled=output_truncate)
    config.projects.override.update(
        base_url=base_url,
        headers=headers or None,
        basic_auth=auth,
        workers=workers,
        continue_on_failure=continue_on_failure,
        rate_limit=rate_limit,
        max_redirects=max_redirects,
        request_timeout=request_timeout,
        request_retries=request_retries,
        tls_verify=request_tls_verify,
        request_cert=request_cert,
        request_cert_key=request_cert_key,
        proxy=request_proxy,
        warnings=WarningsConfig.from_value([w.value for w in warnings] if isinstance(warnings, list) else warnings)
        if warnings is not None
        else None,
    )
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
    config.projects.override.checks.update(
        included_check_names=included_check_names,
        excluded_check_names=excluded_check_names,
        max_response_time=max_response_time,
    )
    config.projects.override.generation.update(
        modes=generation_modes,
        max_examples=generation_max_examples,
        maximize=generation_maximize,
        deterministic=generation_deterministic,
        database=generation_database,
        unique_inputs=generation_unique_inputs,
        allow_x00=generation_allow_x00,
        graphql_allow_null=generation_graphql_allow_null,
        with_security_parameters=generation_with_security_parameters,
        codec=generation_codec,
    )

    fuzz_config = FuzzConfig(max_time=max_time, max_steps=max_steps)

    executor.execute(
        location=location,
        filter_set=filter_set,
        config=config.projects.get_default(),
        fuzz_config=fuzz_config,
    )
