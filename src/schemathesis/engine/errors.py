"""Handling of recoverable errors in Schemathesis Engine.

This module provides utilities for analyzing, classifying, and formatting exceptions
that occur during test execution via Schemathesis Engine.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Callable, Iterator, Sequence, cast

from schemathesis import errors
from schemathesis.core.errors import (
    RECURSIVE_REFERENCE_ERROR_MESSAGE,
    InvalidTransition,
    SerializationNotPossible,
    format_exception,
    get_request_error_extras,
    get_request_error_message,
    split_traceback,
)

if TYPE_CHECKING:
    import hypothesis.errors
    import requests
    from requests.exceptions import ChunkedEncodingError

__all__ = ["EngineErrorInfo", "DeadlineExceeded", "UnsupportedRecursiveReference", "UnexpectedError"]


class DeadlineExceeded(errors.SchemathesisError):
    """Test took too long to run."""

    @classmethod
    def from_exc(cls, exc: hypothesis.errors.DeadlineExceeded) -> DeadlineExceeded:
        runtime = exc.runtime.total_seconds() * 1000
        deadline = exc.deadline.total_seconds() * 1000
        return cls(
            f"Test running time is too slow! It took {runtime:.2f}ms, which exceeds the deadline of {deadline:.2f}ms.\n"
        )


class UnsupportedRecursiveReference(errors.SchemathesisError):
    """Recursive reference is impossible to resolve due to current limitations."""

    def __init__(self) -> None:
        super().__init__(RECURSIVE_REFERENCE_ERROR_MESSAGE)


class UnexpectedError(errors.SchemathesisError):
    """An unexpected error during the engine execution.

    Used primarily to not let Hypothesis consider the test as flaky or detect multiple failures as we handle it
    on our side.
    """


class EngineErrorInfo:
    """Extended information about errors that happen during engine execution.

    It serves as a caching wrapper around exceptions to avoid repeated computations.
    """

    def __init__(self, error: Exception, code_sample: str | None = None) -> None:
        self._error = error
        self._code_sample = code_sample

    def __str__(self) -> str:
        return self._error_repr

    @cached_property
    def _kind(self) -> RuntimeErrorKind:
        """Error kind."""
        return _classify(error=self._error)

    @property
    def title(self) -> str:
        """A general error description."""
        import requests

        if isinstance(self._error, InvalidTransition):
            return "Invalid Link Definition"

        if isinstance(self._error, requests.RequestException):
            return "Network Error"

        if self._kind in (
            RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_DATA_TOO_LARGE,
            RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_FILTER_TOO_MUCH,
            RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_TOO_SLOW,
            RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_LARGE_BASE_EXAMPLE,
        ):
            return "Failed Health Check"

        if self._kind in (
            RuntimeErrorKind.SCHEMA_INVALID_REGULAR_EXPRESSION,
            RuntimeErrorKind.SCHEMA_GENERIC,
            RuntimeErrorKind.HYPOTHESIS_UNSATISFIABLE,
        ):
            return "Schema Error"

        return {
            RuntimeErrorKind.SCHEMA_UNSUPPORTED: "Unsupported Schema",
            RuntimeErrorKind.SCHEMA_NO_LINKS_FOUND: "Missing Open API links",
            RuntimeErrorKind.SCHEMA_INVALID_STATE_MACHINE: "Invalid OpenAPI Links Definition",
            RuntimeErrorKind.HYPOTHESIS_UNSUPPORTED_GRAPHQL_SCALAR: "Unknown GraphQL Scalar",
            RuntimeErrorKind.SERIALIZATION_UNBOUNDED_PREFIX: "XML serialization error",
            RuntimeErrorKind.SERIALIZATION_NOT_POSSIBLE: "Serialization not possible",
        }.get(self._kind, "Runtime Error")

    @property
    def message(self) -> str:
        """Detailed error description."""
        import hypothesis.errors
        import requests

        if isinstance(self._error, requests.RequestException):
            return get_request_error_message(self._error)

        if self._kind == RuntimeErrorKind.SCHEMA_UNSUPPORTED:
            return str(self._error).strip()

        if self._kind == RuntimeErrorKind.HYPOTHESIS_UNSUPPORTED_GRAPHQL_SCALAR and isinstance(
            self._error, hypothesis.errors.InvalidArgument
        ):
            scalar_name = scalar_name_from_error(self._error)
            return f"Scalar type '{scalar_name}' is not recognized"

        if self._kind == RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_DATA_TOO_LARGE:
            return HEALTH_CHECK_MESSAGE_DATA_TOO_LARGE
        if self._kind == RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_FILTER_TOO_MUCH:
            return HEALTH_CHECK_MESSAGE_FILTER_TOO_MUCH
        if self._kind == RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_TOO_SLOW:
            return HEALTH_CHECK_MESSAGE_TOO_SLOW
        if self._kind == RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_LARGE_BASE_EXAMPLE:
            return HEALTH_CHECK_MESSAGE_LARGE_BASE_EXAMPLE

        if self._kind == RuntimeErrorKind.HYPOTHESIS_UNSATISFIABLE:
            return f"{self._error}. Possible reasons:"

        if self._kind in (
            RuntimeErrorKind.SCHEMA_INVALID_REGULAR_EXPRESSION,
            RuntimeErrorKind.SCHEMA_GENERIC,
        ):
            return self._error.message  # type: ignore

        return str(self._error)

    @cached_property
    def extras(self) -> list[str]:
        """Additional context about the error."""
        import requests

        if isinstance(self._error, requests.RequestException):
            return get_request_error_extras(self._error)

        if self._kind == RuntimeErrorKind.HYPOTHESIS_UNSATISFIABLE:
            return [
                "- Contradictory schema constraints, such as a minimum value exceeding the maximum.",
                "- Invalid schema definitions for headers or cookies, for example allowing for non-ASCII characters.",
                "- Excessive schema complexity, which hinders parameter generation.",
            ]

        return []

    @cached_property
    def _error_repr(self) -> str:
        return format_exception(self._error, with_traceback=False)

    @property
    def has_useful_traceback(self) -> bool:
        return self._kind not in (
            RuntimeErrorKind.SCHEMA_INVALID_REGULAR_EXPRESSION,
            RuntimeErrorKind.SCHEMA_INVALID_STATE_MACHINE,
            RuntimeErrorKind.SCHEMA_UNSUPPORTED,
            RuntimeErrorKind.SCHEMA_GENERIC,
            RuntimeErrorKind.SCHEMA_NO_LINKS_FOUND,
            RuntimeErrorKind.SERIALIZATION_NOT_POSSIBLE,
            RuntimeErrorKind.HYPOTHESIS_UNSUPPORTED_GRAPHQL_SCALAR,
            RuntimeErrorKind.HYPOTHESIS_UNSATISFIABLE,
            RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_LARGE_BASE_EXAMPLE,
            RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_TOO_SLOW,
            RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_DATA_TOO_LARGE,
            RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_FILTER_TOO_MUCH,
            RuntimeErrorKind.NETWORK_OTHER,
        )

    @cached_property
    def traceback(self) -> str:
        return format_exception(self._error, with_traceback=True)

    def format(self, *, bold: Callable[[str], str] = str, indent: str = "    ") -> str:
        """Format error message with optional styling and traceback."""
        message = []

        title = self.title
        if title:
            message.append(f"{title}\n")

        # Main message
        body = self.message or str(self._error)
        message.append(body)

        # Extras
        if self.extras:
            extras = self.extras
        elif self.has_useful_traceback:
            extras = split_traceback(self.traceback)
        else:
            extras = []

        if extras:
            message.append("")  # Empty line before extras
            message.extend(f"{indent}{extra}" for extra in extras)

        if self._code_sample is not None:
            message.append(f"\nReproduce with: \n\n    {self._code_sample}")

        # Suggestion
        suggestion = get_runtime_error_suggestion(self._kind, bold=bold)
        if suggestion is not None:
            message.append(f"\nTip: {suggestion}")

        return "\n".join(message)


def scalar_name_from_error(exception: hypothesis.errors.InvalidArgument) -> str:
    # This one is always available as the format is checked upfront
    match = re.search(r"Scalar '(\w+)' is not supported", str(exception))
    match = cast(re.Match, match)
    return match.group(1)


def extract_health_check_error(error: hypothesis.errors.FailedHealthCheck) -> hypothesis.HealthCheck | None:
    from hypothesis import HealthCheck

    match = re.search(r"add HealthCheck\.(\w+) to the suppress_health_check ", str(error))
    if match:
        return {
            "data_too_large": HealthCheck.data_too_large,
            "filter_too_much": HealthCheck.filter_too_much,
            "too_slow": HealthCheck.too_slow,
            "large_base_example": HealthCheck.large_base_example,
        }.get(match.group(1))
    return None


def get_runtime_error_suggestion(error_type: RuntimeErrorKind, bold: Callable[[str], str] = str) -> str | None:
    """Get a user-friendly suggestion for handling the error."""

    def _format_health_check_suggestion(label: str) -> str:
        return f"Bypass this health check using {bold(f'`--suppress-health-check={label}`')}."

    return {
        RuntimeErrorKind.CONNECTION_SSL: f"Bypass SSL verification with {bold('`--tls-verify=false`')}.",
        RuntimeErrorKind.HYPOTHESIS_UNSATISFIABLE: "Examine the schema for inconsistencies and consider simplifying it.",
        RuntimeErrorKind.SCHEMA_NO_LINKS_FOUND: "Review your endpoint filters to include linked operations",
        RuntimeErrorKind.SCHEMA_INVALID_REGULAR_EXPRESSION: "Ensure your regex is compatible with Python's syntax.\n"
        "For guidance, visit: https://docs.python.org/3/library/re.html",
        RuntimeErrorKind.HYPOTHESIS_UNSUPPORTED_GRAPHQL_SCALAR: "Define a custom strategy for it.\n"
        "For guidance, visit: https://schemathesis.readthedocs.io/en/stable/guides/graphql-custom-scalars/",
        RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_DATA_TOO_LARGE: _format_health_check_suggestion("data_too_large"),
        RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_FILTER_TOO_MUCH: _format_health_check_suggestion("filter_too_much"),
        RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_TOO_SLOW: _format_health_check_suggestion("too_slow"),
        RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_LARGE_BASE_EXAMPLE: _format_health_check_suggestion(
            "large_base_example"
        ),
    }.get(error_type)


HEALTH_CHECK_MESSAGE_DATA_TOO_LARGE = """There's a notable occurrence of examples surpassing the maximum size limit.
Typically, generating excessively large examples can compromise the quality of test outcomes.

Consider revising the schema to more accurately represent typical use cases
or applying constraints to reduce the data size."""
HEALTH_CHECK_MESSAGE_FILTER_TOO_MUCH = """A significant number of generated examples are being filtered out, indicating
that the schema's constraints may be too complex.

This level of filtration can slow down testing and affect the distribution
of generated data. Review and simplify the schema constraints where
possible to mitigate this issue."""
HEALTH_CHECK_MESSAGE_TOO_SLOW = "Data generation is extremely slow. Consider reducing the complexity of the schema."
HEALTH_CHECK_MESSAGE_LARGE_BASE_EXAMPLE = """A health check has identified that the smallest example derived from the schema
is excessively large, potentially leading to inefficient test execution.

This is commonly due to schemas that specify large-scale data structures by
default, such as an array with an extensive number of elements.

Consider revising the schema to more accurately represent typical use cases
or applying constraints to reduce the data size."""


@enum.unique
class RuntimeErrorKind(str, enum.Enum):
    """Classification of runtime errors."""

    # Connection related issues
    CONNECTION_SSL = "connection_ssl"
    CONNECTION_OTHER = "connection_other"
    NETWORK_OTHER = "network_other"

    # Hypothesis issues
    HYPOTHESIS_UNSATISFIABLE = "hypothesis_unsatisfiable"
    HYPOTHESIS_UNSUPPORTED_GRAPHQL_SCALAR = "hypothesis_unsupported_graphql_scalar"
    HYPOTHESIS_HEALTH_CHECK_DATA_TOO_LARGE = "hypothesis_health_check_data_too_large"
    HYPOTHESIS_HEALTH_CHECK_FILTER_TOO_MUCH = "hypothesis_health_check_filter_too_much"
    HYPOTHESIS_HEALTH_CHECK_TOO_SLOW = "hypothesis_health_check_too_slow"
    HYPOTHESIS_HEALTH_CHECK_LARGE_BASE_EXAMPLE = "hypothesis_health_check_large_base_example"

    SCHEMA_INVALID_REGULAR_EXPRESSION = "schema_invalid_regular_expression"
    SCHEMA_INVALID_STATE_MACHINE = "schema_invalid_state_machine"
    SCHEMA_NO_LINKS_FOUND = "schema_no_links_found"
    SCHEMA_UNSUPPORTED = "schema_unsupported"
    SCHEMA_GENERIC = "schema_generic"

    SERIALIZATION_NOT_POSSIBLE = "serialization_not_possible"
    SERIALIZATION_UNBOUNDED_PREFIX = "serialization_unbounded_prefix"

    UNCLASSIFIED = "unclassified"


def _classify(*, error: Exception) -> RuntimeErrorKind:
    """Classify an error."""
    import hypothesis.errors
    import requests
    from hypothesis import HealthCheck

    # Network-related errors
    if isinstance(error, requests.RequestException):
        if isinstance(error, requests.exceptions.SSLError):
            return RuntimeErrorKind.CONNECTION_SSL
        if isinstance(error, requests.exceptions.ConnectionError):
            return RuntimeErrorKind.CONNECTION_OTHER
        return RuntimeErrorKind.NETWORK_OTHER

    # Hypothesis errors
    if (
        isinstance(error, hypothesis.errors.InvalidArgument)
        and str(error).endswith("larger than Hypothesis is designed to handle")
        or "can never generate an example, because min_size is larger than Hypothesis supports" in str(error)
    ):
        return RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_LARGE_BASE_EXAMPLE
    if isinstance(error, hypothesis.errors.Unsatisfiable):
        return RuntimeErrorKind.HYPOTHESIS_UNSATISFIABLE
    if isinstance(error, hypothesis.errors.FailedHealthCheck):
        health_check = extract_health_check_error(error)
        if health_check is not None:
            return {
                HealthCheck.data_too_large: RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_DATA_TOO_LARGE,
                HealthCheck.filter_too_much: RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_FILTER_TOO_MUCH,
                HealthCheck.too_slow: RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_TOO_SLOW,
                HealthCheck.large_base_example: RuntimeErrorKind.HYPOTHESIS_HEALTH_CHECK_LARGE_BASE_EXAMPLE,
            }[health_check]
        return RuntimeErrorKind.UNCLASSIFIED
    if isinstance(error, hypothesis.errors.InvalidArgument) and str(error).startswith("Scalar "):
        # Comes from `hypothesis-graphql`
        return RuntimeErrorKind.HYPOTHESIS_UNSUPPORTED_GRAPHQL_SCALAR

    # Schema errors
    if isinstance(error, errors.InvalidSchema):
        if isinstance(error, errors.InvalidRegexPattern):
            return RuntimeErrorKind.SCHEMA_INVALID_REGULAR_EXPRESSION
        return RuntimeErrorKind.SCHEMA_GENERIC
    if isinstance(error, errors.InvalidStateMachine):
        return RuntimeErrorKind.SCHEMA_INVALID_STATE_MACHINE
    if isinstance(error, errors.NoLinksFound):
        return RuntimeErrorKind.SCHEMA_NO_LINKS_FOUND
    if isinstance(error, UnsupportedRecursiveReference):
        # Recursive references are not supported right now
        return RuntimeErrorKind.SCHEMA_UNSUPPORTED
    if isinstance(error, errors.SerializationError):
        if isinstance(error, errors.UnboundPrefix):
            return RuntimeErrorKind.SERIALIZATION_UNBOUNDED_PREFIX
        return RuntimeErrorKind.SERIALIZATION_NOT_POSSIBLE
    return RuntimeErrorKind.UNCLASSIFIED


def deduplicate_errors(errors: Sequence[Exception]) -> Iterator[Exception]:
    """Deduplicate a list of errors."""
    seen = set()
    serialization_media_types = set()

    for error in errors:
        # Collect media types
        if isinstance(error, SerializationNotPossible):
            for media_type in error.media_types:
                serialization_media_types.add(media_type)
            continue

        message = canonicalize_error_message(error)
        if message not in seen:
            seen.add(message)
            yield error

    if serialization_media_types:
        yield SerializationNotPossible.from_media_types(*sorted(serialization_media_types))


MEMORY_ADDRESS_RE = re.compile("0x[0-9a-fA-F]+")
URL_IN_ERROR_MESSAGE_RE = re.compile(r"Max retries exceeded with url: .*? \(Caused by")


def canonicalize_error_message(error: Exception, with_traceback: bool = True) -> str:
    """Canonicalize error messages by removing dynamic components."""
    message = format_exception(error, with_traceback=with_traceback)
    # Replace memory addresses
    message = MEMORY_ADDRESS_RE.sub("0xbaaaaaaaaaad", message)
    # Remove URL information
    return URL_IN_ERROR_MESSAGE_RE.sub("", message)


def clear_hypothesis_notes(exc: Exception) -> None:
    notes = getattr(exc, "__notes__", [])
    if any("while generating" in note for note in notes):
        notes.clear()


def is_unrecoverable_network_error(exc: Exception) -> bool:
    from http.client import RemoteDisconnected

    from urllib3.exceptions import ProtocolError

    def has_connection_reset(inner: BaseException) -> bool:
        exc_str = str(inner)
        if any(pattern in exc_str for pattern in ["Connection reset by peer", "[Errno 104]", "ECONNRESET"]):
            return True

        if inner.__context__ is not None:
            return has_connection_reset(inner.__context__)

        return False

    if isinstance(exc.__context__, ProtocolError):
        if len(exc.__context__.args) == 2 and isinstance(exc.__context__.args[1], RemoteDisconnected):
            return True
        if len(exc.__context__.args) == 1 and exc.__context__.args[0] == "Response ended prematurely":
            return True

    return has_connection_reset(exc)


@dataclass()
class UnrecoverableNetworkError:
    error: requests.ConnectionError | ChunkedEncodingError
    code_sample: str

    __slots__ = ("error", "code_sample")

    def __init__(self, error: requests.ConnectionError | ChunkedEncodingError, code_sample: str) -> None:
        self.error = error
        self.code_sample = code_sample


@dataclass
class TestingState:
    unrecoverable_network_error: UnrecoverableNetworkError | None

    __slots__ = ("unrecoverable_network_error",)

    def __init__(self) -> None:
        self.unrecoverable_network_error = None
