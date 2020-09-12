from typing import Callable, Tuple

import attr

from .models import Case
from .utils import GenericResponse


@attr.s(slots=True)  # pragma: no mutate
class TargetContext:
    case: Case = attr.ib()  # pragma: no mutate
    response: GenericResponse = attr.ib()  # pragma: no mutate
    response_time: float = attr.ib()  # pragma: no mutate


def response_time(context: TargetContext) -> float:
    return context.response_time


Target = Callable[[TargetContext], float]
DEFAULT_TARGETS = ()
OPTIONAL_TARGETS = (response_time,)
ALL_TARGETS: Tuple[Target, ...] = DEFAULT_TARGETS + OPTIONAL_TARGETS
