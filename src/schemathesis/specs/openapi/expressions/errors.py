class RuntimeExpressionError(ValueError):
    """Error while evaluating a runtime expression."""


class UnknownToken(RuntimeExpressionError):
    """Token value the evaluator cannot handle."""
