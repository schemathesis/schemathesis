from __future__ import annotations

import codecs
import enum
import os
import re
import traceback
from contextlib import contextmanager
from functools import partial
from typing import TYPE_CHECKING, Callable, Generator
from urllib.parse import urlparse

import click
from click.types import LazyFile  # type: ignore

from .. import exceptions, experimental, throttling
from ..code_samples import CodeSampleStyle
from ..constants import TRUE_VALUES
from ..exceptions import extract_nth_traceback
from ..generation import DataGenerationMethod
from ..internal.transformation import convert_boolean_string as _convert_boolean_string
from ..internal.validation import file_exists, is_filename, is_illegal_surrogate
from ..loaders import load_app
from ..service.hosts import get_temporary_hosts_file
from ..stateful import Stateful
from ..transports.headers import has_invalid_characters, is_latin_1_encodable
from ..types import PathLike
from .cassettes import CassetteFormat
from .constants import DEFAULT_WORKERS

if TYPE_CHECKING:
    import hypothesis

INVALID_DERANDOMIZE_MESSAGE = (
    "`--hypothesis-derandomize` implies no database, so passing `--hypothesis-database` too is invalid."
)
MISSING_CASSETTE_PATH_ARGUMENT_MESSAGE = (
    "Missing argument, `--cassette-path` should be specified as well if you use `--cassette-preserve-exact-body-bytes`."
)
INVALID_SCHEMA_MESSAGE = "Invalid SCHEMA, must be a valid URL, file path or an API name from Schemathesis.io."
FILE_DOES_NOT_EXIST_MESSAGE = "The specified file does not exist. Please provide a valid path to an existing file."
INVALID_BASE_URL_MESSAGE = (
    "The provided base URL is invalid. This URL serves as a prefix for all API endpoints you want to test. "
    "Make sure it is a properly formatted URL."
)
MISSING_BASE_URL_MESSAGE = "The `--base-url` option is required when specifying a schema via a file."
WSGI_DOCUMENTATION_URL = "https://schemathesis.readthedocs.io/en/stable/python.html#asgi-wsgi-support"
APPLICATION_MISSING_MODULE_MESSAGE = f"""Unable to import application from {{module}}.

The `--app` option should follow this format:

    module_path:variable_name

- `module_path`: A path to an importable Python module.
- `variable_name`: The name of the application variable within that module.

Example: `st run --app=your_module:app ...`

For details on working with WSGI applications, visit {WSGI_DOCUMENTATION_URL}"""
APPLICATION_IMPORT_ERROR_MESSAGE = f"""An error occurred while loading the application from {{module}}.

Traceback:

{{traceback}}

For details on working with WSGI applications, visit {WSGI_DOCUMENTATION_URL}"""
MISSING_REQUEST_CERT_MESSAGE = "The `--request-cert` option must be specified if `--request-cert-key` is used."


@enum.unique
class SchemaInputKind(enum.Enum):
    """Kinds of SCHEMA input."""

    # Regular URL like https://example.schemathesis.io/openapi.json
    URL = 1
    # Local path
    PATH = 2
    # Relative path within a Python app
    APP_PATH = 3
    # A name for API created in Schemathesis.io
    NAME = 4


def parse_schema_kind(schema: str, app: str | None) -> SchemaInputKind:
    """Detect what kind the input schema is."""
    try:
        netloc = urlparse(schema).netloc
    except ValueError as exc:
        raise click.UsageError(INVALID_SCHEMA_MESSAGE) from exc
    if "\x00" in schema or not schema:
        raise click.UsageError(INVALID_SCHEMA_MESSAGE)
    if netloc:
        return SchemaInputKind.URL
    if file_exists(schema) or is_filename(schema):
        return SchemaInputKind.PATH
    if app is not None:
        return SchemaInputKind.APP_PATH
    # Assume NAME if it is not a URL or PATH or APP_PATH
    return SchemaInputKind.NAME


def validate_schema(
    schema: str,
    kind: SchemaInputKind,
    *,
    base_url: str | None,
    dry_run: bool,
    app: str | None,
    api_name: str | None,
) -> None:
    if kind == SchemaInputKind.URL:
        validate_url(schema)
    if kind == SchemaInputKind.PATH:
        if app is None:
            if not file_exists(schema):
                raise click.UsageError(FILE_DOES_NOT_EXIST_MESSAGE)
            # Base URL is required if it is not a dry run
            if base_url is None and not dry_run:
                raise click.UsageError(MISSING_BASE_URL_MESSAGE)
    if kind == SchemaInputKind.NAME:
        if api_name is not None:
            raise click.UsageError(f"Got unexpected extra argument ({api_name})")


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
        throttling.parse_units(raw_value)
        return raw_value
    except exceptions.UsageError as exc:
        raise click.UsageError(exc.args[0]) from exc


def validate_app(ctx: click.core.Context, param: click.core.Parameter, raw_value: str | None) -> str | None:
    if raw_value is None:
        return raw_value
    try:
        load_app(raw_value)
        # String is returned instead of an app because it might be passed to a subprocess
        # Since most app instances are not-transferable to another process, they are passed as strings and
        # imported in a subprocess
        return raw_value
    except Exception as exc:
        formatted_module_name = click.style(f"'{raw_value}'", bold=True)
        if isinstance(exc, ModuleNotFoundError):
            message = APPLICATION_MISSING_MODULE_MESSAGE.format(module=formatted_module_name)
            click.echo(message)
        else:
            trace = extract_nth_traceback(exc.__traceback__, 2)
            lines = traceback.format_exception(type(exc), exc, trace)
            traceback_message = "".join(lines).strip()
            message = APPLICATION_IMPORT_ERROR_MESSAGE.format(
                module=formatted_module_name, traceback=click.style(traceback_message, fg="red")
            )
            click.echo(message)
        raise click.exceptions.Exit(1) from None


def validate_hypothesis_database(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: str | None
) -> str | None:
    if raw_value is None:
        return raw_value
    if ctx.params.get("hypothesis_derandomize"):
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


def _validate_set_query(_: str, value: str) -> None:
    if is_illegal_surrogate(value):
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
    if is_illegal_surrogate(value):
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


def validate_preserve_exact_body_bytes(ctx: click.core.Context, param: click.core.Parameter, raw_value: bool) -> bool:
    if raw_value and ctx.params["cassette_path"] is None:
        raise click.UsageError(MISSING_CASSETTE_PATH_ARGUMENT_MESSAGE)
    return raw_value


def convert_verbosity(
    ctx: click.core.Context, param: click.core.Parameter, value: str | None
) -> hypothesis.Verbosity | None:
    import hypothesis

    if value is None:
        return value
    return hypothesis.Verbosity[value]


def convert_stateful(ctx: click.core.Context, param: click.core.Parameter, value: str) -> Stateful | None:
    if value == "none":
        return None
    return Stateful[value]


def convert_experimental(
    ctx: click.core.Context, param: click.core.Parameter, value: tuple[str, ...]
) -> list[experimental.Experiment]:
    return [
        feature
        for feature in experimental.GLOBAL_EXPERIMENTS.available
        if feature.name in value or feature.is_env_var_set
    ]


def convert_checks(ctx: click.core.Context, param: click.core.Parameter, value: tuple[list[str]]) -> list[str]:
    return sum(value, [])


def convert_code_sample_style(ctx: click.core.Context, param: click.core.Parameter, value: str) -> CodeSampleStyle:
    return CodeSampleStyle.from_str(value)


def convert_cassette_format(ctx: click.core.Context, param: click.core.Parameter, value: str) -> CassetteFormat:
    return CassetteFormat.from_str(value)


def convert_data_generation_method(
    ctx: click.core.Context, param: click.core.Parameter, value: str
) -> list[DataGenerationMethod]:
    if value == "all":
        return DataGenerationMethod.all()
    return [DataGenerationMethod[value]]


def _is_usable_dir(path: PathLike) -> bool:
    if os.path.isfile(path):
        path = os.path.dirname(path)
    while not os.path.exists(path):
        path = os.path.dirname(path)
    return os.path.isdir(path) and os.access(path, os.R_OK | os.W_OK | os.X_OK)


def convert_hosts_file(ctx: click.core.Context, param: click.core.Parameter, value: PathLike) -> PathLike:
    if not _is_usable_dir(value):
        path = get_temporary_hosts_file()
        click.secho(
            "WARNING: The provided hosts.toml file location is unusable - using a temporary file for this session. "
            f"path={str(value)!r}",
            fg="yellow",
        )
        return path
    return value


def convert_boolean_string(ctx: click.core.Context, param: click.core.Parameter, value: str) -> str | bool:
    return _convert_boolean_string(value)


def convert_report(ctx: click.core.Context, param: click.core.Option, value: LazyFile) -> LazyFile:
    if param.resolve_envvar_value(ctx) is not None and value.lower() in TRUE_VALUES:
        value = param.flag_value
    return value


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
