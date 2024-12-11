from __future__ import annotations

from functools import reduce
from operator import or_
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hypothesis import strategies as st


def combine(strategies: list[st.SearchStrategy] | tuple[st.SearchStrategy]) -> st.SearchStrategy:
    """Combine a list of strategies into a single one.

    If the input is `[a, b, c]`, then the result is equivalent to `a | b | c`.
    """
    return reduce(or_, strategies[1:], strategies[0])
