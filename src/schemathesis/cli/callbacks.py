import pathlib
from contextlib import contextmanager
from typing import Dict, Generator, Optional, Tuple
from urllib.parse import urlparse

import click
import hypothesis


def validate_schema(ctx: click.core.Context, param: click.core.Parameter, raw_value: str) -> str:
    if not urlparse(raw_value).netloc:
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
def reraise_format_error(raw_value: str) -> Generator:
    try:
        yield
    except ValueError:
        raise click.BadParameter(f"Should be in KEY:VALUE format. Got: {raw_value}")
