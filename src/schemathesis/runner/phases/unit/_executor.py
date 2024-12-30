from __future__ import annotations

import time
import unittest
import uuid
from typing import TYPE_CHECKING, Any, Callable, Iterable
from warnings import WarningMessage, catch_warnings

import requests
from hypothesis.errors import InvalidArgument
from hypothesis_jsonschema._canonicalise import HypothesisRefResolutionError
from jsonschema.exceptions import SchemaError as JsonSchemaError
from jsonschema.exceptions import ValidationError

from schemathesis.checks import CheckContext, CheckFunction, run_checks
from schemathesis.core.compat import BaseExceptionGroup
from schemathesis.core.control import SkipTest
from schemathesis.core.errors import (
    SERIALIZERS_SUGGESTION_MESSAGE,
    InternalError,
    InvalidHeadersExample,
    InvalidRegexPattern,
    InvalidRegexType,
    InvalidSchema,
    MalformedMediaType,
    SerializationNotPossible,
)
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.transport import Response
from schemathesis.generation import targets
from schemathesis.generation.hypothesis.builder import (
    InvalidHeadersExampleMark,
    InvalidRegexMark,
    NonSerializableMark,
    UnsatisfiableExampleMark,
)
from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output
from schemathesis.runner import Status
from schemathesis.runner.errors import (
    DeadlineExceeded,
    UnexpectedError,
    UnsupportedRecursiveReference,
    deduplicate_errors,
)
from schemathesis.runner.phases import PhaseName

from ... import events
from ...context import EngineContext
from ...models.check import Check
from ...models.outcome import TestResult
from ...models.transport import Request

if TYPE_CHECKING:
    import requests

    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


def run_test(*, operation: APIOperation, test_function: Callable, ctx: EngineContext) -> events.EventGenerator:
    """A single test run with all error handling needed."""
    import hypothesis.errors

    result = TestResult(label=operation.label)
    # To simplify connecting `before` and `after` events in external systems
    correlation_id = uuid.uuid4()
    yield events.BeforeExecution(label=operation.label, correlation_id=correlation_id)
    errors: list[Exception] = []
    test_start_time = time.monotonic()

    def non_fatal_error(error: Exception) -> events.NonFatalError:
        return events.NonFatalError(error=error, phase=PhaseName.UNIT_TESTING, label=operation.label)

    try:
        setup_hypothesis_database_key(test_function, operation)
        with catch_warnings(record=True) as warnings, ignore_hypothesis_output():
            test_function(ctx=ctx, result=result, errors=errors)
        # Test body was not executed at all - Hypothesis did not generate any tests, but there is no error
        status = Status.SUCCESS
    except (SkipTest, unittest.case.SkipTest) as exc:
        status = Status.SKIP
        result.mark_skipped(exc)
    except (FailureGroup, Failure):
        status = Status.FAILURE
    except UnexpectedError:
        # It could be an error in user-defined extensions, network errors or internal Schemathesis errors
        status = Status.ERROR
        for idx, err in enumerate(errors):
            if isinstance(err, MalformedMediaType):
                errors[idx] = InvalidSchema(str(err))
    except hypothesis.errors.Flaky as exc:
        if isinstance(exc.__cause__, hypothesis.errors.DeadlineExceeded):
            status = Status.ERROR
            yield non_fatal_error(DeadlineExceeded.from_exc(exc.__cause__))
        elif isinstance(exc, hypothesis.errors.FlakyFailure) and any(
            isinstance(subexc, hypothesis.errors.DeadlineExceeded) for subexc in exc.exceptions
        ):
            for sub_exc in exc.exceptions:
                if isinstance(sub_exc, hypothesis.errors.DeadlineExceeded):
                    yield non_fatal_error(DeadlineExceeded.from_exc(sub_exc))
            status = Status.ERROR
        elif errors:
            status = Status.ERROR
        else:
            status = Status.FAILURE
    except BaseExceptionGroup:
        status = Status.ERROR
    except hypothesis.errors.Unsatisfiable:
        # We need more clear error message here
        status = Status.ERROR
        yield non_fatal_error(hypothesis.errors.Unsatisfiable("Failed to generate test cases for this API operation"))
    except KeyboardInterrupt:
        yield events.Interrupted(phase=PhaseName.UNIT_TESTING)
        return
    except AssertionError as exc:  # May come from `hypothesis-jsonschema` or `hypothesis`
        status = Status.ERROR
        try:
            operation.schema.validate()
            msg = "Unexpected error during testing of this API operation"
            exc_msg = str(exc)
            if exc_msg:
                msg += f": {exc_msg}"
            try:
                raise InternalError(msg) from exc
            except InternalError as exc:
                yield non_fatal_error(exc)
        except ValidationError as exc:
            yield non_fatal_error(
                InvalidSchema.from_jsonschema_error(
                    exc,
                    path=operation.path,
                    method=operation.method,
                    full_path=operation.schema.get_full_path(operation.path),
                )
            )
    except HypothesisRefResolutionError:
        status = Status.ERROR
        yield non_fatal_error(UnsupportedRecursiveReference())
    except InvalidArgument as exc:
        status = Status.ERROR
        message = get_invalid_regular_expression_message(warnings)
        if message:
            # `hypothesis-jsonschema` emits a warning on invalid regular expression syntax
            yield non_fatal_error(InvalidRegexPattern.from_hypothesis_jsonschema_message(message))
        else:
            yield non_fatal_error(exc)
    except hypothesis.errors.DeadlineExceeded as exc:
        status = Status.ERROR
        yield non_fatal_error(DeadlineExceeded.from_exc(exc))
    except JsonSchemaError as exc:
        status = Status.ERROR
        yield non_fatal_error(InvalidRegexPattern.from_schema_error(exc, from_examples=False))
    except Exception as exc:
        status = Status.ERROR
        # Likely a YAML parsing issue. E.g. `00:00:00.00` (without quotes) is parsed as float `0.0`
        if str(exc) == "first argument must be string or compiled pattern":
            yield non_fatal_error(
                InvalidRegexType(
                    "Invalid `pattern` value: expected a string. "
                    "If your schema is in YAML, ensure `pattern` values are quoted",
                )
            )
        else:
            yield non_fatal_error(exc)
    if (
        status == Status.SUCCESS
        and ctx.config.execution.no_failfast
        and any(check.status == Status.FAILURE for check in result.checks)
    ):
        status = Status.FAILURE
    if UnsatisfiableExampleMark.is_set(test_function):
        status = Status.ERROR
        yield non_fatal_error(
            hypothesis.errors.Unsatisfiable("Failed to generate test cases from examples for this API operation")
        )
    non_serializable = NonSerializableMark.get(test_function)
    if non_serializable is not None and status != Status.ERROR:
        status = Status.ERROR
        media_types = ", ".join(non_serializable.media_types)
        yield non_fatal_error(
            SerializationNotPossible(
                "Failed to generate test cases from examples for this API operation because of"
                f" unsupported payload media types: {media_types}\n{SERIALIZERS_SUGGESTION_MESSAGE}",
                media_types=non_serializable.media_types,
            )
        )

    invalid_regex = InvalidRegexMark.get(test_function)
    if invalid_regex is not None and status != Status.ERROR:
        status = Status.ERROR
        yield non_fatal_error(InvalidRegexPattern.from_schema_error(invalid_regex, from_examples=True))
    invalid_headers = InvalidHeadersExampleMark.get(test_function)
    if invalid_headers:
        status = Status.ERROR
        yield non_fatal_error(InvalidHeadersExample.from_headers(invalid_headers))
    test_elapsed_time = time.monotonic() - test_start_time
    ctx.add_result(result)
    for status_code in (401, 403):
        if has_too_many_responses_with_status(result, status_code):
            ctx.add_warning(TOO_MANY_RESPONSES_WARNING_TEMPLATE.format(f"`{operation.label}`", status_code))
    for error in deduplicate_errors(errors):
        yield non_fatal_error(error)
    yield events.AfterExecution(
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
    extra = operation.label.encode("utf8")
    for parameter in operation.iter_parameters():
        extra += parameter.serialize(operation).encode("utf8")
    test.hypothesis.inner_test._hypothesis_internal_add_digest = extra  # type: ignore


def get_invalid_regular_expression_message(warnings: list[WarningMessage]) -> str | None:
    for warning in warnings:
        message = str(warning.message)
        if "is not valid syntax for a Python regular expression" in message:
            return message
    return None


def validate_response(
    *,
    case: Case,
    ctx: CheckContext,
    checks: Iterable[CheckFunction],
    check_results: list[Check],
    result: TestResult,
    response: Response,
    no_failfast: bool,
) -> None:
    failures = set()

    def on_failure(name: str, collected: set[Failure], failure: Failure) -> None:
        collected.add(failure)
        check_results.append(
            result.add_failure(
                name=name,
                case=case,
                request=Request.from_prepared_request(response.request),
                response=response,
                failure=failure,
            )
        )

    def on_success(name: str, _case: Case) -> None:
        check_results.append(
            result.add_success(
                name=name,
                case=_case,
                request=Request.from_prepared_request(response.request),
                response=response,
            )
        )

    failures = run_checks(
        case=case,
        response=response,
        ctx=ctx,
        checks=checks,
        on_failure=on_failure,
        on_success=on_success,
    )

    if failures and not no_failfast:
        raise FailureGroup(list(failures)) from None


def cached_test_func(f: Callable) -> Callable:
    def wrapped(*, ctx: EngineContext, case: Case, errors: list[Exception], result: TestResult, **kwargs: Any) -> None:
        try:
            if ctx.is_stopped:
                raise KeyboardInterrupt
            if ctx.config.execution.unique_data:
                cached = ctx.get_cached_outcome(case)
                if isinstance(cached, BaseException):
                    raise cached
                elif cached is None:
                    return None
                try:
                    f(ctx=ctx, case=case, result=result, **kwargs)
                except BaseException as exc:
                    ctx.cache_outcome(case, exc)
                    raise
                else:
                    ctx.cache_outcome(case, None)
            else:
                f(ctx=ctx, case=case, result=result, **kwargs)
        except (KeyboardInterrupt, Failure):
            raise
        except Exception as exc:
            errors.append(exc)
            raise UnexpectedError from None

    wrapped.__name__ = f.__name__

    return wrapped


@cached_test_func
def test_func(*, ctx: EngineContext, case: Case, result: TestResult) -> None:
    if not ctx.config.execution.dry_run:
        try:
            response = case.call(**ctx.transport_kwargs)
        except (requests.Timeout, requests.ConnectionError):
            result.store_requests_response(case, None, Status.FAILURE, [], session=ctx.session)
            raise
        targets.run(ctx.config.execution.targets, case=case, response=response)
        status = Status.SUCCESS
        check_results: list[Check] = []
        try:
            validate_response(
                case=case,
                ctx=ctx.check_context,
                checks=ctx.config.execution.checks,
                check_results=check_results,
                result=result,
                response=response,
                no_failfast=ctx.config.execution.no_failfast,
            )
        except FailureGroup:
            status = Status.FAILURE
            raise
        finally:
            result.store_requests_response(case, response, status, check_results, session=ctx.session)
    else:
        result.store_requests_response(case, None, Status.SKIP, [], session=ctx.session)
