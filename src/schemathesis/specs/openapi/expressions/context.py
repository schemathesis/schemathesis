import attr

from ....models import Case
from ....utils import GenericResponse


@attr.s(slots=True)  # pragma: no mutate
class ExpressionContext:
    """Context in what an expression are evaluated."""

    response: GenericResponse = attr.ib()  # pragma: no mutate
    case: Case = attr.ib()  # pragma: no mutate
