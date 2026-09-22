"""Which response values a schema says are possible.

An `enum` in a response schema names every value a field can hold. That makes it the one part of an
operation's behavior we know the size of without guessing - everywhere else we count what happened
and estimate what did not.

Only fields the behavior alphabet can see are collected: a top-level field of a JSON object body,
or one level of nesting. Values are named exactly as the alphabet names them, or a declared value
could never match an observed one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from schemathesis.core.behaviors import value_label
from schemathesis.core.jsonschema import maybe_resolve_bundled, schema_with_bundle

if TYPE_CHECKING:
    from schemathesis.core.jsonschema.types import JsonSchema, JsonSchemaObject
    from schemathesis.schemas import APIOperation

# How far the walk descends before giving up, so a recursive schema cannot hang it.
MAX_DEPTH = 12

COMBINATORS = ("allOf", "anyOf", "oneOf")


def collect(operation: APIOperation) -> dict[str, frozenset[str]]:
    """Every response field whose schema declares the values it can hold."""
    found: dict[str, set[str]] = {}
    for _, response in operation.responses.items():
        root = response.get_schema().schema
        if isinstance(root, dict):
            _walk(root, "", found, root=root, depth=0)
    return {name: frozenset(values) for name, values in found.items() if values}


def _resolve(schema: Any, root: JsonSchema) -> Any:
    """Follow a bundled `$ref` by splicing the root's bundle into the fragment."""
    if not isinstance(schema, dict):
        return schema
    # `schema_with_bundle` preserves shape: dict in, dict out.
    return maybe_resolve_bundled(cast("JsonSchemaObject", schema_with_bundle(schema, root)))


def _walk(schema: Any, prefix: str, found: dict[str, set[str]], *, root: JsonSchema, depth: int) -> None:
    if depth > MAX_DEPTH:
        return
    schema = _resolve(schema, root)
    if not isinstance(schema, dict):
        return
    for keyword in COMBINATORS:
        for branch in schema.get(keyword) or ():
            # A branch describes the same object, so its properties land at the same prefix.
            _walk(branch, prefix, found, root=root, depth=depth + 1)
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    for name, child in properties.items():
        full = f"{prefix}.{name}" if prefix else name
        _declared(child, full, found, root=root, depth=depth)
        if not prefix:
            # The alphabet reads one level into nested objects and no further.
            _walk(child, full, found, root=root, depth=depth + 1)


def _declared(schema: Any, name: str, found: dict[str, set[str]], *, root: JsonSchema, depth: int) -> None:
    """Values this field declares, including through a combinator that wraps it."""
    if depth > MAX_DEPTH:
        return
    schema = _resolve(schema, root)
    if not isinstance(schema, dict):
        return
    values = schema.get("enum")
    if isinstance(values, list):
        labels = {label for label in map(value_label, values) if label is not None}
        if labels:
            found.setdefault(name, set()).update(labels)
    for keyword in COMBINATORS:
        for branch in schema.get(keyword) or ():
            _declared(branch, name, found, root=root, depth=depth + 1)
