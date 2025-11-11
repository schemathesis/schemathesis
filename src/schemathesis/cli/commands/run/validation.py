from __future__ import annotations

import codecs
import operator
import pathlib
from collections.abc import Callable, Generator
from contextlib import contextmanager
from functools import reduce
from urllib.parse import urlparse

import click

from schemathesis.cli.ext.options import CsvEnumChoice
from schemathesis.config import ReportFormat, SchemathesisWarning, get_workers_count
from schemathesis.core import errors, rate_limit, string_to_boolean
from schemathesis.core.fs import file_exists
from schemathesis.core.validation import has_invalid_characters, is_latin_1_encodable
from schemathesis.filters import expression_to_filter_function
from schemathesis.generation import GenerationMode
from schemathesis.generation.metrics import MetricFunction

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
MISSING_REQUEST_CERT_MESSAGE = "The `--request-cert` option must be specified if `--request-cert-key` is used."


def validate_schema_location(ctx: click.core.Context, param: click.core.Parameter, location: str) -> str:
    try:
        netloc = urlparse(location).netloc
        if netloc:
            validate_url(location)
            return location
    except ValueError as exc:
        raise click.UsageError(INVALID_SCHEMA_MESSAGE) from exc
    if "\x00" in location or not location:
        raise click.UsageError(INVALID_SCHEMA_MESSAGE)
    exists = file_exists(location)
    if exists or bool(pathlib.Path(location).suffix):
        if not exists:
            raise click.UsageError(FILE_DOES_NOT_EXIST_MESSAGE)
        return location
    raise click.UsageError(INVALID_SCHEMA_MESSAGE)


def validate_url(value: str) -> None:
    from requests import PreparedRequest, RequestException

    try:
        PreparedRequest().prepare_url(value, {})
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


def validate_generation_codec(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: str | None
) -> str | None:
    if raw_value is None:
        return raw_value
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


def validate_auth_overlap(auth: tuple[str, str] | None, headers: dict[str, str]) -> None:
    auth_is_set = auth is not None
    header_is_set = "authorization" in {header.lower() for header in headers}
    if len([is_set for is_set in (auth_is_set, header_is_set) if is_set]) > 1:
        message = "The "
        used = []
        if auth_is_set:
            used.append("`--auth`")
        if header_is_set:
            used.append("`--header`")
        message += " and ".join(used)
        message += " options were both used to set the 'Authorization' header, which is not permitted."
        raise click.BadParameter(message)


def validate_filter_expression(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: str | None
) -> Callable | None:
    if raw_value:
        try:
            return expression_to_filter_function(raw_value)
        except ValueError:
            arg_name = param.opts[0]
            raise click.UsageError(f"Invalid expression for {arg_name}: {raw_value}") from None
    return None


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


def reduce_list(
    ctx: click.core.Context, param: click.core.Parameter, value: tuple[list[str]] | None
) -> list[str] | None:
    if not value:
        return None
    return reduce(operator.iadd, value, [])


def convert_maximize(
    ctx: click.core.Context, param: click.core.Parameter, value: tuple[list[str]]
) -> list[MetricFunction]:
    from schemathesis.generation.metrics import METRICS

    names: list[str] = reduce(operator.iadd, value, [])
    return METRICS.get_by_names(names)


def convert_generation_mode(ctx: click.core.Context, param: click.core.Parameter, value: str) -> list[GenerationMode]:
    if value == "all":
        return list(GenerationMode)
    return [GenerationMode(value)]


def convert_boolean_string(
    ctx: click.core.Context, param: click.core.Parameter, value: str | None
) -> str | bool | None:
    if value is None:
        return value
    return string_to_boolean(value)


@contextmanager
def reraise_format_error(raw_value: str) -> Generator[None, None, None]:
    try:
        yield
    except ValueError as exc:
        raise click.BadParameter(f"Expected KEY:VALUE format, received {raw_value}.") from exc


def convert_workers(ctx: click.core.Context, param: click.core.Parameter, value: str | None) -> int | None:
    if value is None:
        return value
    if value == "auto":
        return get_workers_count()
    return int(value)


WARNINGS_CHOICE = CsvEnumChoice(SchemathesisWarning)


def validate_warnings(
    ctx: click.core.Context, param: click.core.Parameter, value: str | None
) -> bool | None | list[SchemathesisWarning]:
    if value is None:
        return None
    boolean = string_to_boolean(value)
    if isinstance(boolean, bool):
        return boolean
    return WARNINGS_CHOICE.convert(value, param, ctx)  # type: ignore[return-value]
