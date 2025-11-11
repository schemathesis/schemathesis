from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Literal

import click

from schemathesis.cli.ext.groups import grouped_option


def _with_filter(*, by: str, mode: Literal["include", "exclude"], modifier: Literal["regex"] | None) -> Callable:
    """Generate a CLI option for filtering API operations."""
    param = f"--{mode}-{by}"
    action = "include in" if mode == "include" else "exclude from"
    prop = {
        "operation-id": "ID",
        "name": "Operation name",
    }.get(by, by.capitalize())
    callback = None
    if modifier:
        param += f"-{modifier}"
        prop += " pattern"
    else:
        callback = partial(validate_filter, arg_name=param)
    help_text = f"{prop} to {action} testing."
    return grouped_option(
        param,
        help=help_text,
        type=str,
        multiple=modifier is None,
        callback=callback,
        hidden=True,
    )


def validate_filter(
    ctx: click.core.Context, param: click.core.Parameter, raw_value: list[str], arg_name: str
) -> list[str]:
    if len(raw_value) != len(set(raw_value)):
        duplicates = ",".join(sorted({value for value in raw_value if raw_value.count(value) > 1}))
        raise click.UsageError(f"Duplicate values are not allowed for `{arg_name}`: {duplicates}")
    return raw_value


_BY_VALUES = ("operation-id", "tag", "name", "method", "path")


def with_filters(command: Callable) -> Callable:
    for by in _BY_VALUES:
        for mode in ("exclude", "include"):
            for modifier in ("regex", None):
                command = _with_filter(by=by, mode=mode, modifier=modifier)(command)
    return command
