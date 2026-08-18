"""Translate deferred and iteration-time errors into `NonFatalError` events."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Protocol

import hypothesis.errors
from jsonschema_rs import ValidationError

from schemathesis.core.compat import BaseExceptionGroup
from schemathesis.core.errors import (
    AuthenticationError,
    InfiniteRecursiveReference,
    InternalError,
    InvalidHeadersExample,
    InvalidRegexPattern,
    InvalidRegexType,
    InvalidSchema,
    RejectedSchemaDefinition,
    SchemaLocation,
    SerializationNotPossible,
    UnresolvableReference,
    is_regex_validation_error,
)
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.engine import Status, events
from schemathesis.engine.errors import DeadlineExceeded, TestingState, UnexpectedError, clear_hypothesis_notes
from schemathesis.generation.hypothesis.builder import (
    InfiniteRecursiveReferenceMark,
    InvalidHeadersExampleMark,
    InvalidRegexMark,
    MissingPathParameters,
    NonSerializableMark,
    UnresolvableReferenceMark,
    UnsatisfiableExampleMark,
)
from schemathesis.generation.hypothesis.reporting import (
    build_health_check_error,
    build_unsatisfiable_error,
    is_empty_strategy_error,
)

if TYPE_CHECKING:
    from schemathesis.generation.drivers import Controller
    from schemathesis.schemas import APIOperation


class NonFatalErrorFactory(Protocol):
    def __call__(
        self, error: Exception, code_sample: str | None = None
    ) -> events.NonFatalError: ...  # pragma: no cover


def iter_mark_error_events(
    *,
    test_function: Callable,
    non_fatal_error: NonFatalErrorFactory,
    current_status: Status | None,
    serializers_suggestion: str,
) -> Iterator[events.NonFatalError]:
    """Yield events for errors stashed on a Hypothesis test function via `*Mark` slots."""
    status = current_status
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
                f" unsupported payload media types: {media_types}\n{serializers_suggestion}",
                media_types=non_serializable.media_types,
            )
        )
    invalid_regex = InvalidRegexMark.get(test_function)
    if invalid_regex is not None and status != Status.ERROR:
        status = Status.ERROR
        yield non_fatal_error(InvalidRegexPattern.from_jsonschema_rs_error(invalid_regex))
    invalid_headers = InvalidHeadersExampleMark.get(test_function)
    if invalid_headers:
        yield non_fatal_error(InvalidHeadersExample.from_headers(invalid_headers))
    missing = MissingPathParameters.get(test_function)
    if missing:
        yield non_fatal_error(missing)
    infinite = InfiniteRecursiveReferenceMark.get(test_function)
    if infinite:
        yield non_fatal_error(infinite)
    unresolvable = UnresolvableReferenceMark.get(test_function)
    if unresolvable:
        yield non_fatal_error(unresolvable)


def iter_controller_error_events(
    *,
    controller: Controller,
    non_fatal_error: NonFatalErrorFactory,
) -> Iterator[events.NonFatalError]:
    """Yield events for errors stashed on a `Controller` during pre-iteration setup."""
    for exc in controller.deferred_errors:
        yield non_fatal_error(exc)


def prefer_spec_error(exc: Exception, operation: APIOperation) -> Exception:
    """Prefer the specification's own error - it names the offending object and where it sits in the document."""
    if not isinstance(exc, RejectedSchemaDefinition):
        return exc
    try:
        operation.schema.validate()
    except ValidationError as error:
        return InvalidSchema.from_jsonschema_error(
            error,
            path=operation.path,
            method=operation.method,
            config=operation.schema.config.output,
            location=SchemaLocation.maybe_from_error_path(error.instance_path, operation.schema.specification.version),
        )
    return exc


def translate_iteration_exception(
    exc: Exception,
    *,
    operation: APIOperation,
    state: TestingState,
    non_fatal_error: NonFatalErrorFactory,
) -> events.NonFatalError:
    """Translate an iteration-time exception into a `NonFatalError`."""
    if isinstance(exc, hypothesis.errors.Unsatisfiable):
        return non_fatal_error(
            build_unsatisfiable_error(operation, with_tip=False, filter_tracker=operation.filter_case_tracker)
        )
    if isinstance(exc, hypothesis.errors.InvalidArgument):
        return non_fatal_error(exc)
    if isinstance(exc, ValidationError):
        if is_regex_validation_error(exc):
            return non_fatal_error(InvalidRegexPattern.from_jsonschema_rs_error(exc))
        code_sample = state.get_code_sample_for(exc)
        return non_fatal_error(exc, code_sample=code_sample)
    if isinstance(
        exc,
        InvalidSchema
        | SerializationNotPossible
        | InfiniteRecursiveReference
        | UnresolvableReference
        | InvalidHeadersExample,
    ):
        return non_fatal_error(prefer_spec_error(exc, operation))
    clear_hypothesis_notes(exc)
    if str(exc) == "first argument must be string or compiled pattern":
        return non_fatal_error(
            InvalidRegexType(
                "Invalid `pattern` value: expected a string. "
                "If your schema is in YAML, ensure `pattern` values are quoted",
            )
        )
    code_sample = state.get_code_sample_for(exc)
    return non_fatal_error(exc, code_sample=code_sample)


def classify_test_exception(
    exc: Exception | BaseExceptionGroup,
    *,
    operation: APIOperation,
    state: TestingState,
    errors: list[Exception],
    non_fatal_error: NonFatalErrorFactory,
) -> tuple[Status, list[events.NonFatalError]]:
    """Map an exception raised by a Hypothesis-driven test into a scenario status and events to report."""
    if isinstance(exc, FailureGroup | Failure):
        return Status.FAILURE, []
    if isinstance(exc, UnexpectedError):
        # It could be an error in user-defined extensions, network errors or internal Schemathesis errors
        return Status.ERROR, []
    if isinstance(exc, hypothesis.errors.Flaky):
        return _classify_flaky(exc, state=state, errors=errors, non_fatal_error=non_fatal_error)
    if isinstance(exc, BaseExceptionGroup):
        return Status.ERROR, list(_iter_group_errors(exc, state=state, non_fatal_error=non_fatal_error))
    if isinstance(exc, hypothesis.errors.FailedHealthCheck):
        return Status.ERROR, [non_fatal_error(build_health_check_error(operation, exc, with_tip=False))]
    if isinstance(exc, hypothesis.errors.Unsatisfiable):
        # We need more clear error message here
        return Status.ERROR, [
            non_fatal_error(
                build_unsatisfiable_error(operation, with_tip=False, filter_tracker=operation.filter_case_tracker)
            )
        ]
    if isinstance(exc, AuthenticationError):
        return Status.ERROR, [non_fatal_error(exc)]
    if isinstance(exc, AssertionError):
        # Comes from `hypothesis`
        return Status.ERROR, [_from_assertion_error(exc, operation=operation, non_fatal_error=non_fatal_error)]
    if isinstance(exc, hypothesis.errors.InvalidArgument):
        if is_empty_strategy_error(exc):
            return Status.ERROR, [non_fatal_error(build_unsatisfiable_error(operation, with_tip=False))]
        health_check = build_health_check_error(operation, exc, with_tip=False)
        if isinstance(health_check, hypothesis.errors.FailedHealthCheck):
            return Status.ERROR, [non_fatal_error(health_check)]
        return Status.ERROR, [non_fatal_error(exc)]
    if isinstance(exc, hypothesis.errors.DeadlineExceeded):
        return Status.ERROR, [non_fatal_error(DeadlineExceeded.from_exc(exc))]
    if isinstance(exc, ValidationError):
        if is_regex_validation_error(exc):
            return Status.ERROR, [non_fatal_error(InvalidRegexPattern.from_jsonschema_rs_error(exc))]
        return Status.ERROR, [non_fatal_error(exc, code_sample=state.get_code_sample_for(exc))]
    clear_hypothesis_notes(exc)
    # Likely a YAML parsing issue. E.g. `00:00:00.00` (without quotes) is parsed as float `0.0`
    if str(exc) == "first argument must be string or compiled pattern":
        return Status.ERROR, [
            non_fatal_error(
                InvalidRegexType(
                    "Invalid `pattern` value: expected a string. "
                    "If your schema is in YAML, ensure `pattern` values are quoted",
                )
            )
        ]
    return Status.ERROR, [
        non_fatal_error(prefer_spec_error(exc, operation), code_sample=state.get_code_sample_for(exc))
    ]


def _classify_flaky(
    exc: hypothesis.errors.Flaky,
    *,
    state: TestingState,
    errors: list[Exception],
    non_fatal_error: NonFatalErrorFactory,
) -> tuple[Status, list[events.NonFatalError]]:
    if isinstance(exc.__cause__, hypothesis.errors.DeadlineExceeded):
        return Status.ERROR, [non_fatal_error(DeadlineExceeded.from_exc(exc.__cause__))]
    if isinstance(exc, hypothesis.errors.FlakyFailure):
        deadlines = [sub for sub in exc.exceptions if isinstance(sub, hypothesis.errors.DeadlineExceeded)]
        if deadlines:
            return Status.ERROR, [non_fatal_error(DeadlineExceeded.from_exc(sub)) for sub in deadlines]
    if errors:
        return Status.ERROR, []
    # Unrecoverable network errors (e.g. timeouts) are not appended to `errors`
    # and are re-raised so Hypothesis sees the original exception; surface them
    # here so a replay-induced `Flaky` is not misclassified as a check failure.
    unrecoverable = state.unrecoverable_network_error
    if unrecoverable is not None:
        return Status.ERROR, [non_fatal_error(unrecoverable.error, code_sample=unrecoverable.code_sample)]
    # Hypothesis could not reproduce the result on replay. Real check failures are recorded when they happen,
    # so an unreproducible result with no observed failure is generation noise, not a failure.
    return Status.SUCCESS, []


def _iter_group_errors(
    exc: BaseExceptionGroup,
    *,
    state: TestingState,
    non_fatal_error: NonFatalErrorFactory,
) -> Iterator[events.NonFatalError]:
    for sub_exc in exc.exceptions:
        if is_regex_validation_error(sub_exc):
            yield non_fatal_error(InvalidRegexPattern.from_jsonschema_rs_error(sub_exc))
        elif isinstance(sub_exc, InvalidSchema):
            yield non_fatal_error(sub_exc)
        else:
            code_sample = state.get_code_sample_for(sub_exc)
            if code_sample is not None:
                clear_hypothesis_notes(sub_exc)
                yield non_fatal_error(sub_exc, code_sample=code_sample)


def _from_assertion_error(
    exc: AssertionError, *, operation: APIOperation, non_fatal_error: NonFatalErrorFactory
) -> events.NonFatalError:
    try:
        operation.schema.validate()
        message = "Unexpected error during testing of this API operation"
        text = str(exc)
        if text:
            message += f": {text}"
        try:
            raise InternalError(message) from exc
        except InternalError as internal:
            return non_fatal_error(internal)
    except ValidationError as error:
        return non_fatal_error(
            InvalidSchema.from_jsonschema_error(
                error,
                path=operation.path,
                method=operation.method,
                config=operation.schema.config.output,
                location=SchemaLocation.maybe_from_error_path(
                    error.instance_path, operation.schema.specification.version
                ),
            )
        )
