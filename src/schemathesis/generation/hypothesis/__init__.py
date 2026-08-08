from __future__ import annotations

from collections.abc import Callable
from typing import Final

from schemathesis.core.cache import MISSING, BoundedCache

# Sentinel cached when generation raised `Unsatisfiable`.
UNSATISFIABLE_RESULT: Final = object()
# Cross-operation cache for `CoverageContext.generate_from_schema`.
schema_generation_cache: Final[BoundedCache] = BoundedCache(maxsize=2048)
# Stable identity for per-(generation_config, mode) custom-format dicts, so downstream caches
# keyed on `id(custom_formats)` actually hit instead of seeing a fresh dict per call.
custom_formats_cache: Final[BoundedCache] = BoundedCache(maxsize=32)
# Cross-operation cache for the canonical-form strategy keyed on (schema, validator, alphabet).
canonical_strategy_cache: Final[BoundedCache] = BoundedCache(maxsize=512)
# Cross-operation cache for the canonical form itself, which answers to (schema, draft) only.
canonical_form_cache: Final[BoundedCache] = BoundedCache(maxsize=512)
# `is_first_param_referenced_in_function` re-parses a function's AST per call; cache by code object.
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
