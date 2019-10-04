from contextlib import contextmanager
from typing import Dict, Generator, Optional, Tuple
from urllib.parse import urlparse

import click


def validate_schema(ctx: click.core.Context, param: click.core.Option, raw_value: str) -> str:
    if not urlparse(raw_value).netloc:
        raise click.UsageError("Invalid SCHEMA, must be a valid URL.")
    return raw_value


def validate_auth(
    ctx: click.core.Context, param: click.core.Option, raw_value: Optional[str]
) -> Optional[Tuple[str, str]]:
    if raw_value is not None:
        with reraise_format_error(raw_value):
            user, password = tuple(raw_value.split(":"))
        return user, password
    return None


def validate_headers(ctx: click.core.Context, param: click.core.Option, raw_value: Tuple[str, ...]) -> Dict[str, str]:
    headers = {}
    for header in raw_value:
        with reraise_format_error(header):
            key, value = header.split(":")
        headers[key] = value.lstrip()
    return headers


@contextmanager
def reraise_format_error(raw_value: str) -> Generator:
    try:
        yield
    except ValueError:
        raise click.BadParameter(f"Should be in KEY:VALUE format. Got: {raw_value}")
