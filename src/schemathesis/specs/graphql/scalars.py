from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from schemathesis.core.errors import IncorrectUsage

if TYPE_CHECKING:
    import graphql
    from hypothesis import strategies as st

CUSTOM_SCALARS: dict[str, st.SearchStrategy[graphql.ValueNode]] = {}


def scalar(name: str, strategy: st.SearchStrategy[graphql.ValueNode]) -> None:
    r"""Register a custom Hypothesis strategy for generating GraphQL scalar values.

    Args:
        name: Scalar name that matches your GraphQL schema scalar definition
        strategy: Hypothesis strategy that generates GraphQL AST ValueNode objects

    Example:
        ```python
        import schemathesis
        from hypothesis import strategies as st
        from schemathesis.graphql import nodes

        # Register email scalar
        schemathesis.graphql.scalar("Email", st.emails().map(nodes.String))

        # Register positive integer scalar
        schemathesis.graphql.scalar(
            "PositiveInt",
            st.integers(min_value=1).map(nodes.Int)
        )

        # Register phone number scalar
        schemathesis.graphql.scalar(
            "Phone",
            st.from_regex(r"\+1-\d{3}-\d{3}-\d{4}").map(nodes.String)
        )
        ```

    Schema usage:
        ```graphql
        scalar Email
        scalar PositiveInt

        type Query {
          getUser(email: Email!, rating: PositiveInt!): User
        }
        ```

    """
    from hypothesis.strategies import SearchStrategy

    if not isinstance(name, str):
        raise IncorrectUsage(f"Scalar name {name!r} must be a string")
    if not isinstance(strategy, SearchStrategy):
        raise IncorrectUsage(
            f"{strategy!r} must be a Hypothesis strategy which generates AST nodes matching this scalar"
        )
    CUSTOM_SCALARS[name] = strategy


@lru_cache
def get_extra_scalar_strategies() -> dict[str, st.SearchStrategy]:
    """Get all extra GraphQL strategies."""
    from hypothesis import strategies as st

    from . import nodes

    dates = st.dates().map(str)
    times = st.times().map("%sZ".__mod__)

    return {
        "Date": dates.map(nodes.String),
        "Time": times.map(nodes.String),
        "DateTime": st.tuples(dates, times).map("T".join).map(nodes.String),
        "IP": st.ip_addresses().map(str).map(nodes.String),
        "IPv4": st.ip_addresses(v=4).map(str).map(nodes.String),
        "IPv6": st.ip_addresses(v=6).map(str).map(nodes.String),
        "BigInt": st.integers().map(nodes.Int),
        "Long": st.integers(min_value=-(2**63), max_value=2**63 - 1).map(nodes.Int),
        "UUID": st.uuids().map(str).map(nodes.String),
    }
