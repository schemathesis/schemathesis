from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from ....models import Case
    from ....transports import Response


@dataclass
class ExpressionContext:
    """Context in what an expression are evaluated."""

    response: Response
    case: Case
