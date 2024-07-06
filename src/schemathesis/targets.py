from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass
class TargetMetricCollector:
    """Collect multiple observations for target metrics."""

    targets: list[Target]
    observations: dict[str, list[int | float]] = field(init=False)

    def __post_init__(self) -> None:
        self.observations = {target.__name__: [] for target in self.targets}

    def reset(self) -> None:
        """Reset all collected observations."""
        for target in self.targets:
            self.observations[target.__name__].clear()

    def store(self, case: Case, response: GenericResponse) -> None:
        """Calculate target metrics & store them."""
        context = TargetContext(case=case, response=response, response_time=response.elapsed.total_seconds())
        for target in self.targets:
            self.observations[target.__name__].append(target(context))

    def maximize(self) -> None:
        """Give feedback to the Hypothesis engine, so it maximizes the aggregated metrics."""
        import hypothesis

        for target in self.targets:
            # Currently aggregation is just a sum
            metric = sum(self.observations[target.__name__])
            hypothesis.target(metric, label=target.__name__)


def register(target: Target) -> Target:
    """Register a new testing target for schemathesis CLI.

    :param target: A function that will be called to calculate a metric passed to ``hypothesis.target``.
    """
    from . import cli

    global ALL_TARGETS

    ALL_TARGETS += (target,)
    cli.TARGETS_TYPE.choices += (target.__name__,)  # type: ignore
    return target
