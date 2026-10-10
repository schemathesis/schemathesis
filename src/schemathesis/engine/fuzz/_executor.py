from __future__ import annotations

import queue
import sys
import threading
import time
import uuid
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING
from warnings import catch_warnings

import requests
from hypothesis.errors import Flaky, Unsatisfiable, UnsatisfiedAssumption
from hypothesis.strategies import one_of, sampled_from
from requests.exceptions import ChunkedEncodingError
from urllib3.exceptions import InsecureRequestWarning

from schemathesis.core.failures import FailureGroup
from schemathesis.core.result import Ok
from schemathesis.core.transport import Response
from schemathesis.engine import Status, events
from schemathesis.engine._baseline import has_new_failures
from schemathesis.engine._check_context import CheckContextCache
from schemathesis.engine._rate_limit_retry import call_with_retry
from schemathesis.engine._validate import validate_response
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import PhaseName
from schemathesis.generation import overrides
from schemathesis.generation.hypothesis import examples
from schemathesis.generation.hypothesis.reporting import FILTER_CASE_EXHAUSTED_MESSAGE, build_unsatisfiable_error

if TYPE_CHECKING:
    import hypothesis
    from hypothesis.strategies import DrawFn

    from schemathesis.checks import ResponseChecks
    from schemathesis.config import FuzzConfig
    from schemathesis.core.transport import Response
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EventGenerator
    from schemathesis.generation.case import Case
    from schemathesis.generation.feedback import FeedbackSources
    from schemathesis.schemas import APIOperation

FUZZ_TESTS_LABEL = "Fuzz tests"
EVENT_QUEUE_TIMEOUT = 0.01


class _StopFuzzing(KeyboardInterrupt):
    """Raised inside the scheduler to stop a thread's Hypothesis run without signaling other workers.

    Inherits from KeyboardInterrupt so Hypothesis treats it as immediate abort (no shrinking/replay),
    but is caught before the generic KeyboardInterrupt handler to avoid calling ctx.stop().
    """


FUZZ_MAX_EXAMPLES = sys.maxsize
MAX_SCENARIO_STEPS = 6
# link-vs-random bias; Hypothesis prefers index 0 on shrinking, so True is the canonical step.
_LINK_BIAS_CHOICES = [True] * 8 + [False] * 2


def _build_strategy_kwargs(ctx: EngineContext, *, operation: APIOperation) -> dict[str, object]:
    override = overrides.for_operation(ctx.config, operation=operation)
    # `body` is not part of the parameter override system and is never populated by `for_operation`.
    return {
        loc: getattr(override, loc)
        for loc in ("query", "headers", "cookies", "path_parameters")
        if getattr(override, loc)
    }


def _build_strategy_inputs(
    ctx: EngineContext, *, operations: list[APIOperation]
) -> tuple[dict[str, dict[str, object]], dict[str, FeedbackSources]]:
    strategy_kwargs_by_label = {op.label: _build_strategy_kwargs(ctx, operation=op) for op in operations}
    feedback_by_label = {op.label: ctx.feedback_for(operation=op, phase=PhaseName.FUZZING) for op in operations}
    return strategy_kwargs_by_label, feedback_by_label


def _preflight_operations(
    *,
    operations: list[APIOperation],
    strategy_kwargs_by_label: dict[str, dict[str, object]],
    feedback_by_label: dict[str, FeedbackSources],
    generation_modes_by_label: dict[str, list],
    event_queue: queue.Queue[events.EngineEvent],
) -> tuple[list[APIOperation], dict[str, list]]:
    """Return active operations and only the generation modes that can produce cases."""
    from hypothesis.errors import Unsatisfiable

    active_operations = []
    active_generation_modes_by_label = {}
    for operation in operations:
        last_exc: Exception | None = None
        viable_modes = []
        for mode in generation_modes_by_label[operation.label]:
            try:
                examples.generate_one(
                    operation.as_strategy(
                        generation_mode=mode,
                        feedback=feedback_by_label[operation.label],
                        **strategy_kwargs_by_label[operation.label],
                    )
                )
                viable_modes.append(mode)
            except Unsatisfiable:
                # Schema constraints make this mode impossible — try the next mode.
                last_exc = build_unsatisfiable_error(
                    operation, with_tip=False, filter_tracker=operation.filter_case_tracker
                )
            except Exception as exc:
                # Real generation errors should exclude the operation entirely.
                last_exc = exc
                viable_modes = []
                break
        if viable_modes:
            active_operations.append(operation)
            active_generation_modes_by_label[operation.label] = viable_modes
        elif last_exc is not None:
            event_queue.put(
                events.NonFatalError(
                    error=last_exc,
                    phase=None,
                    label=operation.label,
                    related_to_operation=True,
                )
            )
    return active_operations, active_generation_modes_by_label


@dataclass(frozen=True, slots=True)
class FuzzPlan:
    """Per-run scheduling data shared by every fuzz worker."""

    hypothesis_settings: hypothesis.settings
    operations: list[APIOperation]
    # Each operation repeated by its weight; layer-0 producers appear more often.
    weighted_operations: list[APIOperation]
    operations_by_label: dict[str, APIOperation]
    continue_on_failure_by_label: dict[str, bool]


def build_fuzz_plan(ctx: EngineContext, *, operations: list[APIOperation]) -> FuzzPlan:
    """Resolve settings, sampling weights and per-operation flags once for all workers."""
    import hypothesis
    from hypothesis import Phase

    weights_by_label = ctx.schema.compute_fuzz_operation_weights(operations)
    return FuzzPlan(
        hypothesis_settings=hypothesis.settings(
            ctx.config.get_hypothesis_settings(apply_ci_profile=False),
            max_examples=FUZZ_MAX_EXAMPLES,
            phases=[Phase.generate, Phase.reuse],
            deadline=None,
        ),
        operations=operations,
        weighted_operations=[op for op in operations for _ in range(weights_by_label[op.label])],
        operations_by_label={operation.label: operation for operation in operations},
        continue_on_failure_by_label={
            op.label: bool(
                ctx.config.operations.get_for_operation(operation=op).continue_on_failure
                or ctx.config.continue_on_failure
            )
            for op in operations
        },
    )


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
    from hypothesis.errors import HypothesisWarning

    event_queue: queue.Queue[events.EngineEvent] = queue.Queue()
    operations = []
    for result in ctx.schema.get_all_operations():
        if isinstance(result, Ok):
            if not result.ok().has_skipped_required_body:
                operations.append(result.ok())
            continue
        error = result.err()
        yield events.NonFatalError(
            error=error, phase=None, label=error.label, related_to_operation=bool(error.method and error.path)
        )
    if not operations:
        return

    with catch_warnings():
        warnings.filterwarnings("ignore", category=HypothesisWarning)
        yield from _run_forever(ctx, config, operations=operations, event_queue=event_queue)


def _relay_events(
    ctx: EngineContext, *, event_queue: queue.Queue[events.EngineEvent], threads: list[threading.Thread]
) -> EventGenerator:
    """Yield worker events, counting failed scenarios, until every worker has exited."""
    while True:
        try:
            event = event_queue.get(timeout=EVENT_QUEUE_TIMEOUT)
            if isinstance(event, events.FuzzScenarioFinished) and event.status in (Status.FAILURE, Status.ERROR):
                ctx.control.count_failure(event.id)
            yield event
        except queue.Empty:
            if not any(t.is_alive() for t in threads):
                break


def _run_forever(
    ctx: EngineContext,
    config: FuzzConfig,
    *,
    operations: list[APIOperation],
    event_queue: queue.Queue[events.EngineEvent],
) -> EventGenerator:
    ctx.apply_stateful_inference()
    strategy_kwargs_by_label, feedback_by_label = _build_strategy_inputs(ctx, operations=operations)
    generation_modes_by_label: dict[str, list] = {
        op.label: ctx.config.generation_for(operation=op).modes for op in operations
    }
    active_operations, active_generation_modes_by_label = _preflight_operations(
        operations=operations,
        strategy_kwargs_by_label=strategy_kwargs_by_label,
        feedback_by_label=feedback_by_label,
        generation_modes_by_label=generation_modes_by_label,
        event_queue=event_queue,
    )
    plan = build_fuzz_plan(ctx, operations=active_operations)
    scenario_started = threading.Event()
    threads = [
        threading.Thread(
            target=_run_forever_thread,
            name=f"schemathesis_fuzz_{worker_id}",
            kwargs={
                "ctx": ctx,
                "config": config,
                "event_queue": event_queue,
                "worker_id": worker_id,
                "plan": plan,
                "strategy_kwargs_by_label": strategy_kwargs_by_label,
                "feedback_by_label": feedback_by_label,
                "generation_modes_by_label": active_generation_modes_by_label,
                "scenario_started": scenario_started,
            },
            daemon=True,  # killed when the main thread exits; prevents _thread._shutdown() hang
        )
        for worker_id in range(ctx.config.workers)
        if active_operations
    ]
    for thread in threads:
        thread.start()
    try:
        yield from _relay_events(ctx, event_queue=event_queue, threads=threads)
    except KeyboardInterrupt:
        ctx.stop()
        yield events.Interrupted(phase=None)
    finally:
        # ctx.stop() has been called (either by interrupt or a worker), so threads won't start new
        # scenarios. They'll finish their current in-flight HTTP call and exit. join() waits for
        # that bounded cleanup — important for Python API callers where the process doesn't exit.
        for thread in threads:
            thread.join()
    outage = ctx.server.take_report(None)
    if outage is not None:
        yield outage


def _report_once(
    exc: Exception, *, label: str, seen_labels: set[str], event_queue: queue.Queue[events.EngineEvent]
) -> None:
    """Report an operation's error the first time this worker sees one for it."""
    if label not in seen_labels:
        seen_labels.add(label)
        event_queue.put(events.NonFatalError(error=exc, phase=None, label=label, related_to_operation=True))


def _draw_operation(
    draw: DrawFn,
    ctx: EngineContext,
    *,
    last_step: tuple[APIOperation, Case, Response] | None,
    weighted_operations: list[APIOperation],
    operations_by_label: dict[str, APIOperation],
    excluded_operations: set[str],
) -> tuple[APIOperation, dict[str, object]]:
    """Mostly follow a link from the previous step, otherwise pick a weighted-random operation."""
    candidates: list[tuple[APIOperation, dict[str, object]]] = []
    if last_step is not None:
        previous_operation, previous_case, previous_response = last_step
        candidates = ctx.schema.iter_link_candidates(
            operation=previous_operation,
            case=previous_case,
            response=previous_response,
            operations_by_label=operations_by_label,
            excluded_labels=excluded_operations,
        )
    if candidates and draw(sampled_from(_LINK_BIAS_CHOICES)):
        return draw(sampled_from(candidates))
    return draw(sampled_from(weighted_operations)), {}


def _draw_case(
    draw: DrawFn,
    operation: APIOperation,
    *,
    strategy_kwargs: dict[str, object],
    link_overrides: dict[str, object],
    feedback: FeedbackSources,
    generation_modes: list,
    excluded_operations: set[str],
    seen_error_labels: set[str],
    event_queue: queue.Queue[events.EngineEvent],
) -> Case | None:
    """Draw a case in any viable generation mode; `None` excludes an operation that fails to generate."""
    merged_kwargs = {**strategy_kwargs, **link_overrides}
    try:
        return draw(
            one_of(
                operation.as_strategy(generation_mode=mode, feedback=feedback, **merged_kwargs)
                for mode in generation_modes
            )
        )
    except UnsatisfiedAssumption:
        # Let Hypothesis handle filtered examples normally.
        raise
    except Exception as exc:
        excluded_operations.add(operation.label)
        _report_once(exc, label=operation.label, seen_labels=seen_error_labels, event_queue=event_queue)
        return None


def _send_step(
    ctx: EngineContext,
    case: Case,
    *,
    operation: APIOperation,
    event_queue: queue.Queue[events.EngineEvent],
    seen_error_labels: set[str],
) -> Response | None:
    """Send the step's request, waiting out rate limits; `None` when a transport error skips the step."""
    auto_mode = ctx.config.rate_limit_for(operation=operation) == "auto"
    transport_kwargs = ctx.get_transport_kwargs(operation=operation)

    def call() -> Response:
        return ctx.server.track(case, lambda: case.call(**transport_kwargs), transport_kwargs=transport_kwargs)

    def on_delay(delay: float, retries_left: int) -> None:
        event_queue.put(events.RateLimitRetry(operation=operation.label, delay=delay, retries_left=retries_left))

    try:
        _, response = call_with_retry(call_fn=call, auto_mode=auto_mode, on_delay=on_delay)
    except (requests.Timeout, requests.ConnectionError, ChunkedEncodingError) as exc:
        if isinstance(exc, requests.ConnectionError) and ctx.detect_server_outage(exc):
            raise _StopFuzzing from None
        _report_once(exc, label=operation.label, seen_labels=seen_error_labels, event_queue=event_queue)
        return None
    return response


def _validate_scenario(
    ctx: EngineContext,
    recorder: ScenarioRecorder,
    *,
    response_checks: ResponseChecks,
    check_context_cache: CheckContextCache,
    continue_on_failure_by_label: dict[str, bool],
) -> Status:
    """Run all checks against every answered step; raises on the first failure unless it may continue."""
    for case_id, node in recorder.cases.items():
        interaction = recorder.interactions.get(case_id)
        if interaction is None or interaction.response is None:
            continue
        case = node.value
        check_ctx = check_context_cache.get_or_create(operation=case.operation, ctx=ctx, phase=None).to_check_context(
            recorder=recorder,
            response_checks=response_checks,
            phase=None,
            auth_enforced_operations=ctx.auth_enforced_operations,
        )
        validate_response(
            case=case,
            ctx=check_ctx,
            response=interaction.response,
            continue_on_failure=continue_on_failure_by_label[case.operation.label],
            recorder=recorder,
            baseline=ctx.config.load_baseline(),
        )
    # Failures under `continue_on_failure` were recorded without raising.
    if has_new_failures(recorder, ctx.config.load_baseline()):
        return Status.FAILURE
    return Status.SUCCESS


def _suite_error(exc: Exception) -> events.NonFatalError:
    return events.NonFatalError(error=exc, phase=None, label=FUZZ_TESTS_LABEL, related_to_operation=False)


def _explain_unsatisfiable(exc: Exception, operations: list[APIOperation]) -> Exception:
    """Blame a filter hook when one rejected cases; otherwise keep Hypothesis's own error."""
    rejecting_hook = any(
        operation.filter_case_tracker is not None and operation.filter_case_tracker.rejected > 0
        for operation in operations
    )
    return Unsatisfiable(FILTER_CASE_EXHAUSTED_MESSAGE) if rejecting_hook else exc


def _must_stop_worker(
    ctx: EngineContext, *, weighted_operations: list[APIOperation], scenario_started: threading.Event
) -> bool:
    # A budget too small to survive startup would otherwise buy nothing at all.
    return not weighted_operations or (ctx.has_to_stop and scenario_started.is_set())


def _start_scenario(
    cell: Cell,
    *,
    started_at: float,
    suite_id: uuid.UUID,
    worker_id: int,
    event_queue: queue.Queue[events.EngineEvent],
) -> None:
    """Announce the scenario and hand its identity and start time to the test body."""
    started = events.FuzzScenarioStarted(suite_id=suite_id, worker_id=worker_id)
    event_queue.put(started)
    cell.value = ActiveScenario(scenario_id=started.id, started_at=started_at)


def _status_for(exc: FailureGroup | Exception | KeyboardInterrupt) -> Status:
    # A failure without `continue-on-failure` stops checking remaining steps and stops the campaign.
    if isinstance(exc, FailureGroup):
        return Status.FAILURE
    if isinstance(exc, KeyboardInterrupt):
        return Status.INTERRUPTED
    return Status.ERROR


def _finish_scenario(
    active: ActiveScenario,
    recorder: ScenarioRecorder,
    *,
    status: Status,
    suite_id: uuid.UUID,
    worker_id: int,
    event_queue: queue.Queue[events.EngineEvent],
) -> None:
    event_queue.put(
        events.FuzzScenarioFinished(
            id=active.scenario_id,
            suite_id=suite_id,
            worker_id=worker_id,
            recorder=recorder,
            status=status,
            elapsed_time=time.monotonic() - active.started_at,
        )
    )


def _handle_session_end(
    ctx: EngineContext,
    exc: FailureGroup | Exception | KeyboardInterrupt,
    *,
    operations: list[APIOperation],
    event_queue: queue.Queue[events.EngineEvent],
) -> None:
    """React to whatever ended this worker's Hypothesis session."""
    if isinstance(exc, _StopFuzzing):
        # Natural thread completion: no work left, other workers keep running.
        return
    if isinstance(exc, KeyboardInterrupt):
        ctx.stop()
    elif isinstance(exc, FailureGroup):
        # Failures are already captured; without `continue-on-failure` the first one ends the whole run.
        ctx.control.reach_failure_limit()
    elif isinstance(exc, Flaky) and ctx.has_to_stop:
        # Deadline-induced data-tree noise; campaign already stopping, suppress.
        return
    elif isinstance(exc, Unsatisfiable):
        event_queue.put(_suite_error(_explain_unsatisfiable(exc, operations)))
    else:
        event_queue.put(_suite_error(exc))


def _run_forever_thread(
    ctx: EngineContext,
    config: FuzzConfig,
    event_queue: queue.Queue[events.EngineEvent],
    worker_id: int,
    plan: FuzzPlan,
    strategy_kwargs_by_label: dict[str, dict[str, object]],
    feedback_by_label: dict[str, FeedbackSources],
    generation_modes_by_label: dict[str, list],
    scenario_started: threading.Event,
) -> None:
    import hypothesis
    import hypothesis.strategies as st

    from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output

    if not plan.operations:
        return

    hypothesis_settings = plan.hypothesis_settings
    weighted_operations = plan.weighted_operations
    operations_by_label = plan.operations_by_label
    continue_on_failure_by_label = plan.continue_on_failure_by_label

    suite_id = uuid.uuid4()
    # Used to communicate scenario start time from hypothesis strategy to the test function
    scenario_cell: Cell = Cell(value=None)
    check_context_cache = CheckContextCache()

    # Per-thread dedup: suppress repeated NonFatalError events for the same operation.
    seen_error_labels: set[str] = set()

    @st.composite  # type: ignore[untyped-decorator]
    def scheduler(draw: hypothesis.strategies.DrawFn) -> ScenarioRecorder:
        """Compose a scenario: weighted-random producers, then link-biased follow-ups."""
        if _must_stop_worker(ctx, weighted_operations=weighted_operations, scenario_started=scenario_started):
            raise _StopFuzzing
        scenario_started.set()

        # Capture timing before any draws so elapsed_time covers HTTP calls made in the strategy.
        scenario_started_at = time.monotonic()
        recorder = ScenarioRecorder(label=FUZZ_TESTS_LABEL, config=ctx.config.output)
        excluded_operations: set[str] = set()

        last_step: tuple[APIOperation, Case, Response] | None = None

        for step in range(MAX_SCENARIO_STEPS):
            if step > 0 and ctx.has_to_stop:
                # Outer `except Flaky` swallows any data-tree fallout from this mid-draw break.
                break
            operation, link_overrides = _draw_operation(
                draw,
                ctx,
                last_step=last_step,
                weighted_operations=weighted_operations,
                operations_by_label=operations_by_label,
                excluded_operations=excluded_operations,
            )
            case = _draw_case(
                draw,
                operation,
                strategy_kwargs=strategy_kwargs_by_label[operation.label],
                link_overrides=link_overrides,
                feedback=feedback_by_label[operation.label],
                generation_modes=generation_modes_by_label[operation.label],
                excluded_operations=excluded_operations,
                seen_error_labels=seen_error_labels,
                event_queue=event_queue,
            )
            if case is None:
                continue
            recorder.record_case(
                parent_id=None,
                case=case,
                transition=None,
                is_transition_applied=False,
            )
            response = _send_step(
                ctx, case, operation=operation, event_queue=event_queue, seen_error_labels=seen_error_labels
            )
            if response is None:
                continue
            recorder.record_response(case_id=case.id, response=response)
            last_step = (operation, case, response)

        _start_scenario(
            scenario_cell,
            started_at=scenario_started_at,
            suite_id=suite_id,
            worker_id=worker_id,
            event_queue=event_queue,
        )
        return recorder

    # Any seed takes precedence over `derandomize`, so deterministic mode skips a generated one.
    seed = ctx.config.seed if not hypothesis_settings.derandomize or ctx.config.has_explicit_seed else None

    # Settings go on top: seeding resets the example database, and the configured one must win.
    @hypothesis.settings(hypothesis_settings)  # type: ignore[untyped-decorator]
    @hypothesis.seed(seed)  # type: ignore[untyped-decorator]
    @hypothesis.given(scheduler())  # type: ignore[untyped-decorator]
    def fuzz_test(recorder: ScenarioRecorder) -> None:
        """Validate all responses in the drawn scenario and emit FuzzScenarioFinished."""
        active = scenario_cell.value
        assert active is not None
        status = Status.SUCCESS
        response_checks = ctx.checks.for_responses()
        try:
            status = _validate_scenario(
                ctx,
                recorder,
                response_checks=response_checks,
                check_context_cache=check_context_cache,
                continue_on_failure_by_label=continue_on_failure_by_label,
            )
        except (FailureGroup, Exception, KeyboardInterrupt) as exc:
            status = _status_for(exc)
            raise
        finally:
            _finish_scenario(
                active, recorder, status=status, suite_id=suite_id, worker_id=worker_id, event_queue=event_queue
            )

    try:
        with catch_warnings(), ignore_hypothesis_output():
            warnings.filterwarnings("ignore", category=InsecureRequestWarning)
            fuzz_test()
    except (FailureGroup, Exception, KeyboardInterrupt) as exc:
        _handle_session_end(ctx, exc, operations=plan.operations, event_queue=event_queue)
