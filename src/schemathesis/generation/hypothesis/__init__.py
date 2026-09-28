from __future__ import annotations

import functools
import zlib
from collections.abc import Callable
from random import Random
from typing import TYPE_CHECKING, Final

from schemathesis.core.cache import MISSING, BoundedCache

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

# Sentinel cached when generation raised `Unsatisfiable`.
UNSATISFIABLE_RESULT: Final = object()
custom_formats_cache: Final[BoundedCache] = BoundedCache(maxsize=32)
canonical_strategy_cache: Final[BoundedCache] = BoundedCache(maxsize=128)
canonical_form_cache: Final[BoundedCache] = BoundedCache(maxsize=128)
_first_param_cache: Final[BoundedCache] = BoundedCache(maxsize=1024)


def setup() -> None:
    from hypothesis import core as root_core
    from hypothesis.internal.conjecture import engine
    from hypothesis.internal.entropy import deterministic_PRNG
    from hypothesis.internal.reflection import is_first_param_referenced_in_function
    from hypothesis.strategies._internal import collections, core
    from hypothesis.vendor import pretty

    from schemathesis.core import INTERNAL_BUFFER_SIZE

    if getattr(setup, "_is_patched", False):
        return

    # Forcefully initializes Hypothesis' global PRNG to avoid races that initialize it
    # if e.g. Schemathesis CLI is used with multiple workers
    with deterministic_PRNG():
        pass

    def _is_first_param_referenced_in_function(f: Callable) -> bool:
        code = getattr(f, "__code__", None)
        if code is None:
            return is_first_param_referenced_in_function(f)
        cached = _first_param_cache.get(code)
        if cached is MISSING:
            cached = is_first_param_referenced_in_function(f)
            _first_param_cache[code] = cached
        return cached

    core.is_first_param_referenced_in_function = _is_first_param_referenced_in_function

    class RepresentationPrinter(pretty.RepresentationPrinter):
        def pretty(self, obj: object) -> None:
            # This one takes way too much - in the coverage phase it may give >2 orders of magnitude improvement
            # depending on the schema size (~300 seconds -> 4.5 seconds in one of the benchmarks)
            return None

    root_core.RepresentationPrinter = RepresentationPrinter
    root_core.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    engine.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    collections.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    setup._is_patched = True  # type: ignore[attr-defined]


def uniform_randoms() -> SearchStrategy[Random]:
    """`Random` instances whose draws follow the distribution they name, for probability gates and weighted picks."""
    return _uniform_randoms_strategy()


@functools.cache
def _uniform_randoms_strategy() -> SearchStrategy[Random]:
    from hypothesis.internal.conjecture.data import ConjectureData
    from hypothesis.strategies import SearchStrategy

    class UniformRandoms(SearchStrategy[Random]):
        def do_draw(self, data: ConjectureData) -> Random:
            # Tracked `st.randoms()` makes every call a Hypothesis choice skewed toward boundary values, so a
            # `random() < 0.05` gate fires ~20% of the time. One seeded generator per draw keeps the stated rate.
            seed = data.draw_integer(0, 2**64 - 1)
            if seed == 0:
                # Hypothesis fills the tail of its early examples with zeros, which would give up to half of all
                # cases the same generator; what they drew before this point still differs, so seed from that.
                seed = zlib.crc32(repr(data.choices).encode())
            return Random(seed)

    return UniformRandoms()
