from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from schemathesis.core.jsonschema.resolver import Resolver, resolve_reference
from schemathesis.core.jsonschema.types import as_object_schema
from schemathesis.specs.openapi.adapter.validators import ensure_object


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
