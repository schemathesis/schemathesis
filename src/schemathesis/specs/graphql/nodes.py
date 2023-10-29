from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from graphql import ValueNode

# Re-export `hypothesis_graphql` helpers

__all__ = [  # noqa: F822
    "Boolean",
    "Enum",
    "Float",
    "Int",
    "List",
    "Null",
    "Object",
    "String",
]


def __getattr__(name: str) -> Optional["ValueNode"]:
    if name in __all__:
        import hypothesis_graphql.nodes

        return getattr(hypothesis_graphql.nodes, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
