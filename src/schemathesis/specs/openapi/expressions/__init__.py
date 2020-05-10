"""Runtime expressions support.

https://swagger.io/docs/specification/links/#runtime-expressions
"""
from typing import Any

from . import lexer, nodes, parser
from .context import ExpressionContext


def evaluate(expr: Any, context: ExpressionContext) -> str:
    """Evaluate runtime expression in context."""
    if not isinstance(expr, str):
        # Can be a non-string constant
        return expr
    parts = [node.evaluate(context) for node in parser.parse(expr)]
    if len(parts) == 1:
        return parts[0]  # keep the return type the same as the internal value type
    # otherwise concatenate into a string
    return "".join(map(str, parts))
