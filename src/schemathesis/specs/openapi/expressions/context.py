from __future__ import annotations

from dataclasses import dataclass

from schemathesis.core.transport import Response
from schemathesis.generation.case import Case


@dataclass
class ExpressionContext:
    """Context in what an expression are evaluated."""

    response: Response
    case: Case
