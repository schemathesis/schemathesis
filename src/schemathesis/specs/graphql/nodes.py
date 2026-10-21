from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hypothesis_graphql.nodes import Boolean, Enum, Float, Int, List, Null, Object, String

# Re-export `hypothesis_graphql` helpers

__all__ = [
    "Boolean",
    "Enum",
    "Float",
    "Int",
    "List",
    "Null",
    "Object",
    "String",
]


def __getattr__(name: str) -> object:
    if name in __all__:
        import hypothesis_graphql.nodes

        return getattr(hypothesis_graphql.nodes, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
