from typing import Any, Mapping

from referencing._core import Resolver


def maybe_resolve(item: Mapping[str, Any], resolver: Resolver, scope: str) -> tuple[Mapping[str, Any], Resolver]:
    reference = item.get("$ref")
    if reference is not None:
        resolved = resolver.lookup(reference)
        return resolved.contents, resolved.resolver
    return item, resolver
