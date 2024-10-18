"""A set of performance-related patches."""

from typing import Any


def install() -> None:
    from hypothesis.internal.reflection import is_first_param_referenced_in_function
    from hypothesis.strategies._internal import core
    from hypothesis_jsonschema import _from_schema, _resolve

    from .internal.copy import fast_deepcopy

    # This one is used a lot, and under the hood it re-parses the AST of the same function
    def _is_first_param_referenced_in_function(f: Any) -> bool:
        if f.__name__ == "from_object_schema" and f.__module__ == "hypothesis_jsonschema._from_schema":
            return True
        return is_first_param_referenced_in_function(f)

    core.is_first_param_referenced_in_function = _is_first_param_referenced_in_function  # type: ignore
    _resolve.deepcopy = fast_deepcopy  # type: ignore
    _from_schema.deepcopy = fast_deepcopy  # type: ignore
