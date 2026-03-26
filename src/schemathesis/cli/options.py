from __future__ import annotations

import click

from schemathesis.checks import CHECKS
from schemathesis.cli.constants import MAX_WORKERS, MIN_WORKERS
from schemathesis.cli.ext.options import CsvEnumChoice, CustomHelpMessageChoice, RegistryChoice
from schemathesis.cli.validation import (
    convert_boolean_string,
    convert_generation_mode,
    convert_maximize,
    convert_workers,
    reduce_list,
    validate_auth,
    validate_base_url,
    validate_filter_expression,
    validate_generation_codec,
    validate_headers,
    validate_hypothesis_database,
    validate_preserve_bytes,
    validate_rate_limit,
    validate_request_cert_key,
    validate_schema_location,
    validate_warnings,
)
from schemathesis.config import DEFAULT_REPORT_DIRECTORY, HealthCheck, ReportFormat
from schemathesis.core import HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT
from schemathesis.generation import GenerationMode
from schemathesis.generation.metrics import METRICS


class OptionSpec:
    __slots__ = ("args", "kwargs")

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs


LOCATION = OptionSpec(
    "location",
    type=str,
    callback=validate_schema_location,
)

WAIT_FOR_SCHEMA = OptionSpec(
    "--wait-for-schema",
    help="Maximum duration, in seconds, to wait for the API schema to become available. Disabled by default",
    type=click.FloatRange(1.0),
    default=None,
    envvar="SCHEMATHESIS_WAIT_FOR_SCHEMA",
)

BASE_URL = OptionSpec(
    "--url",
    "-u",
    "base_url",
    help="API base URL (required for file-based schemas)",
    metavar="URL",
    type=str,
    callback=validate_base_url,
    envvar="SCHEMATHESIS_BASE_URL",
)

WORKERS = OptionSpec(
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
    callback=convert_workers,
    metavar="",
)

MAX_FAILURES = OptionSpec(
    "--max-failures",
    "max_failures",
    type=click.IntRange(min=1),
    help="Terminate the test suite after reaching a specified number of failures or errors",
    show_default=True,
)

CONTINUE_ON_FAILURE = OptionSpec(
    "--continue-on-failure",
    "continue_on_failure",
    help="Continue executing all test cases within a scenario, even after encountering failures",
    is_flag=True,
    default=False,
    metavar="",
)

MAX_RESPONSE_TIME = OptionSpec(
    "--max-response-time",
    help="Maximum allowed API response time in seconds",
    type=click.FloatRange(min=0.0, min_open=True),
    metavar="SECONDS",
)

INCLUDE_BY = OptionSpec(
    "--include-by",
    "include_by",
    type=str,
    metavar="EXPR",
    callback=validate_filter_expression,
    help="Include using custom expression",
)

EXCLUDE_BY = OptionSpec(
    "--exclude-by",
    "exclude_by",
    type=str,
    callback=validate_filter_expression,
    metavar="EXPR",
    help="Exclude using custom expression",
)

EXCLUDE_DEPRECATED = OptionSpec(
    "--exclude-deprecated",
    help="Skip deprecated operations",
    is_flag=True,
    is_eager=True,
    default=None,
    show_default=True,
)

HEADER = OptionSpec(
    "--header",
    "-H",
    "headers",
    help=r"Add a custom HTTP header to all API requests",
    metavar="NAME:VALUE",
    multiple=True,
    type=str,
    callback=validate_headers,
)

AUTH = OptionSpec(
    "--auth",
    "-a",
    help="Authenticate all API requests with basic authentication",
    metavar="USER:PASS",
    type=str,
    callback=validate_auth,
)

PROXY = OptionSpec(
    "--proxy",
    "request_proxy",
    help="Set the proxy for all network requests",
    metavar="URL",
    type=str,
)

TLS_VERIFY = OptionSpec(
    "--tls-verify",
    "request_tls_verify",
    help="Path to CA bundle for TLS verification, or 'false' to disable",
    type=str,
    default=None,
    show_default=True,
    callback=convert_boolean_string,
)

RATE_LIMIT = OptionSpec(
    "--rate-limit",
    help=(
        "Specify a rate limit for test requests in '<limit>/<duration>' format. "
        "Example - `100/m` for 100 requests per minute"
    ),
    type=str,
    callback=validate_rate_limit,
)

MAX_REDIRECTS = OptionSpec(
    "--max-redirects",
    help="Maximum number of redirects to follow for each request",
    type=click.IntRange(min=0),
    show_default=True,
)

REQUEST_TIMEOUT = OptionSpec(
    "--request-timeout",
    help="Timeout limit, in seconds, for each network request during tests",
    type=click.FloatRange(min=0.0, min_open=True),
    default=DEFAULT_RESPONSE_TIMEOUT,
)

REQUEST_RETRIES = OptionSpec(
    "--request-retries",
    help="Number of times to retry a request on network-level failures",
    type=click.IntRange(min=0),
    default=None,
)

REQUEST_CERT = OptionSpec(
    "--request-cert",
    help=(
        "File path of unencrypted client certificate for authentication. "
        "The certificate can be bundled with a private key (e.g. PEM) or the private "
        "key can be provided with the --request-cert-key argument"
    ),
    type=click.Path(exists=True),
    default=None,
    show_default=False,
)

REQUEST_CERT_KEY = OptionSpec(
    "--request-cert-key",
    help="Specify the file path of the private key for the client certificate",
    type=click.Path(exists=True),
    default=None,
    show_default=False,
    callback=validate_request_cert_key,
)

OUTPUT_SANITIZE = OptionSpec(
    "--output-sanitize",
    type=str,
    default=None,
    show_default=True,
    help="Enable or disable automatic output sanitization to obscure sensitive data",
    metavar="BOOLEAN",
    callback=convert_boolean_string,
)

OUTPUT_TRUNCATE = OptionSpec(
    "--output-truncate",
    help="Truncate schemas and responses in error messages",
    type=str,
    default=None,
    show_default=True,
    metavar="BOOLEAN",
    callback=convert_boolean_string,
)

GENERATION_MODE = OptionSpec(
    "--mode",
    "-m",
    "generation_modes",
    help="Test data generation mode",
    type=click.Choice([item.value for item in GenerationMode] + ["all"]),
    default="all",
    callback=convert_generation_mode,
    show_default=True,
    metavar="",
)

GENERATION_MAX_EXAMPLES = OptionSpec(
    "--max-examples",
    "-n",
    "generation_max_examples",
    help="Maximum number of test cases per API operation",
    type=click.IntRange(1),
)

GENERATION_SEED = OptionSpec(
    "--seed",
    "generation_seed",
    help="Random seed for reproducible test runs",
    type=int,
)

GENERATION_DETERMINISTIC = OptionSpec(
    "--generation-deterministic",
    help="Enables deterministic mode, which eliminates random variation between tests",
    is_flag=True,
    is_eager=True,
    default=None,
    show_default=True,
)

GENERATION_ALLOW_X00 = OptionSpec(
    "--generation-allow-x00",
    help="Whether to allow the generation of 'NULL' bytes within strings",
    type=str,
    default=None,
    show_default=True,
    metavar="BOOLEAN",
    callback=convert_boolean_string,
)

GENERATION_CODEC = OptionSpec(
    "--generation-codec",
    help="The codec used for generating strings",
    type=str,
    default=None,
    callback=validate_generation_codec,
)

GENERATION_MAXIMIZE = OptionSpec(
    "--generation-maximize",
    "generation_maximize",
    help="Guide input generation to values more likely to expose bugs via targeted property-based testing",
    multiple=True,
    type=RegistryChoice(METRICS),
    default=None,
    callback=convert_maximize,
    show_default=True,
    metavar="METRIC",
)

GENERATION_WITH_SECURITY_PARAMETERS = OptionSpec(
    "--generation-with-security-parameters",
    help="Whether to generate security parameters",
    type=str,
    default=None,
    show_default=True,
    callback=convert_boolean_string,
    metavar="BOOLEAN",
)

GENERATION_GRAPHQL_ALLOW_NULL = OptionSpec(
    "--generation-graphql-allow-null",
    help="Whether to use `null` values for optional arguments in GraphQL queries",
    type=str,
    default=None,
    show_default=True,
    callback=convert_boolean_string,
    metavar="BOOLEAN",
)

GENERATION_DATABASE = OptionSpec(
    "--generation-database",
    help=(
        "Storage for examples discovered by Schemathesis. "
        f"Use 'none' to disable, '{HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER}' for temporary storage, "
        f"or specify a file path for persistent storage"
    ),
    type=str,
    callback=validate_hypothesis_database,
)

GENERATION_UNIQUE_INPUTS = OptionSpec(
    "--generation-unique-inputs",
    "generation_unique_inputs",
    help="Force the generation of unique test cases",
    is_flag=True,
    default=None,
    show_default=True,
    metavar="BOOLEAN",
)

NO_COLOR = OptionSpec(
    "--no-color",
    help="Disable ANSI color escape codes",
    type=bool,
    is_flag=True,
)

FORCE_COLOR = OptionSpec(
    "--force-color",
    help="Explicitly tells to enable ANSI color escape codes",
    type=bool,
    is_flag=True,
)

SUPPRESS_HEALTH_CHECK = OptionSpec(
    "--suppress-health-check",
    help="A comma-separated list of Schemathesis health checks to disable",
    type=CsvEnumChoice(HealthCheck),
    metavar="",
)

WARNINGS = OptionSpec(
    "--warnings",
    help="Control warning display: 'off' to disable all, or comma-separated list of warning types to enable",
    type=str,
    default=None,
    callback=validate_warnings,
    metavar="WARNINGS",
)

CHECKS_OPTION = OptionSpec(
    "--checks",
    "-c",
    "included_check_names",
    multiple=True,
    help="Comma-separated list of checks to run against API responses",
    type=RegistryChoice(CHECKS, with_all=True),
    default=None,
    callback=reduce_list,
    show_default=True,
    metavar="",
)

EXCLUDE_CHECKS = OptionSpec(
    "--exclude-checks",
    "excluded_check_names",
    multiple=True,
    help="Comma-separated list of checks to skip during testing",
    type=RegistryChoice(CHECKS, with_all=True),
    default=None,
    callback=reduce_list,
    show_default=True,
    metavar="",
)

REPORT = OptionSpec(
    "--report",
    "report_formats",
    help="Generate test reports in formats specified as a comma-separated list",
    type=CsvEnumChoice(ReportFormat),
    is_eager=True,
    metavar="FORMAT",
)

REPORT_DIR = OptionSpec(
    "--report-dir",
    "report_directory",
    help="Directory to store all report files",
    type=click.Path(file_okay=False, dir_okay=True),
    default=DEFAULT_REPORT_DIRECTORY,
    show_default=True,
)

REPORT_JUNIT_PATH = OptionSpec(
    "--report-junit-path",
    help="Custom path for JUnit XML report",
    type=click.File("w", encoding="utf-8"),
    is_eager=True,
)

REPORT_VCR_PATH = OptionSpec(
    "--report-vcr-path",
    help="Custom path for VCR cassette",
    type=click.File("w", encoding="utf-8"),
    is_eager=True,
)

REPORT_HAR_PATH = OptionSpec(
    "--report-har-path",
    help="Custom path for HAR file",
    type=click.File("w", encoding="utf-8"),
    is_eager=True,
)

REPORT_NDJSON_PATH = OptionSpec(
    "--report-ndjson-path",
    help="Custom path for NDJSON events file",
    type=click.File("w", encoding="utf-8"),
    is_eager=True,
)

REPORT_ALLURE_PATH = OptionSpec(
    "--report-allure-path",
    help="Directory for Allure result files",
    type=click.Path(file_okay=False),
    is_eager=True,
)

REPORT_PRESERVE_BYTES = OptionSpec(
    "--report-preserve-bytes",
    help="Retain exact byte sequence of payloads in cassettes, encoded as base64",
    type=bool,
    is_flag=True,
    default=None,
    callback=validate_preserve_bytes,
)
