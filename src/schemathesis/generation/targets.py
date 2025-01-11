"""Support for Targeted Property-Based Testing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from schemathesis.core.registries import Registry
from schemathesis.core.transport import Response
from schemathesis.generation.case import Case


@dataclass
class TargetContext:
    case: Case
    response: Response

    __slots__ = ("case", "response")


TargetFunction = Callable[[TargetContext], float]

TARGETS = Registry[TargetFunction]()
target = TARGETS.register


@target
def response_time(ctx: TargetContext) -> float:
    """Response time as a metric to maximize."""
    return ctx.response.elapsed


class TargetMetricCollector:
    """Collect multiple observations for target metrics."""

    __slots__ = ("targets", "observations")

    def __init__(self, targets: list[TargetFunction] | None = None) -> None:
        self.targets = targets or []
        self.observations: dict[str, list[float]] = {target.__name__: [] for target in self.targets}

    def reset(self) -> None:
        """Reset all collected observations."""
        for target in self.targets:
            self.observations[target.__name__].clear()

    def store(self, case: Case, response: Response) -> None:
        """Calculate target metrics & store them."""
        context = TargetContext(case=case, response=response)
        for target in self.targets:
            self.observations[target.__name__].append(target(context))

    def maximize(self) -> None:
        """Give feedback to the Hypothesis engine, so it maximizes the aggregated metrics."""
        import hypothesis

        for target in self.targets:
            # Currently aggregation is just a sum
            metric = sum(self.observations[target.__name__])
            hypothesis.target(metric, label=target.__name__)


def run(targets: Sequence[TargetFunction], case: Case, response: Response) -> None:
    import hypothesis

    context = TargetContext(case=case, response=response)
    for target in targets:
        value = target(context)
        hypothesis.target(value, label=target.__name__)
