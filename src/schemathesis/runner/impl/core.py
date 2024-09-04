from __future__ import annotations

import logging
import re
import threading
import time
import unittest
import uuid
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import TracebackType
from typing import TYPE_CHECKING, Any, Callable, Generator, Iterable, List, Literal, cast
from warnings import WarningMessage, catch_warnings

import hypothesis
import requests
from _pytest.logging import LogCaptureHandler, catching_logs
from hypothesis.errors import HypothesisException, InvalidArgument
from hypothesis_jsonschema._canonicalise import HypothesisRefResolutionError
from jsonschema.exceptions import SchemaError as JsonSchemaError
from jsonschema.exceptions import ValidationError
from requests.auth import HTTPDigestAuth
from urllib3.exceptions import InsecureRequestWarning

from ... import experimental, failures, hooks
from ..._compat import MultipleFailures
from ..._hypothesis import (
    get_invalid_example_headers_mark,
    get_invalid_regex_mark,
    get_non_serializable_mark,
    has_unsatisfied_example_mark,
)
from ..._override import CaseOverride
from ...auths import unregister as unregister_auth
from ...checks import _make_max_response_time_failure_message
from ...constants import (
    DEFAULT_STATEFUL_RECURSION_LIMIT,
    RECURSIVE_REFERENCE_ERROR_MESSAGE,
    SERIALIZERS_SUGGESTION_MESSAGE,
    USER_AGENT,
)
from ...exceptions import (
    CheckFailed,
    DeadlineExceeded,
    InternalError,
    InvalidHeadersExample,
    InvalidRegularExpression,
    NonCheckError,
    OperationSchemaError,
    RecursiveReferenceError,
    SerializationNotPossible,
    SkipTest,
    format_exception,
    get_grouped_exception,
    maybe_set_assertion_message,
)
from ...generation import DataGenerationMethod, GenerationConfig
from ...hooks import HookContext, get_all_by_name
from ...internal.datetime import current_datetime
from ...internal.result import Err, Ok, Result
from ...models import APIOperation, Case, Check, CheckFunction, Status, TestResult, TestResultSet
from ...runner import events
from ...schemas import BaseSchema
from ...service import extensions
from ...service.models import AnalysisResult, AnalysisSuccess
from ...specs.openapi import formats
from ...stateful import Feedback, Stateful
from ...stateful import events as stateful_events
from ...stateful import runner as stateful_runner
from ...targets import Target, TargetContext
from ...transports import RequestConfig, RequestsTransport
from ...transports.auth import get_requests_auth, prepare_wsgi_headers
from ...types import RawAuth
from ...utils import capture_hypothesis_output
from .. import probes
from ..serialization import SerializedTestResult

if TYPE_CHECKING:
    from ...service.client import ServiceClient
    from ...transports.responses import GenericResponse, WSGIResponse


def _should_count_towards_stop(event: events.ExecutionEvent) -> bool:
    return isinstance(event, events.AfterExecution) and event.status in (Status.error, Status.failure)


@dataclass
class BaseRunner:
    schema: BaseSchema
    checks: Iterable[CheckFunction]
    max_response_time: int | None
    targets: Iterable[Target]
    hypothesis_settings: hypothesis.settings
    generation_config: GenerationConfig
    probe_config: probes.ProbeConfig
    request_config: RequestConfig = field(default_factory=RequestConfig)
    override: CaseOverride | None = None
    auth: RawAuth | None = None
    auth_type: str | None = None
    headers: dict[str, Any] | None = None
    store_interactions: bool = False
    seed: int | None = None
    exit_first: bool = False
    max_failures: int | None = None
    started_at: str = field(default_factory=current_datetime)
    dry_run: bool = False
    stateful: Stateful | None = None
    stateful_recursion_limit: int = DEFAULT_STATEFUL_RECURSION_LIMIT
    count_operations: bool = True
    count_links: bool = True
    service_client: ServiceClient | None = None
    _failures_counter: int = 0
    _is_stopping_due_to_failure_limit: bool = False

    def execute(self) -> EventStream:
        """Common logic for all runners."""
        event = threading.Event()
        return EventStream(self._generate_events(event), event)

    def _generate_events(self, stop_event: threading.Event) -> Generator[events.ExecutionEvent, None, None]:
        # If auth is explicitly provided, then the global provider is ignored
        if self.auth is not None:
            unregister_auth()
        results = TestResultSet(seed=self.seed)
        start_time = time.monotonic()
        initialized = None
        __probes = None
        __analysis: Result[AnalysisResult, Exception] | None = None

        def _initialize() -> events.Initialized:
            nonlocal initialized
            initialized = events.Initialized.from_schema(
                schema=self.schema,
                count_operations=self.count_operations,
                count_links=self.count_links,
                seed=self.seed,
                start_time=start_time,
            )
            return initialized

        def _finish() -> events.Finished:
            if has_all_not_found(results):
                results.add_warning(ALL_NOT_FOUND_WARNING_MESSAGE)
            return events.Finished.from_results(results=results, running_time=time.monotonic() - start_time)

        def _before_probes() -> events.BeforeProbing:
            return events.BeforeProbing()

        def _run_probes() -> None:
            if not self.dry_run:
                nonlocal __probes

                __probes = run_probes(self.schema, self.probe_config)

        def _after_probes() -> events.AfterProbing:
            _probes = cast(List[probes.ProbeRun], __probes)
            return events.AfterProbing(probes=_probes)

        def _before_analysis() -> events.BeforeAnalysis:
            return events.BeforeAnalysis()

        def _run_analysis() -> None:
            nonlocal __analysis, __probes

            if self.service_client is not None:
                try:
                    _probes = cast(List[probes.ProbeRun], __probes)
                    result = self.service_client.analyze_schema(_probes, self.schema.raw_schema)
                    if isinstance(result, AnalysisSuccess):
                        extensions.apply(result.extensions, self.schema)
                    __analysis = Ok(result)
                except Exception as exc:
                    __analysis = Err(exc)

        def _after_analysis() -> events.AfterAnalysis:
            return events.AfterAnalysis(analysis=__analysis)

        if stop_event.is_set():
            yield _finish()
            return

        for event_factory in (
            _initialize,
            _before_probes,
            _run_probes,
            _after_probes,
            _before_analysis,
            _run_analysis,
            _after_analysis,
        ):
            event = event_factory()
            if event is not None:
                yield event
            if stop_event.is_set():
                yield _finish()
                return

        try:
            warnings.simplefilter("ignore", InsecureRequestWarning)
            if not experimental.STATEFUL_ONLY.is_enabled:
                yield from self._execute(results, stop_event)
            if not self._is_stopping_due_to_failure_limit:
                yield from self._run_stateful_tests(results)
        except KeyboardInterrupt:
            yield events.Interrupted()

        yield _finish()

    def _should_stop(self, event: events.ExecutionEvent) -> bool:
        result = self.__should_stop(event)
        if result:
            self._is_stopping_due_to_failure_limit = True
        return result

    def __should_stop(self, event: events.ExecutionEvent) -> bool:
        if _should_count_towards_stop(event):
            if self.exit_first:
                return True
            if self.max_failures is not None:
                self._failures_counter += 1
                return self._failures_counter >= self.max_failures
        return False

    def _execute(
        self, results: TestResultSet, stop_event: threading.Event
    ) -> Generator[events.ExecutionEvent, None, None]:
        raise NotImplementedError

    def _run_stateful_tests(self, results: TestResultSet) -> Generator[events.ExecutionEvent, None, None]:
        # Run new-style stateful tests
        if self.stateful is not None and experimental.STATEFUL_TEST_RUNNER.is_enabled and self.schema.links_count > 0:
            result = TestResult(
                method="",
                path="",
                verbose_name="Stateful tests",
                seed=self.seed,
                data_generation_method=self.schema.data_generation_methods,
            )
            headers = self.headers or {}
            if isinstance(self.schema.transport, RequestsTransport):
                auth = get_requests_auth(self.auth, self.auth_type)
            else:
                auth = None
                headers = prepare_wsgi_headers(headers, self.auth, self.auth_type)
            config = stateful_runner.StatefulTestRunnerConfig(
                checks=tuple(self.checks),
                headers=headers,
                hypothesis_settings=self.hypothesis_settings,
                exit_first=self.exit_first,
                max_failures=None if self.max_failures is None else self.max_failures - self._failures_counter,
                request=self.request_config,
                auth=auth,
                seed=self.seed,
                override=self.override,
            )
            state_machine = self.schema.as_state_machine()
            runner = state_machine.runner(config=config)
            status = Status.success

            def from_step_status(step_status: stateful_events.StepStatus) -> Status:
                return {
                    stateful_events.StepStatus.SUCCESS: Status.success,
                    stateful_events.StepStatus.FAILURE: Status.failure,
                    stateful_events.StepStatus.ERROR: Status.error,
                    stateful_events.StepStatus.INTERRUPTED: Status.error,
                }[step_status]

            if self.store_interactions:
                if isinstance(state_machine.schema.transport, RequestsTransport):

                    def on_step_finished(event: stateful_events.StepFinished) -> None:
                        if event.response is not None and event.status is not None:
                            response = cast(requests.Response, event.response)
                            result.store_requests_response(
                                status=from_step_status(event.status),
                                case=event.case,
                                response=response,
                                checks=event.checks,
                            )

                else:

                    def on_step_finished(event: stateful_events.StepFinished) -> None:
                        from ...transports.responses import WSGIResponse

                        if event.response is not None and event.status is not None:
                            response = cast(WSGIResponse, event.response)
                            result.store_wsgi_response(
                                status=from_step_status(event.status),
                                case=event.case,
                                response=response,
                                headers=headers,
                                elapsed=response.elapsed.total_seconds(),
                                checks=event.checks,
                            )
            else:

                def on_step_finished(event: stateful_events.StepFinished) -> None:
                    return None

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
            results.append(result)
            yield events.AfterStatefulExecution(
                status=status,
                result=SerializedTestResult.from_test_result(result),
                elapsed_time=cast(float, test_elapsed_time),
                data_generation_method=self.schema.data_generation_methods,
            )

    def _run_tests(
        self,
        maker: Callable,
        template: Callable,
        settings: hypothesis.settings,
        generation_config: GenerationConfig,
        seed: int | None,
        results: TestResultSet,
        recursion_level: int = 0,
        headers: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Generator[events.ExecutionEvent, None, None]:
        """Run tests and recursively run additional tests."""
        if recursion_level > self.stateful_recursion_limit:
            return

        def as_strategy_kwargs(_operation: APIOperation) -> dict[str, Any]:
            kw = {}
            if self.override is not None:
                for location, entry in self.override.for_operation(_operation).items():
                    if entry:
                        kw[location] = entry
            if headers:
                kw["headers"] = {key: value for key, value in headers.items() if key.lower() != "user-agent"}
            return kw

        for result in maker(
            template,
            settings=settings,
            generation_config=generation_config,
            seed=seed,
            as_strategy_kwargs=as_strategy_kwargs,
        ):
            if isinstance(result, Ok):
                operation, test = result.ok()
                if self.stateful is not None and not experimental.STATEFUL_TEST_RUNNER.is_enabled:
                    feedback = Feedback(self.stateful, operation)
                else:
                    feedback = None
                # Track whether `BeforeExecution` was already emitted.
                # Schema error may happen before / after `BeforeExecution`, but it should be emitted only once
                # and the `AfterExecution` event should have the same correlation id as previous `BeforeExecution`
                before_execution_correlation_id = None
                try:
                    for event in run_test(
                        operation,
                        test,
                        results=results,
                        feedback=feedback,
                        recursion_level=recursion_level,
                        data_generation_methods=self.schema.data_generation_methods,
                        headers=headers,
                        **kwargs,
                    ):
                        yield event
                        if isinstance(event, events.BeforeExecution):
                            before_execution_correlation_id = event.correlation_id
                        if isinstance(event, events.Interrupted):
                            return
                    # Additional tests, generated via the `feedback` instance
                    if feedback is not None:
                        yield from self._run_tests(
                            feedback.get_stateful_tests,
                            template,
                            settings=settings,
                            generation_config=generation_config,
                            seed=seed,
                            recursion_level=recursion_level + 1,
                            results=results,
                            headers=headers,
                            **kwargs,
                        )
                except OperationSchemaError as exc:
                    yield from handle_schema_error(
                        exc,
                        results,
                        self.schema.data_generation_methods,
                        recursion_level,
                        before_execution_correlation_id=before_execution_correlation_id,
                    )
            else:
                # Schema errors
                yield from handle_schema_error(
                    result.err(), results, self.schema.data_generation_methods, recursion_level
                )


def run_probes(schema: BaseSchema, config: probes.ProbeConfig) -> list[probes.ProbeRun]:
    """Discover capabilities of the tested app."""
    results = probes.run(schema, config)
    for result in results:
        if isinstance(result.probe, probes.NullByteInHeader) and result.is_failure:
            from ...specs.openapi._hypothesis import HEADER_FORMAT, header_values

            formats.register(HEADER_FORMAT, header_values(blacklist_characters="\n\r\x00"))
    return results


@dataclass
class EventStream:
    """Schemathesis event stream.

    Provides an API to control the execution flow.
    """

    generator: Generator[events.ExecutionEvent, None, None]
    stop_event: threading.Event

    def __next__(self) -> events.ExecutionEvent:
        return next(self.generator)

    def __iter__(self) -> Generator[events.ExecutionEvent, None, None]:
        return self.generator

    def stop(self) -> None:
        """Stop the event stream.

        Its next value will be the last one (Finished).
        """
        self.stop_event.set()

    def finish(self) -> events.ExecutionEvent:
        """Stop the event stream & return the last event."""
        self.stop()
        return next(self)


def handle_schema_error(
    error: OperationSchemaError,
    results: TestResultSet,
    data_generation_methods: Iterable[DataGenerationMethod],
    recursion_level: int,
    *,
    before_execution_correlation_id: str | None = None,
) -> Generator[events.ExecutionEvent, None, None]:
    if error.method is not None:
        assert error.path is not None
        assert error.full_path is not None
        data_generation_methods = list(data_generation_methods)
        method = error.method.upper()
        verbose_name = f"{method} {error.full_path}"
        result = TestResult(
            method=method,
            path=error.full_path,
            verbose_name=verbose_name,
            data_generation_method=data_generation_methods,
        )
        result.add_error(error)
        # It might be already emitted - reuse its correlation id
        if before_execution_correlation_id is not None:
            correlation_id = before_execution_correlation_id
        else:
            correlation_id = uuid.uuid4().hex
            yield events.BeforeExecution(
                method=method,
                path=error.full_path,
                verbose_name=verbose_name,
                relative_path=error.path,
                recursion_level=recursion_level,
                data_generation_method=data_generation_methods,
                correlation_id=correlation_id,
            )
        yield events.AfterExecution(
            method=method,
            path=error.full_path,
            relative_path=error.path,
            verbose_name=verbose_name,
            status=Status.error,
            result=SerializedTestResult.from_test_result(result),
            data_generation_method=data_generation_methods,
            elapsed_time=0.0,
            hypothesis_output=[],
            correlation_id=correlation_id,
        )
        results.append(result)
    else:
        # When there is no `method`, then the schema error may cover multiple operations, and we can't display it in
        # the progress bar
        results.generic_errors.append(error)


def run_test(
    operation: APIOperation,
    test: Callable,
    checks: Iterable[CheckFunction],
    data_generation_methods: Iterable[DataGenerationMethod],
    targets: Iterable[Target],
    results: TestResultSet,
    headers: dict[str, Any] | None,
    recursion_level: int,
    **kwargs: Any,
) -> Generator[events.ExecutionEvent, None, None]:
    """A single test run with all error handling needed."""
    data_generation_methods = list(data_generation_methods)
    result = TestResult(
        method=operation.method.upper(),
        path=operation.full_path,
        verbose_name=operation.verbose_name,
        data_generation_method=data_generation_methods,
    )
    # To simplify connecting `before` and `after` events in external systems
    correlation_id = uuid.uuid4().hex
    yield events.BeforeExecution.from_operation(
        operation=operation,
        recursion_level=recursion_level,
        data_generation_method=data_generation_methods,
        correlation_id=correlation_id,
    )
    hypothesis_output: list[str] = []
    errors: list[Exception] = []
    test_start_time = time.monotonic()
    setup_hypothesis_database_key(test, operation)

    def _on_flaky(exc: Exception) -> Status:
        if isinstance(exc.__cause__, hypothesis.errors.DeadlineExceeded):
            status = Status.error
            result.add_error(DeadlineExceeded.from_exc(exc.__cause__))
        elif (
            hasattr(hypothesis.errors, "FlakyFailure")
            and isinstance(exc, hypothesis.errors.FlakyFailure)
            and any(isinstance(subexc, hypothesis.errors.DeadlineExceeded) for subexc in exc.exceptions)
        ):
            for sub_exc in exc.exceptions:
                if isinstance(sub_exc, hypothesis.errors.DeadlineExceeded):
                    result.add_error(DeadlineExceeded.from_exc(sub_exc))
            status = Status.error
        elif errors:
            status = Status.error
            add_errors(result, errors)
        else:
            status = Status.failure
            result.mark_flaky()
        return status

    try:
        with catch_warnings(record=True) as warnings, capture_hypothesis_output() as hypothesis_output:
            test(
                checks,
                targets,
                result,
                errors=errors,
                headers=headers,
                data_generation_methods=data_generation_methods,
                **kwargs,
            )
        # Test body was not executed at all - Hypothesis did not generate any tests, but there is no error
        if not result.is_executed:
            status = Status.skip
            result.mark_skipped(None)
        else:
            status = Status.success
    except unittest.case.SkipTest as exc:
        # Newer Hypothesis versions raise this exception if no tests were executed
        status = Status.skip
        result.mark_skipped(exc)
    except CheckFailed:
        status = Status.failure
    except NonCheckError:
        # It could be an error in user-defined extensions, network errors or internal Schemathesis errors
        status = Status.error
        result.mark_errored()
        for error in deduplicate_errors(errors):
            result.add_error(error)
    except hypothesis.errors.Flaky as exc:
        status = _on_flaky(exc)
    except MultipleFailures:
        # Schemathesis may detect multiple errors that come from different check results
        # They raise different "grouped" exceptions
        if errors:
            status = Status.error
            add_errors(result, errors)
        else:
            status = Status.failure
    except hypothesis.errors.Unsatisfiable:
        # We need more clear error message here
        status = Status.error
        result.add_error(hypothesis.errors.Unsatisfiable("Failed to generate test cases for this API operation"))
    except KeyboardInterrupt:
        yield events.Interrupted()
        return
    except SkipTest as exc:
        status = Status.skip
        result.mark_skipped(exc)
    except AssertionError as exc:  # May come from `hypothesis-jsonschema` or `hypothesis`
        status = Status.error
        try:
            operation.schema.validate()
            msg = "Unexpected error during testing of this API operation"
            exc_msg = str(exc)
            if exc_msg:
                msg += f": {exc_msg}"
            try:
                raise InternalError(msg) from exc
            except InternalError as exc:
                error = exc
        except ValidationError as exc:
            error = OperationSchemaError.from_jsonschema_error(
                exc,
                path=operation.path,
                method=operation.method,
                full_path=operation.schema.get_full_path(operation.path),
            )
        result.add_error(error)
    except HypothesisRefResolutionError:
        status = Status.error
        result.add_error(RecursiveReferenceError(RECURSIVE_REFERENCE_ERROR_MESSAGE))
    except InvalidArgument as error:
        status = Status.error
        message = get_invalid_regular_expression_message(warnings)
        if message:
            # `hypothesis-jsonschema` emits a warning on invalid regular expression syntax
            result.add_error(InvalidRegularExpression.from_hypothesis_jsonschema_message(message))
        else:
            result.add_error(error)
    except hypothesis.errors.DeadlineExceeded as error:
        status = Status.error
        result.add_error(DeadlineExceeded.from_exc(error))
    except JsonSchemaError as error:
        status = Status.error
        result.add_error(InvalidRegularExpression.from_schema_error(error, from_examples=False))
    except Exception as error:
        status = Status.error
        # Likely a YAML parsing issue. E.g. `00:00:00.00` (without quotes) is parsed as float `0.0`
        if str(error) == "first argument must be string or compiled pattern":
            result.add_error(
                InvalidRegularExpression(
                    "Invalid `pattern` value: expected a string. "
                    "If your schema is in YAML, ensure `pattern` values are quoted",
                    is_valid_type=False,
                )
            )
        else:
            result.add_error(error)
    if has_unsatisfied_example_mark(test):
        status = Status.error
        result.add_error(
            hypothesis.errors.Unsatisfiable("Failed to generate test cases from examples for this API operation")
        )
    non_serializable = get_non_serializable_mark(test)
    if non_serializable is not None and status != Status.error:
        status = Status.error
        media_types = ", ".join(non_serializable.media_types)
        result.add_error(
            SerializationNotPossible(
                "Failed to generate test cases from examples for this API operation because of"
                f" unsupported payload media types: {media_types}\n{SERIALIZERS_SUGGESTION_MESSAGE}",
                media_types=non_serializable.media_types,
            )
        )
    invalid_regex = get_invalid_regex_mark(test)
    if invalid_regex is not None and status != Status.error:
        status = Status.error
        result.add_error(InvalidRegularExpression.from_schema_error(invalid_regex, from_examples=True))
    invalid_headers = get_invalid_example_headers_mark(test)
    if invalid_headers:
        status = Status.error
        result.add_error(InvalidHeadersExample.from_headers(invalid_headers))
    test_elapsed_time = time.monotonic() - test_start_time
    # DEPRECATED: Seed is the same per test run
    # Fetch seed value, hypothesis generates it during test execution
    # It may be `None` if the `derandomize` config option is set to `True`
    result.seed = getattr(test, "_hypothesis_internal_use_seed", None) or getattr(
        test, "_hypothesis_internal_use_generated_seed", None
    )
    results.append(result)
    for status_code in (401, 403):
        if has_too_many_responses_with_status(result, status_code):
            results.add_warning(TOO_MANY_RESPONSES_WARNING_TEMPLATE.format(f"`{operation.verbose_name}`", status_code))
    yield events.AfterExecution.from_result(
        result=result,
        status=status,
        elapsed_time=test_elapsed_time,
        hypothesis_output=hypothesis_output,
        operation=operation,
        data_generation_method=data_generation_methods,
        correlation_id=correlation_id,
    )


TOO_MANY_RESPONSES_WARNING_TEMPLATE = (
    "Most of the responses from {} have a {} status code. Did you specify proper API credentials?"
)
TOO_MANY_RESPONSES_THRESHOLD = 0.9


def has_too_many_responses_with_status(result: TestResult, status_code: int) -> bool:
    # It is faster than creating an intermediate list
    unauthorized_count = 0
    total = 0
    for check in result.checks:
        if check.response is not None:
            if check.response.status_code == status_code:
                unauthorized_count += 1
            total += 1
    if not total:
        return False
    return unauthorized_count / total >= TOO_MANY_RESPONSES_THRESHOLD


ALL_NOT_FOUND_WARNING_MESSAGE = "All API responses have a 404 status code. Did you specify the proper API location?"


def has_all_not_found(results: TestResultSet) -> bool:
    """Check if all responses are 404."""
    has_not_found = False
    for result in results.results:
        for check in result.checks:
            if check.response is not None:
                if check.response.status_code == 404:
                    has_not_found = True
                else:
                    # There are non-404 responses, no reason to check any other response
                    return False
    # Only happens if all responses are 404, or there are no responses at all.
    # In the first case, it returns True, for the latter - False
    return has_not_found


def setup_hypothesis_database_key(test: Callable, operation: APIOperation) -> None:
    """Make Hypothesis use separate database entries for every API operation.

    It increases the effectiveness of the Hypothesis database in the CLI.
    """
    # Hypothesis's function digest depends on the test function signature. To reflect it for the web API case,
    # we use all API operation parameters in the digest.
    extra = operation.verbose_name.encode("utf8")
    for parameter in operation.iter_parameters():
        extra += parameter.serialize(operation).encode("utf8")
    test.hypothesis.inner_test._hypothesis_internal_add_digest = extra  # type: ignore


def get_invalid_regular_expression_message(warnings: list[WarningMessage]) -> str | None:
    for warning in warnings:
        message = str(warning.message)
        if "is not valid syntax for a Python regular expression" in message:
            return message
    return None


MEMORY_ADDRESS_RE = re.compile("0x[0-9a-fA-F]+")
URL_IN_ERROR_MESSAGE_RE = re.compile(r"Max retries exceeded with url: .*? \(Caused by")


def add_errors(result: TestResult, errors: list[Exception]) -> None:
    group_errors(errors)
    for error in deduplicate_errors(errors):
        result.add_error(error)


def group_errors(errors: list[Exception]) -> None:
    """Group errors of the same kind info a single one, avoiding duplicate error messages."""
    serialization_errors = [error for error in errors if isinstance(error, SerializationNotPossible)]
    if len(serialization_errors) > 1:
        errors[:] = [error for error in errors if not isinstance(error, SerializationNotPossible)]
        media_types = sum((entry.media_types for entry in serialization_errors), [])
        errors.append(SerializationNotPossible.from_media_types(*media_types))


def canonicalize_error_message(error: Exception, include_traceback: bool = True) -> str:
    message = format_exception(error, include_traceback)
    # Replace memory addresses with a fixed string
    message = MEMORY_ADDRESS_RE.sub("0xbaaaaaaaaaad", message)
    return URL_IN_ERROR_MESSAGE_RE.sub("", message)


def deduplicate_errors(errors: list[Exception]) -> Generator[Exception, None, None]:
    """Deduplicate errors by their messages + tracebacks."""
    seen = set()
    for error in errors:
        message = canonicalize_error_message(error)
        if message in seen:
            continue
        seen.add(message)
        yield error


def run_checks(
    *,
    case: Case,
    checks: Iterable[CheckFunction],
    check_results: list[Check],
    result: TestResult,
    response: GenericResponse,
    elapsed_time: float,
    max_response_time: int | None = None,
) -> None:
    errors = []

    def add_single_failure(error: AssertionError) -> None:
        msg = maybe_set_assertion_message(error, check_name)
        errors.append(error)
        if isinstance(error, CheckFailed):
            context = error.context
        else:
            context = None
        check_results.append(result.add_failure(check_name, copied_case, response, elapsed_time, msg, context))

    for check in checks:
        check_name = check.__name__
        copied_case = case.partial_deepcopy()
        try:
            skip_check = check(response, copied_case)
            if not skip_check:
                check_result = result.add_success(check_name, copied_case, response, elapsed_time)
                check_results.append(check_result)
        except AssertionError as exc:
            add_single_failure(exc)
        except MultipleFailures as exc:
            for exception in exc.exceptions:
                add_single_failure(exception)

    if max_response_time:
        if elapsed_time > max_response_time:
            message = _make_max_response_time_failure_message(elapsed_time, max_response_time)
            errors.append(AssertionError(message))
            result.add_failure(
                "max_response_time",
                case,
                response,
                elapsed_time,
                message,
                failures.ResponseTimeExceeded(message=message, elapsed=elapsed_time, deadline=max_response_time),
            )
        else:
            result.add_success("max_response_time", case, response, elapsed_time)

    if errors:
        raise get_grouped_exception(case.operation.verbose_name, *errors)(causes=tuple(errors))


def run_targets(targets: Iterable[Callable], context: TargetContext) -> None:
    for target in targets:
        value = target(context)
        hypothesis.target(value, label=target.__name__)


def add_cases(case: Case, response: GenericResponse, test: Callable, *args: Any) -> None:
    context = HookContext(case.operation)
    for case_hook in get_all_by_name("add_case"):
        _case = case_hook(context, case.partial_deepcopy(), response)
        # run additional test if _case is not an empty value
        if _case:
            test(_case, *args)


@dataclass
class ErrorCollector:
    """Collect exceptions that are not related to failed checks.

    Such exceptions may be considered as multiple failures or flakiness by Hypothesis. In both cases, Hypothesis hides
    exception information that, in our case, is helpful for the end-user. It either indicates errors in user-defined
    extensions, network-related errors, or internal Schemathesis errors. In all cases, this information is useful for
    debugging.

    To mitigate this, we gather all exceptions manually via this context manager to avoid interfering with the test
    function signatures, which are used by Hypothesis.
    """

    errors: list[Exception]

    def __enter__(self) -> ErrorCollector:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
    ) -> Literal[False]:
        # Don't do anything special if:
        #   - Tests are successful
        #   - Checks failed
        #   - The testing process is interrupted
        if not exc_type or issubclass(exc_type, CheckFailed) or not issubclass(exc_type, Exception):
            return False
        # These exceptions are needed for control flow on the Hypothesis side. E.g. rejecting unsatisfiable examples
        if isinstance(exc_val, HypothesisException):
            raise
        # Exception value is not `None` and is a subclass of `Exception` at this point
        exc_val = cast(Exception, exc_val)
        self.errors.append(exc_val.with_traceback(exc_tb))
        raise NonCheckError from None


def _force_data_generation_method(values: list[DataGenerationMethod], case: Case) -> None:
    # Set data generation method to the one that actually used
    data_generation_method = cast(DataGenerationMethod, case.data_generation_method)
    values[:] = [data_generation_method]


def network_test(
    case: Case,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    result: TestResult,
    session: requests.Session,
    request_config: RequestConfig,
    store_interactions: bool,
    headers: dict[str, Any] | None,
    feedback: Feedback | None,
    max_response_time: int | None,
    data_generation_methods: list[DataGenerationMethod],
    dry_run: bool,
    errors: list[Exception],
) -> None:
    """A single test body will be executed against the target."""
    with ErrorCollector(errors):
        _force_data_generation_method(data_generation_methods, case)
        result.mark_executed()
        headers = headers or {}
        if "user-agent" not in {header.lower() for header in headers}:
            headers["User-Agent"] = USER_AGENT
        if not dry_run:
            args = (
                checks,
                targets,
                result,
                session,
                request_config,
                store_interactions,
                headers,
                feedback,
                max_response_time,
            )
            response = _network_test(case, *args)
            add_cases(case, response, _network_test, *args)


def _network_test(
    case: Case,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    result: TestResult,
    session: requests.Session,
    request_config: RequestConfig,
    store_interactions: bool,
    headers: dict[str, Any] | None,
    feedback: Feedback | None,
    max_response_time: int | None,
) -> requests.Response:
    check_results: list[Check] = []
    try:
        hook_context = HookContext(operation=case.operation)
        kwargs: dict[str, Any] = {
            "session": session,
            "headers": headers,
            "timeout": request_config.prepared_timeout,
            "verify": request_config.tls_verify,
            "cert": request_config.cert,
        }
        if request_config.proxy is not None:
            kwargs["proxies"] = {"all": request_config.proxy}
        hooks.dispatch("process_call_kwargs", hook_context, case, kwargs)
        response = case.call(**kwargs)
    except CheckFailed as exc:
        check_name = "request_timeout"
        requests_kwargs = RequestsTransport().serialize_case(case, base_url=case.get_full_base_url(), headers=headers)
        request = requests.Request(**requests_kwargs).prepare()
        elapsed = cast(
            float, request_config.prepared_timeout
        )  # It is defined and not empty, since the exception happened
        check_result = result.add_failure(
            check_name, case, None, elapsed, f"Response timed out after {1000 * elapsed:.2f}ms", exc.context, request
        )
        check_results.append(check_result)
        raise exc
    context = TargetContext(case=case, response=response, response_time=response.elapsed.total_seconds())
    run_targets(targets, context)
    status = Status.success
    try:
        run_checks(
            case=case,
            checks=checks,
            check_results=check_results,
            result=result,
            response=response,
            elapsed_time=context.response_time * 1000,
            max_response_time=max_response_time,
        )
    except CheckFailed:
        status = Status.failure
        raise
    finally:
        if feedback is not None:
            feedback.add_test_case(case, response)
        if store_interactions:
            result.store_requests_response(case, response, status, check_results)
    return response


@contextmanager
def get_session(auth: HTTPDigestAuth | RawAuth | None = None) -> Generator[requests.Session, None, None]:
    with requests.Session() as session:
        if auth is not None:
            session.auth = auth
        yield session


def wsgi_test(
    case: Case,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    result: TestResult,
    auth: RawAuth | None,
    auth_type: str | None,
    headers: dict[str, Any] | None,
    store_interactions: bool,
    feedback: Feedback | None,
    max_response_time: int | None,
    data_generation_methods: list[DataGenerationMethod],
    dry_run: bool,
    errors: list[Exception],
) -> None:
    with ErrorCollector(errors):
        _force_data_generation_method(data_generation_methods, case)
        result.mark_executed()
        headers = prepare_wsgi_headers(headers, auth, auth_type)
        if not dry_run:
            args = (
                checks,
                targets,
                result,
                headers,
                store_interactions,
                feedback,
                max_response_time,
            )
            response = _wsgi_test(case, *args)
            add_cases(case, response, _wsgi_test, *args)


def _wsgi_test(
    case: Case,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    result: TestResult,
    headers: dict[str, Any],
    store_interactions: bool,
    feedback: Feedback | None,
    max_response_time: int | None,
) -> WSGIResponse:
    from ...transports.responses import WSGIResponse

    with catching_logs(LogCaptureHandler(), level=logging.DEBUG) as recorded:
        hook_context = HookContext(operation=case.operation)
        kwargs: dict[str, Any] = {"headers": headers}
        hooks.dispatch("process_call_kwargs", hook_context, case, kwargs)
        response = cast(WSGIResponse, case.call(**kwargs))
    context = TargetContext(case=case, response=response, response_time=response.elapsed.total_seconds())
    run_targets(targets, context)
    result.logs.extend(recorded.records)
    status = Status.success
    check_results: list[Check] = []
    try:
        run_checks(
            case=case,
            checks=checks,
            check_results=check_results,
            result=result,
            response=response,
            elapsed_time=context.response_time * 1000,
            max_response_time=max_response_time,
        )
    except CheckFailed:
        status = Status.failure
        raise
    finally:
        if feedback is not None:
            feedback.add_test_case(case, response)
        if store_interactions:
            result.store_wsgi_response(case, response, headers, response.elapsed.total_seconds(), status, check_results)
    return response


def asgi_test(
    case: Case,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    result: TestResult,
    store_interactions: bool,
    headers: dict[str, Any] | None,
    feedback: Feedback | None,
    max_response_time: int | None,
    data_generation_methods: list[DataGenerationMethod],
    dry_run: bool,
    errors: list[Exception],
) -> None:
    """A single test body will be executed against the target."""
    with ErrorCollector(errors):
        _force_data_generation_method(data_generation_methods, case)
        result.mark_executed()
        headers = headers or {}

        if not dry_run:
            args = (
                checks,
                targets,
                result,
                store_interactions,
                headers,
                feedback,
                max_response_time,
            )
            response = _asgi_test(case, *args)
            add_cases(case, response, _asgi_test, *args)


def _asgi_test(
    case: Case,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    result: TestResult,
    store_interactions: bool,
    headers: dict[str, Any] | None,
    feedback: Feedback | None,
    max_response_time: int | None,
) -> requests.Response:
    hook_context = HookContext(operation=case.operation)
    kwargs: dict[str, Any] = {"headers": headers}
    hooks.dispatch("process_call_kwargs", hook_context, case, kwargs)
    response = case.call(**kwargs)
    context = TargetContext(case=case, response=response, response_time=response.elapsed.total_seconds())
    run_targets(targets, context)
    status = Status.success
    check_results: list[Check] = []
    try:
        run_checks(
            case=case,
            checks=checks,
            check_results=check_results,
            result=result,
            response=response,
            elapsed_time=context.response_time * 1000,
            max_response_time=max_response_time,
        )
    except CheckFailed:
        status = Status.failure
        raise
    finally:
        if feedback is not None:
            feedback.add_test_case(case, response)
        if store_interactions:
            result.store_requests_response(case, response, status, check_results)
    return response
