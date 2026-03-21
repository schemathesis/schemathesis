from __future__ import annotations

import functools
from collections.abc import Callable

import click

from schemathesis.checks import CHECKS
from schemathesis.cli.commands.run import validation
from schemathesis.cli.commands.run.filters import with_filters
from schemathesis.cli.constants import MAX_WORKERS, MIN_WORKERS
from schemathesis.cli.ext.groups import group, grouped_option
from schemathesis.cli.ext.options import (
    CsvEnumChoice,
    CustomHelpMessageChoice,
    RegistryChoice,
)
from schemathesis.config import HealthCheck
from schemathesis.core import HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT
from schemathesis.generation import GenerationMode
from schemathesis.generation.metrics import METRICS


def _chain(*decorators: Callable) -> Callable:
    def apply(f: Callable) -> Callable:
        return functools.reduce(lambda fn, dec: dec(fn), reversed(decorators), f)

    return apply


def network_options() -> Callable:
    """Shared network/transport and validation options for run and fuzz."""
    return _chain(
        group("Options"),
        grouped_option(
            "--url",
            "-u",
            "base_url",
            help="API base URL (required for file-based schemas)",
            metavar="URL",
            type=str,
            callback=validation.validate_base_url,
            envvar="SCHEMATHESIS_BASE_URL",
        ),
        grouped_option(
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
        ),
        grouped_option(
            "--suppress-health-check",
            help="A comma-separated list of Schemathesis health checks to disable",
            type=CsvEnumChoice(HealthCheck),
            metavar="",
        ),
        grouped_option(
            "--wait-for-schema",
            help="Maximum duration, in seconds, to wait for the API schema to become available. Disabled by default",
            type=click.FloatRange(1.0),
            default=None,
            envvar="SCHEMATHESIS_WAIT_FOR_SCHEMA",
        ),
        group("API validation options"),
        grouped_option(
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
        ),
        grouped_option(
            "--exclude-checks",
            "excluded_check_names",
            multiple=True,
            help="Comma-separated list of checks to skip during testing",
            type=RegistryChoice(CHECKS, with_all=True),
            default=None,
            callback=validation.reduce_list,
            show_default=True,
            metavar="",
        ),
        grouped_option(
            "--max-failures",
            "max_failures",
            type=click.IntRange(min=1),
            help="Terminate the test suite after reaching a specified number of failures or errors",
            show_default=True,
        ),
        grouped_option(
            "--continue-on-failure",
            "continue_on_failure",
            help="Continue executing all test cases within a scenario, even after encountering failures",
            is_flag=True,
            default=False,
            metavar="",
        ),
        grouped_option(
            "--max-response-time",
            help="Maximum allowed API response time in seconds",
            type=click.FloatRange(min=0.0, min_open=True),
            metavar="SECONDS",
        ),
        group(
            "Filtering options",
            description=(
                "Filter operations by path, method, name, tag, or operation-id using:\n\n"
                "--include-TYPE VALUE          Match operations with exact VALUE\n"
                "--include-TYPE-regex PATTERN  Match operations using regular expression\n"
                "--exclude-TYPE VALUE          Exclude operations with exact VALUE\n"
                "--exclude-TYPE-regex PATTERN  Exclude operations using regular expression"
            ),
        ),
        with_filters,
        grouped_option(
            "--include-by",
            "include_by",
            type=str,
            metavar="EXPR",
            callback=validation.validate_filter_expression,
            help="Include using custom expression",
        ),
        grouped_option(
            "--exclude-by",
            "exclude_by",
            type=str,
            callback=validation.validate_filter_expression,
            metavar="EXPR",
            help="Exclude using custom expression",
        ),
        grouped_option(
            "--exclude-deprecated",
            help="Skip deprecated operations",
            is_flag=True,
            is_eager=True,
            default=None,
            show_default=True,
        ),
        group("Network requests options"),
        grouped_option(
            "--header",
            "-H",
            "headers",
            help=r"Add a custom HTTP header to all API requests",
            metavar="NAME:VALUE",
            multiple=True,
            type=str,
            callback=validation.validate_headers,
        ),
        grouped_option(
            "--auth",
            "-a",
            help="Authenticate all API requests with basic authentication",
            metavar="USER:PASS",
            type=str,
            callback=validation.validate_auth,
        ),
        grouped_option(
            "--proxy",
            "request_proxy",
            help="Set the proxy for all network requests",
            metavar="URL",
            type=str,
        ),
        grouped_option(
            "--tls-verify",
            "request_tls_verify",
            help="Path to CA bundle for TLS verification, or 'false' to disable",
            type=str,
            default=None,
            show_default=True,
            callback=validation.convert_boolean_string,
        ),
        grouped_option(
            "--rate-limit",
            help="Specify a rate limit for test requests in '<limit>/<duration>' format. "
            "Example - `100/m` for 100 requests per minute",
            type=str,
            callback=validation.validate_rate_limit,
        ),
        grouped_option(
            "--max-redirects",
            help="Maximum number of redirects to follow for each request",
            type=click.IntRange(min=0),
            show_default=True,
        ),
        grouped_option(
            "--request-timeout",
            help="Timeout limit, in seconds, for each network request during tests",
            type=click.FloatRange(min=0.0, min_open=True),
            default=DEFAULT_RESPONSE_TIMEOUT,
        ),
        grouped_option(
            "--request-retries",
            help="Number of times to retry a request on network-level failures",
            type=click.IntRange(min=0),
            default=None,
        ),
        grouped_option(
            "--request-cert",
            help="File path of unencrypted client certificate for authentication. "
            "The certificate can be bundled with a private key (e.g. PEM) or the private "
            "key can be provided with the --request-cert-key argument",
            type=click.Path(exists=True),
            default=None,
            show_default=False,
        ),
        grouped_option(
            "--request-cert-key",
            help="Specify the file path of the private key for the client certificate",
            type=click.Path(exists=True),
            default=None,
            show_default=False,
            callback=validation.validate_request_cert_key,
        ),
    )


def generation_options() -> Callable:
    """Shared data generation options."""
    return _chain(
        group("Data generation options"),
        grouped_option(
            "--mode",
            "-m",
            "generation_modes",
            help="Test data generation mode",
            type=click.Choice([item.value for item in GenerationMode] + ["all"]),
            default="all",
            callback=validation.convert_generation_mode,
            show_default=True,
            metavar="",
        ),
        grouped_option(
            "--max-examples",
            "-n",
            "generation_max_examples",
            help="Maximum number of test cases per API operation",
            type=click.IntRange(1),
        ),
        grouped_option(
            "--seed",
            "generation_seed",
            help="Random seed for reproducible test runs",
            type=int,
        ),
        grouped_option(
            "--no-shrink",
            "generation_no_shrink",
            help="Disable test case shrinking. Makes test failures harder to debug but improves performance",
            is_flag=True,
            default=None,
        ),
        grouped_option(
            "--generation-deterministic",
            help="Enables deterministic mode, which eliminates random variation between tests",
            is_flag=True,
            is_eager=True,
            default=None,
            show_default=True,
        ),
        grouped_option(
            "--generation-allow-x00",
            help="Whether to allow the generation of 'NULL' bytes within strings",
            type=str,
            default=None,
            show_default=True,
            metavar="BOOLEAN",
            callback=validation.convert_boolean_string,
        ),
        grouped_option(
            "--generation-codec",
            help="The codec used for generating strings",
            type=str,
            default=None,
            callback=validation.validate_generation_codec,
        ),
        grouped_option(
            "--generation-maximize",
            "generation_maximize",
            multiple=True,
            help="Guide input generation to values more likely to expose bugs via targeted property-based testing",
            type=RegistryChoice(METRICS),
            default=None,
            callback=validation.convert_maximize,
            show_default=True,
            metavar="METRIC",
        ),
        grouped_option(
            "--generation-with-security-parameters",
            help="Whether to generate security parameters",
            type=str,
            default=None,
            show_default=True,
            callback=validation.convert_boolean_string,
            metavar="BOOLEAN",
        ),
        grouped_option(
            "--generation-graphql-allow-null",
            help="Whether to use `null` values for optional arguments in GraphQL queries",
            type=str,
            default=None,
            show_default=True,
            callback=validation.convert_boolean_string,
            metavar="BOOLEAN",
        ),
        grouped_option(
            "--generation-database",
            help="Storage for examples discovered by Schemathesis. "
            f"Use 'none' to disable, '{HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER}' for temporary storage, "
            f"or specify a file path for persistent storage",
            type=str,
            callback=validation.validate_hypothesis_database,
        ),
        grouped_option(
            "--generation-unique-inputs",
            "generation_unique_inputs",
            help="Force the generation of unique test cases",
            is_flag=True,
            default=None,
            show_default=True,
            metavar="BOOLEAN",
        ),
    )


def global_options() -> Callable:
    """Shared global options (color flags)."""
    return _chain(
        group("Global options"),
        grouped_option("--no-color", help="Disable ANSI color escape codes", type=bool, is_flag=True),
        grouped_option(
            "--force-color", help="Explicitly tells to enable ANSI color escape codes", type=bool, is_flag=True
        ),
    )
