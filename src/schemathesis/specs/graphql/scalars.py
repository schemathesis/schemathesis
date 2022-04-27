from typing import Dict

import graphql
from hypothesis import strategies as st

from ...exceptions import UsageError

CUSTOM_SCALARS: Dict[str, st.SearchStrategy[graphql.ValueNode]] = {}


def register_scalar(name: str, strategy: st.SearchStrategy[graphql.ValueNode]) -> None:
    """Register a new strategy for generating custom scalars.

    :param str name: Scalar name. It should correspond the one used in the schema.
    :param strategy: Hypothesis strategy you'd like to use to generate values for this scalar.
    """
    if not isinstance(name, str):
        raise UsageError(f"Scalar name {name!r} must be a string")
    if not isinstance(strategy, st.SearchStrategy):
        raise UsageError(f"{strategy!r} must be a Hypothesis strategy which generates AST nodes matching this scalar")
    CUSTOM_SCALARS[name] = strategy
