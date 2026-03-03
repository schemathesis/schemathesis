"""Execution logic for the fuzz command."""

from __future__ import annotations

import signal
import sys
import threading
import time
import uuid
import warnings
from collections.abc import Callable
from types import FrameType
from typing import Any

import click

from schemathesis.cli.commands.fuzz.merged_test import NoValidFuzzOperationsError, build_merged_test
from schemathesis.cli.commands.fuzz.output import FuzzOutputHandler
from schemathesis.cli.commands.fuzz.unguided import run_unguided
from schemathesis.cli.commands.run import executor as run_executor
from schemathesis.cli.commands.run.context import ExecutionContext, GroupedFailures
from schemathesis.cli.commands.run.events import LoadingFinished, LoadingStarted
from schemathesis.cli.commands.run.handlers import display_handler_error
from schemathesis.cli.commands.run.loaders import load_schema
from schemathesis.config import ProjectConfig
from schemathesis.core.errors import LoaderError
from schemathesis.core.failures import Failure
from schemathesis.core.fs import file_exists
from schemathesis.engine import Status, events
from schemathesis.engine.phases import Phase, PhaseName
from schemathesis.engine.phases import analysis as schema_analysis
from schemathesis.engine.recorder import ScenarioRecorder

MISSING_BASE_URL_MESSAGE = "The `--url` option is required when specifying a schema via a file."
USER_CANCEL_EXIT_CODE = 130


def _determine_stop_reason(
    *,
    was_interrupted: bool,
    is_time_limit_reached: bool,
    max_time: float | None,
    max_failures_reached: bool,
    max_failures: int | None,
    fail_fast_triggered: bool,
    input_exhausted: bool,
) -> str | None:
    if was_interrupted:
        return "Fuzzing stopped: interrupted."
    if is_time_limit_reached and max_time is not None:
        return f"Fuzzing stopped: time limit reached ({max_time:g}s)."
    if max_failures_reached and max_failures is not None:
        return f"Fuzzing stopped: max-failures limit reached ({max_failures})."
    if fail_fast_triggered:
        return "Fuzzing stopped: failure detected in fail-fast mode."
    if input_exhausted:
        return "Fuzzing stopped: input space exhausted."
    return None


def _build_handler_params(params: dict[str, Any]) -> dict[str, Any]:
    """Populate fuzz handler params with run option defaults.

    Custom handlers are shared between `st run` and `st fuzz`. Some handlers may
    access run-only options (for example `phases`) by key, so provide defaults for
    all run options to avoid startup crashes in fuzz.
    """
    from schemathesis.cli.commands import run as run_command

    defaults: dict[str, Any] = {}
    for param in run_command.params:
        if isinstance(param, click.Option) and param.name is not None and param.name not in defaults:
            defaults[param.name] = param.default
    defaults.update(params)
    return defaults


def execute(
    *,
    location: str,
    config: ProjectConfig,
    filter_set: dict[str, Any],
    max_time: float | None = None,
    args: list[str] | None = None,
    params: dict[str, Any] | None = None,
) -> None:
    """Load schema, display startup output, and begin fuzzing."""
    run_args = args or []
    run_params = params or {}
    handler_params = _build_handler_params(run_params)
    handlers = run_executor.initialize_handlers(
        config=config,
        args=run_args,
        params=handler_params,
        include_output=False,
    )
    output_handler = FuzzOutputHandler(config=config)
    handlers.append(output_handler)
    exec_ctx = ExecutionContext(config=config)
    dispatch_lock = threading.Lock()

    def dispatch_event(event: events.EngineEvent) -> None:
        with dispatch_lock:
            exec_ctx.on_event(event)
            for handler in handlers:
                try:
                    handler.handle_event(exec_ctx, event)
                except Exception as exc:
                    if not isinstance(exc, click.Abort):
                        display_handler_error(handler, exc)
                    raise

    for handler in handlers:
        handler.start(exec_ctx)

    # Install signal handlers before any output so SIGINT is handled gracefully
    # regardless of which phase we're in (loading, schema analysis, or fuzzing).
    stop_event = threading.Event()
    interrupted_by_signal = threading.Event()
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)

    def _on_sigint(signum: int, frame: FrameType | None) -> None:
        interrupted_by_signal.set()
        stop_event.set()

    def _on_sigterm(signum: int, frame: FrameType | None) -> None:
        interrupted_by_signal.set()
        stop_event.set()

    signal.signal(signal.SIGINT, _on_sigint)
    signal.signal(signal.SIGTERM, _on_sigterm)

    total_start_time = time.monotonic()
    loading_event = LoadingStarted(location=location)
    dispatch_event(loading_event)

    try:
        try:
            schema = load_schema(location=location, config=config)
            schema.filter_set = schema.config.operations.create_filter_set(**filter_set)
            if file_exists(location) and schema.config.base_url is None:
                raise click.UsageError(MISSING_BASE_URL_MESSAGE)
        except KeyboardInterrupt:
            dispatch_event(events.Interrupted(phase=None))
            sys.exit(USER_CANCEL_EXIT_CODE)
        except LoaderError as exc:
            try:
                dispatch_event(events.FatalError(exception=exc))
            except click.Abort:
                pass
            sys.exit(1)

        if max_time is not None:
            exec_ctx.add_initialization_line(f"     Max time:         {max_time:g}s")

        dispatch_event(
            LoadingFinished(
                location=location,
                start_time=loading_event.timestamp,
                base_url=schema.get_base_url(),
                specification=schema.specification,
                statistic=schema.statistic,
                schema=schema.raw_schema,
                config=schema.config,
                base_path=schema.base_path,
                find_operation_by_label=schema.find_operation_by_label,
            )
        )

        schema_analysis_phase = Phase(name=PhaseName.SCHEMA_ANALYSIS, is_supported=True, is_enabled=True)
        dispatch_event(events.PhaseStarted(phase=schema_analysis_phase, payload=None))
        try:
            from schemathesis.engine.context import EngineContext as EngineExecutionContext

            analysis_ctx = EngineExecutionContext(schema=schema, stop_event=threading.Event())
            for event in schema_analysis.execute(analysis_ctx, schema_analysis_phase):
                dispatch_event(event)
        except KeyboardInterrupt:
            dispatch_event(events.Interrupted(phase=PhaseName.SCHEMA_ANALYSIS))
            sys.exit(USER_CANCEL_EXIT_CODE)
        except Exception as exc:
            try:
                dispatch_event(events.FatalError(exception=exc))
            except click.Abort:
                pass
            sys.exit(1)

        if schema.statistic.operations.selected == 0:
            dispatch_event(events.EngineFinished(running_time=time.monotonic() - total_start_time))
            if exec_ctx.exit_code != 0:
                sys.exit(exec_ctx.exit_code)
            sys.exit(0)

        # Create a shared extra_data_source if the fuzzing phase has it enabled.
        # This lets captured responses from one thread be reused as inputs by others.
        extra_data_source = None
        if config.phases.fuzzing.extra_data_sources.is_enabled:
            extra_data_source = schema.create_extra_data_source()

        # Failure collection — deduplicated across all worker threads using Failure.__hash__/__eq__
        seen_failures: set[Failure] = set()
        lock = threading.Lock()
        non_fatal_errors: set[events.NonFatalError] = set()
        phase = Phase(name=PhaseName.FUZZING, is_supported=True, is_enabled=True)
        dispatch_event(events.PhaseStarted(phase=phase, payload=None))
        suite_started = events.SuiteStarted(phase=PhaseName.FUZZING)
        dispatch_event(suite_started)
        suite_id = suite_started.id

        max_failures = config.max_failures
        continue_on_failure = config.continue_on_failure or False

        failure_events = 0
        max_failures_reached = threading.Event()
        fail_fast_triggered = threading.Event()

        def record_failure_event() -> None:
            nonlocal failure_events
            failure_events += 1
            if max_failures is not None and failure_events >= max_failures:
                max_failures_reached.set()
                stop_event.set()

        def _mark_fail_fast_stop() -> None:
            if continue_on_failure:
                return
            fail_fast_triggered.set()
            stop_event.set()

        def on_grouped_failure(label: str, group: GroupedFailures) -> None:
            with lock:
                record_failure_event()
                _mark_fail_fast_stop()
                new_failures = [failure for failure in group.failures if failure not in seen_failures]
                if not new_failures:
                    return
                seen_failures.update(new_failures)

        def on_scenario_started(label: str) -> uuid.UUID:
            event = events.ScenarioStarted(phase=PhaseName.FUZZING, suite_id=suite_id, label=label)
            dispatch_event(event)
            return event.id

        def on_scenario_finished(
            scenario_id: uuid.UUID, label: str, recorder: ScenarioRecorder, status: Status, elapsed_time: float
        ) -> None:
            dispatch_event(
                events.ScenarioFinished(
                    id=scenario_id,
                    phase=PhaseName.FUZZING,
                    suite_id=suite_id,
                    label=label,
                    status=status,
                    recorder=recorder,
                    elapsed_time=elapsed_time,
                    skip_reason=None,
                    is_final=False,
                )
            )

        def on_worker_failure(exc: Exception) -> None:
            event = events.NonFatalError(
                error=exc,
                phase=PhaseName.FUZZING,
                label="Fuzzing worker",
                related_to_operation=False,
            )
            with lock:
                non_fatal_errors.add(event)
                record_failure_event()
                _mark_fail_fast_stop()
            dispatch_event(event)

        def on_non_fatal_error(label: str, error: Exception) -> None:
            event = events.NonFatalError(
                error=error,
                phase=PhaseName.FUZZING,
                label=label,
                related_to_operation=True,
            )
            with lock:
                if event in non_fatal_errors:
                    return
                non_fatal_errors.add(event)
                record_failure_event()
                _mark_fail_fast_stop()
            dispatch_event(event)

        on_invalid_operation = on_non_fatal_error

        time_limit_reached = threading.Event()
        timer = None
        interrupted_in_loop = False
        interrupted_event_dispatched = False
        completed_without_external_stop = False
        try:
            # Build the merged hypothesis test function
            try:
                test_fn = build_merged_test(
                    schema,
                    config=config,
                    extra_data_source=extra_data_source,
                    on_grouped_failure=on_grouped_failure,
                    on_scenario_started=on_scenario_started,
                    on_scenario_finished=on_scenario_finished,
                    on_non_fatal_error=on_non_fatal_error,
                    on_invalid_operation=on_invalid_operation,
                    continue_on_failure=continue_on_failure,
                    stop_event=stop_event,
                )
            except NoValidFuzzOperationsError as exc:
                # If no valid operations are available, surface a non-fatal error and
                # finish with a regular summary output.
                if not non_fatal_errors:
                    on_invalid_operation("Schema", exc)
                elapsed = time.monotonic() - total_start_time
                dispatch_event(events.SuiteFinished(id=suite_started.id, phase=PhaseName.FUZZING, status=Status.ERROR))
                dispatch_event(events.PhaseFinished(phase=phase, status=Status.ERROR, payload=None))
                engine_finished = events.EngineFinished(running_time=elapsed)
                dispatch_event(engine_finished)
                sys.exit(1)

            n_workers = config.workers
            worker_tests: dict[int, Callable[[], None]] = {0: test_fn}
            for worker_id in range(1, n_workers):
                worker_tests[worker_id] = build_merged_test(
                    schema,
                    config=config,
                    extra_data_source=extra_data_source,
                    on_grouped_failure=on_grouped_failure,
                    on_scenario_started=on_scenario_started,
                    on_scenario_finished=on_scenario_finished,
                    on_non_fatal_error=on_non_fatal_error,
                    on_invalid_operation=on_invalid_operation,
                    continue_on_failure=continue_on_failure,
                    stop_event=stop_event,
                    worker_id=worker_id,
                )

            from urllib3.exceptions import InsecureRequestWarning

            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=InsecureRequestWarning)

                if max_time is not None:

                    def _on_time_limit() -> None:
                        time_limit_reached.set()
                        stop_event.set()

                    timer = threading.Timer(max_time, _on_time_limit)
                    timer.daemon = True
                    timer.start()

                start_time = time.monotonic()
                try:
                    run_unguided(
                        n_workers=n_workers,
                        stop_event=stop_event,
                        on_failure=on_worker_failure,
                        continue_on_failure=continue_on_failure,
                        test_fn_factory=lambda worker_id: worker_tests[worker_id],
                    )
                    completed_without_external_stop = not stop_event.is_set()
                except KeyboardInterrupt:
                    interrupted_in_loop = True
                    dispatch_event(events.Interrupted(phase=PhaseName.FUZZING))
                    interrupted_event_dispatched = True
        finally:
            if timer is not None:
                timer.cancel()

        # Only treat the run as interrupted if it didn't already complete naturally.
        # A signal that arrives after all workers finish should not override the
        # "input space exhausted" stop reason.
        if interrupted_by_signal.is_set() and not interrupted_event_dispatched and not completed_without_external_stop:
            dispatch_event(events.Interrupted(phase=PhaseName.FUZZING))

        elapsed = time.monotonic() - start_time

        was_interrupted = (interrupted_in_loop or interrupted_by_signal.is_set()) and not completed_without_external_stop
        is_time_limit_reached = time_limit_reached.is_set()
        stop_reason = _determine_stop_reason(
            was_interrupted=was_interrupted,
            is_time_limit_reached=is_time_limit_reached,
            max_time=max_time,
            max_failures_reached=max_failures_reached.is_set(),
            max_failures=max_failures,
            fail_fast_triggered=fail_fast_triggered.is_set(),
            input_exhausted=completed_without_external_stop,
        )
        if stop_reason is not None:
            exec_ctx.add_summary_line(stop_reason)

        if was_interrupted or is_time_limit_reached:
            phase_status = Status.INTERRUPTED
        elif max_failures_reached.is_set() or fail_fast_triggered.is_set():
            phase_status = Status.FAILURE
        elif non_fatal_errors:
            phase_status = Status.ERROR
        elif exec_ctx.statistic.failures:
            phase_status = Status.FAILURE
        else:
            phase_status = Status.SUCCESS

        dispatch_event(events.SuiteFinished(id=suite_started.id, phase=PhaseName.FUZZING, status=phase_status))
        dispatch_event(events.PhaseFinished(phase=phase, status=phase_status, payload=None))
        engine_finished = events.EngineFinished(running_time=elapsed)
        dispatch_event(engine_finished)

        exit_code = exec_ctx.exit_code
        if exit_code == 0:
            if was_interrupted:
                exit_code = USER_CANCEL_EXIT_CODE
            elif (
                is_time_limit_reached
                or max_failures_reached.is_set()
                or fail_fast_triggered.is_set()
                or exec_ctx.statistic.failures
                or non_fatal_errors
            ):
                exit_code = 1
        if exit_code != 0:
            sys.exit(exit_code)
    except click.Abort:
        # Avoid Click's default "Aborted!" footer to keep output consistent with `st run`.
        sys.exit(1)
    finally:
        signal.signal(signal.SIGINT, original_sigint)
        signal.signal(signal.SIGTERM, original_sigterm)
        for handler in handlers:
            handler.shutdown(exec_ctx)
