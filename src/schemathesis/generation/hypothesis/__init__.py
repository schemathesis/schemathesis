from typing import Any

# Maximum test running time
DEFAULT_DEADLINE = 15000


def setup() -> None:
    from hypothesis import core as root_core
    from hypothesis.internal.conjecture import engine
    from hypothesis.internal.entropy import deterministic_PRNG
    from hypothesis.internal.reflection import is_first_param_referenced_in_function
    from hypothesis.strategies._internal import collections, core
    from hypothesis_jsonschema import _from_schema, _resolve

    from schemathesis.core import INTERNAL_BUFFER_SIZE
    from schemathesis.core.transforms import deepclone

    # Forcefully initializes Hypothesis' global PRNG to avoid races that initialize it
    # if e.g. Schemathesis CLI is used with multiple workers
    with deterministic_PRNG():
        pass

    # A set of performance-related patches

    # This one is used a lot, and under the hood it re-parses the AST of the same function
    def _is_first_param_referenced_in_function(f: Any) -> bool:
        if f.__name__ == "from_object_schema" and f.__module__ == "hypothesis_jsonschema._from_schema":
            return True
        return is_first_param_referenced_in_function(f)

    core.is_first_param_referenced_in_function = _is_first_param_referenced_in_function  # type: ignore
    _resolve.deepcopy = deepclone  # type: ignore
    _from_schema.deepcopy = deepclone  # type: ignore
    root_core.BUFFER_SIZE = INTERNAL_BUFFER_SIZE  # type: ignore
    engine.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    collections.BUFFER_SIZE = INTERNAL_BUFFER_SIZE  # type: ignore
