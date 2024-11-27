from __future__ import annotations

import time
import unittest
import uuid
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal, cast
from warnings import WarningMessage, catch_warnings

from hypothesis.errors import HypothesisException, InvalidArgument
from hypothesis_jsonschema._canonicalise import HypothesisRefResolutionError
from jsonschema.exceptions import SchemaError as JsonSchemaError
from jsonschema.exceptions import ValidationError
from requests.structures import CaseInsensitiveDict

from schemathesis._hypothesis._builder import (
    InvalidHeadersExampleMark,
    InvalidRegexMark,
    NonSerializableMark,
    UnsatisfiableExampleMark,
)
from schemathesis.core.control import SkipTest
from schemathesis.core.errors import (
    InternalError,
    InvalidHeadersExample,
    InvalidRegexPattern,
    InvalidRegexType,
    InvalidSchema,
    MalformedMediaType,
    SerializationNotPossible,
)
from schemathesis.core.failures import Failure, FailureGroup, ResponseTimeExceeded
from schemathesis.runner.errors import DeadlineExceeded, UnexpectedError, UnsupportedRecursiveReference

from .... import targets
from ...._compat import BaseExceptionGroup
from ....constants import SERIALIZERS_SUGGESTION_MESSAGE
from ....internal.checks import CheckContext
from ....internal.exceptions import deduplicate_errors
from ... import events
from ..._hypothesis import ignore_hypothesis_output
from ...context import EngineContext
from ...models.check import Check
from ...models.outcome import TestResult
from ...models.status import Status
from ...models.transport import Request, Response

if TYPE_CHECKING:
    import requests

    from ....checks import CheckFunction
    from ....models import APIOperation, Case
    from ....transports.responses import GenericResponse


def run_test(*, operation: APIOperation, test_function: Callable, ctx: EngineContext) -> events.EventGenerator:
    """A single test run with all error handling needed."""
    import hypothesis.errors

    result = TestResult(verbose_name=operation.verbose_name)
    # To simplify connecting `before` and `after` events in external systems
    correlation_id = uuid.uuid4().hex
    yield events.BeforeExecution.from_operation(operation=operation, correlation_id=correlation_id)
    errors: list[Exception] = []
    test_start_time = time.monotonic()
    setup_hypothesis_database_key(test_function, operation)

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
            result.add_errors(errors)
        else:
            status = Status.failure
            result.mark_flaky()
        return status

    try:
        with catch_warnings(record=True) as warnings, ignore_hypothesis_output():
            test_function(ctx=ctx, result=result, errors=errors)
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
    except (FailureGroup, Failure):
        status = Status.failure
    except UnexpectedError:
        # It could be an error in user-defined extensions, network errors or internal Schemathesis errors
        status = Status.error
        result.mark_errored()
        for error in deduplicate_errors(errors):
            if isinstance(error, MalformedMediaType):
                result.add_error(InvalidSchema(str(error)))
            else:
                result.add_error(error)
    except hypothesis.errors.Flaky as exc:
        status = _on_flaky(exc)
    except BaseExceptionGroup:
        if errors:
            status = Status.error
            result.add_errors(errors)
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
            error = InvalidSchema.from_jsonschema_error(
                exc,
                path=operation.path,
                method=operation.method,
                full_path=operation.schema.get_full_path(operation.path),
            )
        result.add_error(error)
    except HypothesisRefResolutionError:
        status = Status.error
        result.add_error(UnsupportedRecursiveReference())
    except InvalidArgument as error:
        status = Status.error
        message = get_invalid_regular_expression_message(warnings)
        if message:
            # `hypothesis-jsonschema` emits a warning on invalid regular expression syntax
            result.add_error(InvalidRegexPattern.from_hypothesis_jsonschema_message(message))
        else:
            result.add_error(error)
    except hypothesis.errors.DeadlineExceeded as error:
        status = Status.error
        result.add_error(DeadlineExceeded.from_exc(error))
    except JsonSchemaError as error:
        status = Status.error
        result.add_error(InvalidRegexPattern.from_schema_error(error, from_examples=False))
    except Exception as error:
        status = Status.error
        # Likely a YAML parsing issue. E.g. `00:00:00.00` (without quotes) is parsed as float `0.0`
        if str(error) == "first argument must be string or compiled pattern":
            result.add_error(
                InvalidRegexType(
                    "Invalid `pattern` value: expected a string. "
                    "If your schema is in YAML, ensure `pattern` values are quoted",
                )
            )
        else:
            result.add_error(error)
    if UnsatisfiableExampleMark.is_set(test_function):
        status = Status.error
        result.add_error(
            hypothesis.errors.Unsatisfiable("Failed to generate test cases from examples for this API operation")
        )
    non_serializable = NonSerializableMark.get(test_function)
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
    invalid_regex = InvalidRegexMark.get(test_function)
    if invalid_regex is not None and status != Status.error:
        status = Status.error
        result.add_error(InvalidRegexPattern.from_schema_error(invalid_regex, from_examples=True))
    invalid_headers = InvalidHeadersExampleMark.get(test_function)
    if invalid_headers:
        status = Status.error
        result.add_error(InvalidHeadersExample.from_headers(invalid_headers))
    test_elapsed_time = time.monotonic() - test_start_time
    ctx.add_result(result)
    for status_code in (401, 403):
        if has_too_many_responses_with_status(result, status_code):
            ctx.add_warning(TOO_MANY_RESPONSES_WARNING_TEMPLATE.format(f"`{operation.verbose_name}`", status_code))
    yield events.AfterExecution.from_result(
        result=result,
        status=status,
        elapsed_time=test_elapsed_time,
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
        if check.response.status_code == status_code:
            unauthorized_count += 1
        total += 1
    if not total:
        return False
    return unauthorized_count / total >= TOO_MANY_RESPONSES_THRESHOLD


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


def run_checks(
    *,
    case: Case,
    ctx: CheckContext,
    checks: Iterable[CheckFunction],
    check_results: list[Check],
    result: TestResult,
    response: GenericResponse,
    max_response_time: int | None = None,
) -> None:
    failures = set()

    def add_single_failure(failure: Failure) -> None:
        failures.add(failure)
        check_results.append(
            result.add_failure(
                name=check_name,
                case=case,
                request=Request.from_prepared_request(response.request),
                response=Response.from_requests(response=response),
                failure=failure,
            )
        )

    for check in checks:
        check_name = check.__name__
        try:
            skip_check = check(ctx, response, case)
            if not skip_check:
                check_result = result.add_success(
                    name=check_name,
                    case=case,
                    request=Request.from_prepared_request(response.request),
                    response=Response.from_requests(response=response),
                )
                check_results.append(check_result)
        except Failure as failure:
            add_single_failure(failure)
        except AssertionError as exc:
            add_single_failure(
                Failure.from_assertion(
                    name=check_name,
                    operation=case.operation.verbose_name,
                    exc=exc,
                )
            )
        except FailureGroup as group:
            for e in group.exceptions:
                add_single_failure(e)

    if max_response_time:
        elapsed = response.elapsed.total_seconds() * 1000
        if elapsed > max_response_time:
            message = f"Actual: {elapsed:.2f}ms\nLimit: {max_response_time}.00ms"
            fail = ResponseTimeExceeded(
                operation=case.operation.verbose_name, message=message, elapsed=elapsed, deadline=max_response_time
            )
            failures.add(fail)
            result.add_failure(
                name="max_response_time",
                case=case,
                request=Request.from_prepared_request(response.request),
                response=Response.from_requests(response=response),
                failure=fail,
            )
        else:
            result.add_success(
                name="max_response_time",
                case=case,
                request=Request.from_prepared_request(response.request),
                response=Response.from_requests(response=response),
            )

    if failures:
        raise FailureGroup(list(failures)) from None


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
        if not exc_type or issubclass(exc_type, Failure) or not issubclass(exc_type, Exception):
            return False
        # These exceptions are needed for control flow on the Hypothesis side. E.g. rejecting unsatisfiable examples
        if isinstance(exc_val, HypothesisException):
            raise
        # Exception value is not `None` and is a subclass of `Exception` at this point
        exc_val = cast(Exception, exc_val)
        self.errors.append(exc_val.with_traceback(exc_tb))
        raise UnexpectedError from None


def cached_test_func(f: Callable) -> Callable:
    def wrapped(*, ctx: EngineContext, case: Case, errors: list[Exception], result: TestResult, **kwargs: Any) -> None:
        with ErrorCollector(errors):
            if ctx.is_stopped:
                raise KeyboardInterrupt
            if ctx.config.execution.unique_data:
                cached = ctx.get_cached_outcome(case)
                if isinstance(cached, BaseException):
                    raise cached
                elif cached is None:
                    return None
                try:
                    result.mark_executed()
                    f(ctx=ctx, case=case, result=result, **kwargs)
                except BaseException as exc:
                    ctx.cache_outcome(case, exc)
                    raise
                else:
                    ctx.cache_outcome(case, None)
            else:
                result.mark_executed()
                f(ctx=ctx, case=case, result=result, **kwargs)

    wrapped.__name__ = f.__name__

    return wrapped


@cached_test_func
def network_test(*, ctx: EngineContext, case: Case, result: TestResult, session: requests.Session) -> None:
    headers = ctx.config.network.headers
    if not ctx.config.execution.dry_run:
        _network_test(case, ctx, result, session, headers)
    else:
        result.store_requests_response(case, None, Status.skip, [], headers=headers, session=session)


def _network_test(
    case: Case, ctx: EngineContext, result: TestResult, session: requests.Session, headers: dict[str, Any] | None
) -> requests.Response:
    import requests

    check_results: list[Check] = []
    kwargs: dict[str, Any] = {
        "session": session,
        "headers": headers,
        "timeout": ctx.config.network.timeout,
        "verify": ctx.config.network.tls_verify,
        "cert": ctx.config.network.cert,
    }
    if ctx.config.network.proxy is not None:
        kwargs["proxies"] = {"all": ctx.config.network.proxy}
    try:
        response = case.call(**kwargs)
    except (requests.Timeout, requests.ConnectionError):
        result.store_requests_response(case, None, Status.failure, [], headers=headers, session=session)
        raise
    targets.run(ctx.config.execution.targets, case=case, response=response)
    status = Status.success

    check_ctx = CheckContext(
        override=ctx.config.override,
        auth=ctx.config.network.auth,
        headers=CaseInsensitiveDict(headers) if headers else None,
        config=ctx.config.checks_config,
    )
    try:
        run_checks(
            case=case,
            ctx=check_ctx,
            checks=ctx.config.execution.checks,
            check_results=check_results,
            result=result,
            response=response,
            max_response_time=ctx.config.execution.max_response_time,
        )
    except FailureGroup:
        status = Status.failure
        raise
    finally:
        result.store_requests_response(case, response, status, check_results, headers=headers, session=session)
    return response
