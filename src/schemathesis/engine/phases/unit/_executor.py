from __future__ import annotations

import time
import unittest
import uuid
from typing import TYPE_CHECKING, Callable, Iterable
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
from schemathesis.engine import Status, events
from schemathesis.engine.context import EngineContext
from schemathesis.engine.errors import (
    DeadlineExceeded,
    UnexpectedError,
    UnsupportedRecursiveReference,
    deduplicate_errors,
)
from schemathesis.engine.phases import PhaseName
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.generation import targets
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis.builder import (
    InvalidHeadersExampleMark,
    InvalidRegexMark,
    NonSerializableMark,
    UnsatisfiableExampleMark,
)
from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


def run_test(
    *,
    operation: APIOperation,
    test_function: Callable,
    ctx: EngineContext,
    phase: PhaseName,
    suite_id: uuid.UUID,
) -> events.EventGenerator:
    """A single test run with all error handling needed."""
    import hypothesis.errors

    scenario_started = events.ScenarioStarted(label=operation.label, phase=phase, suite_id=suite_id)
    yield scenario_started
    errors: list[Exception] = []
    skip_reason = None
    test_start_time = time.monotonic()
    recorder = ScenarioRecorder(label=operation.label)

    def non_fatal_error(error: Exception) -> events.NonFatalError:
        return events.NonFatalError(error=error, phase=phase, label=operation.label, related_to_operation=True)

    def scenario_finished(status: Status) -> events.ScenarioFinished:
        return events.ScenarioFinished(
            id=scenario_started.id,
            suite_id=suite_id,
            phase=phase,
            label=operation.label,
            recorder=recorder,
            status=status,
            elapsed_time=time.monotonic() - test_start_time,
            skip_reason=skip_reason,
            is_final=False,
        )

    try:
        setup_hypothesis_database_key(test_function, operation)
        with catch_warnings(record=True) as warnings, ignore_hypothesis_output():
            test_function(ctx=ctx, errors=errors, recorder=recorder)
        # Test body was not executed at all - Hypothesis did not generate any tests, but there is no error
        status = Status.SUCCESS
    except (SkipTest, unittest.case.SkipTest) as exc:
        status = Status.SKIP
        skip_reason = {"Hypothesis has been told to run no examples for this test.": "No examples in schema"}.get(
            str(exc), str(exc)
        )
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
        yield scenario_finished(Status.INTERRUPTED)
        yield events.Interrupted(phase=phase)
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
        and ctx.config.execution.continue_on_failure
        and any(check.status == Status.FAILURE for checks in recorder.checks.values() for check in checks)
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
    for error in deduplicate_errors(errors):
        yield non_fatal_error(error)

    yield scenario_finished(status)


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


def cached_test_func(f: Callable) -> Callable:
    def wrapped(*, ctx: EngineContext, case: Case, errors: list[Exception], recorder: ScenarioRecorder) -> None:
        try:
            if ctx.has_to_stop:
                raise KeyboardInterrupt
            if ctx.config.execution.unique_inputs:
                cached = ctx.get_cached_outcome(case)
                if isinstance(cached, BaseException):
                    raise cached
                elif cached is None:
                    return None
                try:
                    f(ctx=ctx, case=case, recorder=recorder)
                except BaseException as exc:
                    ctx.cache_outcome(case, exc)
                    raise
                else:
                    ctx.cache_outcome(case, None)
            else:
                f(ctx=ctx, case=case, recorder=recorder)
        except (KeyboardInterrupt, Failure):
            raise
        except Exception as exc:
            errors.append(exc)
            raise UnexpectedError from None

    wrapped.__name__ = f.__name__

    return wrapped


@cached_test_func
def test_func(*, ctx: EngineContext, case: Case, recorder: ScenarioRecorder) -> None:
    recorder.record_case(parent_id=None, transition=None, case=case)
    try:
        response = case.call(**ctx.transport_kwargs)
    except (requests.Timeout, requests.ConnectionError) as error:
        if isinstance(error.request, requests.Request):
            recorder.record_request(case_id=case.id, request=error.request.prepare())
        elif isinstance(error.request, requests.PreparedRequest):
            recorder.record_request(case_id=case.id, request=error.request)
        raise
    recorder.record_response(case_id=case.id, response=response)
    targets.run(ctx.config.execution.targets, case=case, response=response)
    validate_response(
        case=case,
        ctx=ctx.get_check_context(recorder),
        checks=ctx.config.execution.checks,
        response=response,
        continue_on_failure=ctx.config.execution.continue_on_failure,
        recorder=recorder,
    )


def validate_response(
    *,
    case: Case,
    ctx: CheckContext,
    checks: Iterable[CheckFunction],
    response: Response,
    continue_on_failure: bool,
    recorder: ScenarioRecorder,
) -> None:
    failures = set()

    def on_failure(name: str, collected: set[Failure], failure: Failure) -> None:
        collected.add(failure)
        failure_data = recorder.find_failure_data(parent_id=case.id, failure=failure)
        recorder.record_check_failure(
            name=name,
            case_id=failure_data.case.id,
            code_sample=failure_data.case.as_curl_command(headers=failure_data.headers, verify=failure_data.verify),
            failure=failure,
        )

    def on_success(name: str, _case: Case) -> None:
        recorder.record_check_success(name=name, case_id=_case.id)

    failures = run_checks(
        case=case,
        response=response,
        ctx=ctx,
        checks=checks,
        on_failure=on_failure,
        on_success=on_success,
    )

    if failures and not continue_on_failure:
        raise FailureGroup(list(failures)) from None
