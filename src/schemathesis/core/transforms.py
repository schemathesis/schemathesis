from __future__ import annotations

import string
from collections.abc import Callable, Iterable, Iterator, Mapping
from functools import lru_cache
from typing import Any, overload

import jsonschema_rs

deepclone = jsonschema_rs.canonical.schema.clone


@lru_cache
def get_template_fields(template: str) -> frozenset[str]:
    """Extract named placeholders from a string template.

    "/users/{userId}/posts/{postId}" -> {"userId", "postId"}
    """
    try:
        parameters = frozenset(name for _, name, _, _ in string.Formatter().parse(template) if name is not None)
        # Check for malformed params to avoid injecting them
        template.format(**dict.fromkeys(parameters, ""))
        return parameters
    except (ValueError, IndexError):
        return frozenset()


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

    return resolve_path(document, iter_decoded_pointer_segments(pointer))


def resolve_path(document: Any, path: Iterable[str | int]) -> dict | list | str | int | float | None | Unresolvable:
    target = document
    for token in path:
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
