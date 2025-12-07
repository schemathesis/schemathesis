from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from schemathesis.core.compat import RefResolver


def maybe_resolve(item: Mapping[str, Any], resolver: RefResolver, scope: str) -> tuple[str, Mapping[str, Any]]:
    reference = item.get("$ref")
    if reference is None:
        return scope, item

    # Track seen references to detect circular $refs and resolve nested ones
    seen: set[str] = set()
    current_scope = scope
    current_item = item

    while True:
        reference = current_item.get("$ref")
        if reference is None:
            return current_scope, current_item

        # Detect circular references
        if reference in seen:
            return current_scope, current_item
        seen.add(reference)

        # TODO: this one should be synchronized
        resolver.push_scope(current_scope)
        try:
            current_scope, current_item = resolver.resolve(reference)
        finally:
            resolver.pop_scope()
