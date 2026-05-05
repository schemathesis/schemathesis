"""Translate deferred and iteration-time errors into `NonFatalError` events."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Protocol
from warnings import WarningMessage

import hypothesis.errors
from jsonschema.exceptions import SchemaError
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
    from schemathesis.generation.progressive import Controller
    from schemathesis.schemas import APIOperation


class NonFatalErrorFactory(Protocol):
    def __call__(self, error: Exception, code_sample: str | None = None) -> events.NonFatalError: ...


def iter_mark_error_events(
    *,
    test_function: Callable,
    non_fatal_error: NonFatalErrorFactory,
    current_status: Status | None,
    serializers_suggestion: str,
) -> Iterator[events.NonFatalError]:
    """Yield events for errors stashed on a Hypothesis test function via `*Mark` slots."""
    # Track status locally so guards on later marks see promotions from earlier yields.
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
        if isinstance(invalid_regex, ValidationError):
            yield non_fatal_error(InvalidRegexPattern.from_jsonschema_rs_error(invalid_regex))
        else:
            yield non_fatal_error(InvalidRegexPattern.from_schema_error(invalid_regex, from_examples=True))
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
    if controller.invalid_headers:
        yield non_fatal_error(InvalidHeadersExample.from_headers(controller.invalid_headers))


def translate_iteration_exception(
    exc: Exception,
    *,
    operation: APIOperation,
    state: TestingState,
    non_fatal_error: NonFatalErrorFactory,
    invalid_regex_message: str | None,
) -> events.NonFatalError:
    """Translate an iteration-time exception into a `NonFatalError`."""
    if isinstance(exc, hypothesis.errors.Unsatisfiable):
        return non_fatal_error(
            build_unsatisfiable_error(operation, with_tip=False, filter_tracker=operation.filter_case_tracker)
        )
    if isinstance(exc, hypothesis.errors.InvalidArgument):
        if invalid_regex_message:
            return non_fatal_error(InvalidRegexPattern.from_hypothesis_jsonschema_message(invalid_regex_message))
        return non_fatal_error(exc)
    if isinstance(exc, SchemaError):
        return non_fatal_error(InvalidRegexPattern.from_schema_error(exc, from_examples=False))
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


def get_invalid_regular_expression_message(warnings: list[WarningMessage]) -> str | None:
    for warning in warnings:
        message = str(warning.message)
        if "is not valid syntax for a Python regular expression" in message:
            return message
    return None
