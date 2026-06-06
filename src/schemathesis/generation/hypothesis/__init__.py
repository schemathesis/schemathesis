from __future__ import annotations

from typing import Final

from schemathesis.core.cache import BoundedCache

# Sentinel cached when generation raised `Unsatisfiable`.
UNSATISFIABLE_RESULT: Final = object()
# Cross-operation cache for `CoverageContext.generate_from_schema`.
schema_generation_cache: Final[BoundedCache] = BoundedCache(maxsize=2048)
# Stable identity for per-(generation_config, mode) custom-format dicts, so downstream caches
# keyed on `id(custom_formats)` actually hit instead of seeing a fresh dict per call.
custom_formats_cache: Final[BoundedCache] = BoundedCache(maxsize=32)


def setup() -> None:
    from hypothesis import core as root_core
    from hypothesis.internal.conjecture import engine
    from hypothesis.internal.entropy import deterministic_PRNG
    from hypothesis.strategies._internal import collections
    from hypothesis.vendor import pretty

    from schemathesis.core import INTERNAL_BUFFER_SIZE

    if getattr(setup, "_is_patched", False):
        return

    # Forcefully initializes Hypothesis' global PRNG to avoid races that initialize it
    # if e.g. Schemathesis CLI is used with multiple workers
    with deterministic_PRNG():
        pass

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
