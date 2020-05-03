from typing import Any, Dict

from ..hooks import HookContext, register, unregister
from ..utils import traverse_schema


def install() -> None:
    register(before_load_schema)


def uninstall() -> None:
    unregister(before_load_schema)


def before_load_schema(context: HookContext, schema: Dict[str, Any]) -> None:
    traverse_schema(schema, _handle_boundaries)


def _handle_boundaries(schema: Dict[str, Any]) -> Dict[str, Any]:
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
