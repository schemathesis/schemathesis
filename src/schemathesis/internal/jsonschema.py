from __future__ import annotations

from typing import Any, Callable, Dict, List, Union, overload

JsonValue = Union[Dict[str, Any], List, str, float, int]


@overload
def traverse_schema(schema: dict[str, Any], callback: Callable, *args: Any, **kwargs: Any) -> dict[str, Any]:
    pass


@overload
def traverse_schema(schema: list, callback: Callable, *args: Any, **kwargs: Any) -> list:
    pass


@overload
def traverse_schema(schema: str, callback: Callable, *args: Any, **kwargs: Any) -> str:
    pass


@overload
def traverse_schema(schema: float, callback: Callable, *args: Any, **kwargs: Any) -> float:
    pass


def traverse_schema(schema: JsonValue, callback: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> JsonValue:
    """Apply callback recursively to the given schema."""
    if isinstance(schema, dict):
        schema = callback(schema, *args, **kwargs)
        for key, sub_item in schema.items():
            schema[key] = traverse_schema(sub_item, callback, *args, **kwargs)
    elif isinstance(schema, list):
        schema = [traverse_schema(sub_item, callback, *args, **kwargs) for sub_item in schema]
    return schema
