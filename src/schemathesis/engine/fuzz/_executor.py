from __future__ import annotations

import queue
import sys
import threading
import time
import unittest
import uuid
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from warnings import catch_warnings

import requests
from hypothesis.errors import Flaky, FlakyStrategyDefinition, Unsatisfiable, UnsatisfiedAssumption
from hypothesis.strategies import one_of, sampled_from
from urllib3.exceptions import InsecureRequestWarning

from schemathesis.core import NotSet
from schemathesis.core.control import SkipTest
from schemathesis.core.failures import FailureGroup
from schemathesis.core.result import Ok
from schemathesis.core.transport import Response
from schemathesis.engine import Status, events
from schemathesis.engine._baseline import has_new_failures
from schemathesis.engine._check_context import CheckContextCache
from schemathesis.engine._rate_limit_retry import TRANSPORT_ERRORS, send_tracked, validate_case
from schemathesis.engine.control import BudgetExpired
from schemathesis.engine.errors import build_code_sample, is_inconsistent_replay
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import PhaseName, relay_events
from schemathesis.generation import overrides
from schemathesis.generation.hypothesis import examples
from schemathesis.generation.hypothesis.reporting import FILTER_CASE_EXHAUSTED_MESSAGE, build_unsatisfiable_error

if TYPE_CHECKING:
    import hypothesis
    from hypothesis.strategies import DrawFn

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


def _build_strategy_kwargs(ctx: EngineContext, *, operation: APIOperation) -> dict[str, Any]:
    return dict(overrides.for_operation(ctx.config, operation=operation).as_strategy_kwargs())


def _build_strategy_inputs(
    ctx: EngineContext, *, operations: list[APIOperation]
) -> tuple[dict[str, dict[str, Any]], dict[str, FeedbackSources]]:
    strategy_kwargs_by_label = {op.label: _build_strategy_kwargs(ctx, operation=op) for op in operations}
    feedback_by_label = {op.label: ctx.feedback_for(operation=op, phase=PhaseName.FUZZING) for op in operations}
    return strategy_kwargs_by_label, feedback_by_label


def _preflight_operations(
    *,
    operations: list[APIOperation],
    strategy_kwargs_by_label: dict[str, dict[str, Any]],
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
        skip_reason: str | None = None
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
            except (SkipTest, unittest.SkipTest) as exc:
                skip_reason = str(exc)
            except Exception as exc:
                # Real generation errors should exclude the operation entirely.
                last_exc = exc
                viable_modes = []
                break
        if viable_modes:
            active_operations.append(operation)
            active_generation_modes_by_label[operation.label] = viable_modes
        elif last_exc is None and skip_reason is not None:
            events.put_skipped_scenario(
                event_queue, operation.label, PhaseName.FUZZING, uuid.uuid4(), skip_reason=skip_reason
            )
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
    unique_inputs_by_label: dict[str, bool]


def build_fuzz_plan(ctx: EngineContext, *, operations: list[APIOperation]) -> FuzzPlan:
    """Resolve settings, sampling weights and per-operation flags once for all workers."""
    import hypothesis
    from hypothesis import HealthCheck, Phase

    weights_by_label = ctx.schema.compute_fuzz_operation_weights(operations)
    unique_inputs_by_label = {op.label: ctx.config.generation_for(operation=op).unique_inputs for op in operations}
    settings = ctx.config.get_hypothesis_settings(apply_ci_profile=False)
    suppress_health_check = list(settings.suppress_health_check)
    if any(unique_inputs_by_label.values()):
        # Once inputs run out, most scenarios repeat sent ones and are rejected; drawing them is not slow generation.
        suppress_health_check.extend([HealthCheck.filter_too_much, HealthCheck.too_slow])
    return FuzzPlan(
        hypothesis_settings=hypothesis.settings(
            settings,
            max_examples=FUZZ_MAX_EXAMPLES,
            phases=[Phase.generate, Phase.reuse],
            deadline=None,
            suppress_health_check=suppress_health_check,
        ),
        operations=operations,
        weighted_operations=[op for op in operations for _ in range(weights_by_label[op.label])],
        operations_by_label={operation.label: operation for operation in operations},
        continue_on_failure_by_label={op.label: ctx.config.continue_on_failure_for(operation=op) for op in operations},
        unique_inputs_by_label=unique_inputs_by_label,
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
            operation = result.ok()
            if not operation.has_skipped_required_body:
                operations.append(operation)
            for error in operation.schema_errors:
                yield events.NonFatalError(error=error, phase=None, label=operation.label, related_to_operation=True)
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
    for event in relay_events(event_queue, threads, timeout=EVENT_QUEUE_TIMEOUT):
        if isinstance(event, events.FuzzScenarioFinished) and event.status in (Status.FAILURE, Status.ERROR):
            ctx.control.count_failure(event.id)
        yield event


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
    except BaseException:
        # The consumer stopped reading events or failed, so wind workers down instead of fuzzing forever.
        ctx.stop()
        raise
    finally:
        # Workers won't start new scenarios once stopped; they finish their in-flight HTTP call and exit.
        # join() waits for that bounded cleanup - important for Python API callers where the process doesn't exit.
        for thread in threads:
            thread.join()
    outage = ctx.server.take_report(None)
    if outage is not None:
        yield outage


def _report_once(
    exc: Exception,
    *,
    label: str,
    seen_labels: set[str],
    event_queue: queue.Queue[events.EngineEvent],
    code_sample: str | None = None,
) -> None:
    """Report an operation's error the first time this worker sees one for it."""
    if label not in seen_labels:
        seen_labels.add(label)
        event_queue.put(
            events.NonFatalError(error=exc, phase=None, label=label, related_to_operation=True, code_sample=code_sample)
        )


def _draw_operation(
    draw: DrawFn,
    ctx: EngineContext,
    *,
    last_step: tuple[APIOperation, Case, Response] | None,
    weighted_operations: list[APIOperation],
    operations_by_label: dict[str, APIOperation],
    excluded_operations: set[str],
) -> tuple[APIOperation, dict[str, Any]]:
    """Mostly follow a link from the previous step, otherwise pick a weighted-random operation."""
    candidates: list[tuple[APIOperation, dict[str, Any]]] = []
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
    strategy_kwargs: dict[str, Any],
    link_overrides: dict[str, Any],
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
                [
                    operation.as_strategy(generation_mode=mode, feedback=feedback, **merged_kwargs)
                    for mode in generation_modes
                ]
            )
        )
    except UnsatisfiedAssumption:
        # Let Hypothesis handle filtered examples.
        raise
    except SkipTest:
        # Values pinned by a link can leave nothing to negate; the operation still runs in other steps.
        excluded_operations.add(operation.label)
        return None
    except Exception as exc:
        excluded_operations.add(operation.label)
        _report_once(exc, label=operation.label, seen_labels=seen_error_labels, event_queue=event_queue)
        return None


# Workers share the record of sent inputs, so checking and claiming an input must not interleave.
_SENT_INPUTS_LOCK = threading.Lock()


def _claim_input(ctx: EngineContext, case: Case) -> bool:
    """Claim an input for sending; `False` when it was already sent in this run."""
    # Hooks may change the case before it is sent; it is identified by what was generated.
    key = hash(case)
    with _SENT_INPUTS_LOCK:
        if not isinstance(ctx.get_cached_outcome(key), NotSet):
            return False
        ctx.cache_outcome(key, None)
    return True


def _send_step(
    ctx: EngineContext,
    case: Case,
    *,
    operation: APIOperation,
    recorder: ScenarioRecorder,
    event_queue: queue.Queue[events.EngineEvent],
    seen_error_labels: set[str],
) -> tuple[list[Response], Response] | None:
    """Send the step's request, waiting out rate limits; `None` when a transport error skips the step.

    Returns (rate_limited_responses, final_response).
    """
    transport_kwargs = ctx.get_transport_kwargs(operation=operation)
    try:
        return send_tracked(ctx, case, transport_kwargs=transport_kwargs, emit=event_queue.put)
    except BudgetExpired:
        # Cut off by the time limit, the request says nothing about the operation.
        recorder.forget_case(case_id=case.id)
        return None
    except TRANSPORT_ERRORS as exc:
        outage = isinstance(exc, requests.ConnectionError) and ctx.detect_server_outage(exc)
        if outage and ctx.server.is_after_outage(exc):
            # The outage stops the run, but the steps the server answered before it went away still get checked.
            return None
        recorder.record_network_error(case_id=case.id, error=exc)
        _report_once(
            exc,
            label=operation.label,
            seen_labels=seen_error_labels,
            event_queue=event_queue,
            code_sample=build_code_sample(case, exc.request, transport_kwargs),
        )
        return None


def _validate_scenario(
    ctx: EngineContext,
    recorder: ScenarioRecorder,
    *,
    rate_limited_by_case: dict[str, list[Response]],
    check_context_cache: CheckContextCache,
    continue_on_failure_by_label: dict[str, bool],
) -> Status:
    """Run all checks against every answered step; raises the first failure unless it may continue."""
    # Every step is already sent, so a failure only ends the run after the remaining responses are checked.
    first_failure: FailureGroup | None = None
    # Checks record their probe requests into the same scenario.
    for case_id, node in list(recorder.cases.items()):
        interaction = recorder.interactions.get(case_id)
        if interaction is None or interaction.response is None:
            continue
        case = node.value
        check_ctx = check_context_cache.get_or_create(operation=case.operation, ctx=ctx, phase=None).to_check_context(
            ctx, recorder=recorder, phase=None
        )
        try:
            validate_case(
                ctx,
                recorder,
                case=case,
                check_ctx=check_ctx,
                rate_limited=rate_limited_by_case.get(case_id, []),
                response=interaction.response,
                continue_on_failure=continue_on_failure_by_label[case.operation.label],
            )
        except FailureGroup as exc:
            first_failure = first_failure or exc
    if first_failure is not None:
        raise first_failure
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
    scenario_error: Exception | None,
    rejected_duplicates: bool,
    event_queue: queue.Queue[events.EngineEvent],
) -> None:
    if isinstance(exc, (_StopFuzzing, Flaky)) and scenario_error is not None:
        # The replay was stopped or did not raise the error again.
        event_queue.put(_suite_error(scenario_error))
        return
    if isinstance(exc, _StopFuzzing):
        # This worker has no work left; other workers keep running.
        return
    if isinstance(exc, KeyboardInterrupt):
        ctx.stop()
    elif isinstance(exc, FailureGroup):
        # Failures are already captured; without `continue-on-failure` the first one ends the whole run.
        ctx.control.reach_failure_limit()
    elif isinstance(exc, Flaky) and ctx.has_to_stop:
        # Deadline-induced data-tree noise; campaign already stopping, suppress.
        return
    elif isinstance(exc, Unsatisfiable) and rejected_duplicates:
        # Every input this worker drew was already sent, e.g. by other workers.
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
    strategy_kwargs_by_label: dict[str, dict[str, Any]],
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
    # Hypothesis reports an error only when replaying its scenario raises it again, which the time limit can prevent.
    scenario_errors: list[Exception] = []
    # Set once a scenario is rejected because every input in it was already sent.
    rejected_duplicates = False

    @st.composite
    def scheduler(draw: hypothesis.strategies.DrawFn) -> tuple[ScenarioRecorder, dict[str, list[Response]]]:
        """Compose a scenario: weighted-random producers, then link-biased follow-ups."""
        nonlocal rejected_duplicates
        if _must_stop_worker(ctx, weighted_operations=weighted_operations, scenario_started=scenario_started):
            raise _StopFuzzing

        # Capture timing before any draws so elapsed_time covers HTTP calls made in the strategy.
        scenario_started_at = time.monotonic()
        recorder = ScenarioRecorder(label=FUZZ_TESTS_LABEL, config=ctx.config.output)
        # 429s each step waited out before its final response; they get the same checks.
        rate_limited_by_case: dict[str, list[Response]] = {}
        excluded_operations: set[str] = set()

        last_step: tuple[APIOperation, Case, Response] | None = None
        skipped_duplicate = False

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
            if plan.unique_inputs_by_label[operation.label] and not _claim_input(ctx, case):
                # A failure is reported once, with the scenario that first sent the input.
                skipped_duplicate = True
                continue
            recorder.record_case(case=case)
            sent = _send_step(
                ctx,
                case,
                operation=operation,
                recorder=recorder,
                event_queue=event_queue,
                seen_error_labels=seen_error_labels,
            )
            if sent is None:
                continue
            rate_limited, response = sent
            if rate_limited:
                rate_limited_by_case[case.id] = rate_limited
            recorder.record_response(case_id=case.id, response=response)
            last_step = (operation, case, response)

        if skipped_duplicate and not recorder.cases:
            # Rejecting lets Hypothesis end the session once scenarios rarely find an input not sent yet.
            rejected_duplicates = True
            hypothesis.reject()
        # A draw Hypothesis rejects must not spend the one scenario allowed past the time limit.
        scenario_started.set()
        _start_scenario(
            scenario_cell,
            started_at=scenario_started_at,
            suite_id=suite_id,
            worker_id=worker_id,
            event_queue=event_queue,
        )
        return recorder, rate_limited_by_case

    # Any seed takes precedence over `derandomize`, so deterministic mode skips a generated one.
    seed = ctx.config.seed if not hypothesis_settings.derandomize or ctx.config.has_explicit_seed else None

    def fuzz_test(scenario: tuple[ScenarioRecorder, dict[str, list[Response]]]) -> None:
        """Validate all responses in the drawn scenario and emit FuzzScenarioFinished."""
        recorder, rate_limited_by_case = scenario
        active = scenario_cell.value
        assert active is not None
        status = Status.SUCCESS
        try:
            status = _validate_scenario(
                ctx,
                recorder,
                rate_limited_by_case=rate_limited_by_case,
                check_context_cache=check_context_cache,
                continue_on_failure_by_label=continue_on_failure_by_label,
            )
        except (FailureGroup, Exception, KeyboardInterrupt) as exc:
            status = _status_for(exc)
            if isinstance(exc, Exception):
                scenario_errors.append(exc)
            raise
        finally:
            _finish_scenario(
                active, recorder, status=status, suite_id=suite_id, worker_id=worker_id, event_queue=event_queue
            )

    while True:
        # Settings go on top: seeding resets the example database, and the configured one must win.
        session = hypothesis.settings(hypothesis_settings)(
            hypothesis.seed(seed)(hypothesis.given(scheduler())(fuzz_test))
        )
        try:
            with catch_warnings(), ignore_hypothesis_output():
                warnings.filterwarnings("ignore", category=InsecureRequestWarning)
                session()
        except (FailureGroup, Exception, KeyboardInterrupt) as exc:
            if (isinstance(exc, FlakyStrategyDefinition) or is_inconsistent_replay(exc)) and not ctx.has_to_stop:
                # Server state changed what a replayed scenario drew; keep fuzzing in a fresh session.
                if seed is not None:
                    seed += 1
                continue
            _handle_session_end(
                ctx,
                exc,
                operations=plan.operations,
                scenario_error=scenario_errors[0] if scenario_errors else None,
                rejected_duplicates=rejected_duplicates,
                event_queue=event_queue,
            )
        return
