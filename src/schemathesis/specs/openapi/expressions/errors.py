class RuntimeExpressionError(ValueError):
    """Generic error that happened during evaluation of a runtime expression."""


class UnknownToken(RuntimeExpressionError):
    """Don't know how to handle a token value."""
