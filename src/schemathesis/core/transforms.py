from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Any, TypeVar, overload

T = TypeVar("T")


@overload
def deepclone(value: dict) -> dict: ...  # pragma: no cover


@overload
def deepclone(value: list) -> list: ...  # pragma: no cover


@overload
def deepclone(value: T) -> T: ...  # pragma: no cover


def deepclone(value: Any) -> Any:
    """A specialized version of `deepcopy` that copies only `dict` and `list` and does unrolling.

    It is on average 3x faster than `deepcopy` and given the amount of calls, it is an important optimization.
    """
    if isinstance(value, dict):
        return {
            k1: (
                {
                    k2: (
                        {k3: deepclone(v3) for k3, v3 in v2.items()}
                        if isinstance(v2, dict)
                        else [deepclone(v3) for v3 in v2]
                        if isinstance(v2, list)
                        else v2
                    )
                    for k2, v2 in v1.items()
                }
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


def merge_at(data: dict[str, Any], data_key: str, new: dict[str, Any]) -> None:
    original = data[data_key] or {}
    for key, value in new.items():
        original[key] = value
    data[data_key] = original


JsonValue = dict[str, Any] | list | str | float | int


@overload
def transform(schema: dict[str, Any], callback: Callable, *args: Any, **kwargs: Any) -> dict[str, Any]: ...


@overload
def transform(schema: list, callback: Callable, *args: Any, **kwargs: Any) -> list: ...


@overload
def transform(schema: str, callback: Callable, *args: Any, **kwargs: Any) -> str: ...


@overload
def transform(schema: float, callback: Callable, *args: Any, **kwargs: Any) -> float: ...


def transform(schema: JsonValue, callback: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> JsonValue:
    """Apply callback recursively to the given schema."""
    if isinstance(schema, dict):
        schema = callback(schema, *args, **kwargs)
        for key, sub_item in schema.items():
            schema[key] = transform(sub_item, callback, *args, **kwargs)
    elif isinstance(schema, list):
        schema = [transform(sub_item, callback, *args, **kwargs) for sub_item in schema]
    return schema


class Unresolvable: ...


UNRESOLVABLE = Unresolvable()


def encode_pointer(pointer: str) -> str:
    return pointer.replace("~", "~0").replace("/", "~1")


def decode_pointer(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def iter_decoded_pointer_segments(pointer: str) -> Iterator[str]:
    return map(decode_pointer, pointer.split("/")[1:])


def resolve_pointer(document: Any, pointer: str) -> dict | list | str | int | float | None | Unresolvable:
    """Implementation is adapted from Rust's `serde-json` crate.

    Ref: https://github.com/serde-rs/json/blob/master/src/value/mod.rs#L751
    """
    if not pointer:
        return document
    if not pointer.startswith("/"):
        return UNRESOLVABLE

    target = document
    for token in iter_decoded_pointer_segments(pointer):
        if isinstance(target, dict):
            target = target.get(token, UNRESOLVABLE)
            if target is UNRESOLVABLE:
                return UNRESOLVABLE
        elif isinstance(target, list):
            try:
                target = target[int(token)]
            except (IndexError, ValueError):
                return UNRESOLVABLE
        else:
            return UNRESOLVABLE
    return target
