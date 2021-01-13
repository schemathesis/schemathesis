from typing import TYPE_CHECKING, Callable, Tuple

import attr

from .utils import GenericResponse

if TYPE_CHECKING:
    from .models import Case


@attr.s(slots=True)  # pragma: no mutate
class TargetContext:
    """Context for targeted testing.

    :ivar Case case: Generated example that is being processed.
    :ivar GenericResponse response: API response.
    :ivar float response_time: API response time.
    """

    case: "Case" = attr.ib()  # pragma: no mutate
    response: GenericResponse = attr.ib()  # pragma: no mutate
    response_time: float = attr.ib()  # pragma: no mutate


def response_time(context: TargetContext) -> float:
    return context.response_time


Target = Callable[[TargetContext], float]
DEFAULT_TARGETS = ()
OPTIONAL_TARGETS = (response_time,)
ALL_TARGETS: Tuple[Target, ...] = DEFAULT_TARGETS + OPTIONAL_TARGETS
