from __future__ import annotations

from typing import Any

from ..hooks import HookContext, register, unregister
from ..hooks import is_installed as global_is_installed
from ..internal.jsonschema import traverse_schema


def install() -> None:
    register(before_load_schema)


def uninstall() -> None:
    unregister(before_load_schema)


def is_installed() -> bool:
    return global_is_installed("before_load_schema", before_load_schema)


def before_load_schema(context: HookContext, schema: dict[str, Any]) -> None:
    adjust_schema(schema)


def adjust_schema(schema: dict[str, Any]) -> None:
    traverse_schema(schema, _handle_boundaries)


def _handle_boundaries(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert Draft 7 keywords to Draft 4 compatible versions.

    FastAPI uses ``pydantic``, which generates Draft 7 compatible schemas.
    """
    for boundary_name, boundary_exclusive_name in (("maximum", "exclusiveMaximum"), ("minimum", "exclusiveMinimum")):
        value = schema.get(boundary_exclusive_name)
        # `bool` check is needed, since in Python `True` is an instance of `int`
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            schema[boundary_exclusive_name] = True
            schema[boundary_name] = value
    return schema
