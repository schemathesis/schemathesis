from __future__ import annotations

import queue
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING
from warnings import catch_warnings

import requests
from requests.exceptions import ChunkedEncodingError

from schemathesis.checks import CheckContext
from schemathesis.core.failures import FailureGroup
from schemathesis.core.result import Ok
from schemathesis.engine import Status, events
from schemathesis.engine._check_context import CheckContextCache
from schemathesis.engine._validate import validate_response
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.generation import overrides

if TYPE_CHECKING:
    from schemathesis.config import FuzzConfig, ProjectConfig
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EventGenerator
    from schemathesis.schemas import APIOperation, BaseSchema

FUZZ_TESTS_LABEL = "Fuzz tests"
EVENT_QUEUE_TIMEOUT = 0.01
FUZZ_MAX_EXAMPLES = sys.maxsize


def compute_operation_weights(schema: BaseSchema, operations: list[APIOperation]) -> dict[str, int]:
    """Compute sampling weights from the dependency graph.

    Layer-0 operations (producers with no dependencies) get boosted weight proportional
    to their output count. All other operations get weight 1. Falls back to uniform
    weights for non-OpenAPI schemas or schemas with no useful ordering.
    """
    from schemathesis.specs.openapi.schemas import OpenApiSchema

    if not isinstance(schema, OpenApiSchema):
        return {op.label: 1 for op in operations}

    layers = schema.analysis.dependency_layers
    if layers is None:
        return {op.label: 1 for op in operations}

    layer_0_labels = set(layers[0])
    graph = schema.analysis.dependency_graph

    weights = {}
    for op in operations:
        if op.label not in layer_0_labels:
            weights[op.label] = 1
        else:
            node = graph.operations.get(op.label)
            out_degree = len(node.outputs) if node is not None else 0
            weights[op.label] = 2 + out_degree
    return weights


def _build_strategy_kwargs(config: ProjectConfig, *, operation: APIOperation) -> dict[str, object]:
    override = overrides.for_operation(config, operation=operation)
    # `body` is not part of the parameter override system and is never populated by `for_operation`.
    return {
        loc: getattr(override, loc)
        for loc in ("query", "headers", "cookies", "path_parameters")
        if getattr(override, loc)
    }


@dataclass(slots=True)
class ActiveScenario:
    """Metadata about the currently running scenario, shared between the strategy and test body."""

    scenario_id: uuid.UUID
    started_at: float


@dataclass(slots=True)
class Cell:
    """Shared mutable slot between strategy and test body."""

    value: ActiveScenario | None


def run_forever(ctx: EngineContext, config: FuzzConfig) -> EventGenerator:
    """Yield fuzz scenario events produced by background Hypothesis threads until all stop."""
    event_queue: queue.Queue[events.EngineEvent] = queue.Queue()
    threads = [
        threading.Thread(
            target=_run_forever_thread,
            name=f"schemathesis_fuzz_{worker_id}",
            kwargs={"ctx": ctx, "config": config, "event_queue": event_queue, "worker_id": worker_id},
        )
        for worker_id in range(ctx.config.workers)
    ]
    for thread in threads:
        thread.start()
    try:
        while True:
            try:
                event = event_queue.get(timeout=EVENT_QUEUE_TIMEOUT)
                if isinstance(event, events.FuzzScenarioFinished):
                    if event.status in (Status.FAILURE, Status.ERROR):
                        ctx.control.count_failure()
                yield event
            except queue.Empty:
                if not any(t.is_alive() for t in threads):
                    break
    except KeyboardInterrupt:
        ctx.stop()
        yield events.Interrupted(phase=None)
    finally:
        for thread in threads:
            thread.join()


def _run_forever_thread(
    ctx: EngineContext, config: FuzzConfig, event_queue: queue.Queue[events.EngineEvent], worker_id: int
) -> None:
    import hypothesis
    import hypothesis.strategies as st
    from hypothesis import Phase
    from hypothesis.errors import Flaky, Unsatisfiable, UnsatisfiedAssumption

    from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output

    operations = [op.ok() for op in ctx.schema.get_all_operations() if isinstance(op, Ok)]
    if not operations:
        return

    base_settings = ctx.config.get_hypothesis_settings()
    hypothesis_settings = hypothesis.settings(
        base_settings,
        max_examples=FUZZ_MAX_EXAMPLES,
        phases=[Phase.generate, Phase.reuse],
        deadline=None,
    )

    suite_id = uuid.uuid4()
    # Used to communicate current scenario ID from hypothesis strategy to the test function
    scenario_cell: Cell = Cell(value=None)
    check_context_cache = CheckContextCache()
    # Operations excluded after generation errors; updated across scenario invocations.
    excluded_operations: set[str] = set()
    strategy_kwargs_by_label: dict[str, dict] = {
        op.label: _build_strategy_kwargs(ctx.config, operation=op) for op in operations
    }
    continue_on_failure_by_label: dict[str, bool] = {
        op.label: bool(
            ctx.config.operations.get_for_operation(operation=op).continue_on_failure or ctx.config.continue_on_failure
        )
        for op in operations
    }
    # Dependency-weighted sampling pool: each operation repeated by its weight.
    # Layer-0 producers appear more often; consumers and non-OpenAPI ops get weight 1.
    weights_by_label = compute_operation_weights(ctx.schema, operations)
    weighted_operations = [op for op in operations for _ in range(weights_by_label[op.label])]

    @st.composite  # type: ignore[untyped-decorator]
    def scheduler(draw: hypothesis.strategies.DrawFn) -> ScenarioRecorder:
        """API call scheduler.

        Scheduler is deliberately simplistic right now:

         - Prefers producers over consumers
         - Does not use data from responses
        """
        if not any(op.label not in excluded_operations for op in operations):
            # All operations have been excluded due to errors; stop the campaign.
            raise KeyboardInterrupt

        started = events.FuzzScenarioStarted(suite_id=suite_id, worker_id=worker_id)
        event_queue.put(started)
        scenario_cell.value = ActiveScenario(scenario_id=started.id, started_at=time.monotonic())

        recorder = ScenarioRecorder(label=FUZZ_TESTS_LABEL)

        for _ in range(config.max_steps):
            if ctx.has_to_stop:
                raise KeyboardInterrupt
            choices = [op for op in weighted_operations if op.label not in excluded_operations]
            if not choices:
                raise KeyboardInterrupt
            operation = draw(st.sampled_from(choices))
            try:
                case = draw(operation.as_strategy(**strategy_kwargs_by_label[operation.label]))
            except UnsatisfiedAssumption:
                raise  # let Hypothesis handle filtered examples normally
            except Exception as exc:
                excluded_operations.add(operation.label)
                event_queue.put(
                    events.NonFatalError(
                        error=exc,
                        phase=None,
                        label=operation.label,
                        related_to_operation=True,
                    )
                )
                continue
            recorder.record_case(
                parent_id=None,
                case=case,
                transition=None,
                is_transition_applied=False,
            )
            try:
                response = case.call(**ctx.get_transport_kwargs(operation=operation))
            except (requests.Timeout, requests.ConnectionError, ChunkedEncodingError) as exc:
                event_queue.put(
                    events.NonFatalError(
                        error=exc,
                        phase=None,
                        label=operation.label,
                        related_to_operation=True,
                    )
                )
                continue
            recorder.record_response(case_id=case.id, response=response)

        return recorder

    @hypothesis.seed(ctx.config.seed)  # type: ignore[untyped-decorator]
    @hypothesis.settings(hypothesis_settings)  # type: ignore[untyped-decorator]
    @hypothesis.given(scheduler())  # type: ignore[untyped-decorator]
    def fuzz_test(recorder: ScenarioRecorder) -> None:
        """Validate all responses in the drawn scenario and emit FuzzScenarioFinished."""
        active = scenario_cell.value
        assert active is not None
        scenario_id = active.scenario_id
        start_time = active.started_at
        status = Status.SUCCESS
        try:
            # Run all checks against every recorded response in the scenario.
            for case_id, node in recorder.cases.items():
                interaction = recorder.interactions.get(case_id)
                if interaction is None or interaction.response is None:
                    continue
                case = node.value
                cached = check_context_cache.get_or_create(operation=case.operation, ctx=ctx, phase=None)
                check_ctx = CheckContext(
                    override=cached.override,
                    auth=cached.auth,
                    headers=cached.headers,
                    config=cached.config,
                    transport_kwargs=cached.transport_kwargs,
                    recorder=recorder,
                )
                continue_on_failure = continue_on_failure_by_label[case.operation.label]
                validate_response(
                    case=case,
                    ctx=check_ctx,
                    response=interaction.response,
                    continue_on_failure=continue_on_failure,
                    recorder=recorder,
                )
            # If any case used continue_on_failure=True, failures were recorded without raising.
            # Check the recorder to set the correct scenario status.
            if any(node.status == Status.FAILURE for check_nodes in recorder.checks.values() for node in check_nodes):
                status = Status.FAILURE
        except FailureGroup:
            # continue_on_failure=False: stop checking remaining steps and stop the campaign.
            status = Status.FAILURE
            raise
        except KeyboardInterrupt:
            status = Status.INTERRUPTED
            raise
        except Exception:
            status = Status.ERROR
            raise
        finally:
            event_queue.put(
                events.FuzzScenarioFinished(
                    id=scenario_id,
                    suite_id=suite_id,
                    worker_id=worker_id,
                    recorder=recorder,
                    status=status,
                    elapsed_time=time.monotonic() - start_time,
                )
            )

    try:
        with catch_warnings(), ignore_hypothesis_output():
            fuzz_test()
    except KeyboardInterrupt:
        ctx.stop()
    except FailureGroup:
        # Failures are already captured
        pass
    except Flaky as exc:
        event_queue.put(
            events.NonFatalError(
                error=exc,
                phase=None,
                label=FUZZ_TESTS_LABEL,
                related_to_operation=False,
            )
        )
    except Unsatisfiable as exc:
        event_queue.put(
            events.NonFatalError(
                error=exc,
                phase=None,
                label=FUZZ_TESTS_LABEL,
                related_to_operation=False,
            )
        )
    except Exception as exc:
        event_queue.put(
            events.NonFatalError(
                error=exc,
                phase=None,
                label=FUZZ_TESTS_LABEL,
                related_to_operation=False,
            )
        )
