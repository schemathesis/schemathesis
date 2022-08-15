# pylint: disable=too-many-statements,too-many-branches
import logging
import threading
import time
import unittest
import uuid
from contextlib import contextmanager
from types import TracebackType
from typing import Any, Callable, Dict, Generator, Iterable, List, Optional, Type, Union, cast
from warnings import WarningMessage, catch_warnings

import attr
import hypothesis
import requests
from _pytest.logging import LogCaptureHandler, catching_logs
from hypothesis.errors import HypothesisException, InvalidArgument
from hypothesis_jsonschema._canonicalise import HypothesisRefResolutionError
from requests.auth import HTTPDigestAuth, _basic_auth_str

from ... import failures, hooks
from ..._compat import MultipleFailures
from ...auth import unregister as unregister_auth
from ...constants import (
    DEFAULT_STATEFUL_RECURSION_LIMIT,
    RECURSIVE_REFERENCE_ERROR_MESSAGE,
    USER_AGENT,
    DataGenerationMethod,
)
from ...exceptions import (
    CheckFailed,
    DeadlineExceeded,
    InvalidRegularExpression,
    InvalidSchema,
    NonCheckError,
    SkipTest,
    get_grouped_exception,
)
from ...hooks import HookContext, get_all_by_name
from ...models import APIOperation, Case, Check, CheckFunction, Status, TestResult, TestResultSet
from ...runner import events
from ...schemas import BaseSchema
from ...stateful import Feedback, Stateful
from ...targets import Target, TargetContext
from ...types import RawAuth, RequestCert
from ...utils import (
    GenericResponse,
    Ok,
    WSGIResponse,
    capture_hypothesis_output,
    copy_response,
    current_datetime,
    format_exception,
    maybe_set_assertion_message,
)
from ..serialization import SerializedTestResult


def _should_count_towards_stop(event: events.ExecutionEvent) -> bool:
    return isinstance(event, events.AfterExecution) and event.status in (Status.error, Status.failure)


@attr.s  # pragma: no mutate
class BaseRunner:
    schema: BaseSchema = attr.ib()  # pragma: no mutate
    checks: Iterable[CheckFunction] = attr.ib()  # pragma: no mutate
    max_response_time: Optional[int] = attr.ib()  # pragma: no mutate
    targets: Iterable[Target] = attr.ib()  # pragma: no mutate
    hypothesis_settings: hypothesis.settings = attr.ib()  # pragma: no mutate
    auth: Optional[RawAuth] = attr.ib(default=None)  # pragma: no mutate
    auth_type: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    headers: Optional[Dict[str, Any]] = attr.ib(default=None)  # pragma: no mutate
    request_timeout: Optional[int] = attr.ib(default=None)  # pragma: no mutate
    store_interactions: bool = attr.ib(default=False)  # pragma: no mutate
    seed: Optional[int] = attr.ib(default=None)  # pragma: no mutate
    exit_first: bool = attr.ib(default=False)  # pragma: no mutate
    max_failures: Optional[int] = attr.ib(default=None)  # pragma: no mutate
    started_at: str = attr.ib(factory=current_datetime)  # pragma: no mutate
    dry_run: bool = attr.ib(default=False)  # pragma: no mutate
    stateful: Optional[Stateful] = attr.ib(default=None)  # pragma: no mutate
    stateful_recursion_limit: int = attr.ib(default=DEFAULT_STATEFUL_RECURSION_LIMIT)  # pragma: no mutate
    count_operations: bool = attr.ib(default=True)  # pragma: no mutate
    _failures_counter: int = attr.ib(default=0)

    def execute(self) -> "EventStream":
        """Common logic for all runners."""
        event = threading.Event()
        return EventStream(self._generate_events(event), event)

    def _generate_events(self, stop_event: threading.Event) -> Generator[events.ExecutionEvent, None, None]:
        # If auth is explicitly provided, then the global provider is ignored
        if self.auth is not None:
            unregister_auth()
        results = TestResultSet()

        initialized = events.Initialized.from_schema(schema=self.schema, count_operations=self.count_operations)

        def _finish() -> events.Finished:
            if has_all_not_found(results):
                results.add_warning(ALL_NOT_FOUND_WARNING_MESSAGE)
            return events.Finished.from_results(results=results, running_time=time.monotonic() - initialized.start_time)

        if stop_event.is_set():
            yield _finish()
            return

        yield initialized

        if stop_event.is_set():
            yield _finish()
            return

        try:
            for event in self._execute(results, stop_event):
                yield event
        except KeyboardInterrupt:
            yield events.Interrupted()

        yield _finish()

    def _should_stop(self, event: events.ExecutionEvent) -> bool:
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

    def _run_tests(
        self,
        maker: Callable,
        template: Callable,
        settings: hypothesis.settings,
        seed: Optional[int],
        results: TestResultSet,
        recursion_level: int = 0,
        **kwargs: Any,
    ) -> Generator[events.ExecutionEvent, None, None]:
        """Run tests and recursively run additional tests."""
        if recursion_level > self.stateful_recursion_limit:
            return
        for result in maker(template, settings, seed):
            if isinstance(result, Ok):
                operation, test = result.ok()
                feedback = Feedback(self.stateful, operation)
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
                        **kwargs,
                    ):
                        yield event
                        if isinstance(event, events.BeforeExecution):
                            before_execution_correlation_id = event.correlation_id
                        if isinstance(event, events.Interrupted):
                            return
                    # Additional tests, generated via the `feedback` instance
                    yield from self._run_tests(
                        feedback.get_stateful_tests,
                        template,
                        settings,
                        seed,
                        recursion_level=recursion_level + 1,
                        results=results,
                        **kwargs,
                    )
                except InvalidSchema as exc:
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


@attr.s(slots=True)  # pragma: no mutate
class EventStream:
    """Schemathesis event stream.

    Provides an API to control the execution flow.
    """

    generator: Generator[events.ExecutionEvent, None, None] = attr.ib()  # pragma: no mutate
    stop_event: threading.Event = attr.ib()  # pragma: no mutate

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
    error: InvalidSchema,
    results: TestResultSet,
    data_generation_methods: Iterable[DataGenerationMethod],
    recursion_level: int,
    *,
    before_execution_correlation_id: Optional[str] = None,
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


def run_test(  # pylint: disable=too-many-locals
    operation: APIOperation,
    test: Callable,
    checks: Iterable[CheckFunction],
    data_generation_methods: Iterable[DataGenerationMethod],
    targets: Iterable[Target],
    results: TestResultSet,
    headers: Optional[Dict[str, Any]],
    recursion_level: int,
    **kwargs: Any,
) -> Generator[events.ExecutionEvent, None, None]:
    """A single test run with all error handling needed."""
    data_generation_methods = list(data_generation_methods)
    result = TestResult(
        method=operation.method.upper(),
        path=operation.full_path,
        verbose_name=operation.verbose_name,
        overridden_headers=headers,
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
    hypothesis_output: List[str] = []
    errors: List[Exception] = []
    test_start_time = time.monotonic()
    setup_hypothesis_database_key(test, operation)
    try:
        with catch_warnings(record=True) as warnings, capture_hypothesis_output() as hypothesis_output:
            test(checks, targets, result, errors=errors, headers=headers, **kwargs)
        # Test body was not executed at all - Hypothesis did not generate any tests, but there is no error
        if not result.is_executed:
            status = Status.skip
            result.mark_skipped()
        else:
            status = Status.success
    except unittest.case.SkipTest:
        # Newer Hypothesis versions raise this exception if no tests were executed
        status = Status.skip
        result.mark_skipped()
    except CheckFailed:
        status = Status.failure
    except NonCheckError:
        # It could be an error in user-defined extensions, network errors or internal Schemathesis errors
        status = Status.error
        result.mark_errored()
        for error in deduplicate_errors(errors):
            result.add_error(error)
    except MultipleFailures:
        # Schemathesis may detect multiple errors that come from different check results
        # They raise different "grouped" exceptions
        status = Status.failure
    except hypothesis.errors.Flaky:
        status = Status.failure
        result.mark_flaky()
    except hypothesis.errors.Unsatisfiable:
        # We need more clear error message here
        status = Status.error
        result.add_error(hypothesis.errors.Unsatisfiable("Unable to satisfy schema parameters for this API operation"))
    except KeyboardInterrupt:
        yield events.Interrupted()
        return
    except SkipTest:
        status = Status.skip
        result.mark_skipped()
    except AssertionError as exc:  # comes from `hypothesis-jsonschema`
        error = reraise(exc)
        status = Status.error
        result.add_error(error)
    except HypothesisRefResolutionError:
        status = Status.error
        result.add_error(hypothesis.errors.Unsatisfiable(RECURSIVE_REFERENCE_ERROR_MESSAGE))
    except InvalidArgument as error:
        status = Status.error
        message = get_invalid_regular_expression_message(warnings)
        if message:
            # `hypothesis-jsonschema` emits a warning on invalid regular expression syntax
            result.add_error(InvalidRegularExpression(message))
        else:
            result.add_error(error)
    except hypothesis.errors.DeadlineExceeded as error:
        status = Status.error
        result.add_error(DeadlineExceeded.from_exc(error))
    except Exception as error:
        status = Status.error
        result.add_error(error)
    test_elapsed_time = time.monotonic() - test_start_time
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
    # Only happens if all responses are 404, ot there are no responses at all.
    # In the first case, it returns True, for the latter - False
    return has_not_found


def setup_hypothesis_database_key(test: Callable, operation: APIOperation) -> None:
    """Make Hypothesis use separate database entries for every API operation.

    It increases the effectiveness of the Hypothesis database in the CLI.
    """
    # Hypothesis's function digest depends on the test function signature. To reflect it for the web API case,
    # we use all API operation parameters in the digest.
    extra = operation.verbose_name.encode("utf8")
    for parameter in operation.definition.parameters:
        extra += parameter.serialize(operation).encode("utf8")
    test.hypothesis.inner_test._hypothesis_internal_add_digest = extra  # type: ignore


def get_invalid_regular_expression_message(warnings: List[WarningMessage]) -> Optional[str]:
    for warning in warnings:
        message = str(warning.message)
        if "is not valid syntax for a Python regular expression" in message:
            return message
    return None


def reraise(error: AssertionError) -> InvalidSchema:
    traceback = format_exception(error, True)
    if "assert type_ in TYPE_STRINGS" in traceback:
        message = "Invalid type name"
    else:
        message = "Unknown schema error"
    try:
        raise InvalidSchema(message) from error
    except InvalidSchema as exc:
        return exc


def deduplicate_errors(errors: List[Exception]) -> Generator[Exception, None, None]:
    """Deduplicate errors by their messages + tracebacks."""
    seen = set()
    for error in errors:
        message = format_exception(error, True)
        if message in seen:
            continue
        seen.add(message)
        yield error


def run_checks(
    case: Case,
    checks: Iterable[CheckFunction],
    check_results: List[Check],
    result: TestResult,
    response: GenericResponse,
    elapsed_time: float,
    max_response_time: Optional[int] = None,
) -> None:
    errors = []

    for check in checks:
        check_name = check.__name__
        copied_case = case.partial_deepcopy()
        copied_response = copy_response(response)
        try:
            skip_check = check(copied_response, copied_case)
            if not skip_check:
                check_result = result.add_success(check_name, copied_case, copied_response, elapsed_time)
                check_results.append(check_result)
        except AssertionError as exc:
            message = maybe_set_assertion_message(exc, check_name)
            errors.append(exc)
            if isinstance(exc, CheckFailed):
                context = exc.context
            else:
                context = None
            check_result = result.add_failure(check_name, copied_case, copied_response, elapsed_time, message, context)
            check_results.append(check_result)

    if max_response_time:
        if elapsed_time > max_response_time:
            message = f"Response time exceeded the limit of {max_response_time} ms"
            errors.append(AssertionError(message))
            result.add_failure(
                "max_response_time",
                case,
                response,
                elapsed_time,
                message,
                failures.ResponseTimeExceeded(elapsed=elapsed_time, deadline=max_response_time),
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


@attr.s(slots=True)  # pragma: no mutate
class ErrorCollector:
    """Collect exceptions that are not related to failed checks.

    Such exceptions may be considered as multiple failures or flakiness by Hypothesis. In both cases, Hypothesis hides
    exception information that, in our case, is helpful for the end-user. It either indicates errors in user-defined
    extensions, network-related errors, or internal Schemathesis errors. In all cases, this information is useful for
    debugging.

    To mitigate this, we gather all exceptions manually via this context manager to avoid interfering with the test
    function signatures, which are used by Hypothesis.
    """

    errors: List[Exception] = attr.ib()  # pragma: no mutate

    def __enter__(self) -> "ErrorCollector":
        return self

    # Typing: The return type suggested by mypy is `Literal[False]`, but I don't want to introduce dependency on the
    # `typing_extensions` package for Python 3.7
    def __exit__(
        self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException], exc_tb: Optional[TracebackType]
    ) -> Any:
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


def network_test(
    case: Case,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    result: TestResult,
    session: requests.Session,
    request_timeout: Optional[int],
    request_tls_verify: bool,
    request_cert: Optional[RequestCert],
    store_interactions: bool,
    headers: Optional[Dict[str, Any]],
    feedback: Feedback,
    max_response_time: Optional[int],
    dry_run: bool,
    errors: List[Exception],
) -> None:
    """A single test body will be executed against the target."""
    with ErrorCollector(errors):
        result.mark_executed()
        headers = headers or {}
        if "user-agent" not in {header.lower() for header in headers}:
            headers["User-Agent"] = USER_AGENT
        timeout = prepare_timeout(request_timeout)
        if not dry_run:
            response = _network_test(
                case,
                checks,
                targets,
                result,
                session,
                timeout,
                store_interactions,
                headers,
                feedback,
                request_tls_verify,
                request_cert,
                max_response_time,
            )
            add_cases(
                case,
                response,
                _network_test,
                checks,
                targets,
                result,
                session,
                timeout,
                store_interactions,
                headers,
                feedback,
                request_tls_verify,
                request_cert,
                max_response_time,
            )


def _network_test(
    case: Case,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    result: TestResult,
    session: requests.Session,
    timeout: Optional[float],
    store_interactions: bool,
    headers: Optional[Dict[str, Any]],
    feedback: Feedback,
    request_tls_verify: bool,
    request_cert: Optional[RequestCert],
    max_response_time: Optional[int],
) -> requests.Response:
    check_results: List[Check] = []
    try:
        hook_context = HookContext(operation=case.operation)
        kwargs: Dict[str, Any] = {
            "session": session,
            "headers": headers,
            "timeout": timeout,
            "verify": request_tls_verify,
            "cert": request_cert,
        }
        hooks.dispatch("process_call_kwargs", hook_context, case, kwargs)
        response = case.call(**kwargs)
    except CheckFailed as exc:
        check_name = "request_timeout"
        requests_kwargs = case.as_requests_kwargs(base_url=case.get_full_base_url(), headers=headers)
        request = requests.Request(**requests_kwargs).prepare()
        elapsed = cast(float, timeout)  # It is defined and not empty, since the exception happened
        check_result = result.add_failure(
            check_name, case, None, elapsed, f"Response timed out after {1000 * elapsed:.2f}ms", exc.context, request
        )
        check_results.append(check_result)
        raise exc
    context = TargetContext(case=case, response=response, response_time=response.elapsed.total_seconds())
    run_targets(targets, context)
    status = Status.success
    try:
        run_checks(case, checks, check_results, result, response, context.response_time * 1000, max_response_time)
    except CheckFailed:
        status = Status.failure
        raise
    finally:
        if store_interactions:
            result.store_requests_response(response, status, check_results)
    feedback.add_test_case(case, response)
    return response


@contextmanager
def get_session(auth: Optional[Union[HTTPDigestAuth, RawAuth]] = None) -> Generator[requests.Session, None, None]:
    with requests.Session() as session:
        if auth is not None:
            session.auth = auth
        yield session


def prepare_timeout(timeout: Optional[int]) -> Optional[float]:
    """Request timeout is in milliseconds, but `requests` uses seconds."""
    output: Optional[Union[int, float]] = timeout
    if timeout is not None:
        output = timeout / 1000
    return output


def wsgi_test(
    case: Case,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    result: TestResult,
    auth: Optional[RawAuth],
    auth_type: Optional[str],
    headers: Optional[Dict[str, Any]],
    store_interactions: bool,
    feedback: Feedback,
    max_response_time: Optional[int],
    dry_run: bool,
    errors: List[Exception],
) -> None:
    with ErrorCollector(errors):
        result.mark_executed()
        headers = _prepare_wsgi_headers(headers, auth, auth_type)
        if not dry_run:
            response = _wsgi_test(
                case, checks, targets, result, headers, store_interactions, feedback, max_response_time
            )
            add_cases(
                case,
                response,
                _wsgi_test,
                checks,
                targets,
                result,
                headers,
                store_interactions,
                feedback,
                max_response_time,
            )


def _wsgi_test(
    case: Case,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    result: TestResult,
    headers: Dict[str, Any],
    store_interactions: bool,
    feedback: Feedback,
    max_response_time: Optional[int],
) -> WSGIResponse:
    with catching_logs(LogCaptureHandler(), level=logging.DEBUG) as recorded:
        start = time.monotonic()
        hook_context = HookContext(operation=case.operation)
        kwargs = {"headers": headers}
        hooks.dispatch("process_call_kwargs", hook_context, case, kwargs)
        response = case.call_wsgi(**kwargs)
        elapsed = time.monotonic() - start
    context = TargetContext(case=case, response=response, response_time=elapsed)
    run_targets(targets, context)
    result.logs.extend(recorded.records)
    status = Status.success
    check_results: List[Check] = []
    try:
        run_checks(case, checks, check_results, result, response, context.response_time * 1000, max_response_time)
    except CheckFailed:
        status = Status.failure
        raise
    finally:
        if store_interactions:
            result.store_wsgi_response(case, response, headers, elapsed, status, check_results)
    feedback.add_test_case(case, response)
    return response


def _prepare_wsgi_headers(
    headers: Optional[Dict[str, Any]], auth: Optional[RawAuth], auth_type: Optional[str]
) -> Dict[str, Any]:
    headers = headers or {}
    if "user-agent" not in {header.lower() for header in headers}:
        headers["User-Agent"] = USER_AGENT
    wsgi_auth = get_wsgi_auth(auth, auth_type)
    if wsgi_auth:
        headers["Authorization"] = wsgi_auth
    return headers


def get_wsgi_auth(auth: Optional[RawAuth], auth_type: Optional[str]) -> Optional[str]:
    if auth:
        if auth_type == "digest":
            raise ValueError("Digest auth is not supported for WSGI apps")
        return _basic_auth_str(*auth)
    return None


def asgi_test(
    case: Case,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    result: TestResult,
    store_interactions: bool,
    headers: Optional[Dict[str, Any]],
    feedback: Feedback,
    max_response_time: Optional[int],
    dry_run: bool,
    errors: List[Exception],
) -> None:
    """A single test body will be executed against the target."""
    with ErrorCollector(errors):
        result.mark_executed()
        headers = headers or {}

        if not dry_run:
            response = _asgi_test(
                case, checks, targets, result, store_interactions, headers, feedback, max_response_time
            )
            add_cases(
                case,
                response,
                _asgi_test,
                checks,
                targets,
                result,
                store_interactions,
                headers,
                feedback,
                max_response_time,
            )


def _asgi_test(
    case: Case,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    result: TestResult,
    store_interactions: bool,
    headers: Optional[Dict[str, Any]],
    feedback: Feedback,
    max_response_time: Optional[int],
) -> requests.Response:
    hook_context = HookContext(operation=case.operation)
    kwargs: Dict[str, Any] = {"headers": headers}
    hooks.dispatch("process_call_kwargs", hook_context, case, kwargs)
    response = case.call_asgi(**kwargs)
    context = TargetContext(case=case, response=response, response_time=response.elapsed.total_seconds())
    run_targets(targets, context)
    status = Status.success
    check_results: List[Check] = []
    try:
        run_checks(case, checks, check_results, result, response, context.response_time * 1000, max_response_time)
    except CheckFailed:
        status = Status.failure
        raise
    finally:
        if store_interactions:
            result.store_requests_response(response, status, check_results)
    feedback.add_test_case(case, response)
    return response
