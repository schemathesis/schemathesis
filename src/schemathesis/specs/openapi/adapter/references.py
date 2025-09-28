from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from schemathesis.core.compat import RefResolver


def maybe_resolve(item: Mapping[str, Any], resolver: RefResolver, scope: str) -> tuple[str, Mapping[str, Any]]:
    reference = item.get("$ref")
    if reference is not None:
        # TODO: this one should be synchronized
        resolver.push_scope(scope)
        try:
            return resolver.resolve(reference)
        finally:
            resolver.pop_scope()

    return scope, item
