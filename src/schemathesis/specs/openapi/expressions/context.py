from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.core.transport import Response

if TYPE_CHECKING:
    from ....models import Case


@dataclass
class ExpressionContext:
    """Context in what an expression are evaluated."""

    response: Response
    case: Case
