from __future__ import annotations

from functools import lru_cache, reduce
from operator import or_
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from hypothesis import settings
    from hypothesis import strategies as st


@lru_cache()
def default_settings() -> settings:
    from hypothesis import HealthCheck, Phase, Verbosity, settings

    return settings(
        database=None,
        max_examples=1,
        deadline=None,
        verbosity=Verbosity.quiet,
        phases=(Phase.generate,),
        suppress_health_check=list(HealthCheck),
    )


T = TypeVar("T")


def get_single_example(strategy: st.SearchStrategy[T]) -> T:  # type: ignore[type-var]
    examples: list[T] = []
    add_single_example(strategy, examples)
    return examples[0]


def add_single_example(strategy: st.SearchStrategy[T], examples: list[T]) -> None:
    from hypothesis import given

    @given(strategy)  # type: ignore
    @default_settings()  # type: ignore
    def example_generating_inner_function(ex: T) -> None:
        examples.append(ex)

    example_generating_inner_function()


def combine_strategies(strategies: list[st.SearchStrategy] | tuple[st.SearchStrategy]) -> st.SearchStrategy:
    """Combine a list of strategies into a single one.

    If the input is `[a, b, c]`, then the result is equivalent to `a | b | c`.
    """
    return reduce(or_, strategies[1:], strategies[0])
