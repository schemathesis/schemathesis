from __future__ import annotations

import codecs
import operator
import os
import pathlib
import re
from contextlib import contextmanager
from functools import partial, reduce
from typing import Callable, Generator, Sequence
from urllib.parse import urlparse

import click

from schemathesis import errors, experimental
from schemathesis.cli.commands.run.reports import ReportFormat
from schemathesis.cli.constants import DEFAULT_WORKERS
from schemathesis.core import rate_limit, string_to_boolean
from schemathesis.core.fs import file_exists
from schemathesis.core.validation import contains_unicode_surrogate_pair, has_invalid_characters, is_latin_1_encodable
from schemathesis.generation import GenerationMode
from schemathesis.generation.overrides import Override

INVALID_DERANDOMIZE_MESSAGE = (
    "`--generation-deterministic` implies no database, so passing `--generation-database` too is invalid."
)
INVALID_REPORT_USAGE = (
    "Can't use `--report-preserve-bytes` without enabling cassette formats. "
    "Enable VCR or HAR format with `--report=vcr`, `--report-vcr-path`, "
    "`--report=har`, or `--report-har-path`"
)
INVALID_SCHEMA_MESSAGE = "Invalid SCHEMA, must be a valid URL or file path."
FILE_DOES_NOT_EXIST_MESSAGE = "The specified file does not exist. Please provide a valid path to an existing file."
INVALID_BASE_URL_MESSAGE = (
    "The provided base URL is invalid. This URL serves as a prefix for all API endpoints you want to test. "
    "Make sure it is a properly formatted URL."
)
MISSING_BASE_URL_MESSAGE = "The `--url` option is required when specifying a schema via a file."
MISSING_REQUEST_CERT_MESSAGE = "The `--request-cert` option must be specified if `--request-cert-key` is used."


def validate_schema(schema: str, base_url: str | None) -> None:
    try:
        netloc = urlparse(schema).netloc
        if netloc:
            validate_url(schema)
            return None
    except ValueError as exc:
        raise click.UsageError(INVALID_SCHEMA_MESSAGE) from exc
    if "\x00" in schema or not schema:
        raise click.UsageError(INVALID_SCHEMA_MESSAGE)
    exists = file_exists(schema)
    if exists or bool(pathlib.Path(schema).suffix):
        if not exists:
            raise click.UsageError(FILE_DOES_NOT_EXIST_MESSAGE)
        if base_url is None:
            raise click.UsageError(MISSING_BASE_URL_MESSAGE)
        return None
    raise click.UsageError(INVALID_SCHEMA_MESSAGE)


def validate_url(value: str) -> None:
    from requests import PreparedRequest, RequestException

    try:
        PreparedRequest().prepare_url(value, {})  # type: ignore
    except RequestException as exc:
        raise click.UsageError(INVALID_SCHEMA_MESSAGE) from exc


def validate_base_url(ctx: click.core.Context, param: click.core.Parameter, raw_value: str) -> str:
    try:
        netloc = urlparse(raw_value).netloc
    except ValueError as exc:
        raise click.UsageError(INVALID_BASE_URL_MESSAGE) from exc
    if raw_value and not netloc:
        raise click.UsageError(INVALID_BASE_URL_MESSAGE)
    return raw_value


def validate_generation_codec(ctx: click.core.Context, param: click.core.Parameter, raw_value: str) -> str:
    try:
        codecs.getencoder(raw_value)
    except LookupError as exc:
        raise click.UsageError(f"Codec `{raw_value}` is unknown") from exc
    return raw_value


def validate_rate_limit(ctx: click.core.Context, param: click.core.Parameter, raw_value: str | None) -> str | None:
    if raw_value is None:
        return raw_value
    try:
        rate_limit.parse_units(raw_value)
        return raw_value
    except errors.IncorrectUsage as exc:
        raise click.UsageError(exc.args[0]) from exc


def validate_hypothesis_database(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: str | None
) -> str | None:
    if raw_value is None:
        return raw_value
    if ctx.params.get("generation_deterministic"):
        raise click.UsageError(INVALID_DERANDOMIZE_MESSAGE)
    return raw_value


def validate_auth(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: str | None
) -> tuple[str, str] | None:
    if raw_value is not None:
        with reraise_format_error(raw_value):
            user, password = tuple(raw_value.split(":"))
        if not user:
            raise click.BadParameter("Username should not be empty.")
        if not is_latin_1_encodable(user):
            raise click.BadParameter("Username should be latin-1 encodable.")
        if not is_latin_1_encodable(password):
            raise click.BadParameter("Password should be latin-1 encodable.")
        return user, password
    return None


def validate_auth_overlap(auth: tuple[str, str] | None, headers: dict[str, str], override: Override) -> None:
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


def _validate_and_build_multiple_options(
    values: tuple[str, ...], name: str, callback: Callable[[str, str], None]
) -> dict[str, str]:
    output = {}
    for raw in values:
        try:
            key, value = raw.split("=", maxsplit=1)
        except ValueError as exc:
            raise click.BadParameter(f"Expected NAME=VALUE format, received {raw}.") from exc
        key = key.strip()
        if not key:
            raise click.BadParameter(f"{name} parameter name should not be empty.")
        if key in output:
            raise click.BadParameter(f"{name} parameter {key} is specified multiple times.")
        value = value.strip()
        callback(key, value)
        output[key] = value
    return output


def validate_unique_filter(values: Sequence[str], arg_name: str) -> None:
    if len(values) != len(set(values)):
        duplicates = ",".join(sorted({value for value in values if values.count(value) > 1}))
        raise click.UsageError(f"Duplicate values are not allowed for `{arg_name}`: {duplicates}")


def _validate_set_query(_: str, value: str) -> None:
    if contains_unicode_surrogate_pair(value):
        raise click.BadParameter("Query parameter value should not contain surrogates.")


def validate_set_query(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: tuple[str, ...]
) -> dict[str, str]:
    return _validate_and_build_multiple_options(raw_value, "Query", _validate_set_query)


def validate_set_header(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: tuple[str, ...]
) -> dict[str, str]:
    return _validate_and_build_multiple_options(raw_value, "Header", partial(_validate_header, where="Header"))


def validate_set_cookie(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: tuple[str, ...]
) -> dict[str, str]:
    return _validate_and_build_multiple_options(raw_value, "Cookie", partial(_validate_header, where="Cookie"))


def _validate_set_path(_: str, value: str) -> None:
    if contains_unicode_surrogate_pair(value):
        raise click.BadParameter("Path parameter value should not contain surrogates.")


def validate_set_path(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: tuple[str, ...]
) -> dict[str, str]:
    return _validate_and_build_multiple_options(raw_value, "Path", _validate_set_path)


def _validate_header(key: str, value: str, where: str) -> None:
    if not key:
        raise click.BadParameter(f"{where} name should not be empty.")
    if not is_latin_1_encodable(key):
        raise click.BadParameter(f"{where} name should be latin-1 encodable.")
    if not is_latin_1_encodable(value):
        raise click.BadParameter(f"{where} value should be latin-1 encodable.")
    if has_invalid_characters(key, value):
        raise click.BadParameter(f"Invalid return character or leading space in {where.lower()}.")


def validate_headers(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: tuple[str, ...]
) -> dict[str, str]:
    headers = {}
    for header in raw_value:
        with reraise_format_error(header):
            key, value = header.split(":", maxsplit=1)
        value = value.lstrip()
        key = key.strip()
        _validate_header(key, value, where="Header")
        headers[key] = value
    return headers


def validate_regex(ctx: click.core.Context, param: click.core.Parameter, raw_value: tuple[str, ...]) -> tuple[str, ...]:
    for value in raw_value:
        try:
            re.compile(value)
        except (re.error, OverflowError, RuntimeError) as exc:
            raise click.BadParameter(f"Invalid regex: {exc.args[0]}.")  # noqa: B904
    return raw_value


def validate_request_cert_key(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: str | None
) -> str | None:
    if raw_value is not None and "request_cert" not in ctx.params:
        raise click.UsageError(MISSING_REQUEST_CERT_MESSAGE)
    return raw_value


def validate_preserve_bytes(ctx: click.core.Context, param: click.core.Parameter, raw_value: bool) -> bool:
    if not raw_value:
        return False

    report_formats = ctx.params.get("report_formats", []) or []
    vcr_enabled = ReportFormat.VCR in report_formats or ctx.params.get("report_vcr_path")
    har_enabled = ReportFormat.HAR in report_formats or ctx.params.get("report_har_path")

    if not (vcr_enabled or har_enabled):
        raise click.UsageError(INVALID_REPORT_USAGE)

    return True


def convert_experimental(
    ctx: click.core.Context, param: click.core.Parameter, value: tuple[str, ...]
) -> list[experimental.Experiment]:
    return [
        feature
        for feature in experimental.GLOBAL_EXPERIMENTS.available
        if feature.label in value or feature.is_env_var_set
    ]


def reduce_list(ctx: click.core.Context, param: click.core.Parameter, value: tuple[list[str]]) -> list[str]:
    return reduce(operator.iadd, value, [])


def convert_http_methods(
    ctx: click.core.Context, param: click.core.Parameter, value: list[str] | None
) -> set[str] | None:
    if value is None:
        return value
    return {item.lower() for item in value}


def convert_status_codes(
    ctx: click.core.Context, param: click.core.Parameter, value: list[str] | None
) -> list[str] | None:
    if not value:
        return value

    invalid = []

    for code in value:
        if len(code) != 3:
            invalid.append(code)
            continue

        if code[0] not in {"1", "2", "3", "4", "5"}:
            invalid.append(code)
            continue

        upper_code = code.upper()

        if "X" in upper_code:
            if (
                upper_code[1:] == "XX"
                or (upper_code[1] == "X" and upper_code[2].isdigit())
                or (upper_code[1].isdigit() and upper_code[2] == "X")
            ):
                continue
            else:
                invalid.append(code)
                continue

        if not code.isnumeric():
            invalid.append(code)

    if invalid:
        raise click.UsageError(
            f"Invalid status code(s): {', '.join(invalid)}. "
            "Use valid 3-digit codes between 100 and 599, "
            "or wildcards (e.g., 2XX, 2X0, 20X), where X is a wildcard digit."
        )
    return value


def convert_generation_mode(ctx: click.core.Context, param: click.core.Parameter, value: str) -> list[GenerationMode]:
    if value == "all":
        return GenerationMode.all()
    return [GenerationMode(value)]


def convert_boolean_string(ctx: click.core.Context, param: click.core.Parameter, value: str) -> str | bool:
    return string_to_boolean(value)


@contextmanager
def reraise_format_error(raw_value: str) -> Generator[None, None, None]:
    try:
        yield
    except ValueError as exc:
        raise click.BadParameter(f"Expected KEY:VALUE format, received {raw_value}.") from exc


def get_workers_count() -> int:
    """Detect the number of available CPUs for the current process, if possible.

    Use ``DEFAULT_WORKERS`` if not possible to detect.
    """
    if hasattr(os, "sched_getaffinity"):
        # In contrast with `os.cpu_count` this call respects limits on CPU resources on some Unix systems
        return len(os.sched_getaffinity(0))
    # Number of CPUs in the system, or 1 if undetermined
    return os.cpu_count() or DEFAULT_WORKERS


def convert_workers(ctx: click.core.Context, param: click.core.Parameter, value: str) -> int:
    if value == "auto":
        return get_workers_count()
    return int(value)
