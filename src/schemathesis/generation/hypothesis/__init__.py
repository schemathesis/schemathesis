from __future__ import annotations

from collections.abc import Callable
from typing import Final

from schemathesis.core.cache import MISSING, BoundedCache

# Sentinel cached when generation raised `Unsatisfiable`.
UNSATISFIABLE_RESULT: Final = object()
custom_formats_cache: Final[BoundedCache] = BoundedCache(maxsize=32)
canonical_strategy_cache: Final[BoundedCache] = BoundedCache(maxsize=512)
canonical_form_cache: Final[BoundedCache] = BoundedCache(maxsize=512)
_first_param_cache: Final[BoundedCache] = BoundedCache(maxsize=1024)


def add_database_key(target: object, key: str) -> None:
    """Keep the saved examples of `target` apart from other tests that share its name."""
    # Hypothesis mixes this private attribute into its example database key; there is no public API for it.
    target._hypothesis_internal_add_digest = key.encode("utf8")  # type: ignore[attr-defined]


def setup() -> None:
    from hypothesis import core as root_core
    from hypothesis.internal import entropy
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

    threadlocal = getattr(root_core, "threadlocal", None)
    if threadlocal is not None:
        register_random = entropy.register_random

        def _register_random(r: entropy.RandomLike) -> None:
            # Every new worker thread registers its own Hypothesis PRNG, and the check for an unreferenced
            # instance runs a full garbage collection, which takes seconds on large heaps
            if r is threadlocal._hypothesis_global_random:
                entropy.RANDOMS_TO_MANAGE[next(entropy._RKEY)] = r
            else:
                register_random(r)

        entropy.register_random = _register_random

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
        def pretty(self, obj: object, *, cycle: bool = False) -> None:
            # This one takes way too much - in the coverage phase it may give >2 orders of magnitude improvement
            # depending on the schema size (~300 seconds -> 4.5 seconds in one of the benchmarks)
            return None

    # Hypothesis exposes no hook for its printer, so the class is swapped in place.
    root_core.RepresentationPrinter = RepresentationPrinter  # type: ignore[misc]
    root_core.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    engine.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    collections.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    setup._is_patched = True  # type: ignore[attr-defined]
