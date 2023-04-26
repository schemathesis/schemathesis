from dataclasses import dataclass

from ....models import Case
from ....utils import GenericResponse


@dataclass
class ExpressionContext:
    """Context in what an expression are evaluated."""

    response: GenericResponse
    case: Case
