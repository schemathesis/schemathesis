"""Runtime expressions support.

https://swagger.io/docs/specification/links/#runtime-expressions
"""

from __future__ import annotations

import json
from typing import Any

from schemathesis.core.transforms import UNRESOLVABLE, Unresolvable
from schemathesis.generation.stateful.state_machine import StepOutput

from . import lexer, nodes, parser

__all__ = ["lexer", "nodes", "parser"]


def evaluate(expr: Any, output: StepOutput, evaluate_nested: bool = False) -> Any:
    """Evaluate runtime expression in context."""
    if isinstance(expr, (dict, list)) and evaluate_nested:
        return _evaluate_nested(expr, output)
    if not isinstance(expr, str):
        # Can be a non-string constant
        return expr
    parts = [node.evaluate(output) for node in parser.parse(expr)]
    if len(parts) == 1:
        return parts[0]  # keep the return type the same as the internal value type
    if any(isinstance(part, Unresolvable) for part in parts):
        return UNRESOLVABLE
    return "".join(str(part) for part in parts if part is not None)


def _evaluate_nested(expr: dict[str, Any] | list, output: StepOutput) -> Any:
    if isinstance(expr, dict):
        result_dict = {}
        for key, value in expr.items():
            new_key = _evaluate_object_key(key, output)
            if new_key is UNRESOLVABLE:
                return new_key
            new_value = evaluate(value, output, evaluate_nested=True)
            if new_value is UNRESOLVABLE:
                return new_value
            result_dict[new_key] = new_value
        return result_dict
    result_list = []
    for item in expr:
        new_value = evaluate(item, output, evaluate_nested=True)
        if new_value is UNRESOLVABLE:
            return new_value
        result_list.append(new_value)
    return result_list


def _evaluate_object_key(key: str, output: StepOutput) -> Any:
    evaluated = evaluate(key, output)
    if evaluated is UNRESOLVABLE:
        return evaluated
    if isinstance(evaluated, str):
        return evaluated
    if isinstance(evaluated, bool):
        return "true" if evaluated else "false"
    if isinstance(evaluated, (int, float)):
        return str(evaluated)
    if evaluated is None:
        return "null"
    return json.dumps(evaluated)
