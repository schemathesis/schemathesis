import pathlib
import re
import sys
from contextlib import contextmanager
from typing import Any, Dict, Generator, Optional, Tuple
from urllib.parse import urlparse

import click
import hypothesis

from .. import utils


def validate_schema(ctx: click.core.Context, param: click.core.Parameter, raw_value: str) -> str:
    if "app" not in ctx.params and not urlparse(raw_value).netloc:
        if "\x00" in raw_value or not _verify_path(raw_value):
            raise click.UsageError("Invalid SCHEMA, must be a valid URL or file path.")
        if "base_url" not in ctx.params:
            raise click.UsageError('Missing argument, "--base-url" is required for SCHEMA specified by file.')
    return raw_value


def _verify_path(path: str) -> bool:
    try:
        return pathlib.Path(path).is_file()
    except OSError:
        # For example, path could be too long
        return False


def validate_base_url(ctx: click.core.Context, param: click.core.Parameter, raw_value: str) -> str:
    if raw_value and not urlparse(raw_value).netloc:
        raise click.UsageError("Invalid base URL")
    return raw_value


def validate_app(ctx: click.core.Context, param: click.core.Parameter, raw_value: Optional[str]) -> Any:
    if raw_value is None:
        return raw_value
    path, name = (re.split(r":(?![\\/])", raw_value, 1) + [None])[:2]  # type: ignore
    try:
        __import__(path)
    except (ImportError, ValueError):
        raise click.BadParameter("Can not import application from the given module")
    except Exception as exc:
        message = utils.format_exception(exc)
        click.secho(f"Error: {message}", fg="red")
        raise click.Abort
    # accessing the module from sys.modules returns a proper module, while `__import__`
    # may return a parent module (system dependent)
    module = sys.modules[path]
    try:
        return getattr(module, name)
    except AttributeError:
        raise click.BadParameter("Can not import application from the given module")


def validate_auth(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: Optional[str]
) -> Optional[Tuple[str, str]]:
    if raw_value is not None:
        with reraise_format_error(raw_value):
            user, password = tuple(raw_value.split(":"))
        if not user:
            raise click.BadParameter("Username should not be empty")
        return user, password
    return None


def validate_headers(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: Tuple[str, ...]
) -> Dict[str, str]:
    headers = {}
    for header in raw_value:
        with reraise_format_error(header):
            key, value = header.split(":")
        if not key:
            raise click.BadParameter("Header name should not be empty")
        headers[key] = value.lstrip()
    return headers


def convert_verbosity(
    ctx: click.core.Context, param: click.core.Parameter, value: Optional[str]
) -> Optional[hypothesis.Verbosity]:
    if value is None:
        return value
    return hypothesis.Verbosity[value]


@contextmanager
def reraise_format_error(raw_value: str) -> Generator[None, None, None]:
    try:
        yield
    except ValueError:
        raise click.BadParameter(f"Should be in KEY:VALUE format. Got: {raw_value}")
