import re
from contextlib import contextmanager
from typing import Dict, Generator, Optional, Tuple
from urllib.parse import urlparse

import click
import hypothesis
from requests import PreparedRequest, RequestException

from .. import utils


def validate_schema(ctx: click.core.Context, param: click.core.Parameter, raw_value: str) -> str:
    if "app" not in ctx.params:
        if not urlparse(raw_value).netloc:
            if "\x00" in raw_value or not utils.file_exists(raw_value):
                raise click.UsageError("Invalid SCHEMA, must be a valid URL or file path.")
            if "base_url" not in ctx.params:
                raise click.UsageError('Missing argument, "--base-url" is required for SCHEMA specified by file.')
        else:
            _validate_url(raw_value)
    return raw_value


def _validate_url(value: str) -> None:
    try:
        PreparedRequest().prepare_url(value, {})  # type: ignore
    except RequestException:
        raise click.UsageError("Invalid SCHEMA, must be a valid URL or file path.")


def validate_base_url(ctx: click.core.Context, param: click.core.Parameter, raw_value: str) -> str:
    if raw_value and not urlparse(raw_value).netloc:
        raise click.UsageError("Invalid base URL")
    return raw_value


def validate_app(ctx: click.core.Context, param: click.core.Parameter, raw_value: Optional[str]) -> Optional[str]:
    if raw_value is None:
        return raw_value
    try:
        utils.import_app(raw_value)
        # String is returned instead of an app because it might be passed to a subprocess
        # Since most of app instances are not-transferable to another process, they are passed as strings and
        # imported in a subprocess
        return raw_value
    except Exception as exc:
        show_errors_tracebacks = ctx.params["show_errors_tracebacks"]
        message = utils.format_exception(exc, show_errors_tracebacks)
        click.secho(f"{message}\nCan not import application from the given module", fg="red")
        if not show_errors_tracebacks:
            click.secho(
                "Add this option to your command line parameters to see full tracebacks: --show-errors-tracebacks",
                fg="red",
            )
        raise click.exceptions.Exit(1)


def validate_auth(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: Optional[str]
) -> Optional[Tuple[str, str]]:
    if raw_value is not None:
        with reraise_format_error(raw_value):
            user, password = tuple(raw_value.split(":"))
        if not user:
            raise click.BadParameter("Username should not be empty")
        if not utils.is_latin_1_encodable(user):
            raise click.BadParameter("Username should be latin-1 encodable")
        if not utils.is_latin_1_encodable(password):
            raise click.BadParameter("Password should be latin-1 encodable")
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
            raise click.BadParameter("Header name should not be empty")
        if not utils.is_latin_1_encodable(key):
            raise click.BadParameter("Header name should be latin-1 encodable")
        if not utils.is_latin_1_encodable(value):
            raise click.BadParameter("Header value should be latin-1 encodable")
        if utils.has_invalid_characters(key, value):
            raise click.BadParameter("Invalid return character or leading space in header")
        headers[key] = value
    return headers


def validate_regex(ctx: click.core.Context, param: click.core.Parameter, raw_value: Tuple[str, ...]) -> Tuple[str, ...]:
    for value in raw_value:
        try:
            re.compile(value)
        except re.error as exc:
            raise click.BadParameter(f"Invalid regex: {exc.args[0]}")
    return raw_value


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
