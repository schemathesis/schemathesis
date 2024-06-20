from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from ...exceptions import UsageError

if TYPE_CHECKING:
    import graphql
    from hypothesis import strategies as st

CUSTOM_SCALARS: dict[str, st.SearchStrategy[graphql.ValueNode]] = {}


def scalar(name: str, strategy: st.SearchStrategy[graphql.ValueNode]) -> None:
    """Register a new strategy for generating custom scalars.

    :param str name: Scalar name. It should correspond the one used in the schema.
    :param strategy: Hypothesis strategy you'd like to use to generate values for this scalar.
    """
    from hypothesis.strategies import SearchStrategy

    if not isinstance(name, str):
        raise UsageError(f"Scalar name {name!r} must be a string")
    if not isinstance(strategy, SearchStrategy):
        raise UsageError(f"{strategy!r} must be a Hypothesis strategy which generates AST nodes matching this scalar")
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
