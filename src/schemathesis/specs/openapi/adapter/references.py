from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import jsonschema_rs

from schemathesis.core.jsonschema.resolver import resolve_reference


def maybe_resolve_with_resolver(
    item: Mapping[str, Any], resolver: jsonschema_rs.Resolver
) -> tuple[jsonschema_rs.Resolver, Mapping[str, Any]]:
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
