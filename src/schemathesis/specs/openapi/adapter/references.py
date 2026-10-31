from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.errors import RefResolutionError
from schemathesis.core.jsonschema.resolver import Resolver, resolve_reference, resolve_reference_with_uri
from schemathesis.core.jsonschema.types import JsonValue, as_object_schema
from schemathesis.specs.openapi.adapter.validators import ensure_object


def example_object_value(example: Mapping[str, Any]) -> JsonValue | NotSet:
    """The data an Example Object holds: `value`, or Open API 3.2 `dataValue`."""
    if "value" in example:
        return example["value"]
    return example.get("dataValue", NOT_SET)


def iter_example_values(container: object) -> Iterator[object]:
    """The data an `examples` container holds: a mapping of Example Objects, or a list mixing them with plain values."""
    if isinstance(container, dict):
        for example in container.values():
            if isinstance(example, dict) and not isinstance(value := example_object_value(example), NotSet):
                yield value
    elif isinstance(container, list):
        for item in container:
            if not isinstance(data := _list_example_value(item), NotSet):
                yield data


def _list_example_value(item: object) -> object:
    """The data a list item holds, whether it is an Example Object or a plain value."""
    if not isinstance(item, dict):
        return item
    value = example_object_value(item)
    if not isinstance(value, NotSet):
        return value
    # An Example Object with only its wire form, an external location or a broken reference has no data to send
    if "serializedValue" in item or "externalValue" in item or "$ref" in item:
        return NOT_SET
    return item


def has_example_references(definition: Mapping[str, Any]) -> bool:
    """Whether any Example Object of the definition is given by `$ref`."""
    return any(
        isinstance(container, (dict, list))
        and any(
            isinstance(example, dict) and "$ref" in example
            for example in (container.values() if isinstance(container, dict) else container)
        )
        for container in (definition.get("examples"), definition.get("x-examples"))
    )


def resolve_example_references(definition: Mapping[str, Any], resolver: Resolver) -> Mapping[str, Any]:
    """Inline referenced Example Objects so every phase sees their values."""
    if not has_example_references(definition):
        return definition
    updated = dict(definition)
    for keyword in ("examples", "x-examples"):
        container = definition.get(keyword)
        if isinstance(container, dict):
            updated[keyword] = {name: _resolve_example(example, resolver) for name, example in container.items()}
        elif isinstance(container, list):
            updated[keyword] = [_resolve_example(example, resolver) for example in container]
    return updated


def _resolve_example(example: object, resolver: Resolver) -> object:
    seen: set[str] = set()
    while isinstance(example, dict) and isinstance(reference := example.get("$ref"), str):
        try:
            uri, resolver, target = resolve_reference_with_uri(resolver, reference)
        except RefResolutionError:
            # Kept as is, so the examples phase reports the broken reference.
            return example
        # A chain that loops back or ends on a non-object stays a reference, which holds no data
        if uri in seen or not isinstance(target, dict):
            return example
        seen.add(uri)
        example = target
    return example


def resolve_media_type_object(item: Mapping[str, Any], resolver: Resolver) -> tuple[Resolver, Mapping[str, Any]]:
    """Resolve a Media Type Object and its referenced Example Objects against the document they appear in."""
    media_type_resolver, media_type_object = maybe_resolve_with_resolver(item, resolver, allow_boolean=True)
    return media_type_resolver, resolve_example_references(media_type_object, media_type_resolver)


def maybe_resolve_with_resolver(
    item: Mapping[str, Any], resolver: Resolver, *, allow_boolean: bool = False
) -> tuple[Resolver, Mapping[str, Any]]:
    reference = item.get("$ref")
    if reference is None:
        return resolver, item

    seen: set[str] = set()
    current_resolver = resolver
    current_item = item

    while True:
        reference = current_item.get("$ref")
        if reference is None:
            return current_resolver, current_item

        if reference in seen:
            return current_resolver, current_item
        seen.add(reference)

        current_resolver, current_item = resolve_reference(current_resolver, reference)
        if allow_boolean and isinstance(current_item, bool):
            return current_resolver, as_object_schema(current_item)
        ensure_object(current_item, f"Reference target `{reference}`")
