from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ... import events
from ...models import Status, TestResult

if TYPE_CHECKING:
    from ...context import EngineContext
    from ...events import EventGenerator


def execute(ctx: EngineContext) -> EventGenerator:
    import requests

    from ....stateful import events as stateful_events
    from ....stateful import runner as stateful_runner

    result = TestResult(verbose_name="Stateful tests")
    headers = ctx.config.network.headers or {}
    config = stateful_runner.StatefulTestRunnerConfig(
        checks=ctx.config.execution.checks,
        headers=headers,
        hypothesis_settings=ctx.config.execution.hypothesis_settings,
        max_failures=ctx.control.remaining_failures,
        network=ctx.config.network,
        auth=ctx.config.network.auth,
        seed=ctx.config.execution.seed,
        override=ctx.config.override,
    )
    state_machine = ctx.config.schema.as_state_machine()
    runner = state_machine.runner(config=config)
    status = Status.success

    def from_step_status(step_status: stateful_events.StepStatus) -> Status:
        return {
            stateful_events.StepStatus.SUCCESS: Status.success,
            stateful_events.StepStatus.FAILURE: Status.failure,
            stateful_events.StepStatus.ERROR: Status.error,
            stateful_events.StepStatus.INTERRUPTED: Status.error,
        }[step_status]

    def on_step_finished(event: stateful_events.StepFinished) -> None:
        if event.response is not None and event.status is not None:
            response = cast(requests.Response, event.response)
            result.store_requests_response(
                status=from_step_status(event.status),
                case=event.case,
                response=response,
                checks=event.checks,
                # TODO: reuse engine-wide transport
                session=config.session,
            )

    test_start_time: float | None = None
    test_elapsed_time: float | None = None

    for stateful_event in runner.execute():
        if isinstance(stateful_event, stateful_events.SuiteFinished):
            if stateful_event.failures and status != Status.error:
                status = Status.failure
        elif isinstance(stateful_event, stateful_events.RunStarted):
            test_start_time = stateful_event.timestamp
        elif isinstance(stateful_event, stateful_events.RunFinished):
            test_elapsed_time = stateful_event.timestamp - cast(float, test_start_time)
        elif isinstance(stateful_event, stateful_events.StepFinished):
            result.checks.extend(stateful_event.checks)
            on_step_finished(stateful_event)
        elif isinstance(stateful_event, stateful_events.Errored):
            status = Status.error
            result.add_error(stateful_event.exception)
        yield events.StatefulEvent(data=stateful_event)
    ctx.add_result(result)
    yield events.AfterStatefulExecution(
        status=status,
        result=result,
        elapsed_time=cast(float, test_elapsed_time),
    )
