from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import click
from click.utils import LazyFile

from schemathesis.checks import load_all_checks
from schemathesis.cli.commands.run import executor, validation
from schemathesis.cli.constants import COLOR_OPTIONS_INVALID_USAGE_MESSAGE
from schemathesis.cli.core import ensure_color
from schemathesis.cli.ext.groups import group, grouped_option
from schemathesis.cli.ext.options import (
    CsvChoice,
    CsvEnumChoice,
)
from schemathesis.cli.options import generation_options, global_options, network_options
from schemathesis.config import (
    DEFAULT_REPORT_DIRECTORY,
    HealthCheck,
    ReportFormat,
    SchemathesisConfig,
    SchemathesisWarning,
    WarningsConfig,
)
from schemathesis.generation import GenerationMode
from schemathesis.generation.metrics import MetricFunction

load_all_checks()

DEFAULT_PHASES = ["examples", "coverage", "fuzzing", "stateful"]


@click.argument(  # type: ignore[untyped-decorator]
    "location",
    type=str,
    callback=validation.validate_schema_location,
)
@network_options()
@grouped_option(
    "--phases",
    help="A comma-separated list of test phases to run",
    type=CsvChoice(DEFAULT_PHASES),
    default=",".join(DEFAULT_PHASES),
    metavar="",
    group="Options",
)
@grouped_option(
    "--warnings",
    help="Control warning display: 'off' to disable all, or comma-separated list of warning types to enable",
    type=str,
    default=None,
    callback=validation.validate_warnings,
    metavar="WARNINGS",
    group="Options",
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
    "--report-ndjson-path",
    help="Custom path for NDJSON events file",
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
@generation_options()
@global_options()
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
    request_retries: int | None = None,
    request_tls_verify: bool | None = None,
    request_cert: str | None = None,
    request_cert_key: str | None = None,
    request_proxy: str | None = None,
    report_formats: list[ReportFormat] | None,
    report_directory: Path | str = DEFAULT_REPORT_DIRECTORY,
    report_junit_path: LazyFile | None = None,
    report_vcr_path: LazyFile | None = None,
    report_har_path: LazyFile | None = None,
    report_ndjson_path: LazyFile | None = None,
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
        ndjson_path=report_ndjson_path.name if report_ndjson_path else None,
        directory=Path(report_directory),
        preserve_bytes=report_preserve_bytes,
    )
    # Other CLI options work as an override for all defined projects
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
