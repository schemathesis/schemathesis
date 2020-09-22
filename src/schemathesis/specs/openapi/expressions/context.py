import attr

from ....protocols import CaseProtocol
from ....utils import GenericResponse


@attr.s(slots=True)  # pragma: no mutate
class ExpressionContext:
    """Context in what an expression are evaluated."""

    response: GenericResponse = attr.ib()  # pragma: no mutate
    case: CaseProtocol = attr.ib()  # pragma: no mutate
