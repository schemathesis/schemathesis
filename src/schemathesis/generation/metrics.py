from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from schemathesis.core.registries import Registry
from schemathesis.core.transport import Response, StatusClass
from schemathesis.generation.case import Case


@dataclass(slots=True)
class MetricContext:
    """Context for evaluating a metric on a single test execution.

    Holds the sent `case` and its HTTP `response`; metric functions receive it to compute a numeric score.
    """

    case: Case
    """Generated test case."""
    response: Response
    """The HTTP response returned by the server for this test case."""


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
    """Return 1.0 for successful responses, 0.0 otherwise."""
    return 1.0 if ctx.case.operation.schema.classify_status(ctx.response.status_code) is StatusClass.SUCCESS else 0.0


class MetricCollector:
    __slots__ = ("metrics", "observations")

    def __init__(self, metrics: list[MetricFunction] | None = None) -> None:
        self.metrics = metrics or []
        self.observations: dict[str, list[float]] = {metric.__name__: [] for metric in self.metrics}

    def reset(self) -> None:
        for metric in self.metrics:
            self.observations[metric.__name__].clear()

    def store(self, case: Case, response: Response) -> None:
        ctx = MetricContext(case=case, response=response)
        for metric in self.metrics:
            self.observations[metric.__name__].append(metric(ctx))

    def maximize(self) -> None:
        """Report each metric's summed observations to Hypothesis as a target to maximize."""
        import hypothesis

        for metric in self.metrics:
            value = sum(self.observations[metric.__name__])
            hypothesis.target(value, label=metric.__name__)


def maximize(metrics: Sequence[MetricFunction], case: Case, response: Response) -> None:
    import hypothesis
    from hypothesis.control import currently_in_test_context

    # Coverage runs outside Hypothesis: metrics still run, so buggy ones raise, but `target()` is a no-op there.
    in_test_context = currently_in_test_context()
    ctx = MetricContext(case=case, response=response)
    # Fuzzing leans toward inputs an operation accepts; stateful testing does not, since its steps repeat such inputs.
    if in_test_context:
        hypothesis.target(success_rate(ctx), label=f"{case.operation.label}:{success_rate.__name__}")
    for metric in metrics:
        value = metric(ctx)
        if in_test_context:
            hypothesis.target(value, label=metric.__name__)
