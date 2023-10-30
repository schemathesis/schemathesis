from __future__ import annotations
import enum
import os
import re
import traceback
from contextlib import contextmanager
from typing import Dict, Generator, List, Optional, Tuple, Union, TYPE_CHECKING
from urllib.parse import urlparse

import click

from click.types import LazyFile  # type: ignore

from .. import exceptions, experimental, throttling
from ..code_samples import CodeSampleStyle
from ..exceptions import extract_nth_traceback
from ..generation import DataGenerationMethod
from ..constants import TRUE_VALUES, FALSE_VALUES
from ..internal.validation import file_exists, is_filename
from ..loaders import load_app
from ..service.hosts import get_temporary_hosts_file
from ..transports.headers import has_invalid_characters, is_latin_1_encodable
from ..types import PathLike
from .constants import DEFAULT_WORKERS
from ..stateful import Stateful

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


def parse_schema_kind(schema: str, app: Optional[str]) -> SchemaInputKind:
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
    base_url: Optional[str],
    dry_run: bool,
    app: Optional[str],
    api_name: Optional[str],
) -> None:
    if kind == SchemaInputKind.URL:
        validate_url(schema)
    if kind == SchemaInputKind.PATH:
        # Base URL is required if it is not a dry run
        if app is None and base_url is None and not dry_run:
            if not file_exists(schema):
                message = FILE_DOES_NOT_EXIST_MESSAGE
            else:
                message = MISSING_BASE_URL_MESSAGE
            raise click.UsageError(message)
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


def validate_rate_limit(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: Optional[str]
) -> Optional[str]:
    if raw_value is None:
        return raw_value
    try:
        throttling.parse_units(raw_value)
        return raw_value
    except exceptions.UsageError as exc:
        raise click.UsageError(exc.args[0]) from exc


def validate_app(ctx: click.core.Context, param: click.core.Parameter, raw_value: Optional[str]) -> Optional[str]:
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
    ctx: click.core.Context, param: click.core.Parameter, raw_value: Optional[str]
) -> Optional[str]:
    if raw_value is None:
        return raw_value
    if ctx.params.get("hypothesis_derandomize"):
        raise click.UsageError(INVALID_DERANDOMIZE_MESSAGE)
    return raw_value


def validate_auth(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: Optional[str]
) -> Optional[Tuple[str, str]]:
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


def validate_headers(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: Tuple[str, ...]
) -> Dict[str, str]:
    headers = {}
    for header in raw_value:
        with reraise_format_error(header):
            key, value = header.split(":", maxsplit=1)
        value = value.lstrip()
        key = key.strip()
        if not key:
            raise click.BadParameter("Header name should not be empty.")
        if not is_latin_1_encodable(key):
            raise click.BadParameter("Header name should be latin-1 encodable.")
        if not is_latin_1_encodable(value):
            raise click.BadParameter("Header value should be latin-1 encodable.")
        if has_invalid_characters(key, value):
            raise click.BadParameter("Invalid return character or leading space in header.")
        headers[key] = value
    return headers


def validate_regex(ctx: click.core.Context, param: click.core.Parameter, raw_value: Tuple[str, ...]) -> Tuple[str, ...]:
    for value in raw_value:
        try:
            re.compile(value)
        except (re.error, OverflowError, RuntimeError) as exc:
            raise click.BadParameter(f"Invalid regex: {exc.args[0]}.")  # noqa: B904
    return raw_value


def validate_request_cert_key(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: Optional[str]
) -> Optional[str]:
    if raw_value is not None and "request_cert" not in ctx.params:
        raise click.UsageError(MISSING_REQUEST_CERT_MESSAGE)
    return raw_value


def validate_preserve_exact_body_bytes(ctx: click.core.Context, param: click.core.Parameter, raw_value: bool) -> bool:
    if raw_value and ctx.params["cassette_path"] is None:
        raise click.UsageError(MISSING_CASSETTE_PATH_ARGUMENT_MESSAGE)
    return raw_value


def convert_verbosity(
    ctx: click.core.Context, param: click.core.Parameter, value: Optional[str]
) -> Optional[hypothesis.Verbosity]:
    import hypothesis

    if value is None:
        return value
    return hypothesis.Verbosity[value]


def convert_stateful(ctx: click.core.Context, param: click.core.Parameter, value: str) -> Optional[Stateful]:
    if value == "none":
        return None
    return Stateful[value]


def convert_experimental(
    ctx: click.core.Context, param: click.core.Parameter, value: Tuple[str, ...]
) -> List[experimental.Experiment]:
    return [
        feature
        for feature in experimental.GLOBAL_EXPERIMENTS.available
        if feature.name in value or feature.is_env_var_set
    ]


def convert_checks(ctx: click.core.Context, param: click.core.Parameter, value: Tuple[List[str]]) -> List[str]:
    return sum(value, [])


def convert_code_sample_style(ctx: click.core.Context, param: click.core.Parameter, value: str) -> CodeSampleStyle:
    return CodeSampleStyle.from_str(value)


def convert_data_generation_method(
    ctx: click.core.Context, param: click.core.Parameter, value: str
) -> List[DataGenerationMethod]:
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


def convert_boolean_string(ctx: click.core.Context, param: click.core.Parameter, value: str) -> Union[str, bool]:
    if value.lower() in TRUE_VALUES:
        return True
    if value.lower() in FALSE_VALUES:
        return False
    return value


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
