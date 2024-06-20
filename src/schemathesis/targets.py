from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .models import Case
    from .transports.responses import GenericResponse


@dataclass
class TargetContext:
    """Context for targeted testing.

    :ivar Case case: Generated example that is being processed.
    :ivar GenericResponse response: API response.
    :ivar float response_time: API response time.
    """

    case: Case
    response: GenericResponse
    response_time: float


def response_time(context: TargetContext) -> float:
    return context.response_time


Target = Callable[[TargetContext], float]
DEFAULT_TARGETS = ()
OPTIONAL_TARGETS = (response_time,)
ALL_TARGETS: tuple[Target, ...] = DEFAULT_TARGETS + OPTIONAL_TARGETS


def register(target: Target) -> Target:
    """Register a new testing target for schemathesis CLI.

    :param target: A function that will be called to calculate a metric passed to ``hypothesis.target``.
    """
    from . import cli

    global ALL_TARGETS

    ALL_TARGETS += (target,)
    cli.TARGETS_TYPE.choices += (target.__name__,)  # type: ignore
    return target
