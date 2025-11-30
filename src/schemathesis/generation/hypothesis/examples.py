from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from hypothesis import settings
    from hypothesis import strategies as st

SCHEMATHESIS_BENCHMARK_SEED = os.environ.get("SCHEMATHESIS_BENCHMARK_SEED")


@lru_cache
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


def generate_one(strategy: st.SearchStrategy[T], suppress_health_check: list | None = None) -> T:  # type: ignore[type-var]
    examples: list[T] = []
    add_single_example(strategy, examples, suppress_health_check)
    return examples[0]


def add_single_example(
    strategy: st.SearchStrategy[T], examples: list[T], suppress_health_check: list | None = None
) -> None:
    from hypothesis import given, seed, settings

    applied_settings = default_settings()
    if suppress_health_check is not None:
        applied_settings = settings(applied_settings, suppress_health_check=suppress_health_check)

    @given(strategy)  # type: ignore[untyped-decorator]
    @applied_settings  # type: ignore[untyped-decorator]
    def example_generating_inner_function(ex: T) -> None:
        examples.append(ex)

    example_generating_inner_function._hypothesis_internal_database_key = b""

    if SCHEMATHESIS_BENCHMARK_SEED is not None:
        example_generating_inner_function = seed(SCHEMATHESIS_BENCHMARK_SEED)(example_generating_inner_function)

    example_generating_inner_function()
