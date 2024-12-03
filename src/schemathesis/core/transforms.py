from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Union, overload


def deepclone(value: Any) -> Any:
    """A specialized version of `deepcopy` that copies only `dict` and `list` and does unrolling.

    It is on average 3x faster than `deepcopy` and given the amount of calls, it is an important optimization.
    """
    if isinstance(value, dict):
        return {
            k1: (
                {k2: deepclone(v2) for k2, v2 in v1.items()}
                if isinstance(v1, dict)
                else [deepclone(v2) for v2 in v1]
                if isinstance(v1, list)
                else v1
            )
            for k1, v1 in value.items()
        }
    if isinstance(value, list):
        return [
            {k2: deepclone(v2) for k2, v2 in v1.items()}
            if isinstance(v1, dict)
            else [deepclone(v2) for v2 in v1]
            if isinstance(v1, list)
            else v1
            for v1 in value
        ]
    return value


def diff(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate the difference between two dictionaries."""
    diff = {}
    for key, value in right.items():
        if key not in left or left[key] != value:
            diff[key] = value
    return diff


JsonValue = Union[Dict[str, Any], List, str, float, int]


@overload
def transform(schema: dict[str, Any], callback: Callable, *args: Any, **kwargs: Any) -> dict[str, Any]:
    pass


@overload
def transform(schema: list, callback: Callable, *args: Any, **kwargs: Any) -> list:
    pass


@overload
def transform(schema: str, callback: Callable, *args: Any, **kwargs: Any) -> str:
    pass


@overload
def transform(schema: float, callback: Callable, *args: Any, **kwargs: Any) -> float:
    pass


def transform(schema: JsonValue, callback: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> JsonValue:
    """Apply callback recursively to the given schema."""
    if isinstance(schema, dict):
        schema = callback(schema, *args, **kwargs)
        for key, sub_item in schema.items():
            schema[key] = transform(sub_item, callback, *args, **kwargs)
    elif isinstance(schema, list):
        schema = [transform(sub_item, callback, *args, **kwargs) for sub_item in schema]
    return schema
