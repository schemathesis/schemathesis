"""Support for Targeted Property-Based Testing."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from schemathesis.core.registries import Registry
from schemathesis.core.transport import Response
from schemathesis.generation.case import Case


@dataclass
class MetricContext:
    """Context for evaluating a metric on a single test execution.

    This object bundles together the test `case` that was sent and
    the corresponding HTTP `response`. Metric functions receive an
    instance of `MetricContext` to compute a numeric score.
    """

    case: Case
    """Generated test case."""
    response: Response
    """The HTTP response returned by the server for this test case."""

    __slots__ = ("case", "response")


MetricFunction = Callable[[MetricContext], float]

METRICS = Registry[MetricFunction]()


def metric(func: MetricFunction) -> MetricFunction:
    """Decorator to register a custom metric for targeted property-based testing.

    Example:
        ```python
        import schemathesis

        @schemathesis.metric
        def response_size(ctx: schemathesis.MetricContext) -> float:
            return float(len(ctx.response.content))
        ```

    """
    return METRICS.register(func)


@metric
def response_time(ctx: MetricContext) -> float:
    """Response time as a metric to maximize."""
    return ctx.response.elapsed


def success_rate(ctx: MetricContext) -> float:
    """Return 1.0 for 2xx responses, 0.0 otherwise."""
    return 1.0 if 200 <= ctx.response.status_code < 300 else 0.0


class MetricCollector:
    """Collect multiple observations for metrics."""

    __slots__ = ("metrics", "observations", "success_observations")

    def __init__(self, metrics: list[MetricFunction] | None = None) -> None:
        self.metrics = metrics or []
        self.observations: dict[str, list[float]] = {metric.__name__: [] for metric in self.metrics}
        self.success_observations: dict[str, list[float]] = {}

    def reset(self) -> None:
        """Reset all collected observations."""
        for metric in self.metrics:
            self.observations[metric.__name__].clear()
        self.success_observations.clear()

    def store(self, case: Case, response: Response) -> None:
        """Calculate metrics & store them."""
        ctx = MetricContext(case=case, response=response)
        for metric in self.metrics:
            self.observations[metric.__name__].append(metric(ctx))
        # Track success per operation
        label = case.operation.label
        if label not in self.success_observations:
            self.success_observations[label] = []
        self.success_observations[label].append(success_rate(ctx))

    def maximize(self) -> None:
        """Give feedback to the Hypothesis engine, so it maximizes the aggregated metrics."""
        import hypothesis

        for metric in self.metrics:
            # Currently aggregation is just a sum
            value = sum(self.observations[metric.__name__])
            hypothesis.target(value, label=metric.__name__)
        # Target success per operation
        for label, values in self.success_observations.items():
            hypothesis.target(sum(values), label=f"{label}:{success_rate.__name__}")


def maximize(metrics: Sequence[MetricFunction], case: Case, response: Response) -> None:
    import hypothesis

    ctx = MetricContext(case=case, response=response)
    # Always target 2xx responses per operation
    hypothesis.target(success_rate(ctx), label=f"{case.operation.label}:{success_rate.__name__}")
    for metric in metrics:
        value = metric(ctx)
        hypothesis.target(value, label=metric.__name__)
