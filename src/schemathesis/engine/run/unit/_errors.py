"""Translate deferred and iteration-time errors into `NonFatalError` events."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Protocol

import hypothesis.errors
from jsonschema_rs import ValidationError

from schemathesis.core.errors import (
    InfiniteRecursiveReference,
    InvalidHeadersExample,
    InvalidRegexPattern,
    InvalidRegexType,
    InvalidSchema,
    SerializationNotPossible,
    UnresolvableReference,
    is_regex_validation_error,
)
from schemathesis.engine import Status, events
from schemathesis.engine.errors import TestingState, clear_hypothesis_notes
from schemathesis.generation.hypothesis.builder import (
    InfiniteRecursiveReferenceMark,
    InvalidHeadersExampleMark,
    InvalidRegexMark,
    MissingPathParameters,
    NonSerializableMark,
    UnresolvableReferenceMark,
    UnsatisfiableExampleMark,
)
from schemathesis.generation.hypothesis.reporting import build_unsatisfiable_error

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
        return non_fatal_error(exc)
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
