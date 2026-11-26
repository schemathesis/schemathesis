from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Final

from schemathesis.core.cache import MISSING, BoundedCache

if TYPE_CHECKING:
    from io import TextIOBase

    from hypothesis.control import BuildContext

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
    from schemathesis.generation.hypothesis.marks import ApiOperationMark

    if getattr(setup, "_is_patched", False):
        return

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

    # Initialize Hypothesis' global PRNG up front; multiple CLI workers would race to initialize it
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
        def __init__(self, output: TextIOBase | None = None, *, context: BuildContext | None = None) -> None:
            super().__init__(output, context=context)
            # Older Hypothesis versions do not expose the running test; printing stays off for every test there
            test = getattr(context, "wrapped_test", None)
            self._is_silent = test is None or ApiOperationMark.is_set(test)

        # Older Hypothesis versions take no `cycle` argument
        def pretty(self, obj: object, **kwargs: bool) -> None:
            # Pretty-printing large values is costly, most of all in the coverage phase.
            # Tests built by Schemathesis report their failures on their own; other tests keep Hypothesis output
            if self._is_silent:
                return None
            super().pretty(obj, **kwargs)

    generate_mutations_from = engine.ConjectureRunner.generate_mutations_from

    def _generate_mutations_from(self: engine.ConjectureRunner, data: object) -> None:
        # Mutation copies a generated value over its siblings, so half of the cases repeat one value in several
        # fields. State machines keep it: repeating a step is how they revisit a resource
        test = getattr(getattr(self._test_function, "__self__", None), "wrapped_test", None)
        if test is not None and ApiOperationMark.is_set(test):
            return None
        return generate_mutations_from(self, data)  # type: ignore[arg-type]

    # Hypothesis has no setting for its mutation pass.
    engine.ConjectureRunner.generate_mutations_from = _generate_mutations_from  # type: ignore[method-assign]
    # Hypothesis exposes no hook for its printer, so the class is swapped in place.
    root_core.RepresentationPrinter = RepresentationPrinter  # type: ignore[misc]
    root_core.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    engine.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    collections.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    _skip_local_constants()
    setup._is_patched = True  # type: ignore[attr-defined]


def _skip_local_constants() -> None:
    # Hypothesis draws literals from local modules, such as hooks and auth files, so their secrets would reach requests
    # and the data for a seed would depend on what is imported. No public opt-out: HypothesisWorks/hypothesis#4627
    try:
        from hypothesis.internal.conjecture.providers import CONSTANTS_CACHE, HypothesisProvider
        from hypothesis.internal.constants_ast import Constants

        cached_choices = CONSTANTS_CACHE.cache
    except (ImportError, AttributeError):  # pragma: no cover
        return
    no_constants = Constants()
    # A property, unlike the upstream cached one, also covers providers that already cached local constants.
    HypothesisProvider._local_constants = property(lambda self: no_constants)  # type: ignore[assignment]
    # Constant choices this thread cached earlier may include local constants.
    cached_choices.clear()
