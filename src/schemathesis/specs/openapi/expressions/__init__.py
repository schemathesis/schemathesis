"""Runtime expressions support.

https://swagger.io/docs/specification/links/#runtime-expressions
"""

from __future__ import annotations

import json
from typing import Any

from . import lexer, nodes, parser
from .context import ExpressionContext


def evaluate(expr: Any, context: ExpressionContext, evaluate_nested: bool = False) -> Any:
    """Evaluate runtime expression in context."""
    if isinstance(expr, (dict, list)) and evaluate_nested:
        return _evaluate_nested(expr, context)
    if not isinstance(expr, str):
        # Can be a non-string constant
        return expr
    parts = [node.evaluate(context) for node in parser.parse(expr)]
    if len(parts) == 1:
        return parts[0]  # keep the return type the same as the internal value type
    # otherwise, concatenate into a string
    return "".join(str(part) for part in parts if part is not None)


def _evaluate_nested(expr: dict[str, Any] | list, context: ExpressionContext) -> Any:
    if isinstance(expr, dict):
        return {
            _evaluate_object_key(key, context): evaluate(value, context, evaluate_nested=True)
            for key, value in expr.items()
        }
    return [evaluate(item, context, evaluate_nested=True) for item in expr]


def _evaluate_object_key(key: str, context: ExpressionContext) -> Any:
    evaluated = evaluate(key, context)
    if isinstance(evaluated, str):
        return evaluated
    if isinstance(evaluated, bool):
        return "true" if evaluated else "false"
    if isinstance(evaluated, (int, float)):
        return str(evaluated)
    if evaluated is None:
        return "null"
    return json.dumps(evaluated)
