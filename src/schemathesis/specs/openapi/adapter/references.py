from typing import Any, Mapping

from schemathesis.core.compat import RefResolver


def maybe_resolve(item: Mapping[str, Any], resolver: RefResolver, scope: str) -> tuple[str, Mapping[str, Any]]:
    reference = item.get("$ref")
    if reference is not None:
        resolver.push_scope(scope)
        try:
            return resolver.resolve(reference)
        finally:
            resolver.pop_scope()

    return scope, item
