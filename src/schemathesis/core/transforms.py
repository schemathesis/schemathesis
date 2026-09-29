from __future__ import annotations

import datetime
import string
from collections.abc import Callable, Iterable, Iterator, Mapping
from functools import lru_cache
from typing import Any, overload

import jsonschema_rs

from schemathesis.core.jsonschema.types import JsonValue

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


def to_wire_string(value: object) -> str:
    """Render one value as the wire spells it: JSON scalars, not their Python repr."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def is_json(document: object) -> bool:
    """Whether the document holds only JSON values and string keys."""
    # Serializing stops at the first offending value and is several times faster than walking the document.
    try:
        jsonschema_rs.canonical.json.to_string(document)
    except ValueError:
        return False
    return True


def stringify_keys(document: object) -> None:
    """Spell every non-string mapping key in place as the wire does, e.g. a YAML 1.1 `on:` read as `True`."""
    if is_json(document):
        return
    stack = [document]
    # YAML anchors can make a container its own descendant.
    seen: set[int] = set()
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if id(item) in seen:
                continue
            seen.add(id(item))
            if not all(isinstance(key, str) for key in item):
                items = [(key if isinstance(key, str) else to_wire_string(key), value) for key, value in item.items()]
                item.clear()
                item.update(items)
            stack.extend(item.values())
        elif isinstance(item, list):
            if id(item) in seen:
                continue
            seen.add(id(item))
            stack.extend(item)


def describe_non_json_value(document: object) -> str | None:
    """Describe the first value JSON cannot represent, e.g. bytes from a YAML `!!binary` tag, with its location."""
    if is_json(document):
        return None
    stack: list[tuple[list[str | int], object]] = [([], document)]
    # YAML anchors can make a container its own descendant.
    seen: set[int] = set()
    while stack:
        path, item = stack.pop()
        if isinstance(item, (dict, list, tuple)):
            if id(item) in seen:
                continue
            seen.add(id(item))
            entries = item.items() if isinstance(item, dict) else enumerate(item)
            stack.extend(([*path, key], value) for key, value in reversed(list(entries)))
        elif not (item is None or isinstance(item, (str, int, float))):
            location = " -> ".join(str(entry) for entry in path)
            return f"Unsupported value at `{location}`: {_describe_type(item)} is not valid JSON"
    return None


def _describe_type(value: object) -> str:
    if isinstance(value, bytes):
        return "binary data"
    if isinstance(value, (set, frozenset)):
        return "a set"
    if isinstance(value, datetime.datetime):
        return "a timestamp"
    if isinstance(value, datetime.date):
        return "a date"
    return f"`{type(value).__name__}`"


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


@overload
def transform(schema: dict[str, Any], callback: Callable, *args: Any, **kwargs: Any) -> dict[str, Any]: ...


@overload
def transform(schema: list, callback: Callable, *args: Any, **kwargs: Any) -> list: ...


@overload
def transform(schema: str, callback: Callable, *args: Any, **kwargs: Any) -> str: ...


@overload
def transform(schema: float, callback: Callable, *args: Any, **kwargs: Any) -> float: ...


@overload
def transform(schema: JsonValue, callback: Callable, *args: Any, **kwargs: Any) -> JsonValue: ...


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


def resolve_pointer(document: object, pointer: str) -> object | Unresolvable:
    """Implementation is adapted from Rust's `serde-json` crate.

    Ref: https://github.com/serde-rs/json/blob/master/src/value/mod.rs#L751
    """
    if not pointer:
        return document
    if not pointer.startswith("/"):
        return UNRESOLVABLE

    return resolve_path(document, iter_decoded_pointer_segments(pointer))


def resolve_path(document: object, path: Iterable[str | int]) -> object | Unresolvable:
    target: object = document
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


def resolve_pointer_all(document: object, pointer: str) -> list[object] | Unresolvable:
    """Resolve a JSON Pointer that may contain `*` wildcard segments, fanning out at each `*`.

    Returns a flat list of every match. Returns UNRESOLVABLE only if the
    pointer is malformed or fails before any `*`; failures past a `*` drop
    silently so partial coverage is preserved.
    """
    if not pointer:
        return [document]
    if not pointer.startswith("/"):
        return UNRESOLVABLE
    return _resolve_all(document, list(iter_decoded_pointer_segments(pointer)))


def _resolve_all(target: object, segments: list[str]) -> list[object] | Unresolvable:
    if not segments:
        return [target]
    head, *rest = segments
    if head == "*":
        if not isinstance(target, list):
            return []
        results: list[object] = []
        for item in target:
            sub = _resolve_all(item, rest)
            if isinstance(sub, list):
                results.extend(sub)
        return results
    if isinstance(target, dict):
        if head not in target:
            return UNRESOLVABLE
        return _resolve_all(target[head], rest)
    if isinstance(target, list):
        try:
            return _resolve_all(target[int(head)], rest)
        except (IndexError, ValueError):
            return UNRESOLVABLE
    return UNRESOLVABLE
