from __future__ import annotations

import http.client
import textwrap
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from json import JSONDecodeError
from typing import Any

from schemathesis.config import OutputConfig
from schemathesis.core.compat import BaseExceptionGroup
from schemathesis.core.output import prepare_response_payload
from schemathesis.core.transport import Response


class Severity(Enum):
    # For server errors, security issues like ignored auth
    CRITICAL = auto()
    # For schema violations
    HIGH = auto()
    # For content type issues, header problems
    MEDIUM = auto()
    # For performance issues, minor inconsistencies
    LOW = auto()

    def __lt__(self, other: Severity) -> bool:
        # Lower values are more severe
        return self.value < other.value


@dataclass
class Failure(AssertionError):
    """API check failure."""

    __slots__ = ("operation", "title", "message", "case_id", "severity")

    def __init__(
        self,
        *,
        operation: str,
        title: str,
        message: str,
        case_id: str | None = None,
        severity: Severity = Severity.MEDIUM,
    ) -> None:
        self.operation = operation
        self.title = title
        self.message = message
        self.case_id = case_id
        self.severity = severity

    def __str__(self) -> str:
        if not self.message:
            return self.title
        return f"{self.title}\n\n{self.message}"

    def __lt__(self, other: Failure) -> bool:
        return (
            self.severity,
            self.__class__.__name__,
            self.message,
        ) < (other.severity, other.__class__.__name__, other.message)

    # Comparison & hashing is done purely on classes to simplify keeping the minimized failure during shrinking
    def __hash__(self) -> int:
        return hash(self.__class__)

    def __eq__(self, other: object, /) -> bool:
        if not isinstance(other, Failure):
            return NotImplemented
        return type(self) is type(other) and self.operation == other.operation and self._unique_key == other._unique_key

    @property
    def _unique_key(self) -> Any:
        return self.message


def get_origin(exception: BaseException, seen: tuple[BaseException, ...] = ()) -> tuple:
    filename, lineno = None, None
    if tb := exception.__traceback__:
        filename, lineno, *_ = traceback.extract_tb(tb)[-1]
    seen = (*seen, exception)
    context = ()
    if exception.__context__ is not None and exception.__context__ not in seen:
        context = get_origin(exception.__context__, seen=seen)
    return (
        type(exception),
        filename,
        lineno,
        context,
        (
            tuple(get_origin(exc, seen=seen) for exc in exception.exceptions if exc not in seen)
            if isinstance(exception, BaseExceptionGroup)
            else ()
        ),
    )


class CustomFailure(Failure):
    __slots__ = ("operation", "title", "message", "exception", "case_id", "severity", "origin")

    def __init__(
        self,
        *,
        operation: str,
        title: str,
        message: str,
        exception: AssertionError,
        case_id: str | None = None,
        severity: Severity = Severity.MEDIUM,
    ) -> None:
        self.operation = operation
        self.title = title
        self.message = message
        self.exception = exception
        self.case_id = case_id
        self.severity = severity
        self.origin = get_origin(exception)

    @property
    def _unique_key(self) -> Any:
        return self.origin


class ResponseTimeExceeded(Failure):
    """Response took longer than expected."""

    __slots__ = ("operation", "elapsed", "deadline", "title", "message", "case_id", "severity")

    def __init__(
        self,
        *,
        operation: str,
        elapsed: float,
        deadline: float,
        message: str,
        title: str = "Response time limit exceeded",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.elapsed = elapsed
        self.deadline = deadline
        self.title = title
        self.message = message
        self.case_id = case_id
        self.severity = Severity.LOW

    @property
    def _unique_key(self) -> str:
        return self.title


class ServerError(Failure):
    """Server responded with an error."""

    __slots__ = ("operation", "status_code", "title", "message", "case_id", "severity")

    def __init__(
        self,
        *,
        operation: str,
        status_code: int,
        title: str = "Server error",
        message: str = "",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.status_code = status_code
        self.title = title
        self.message = message
        self.case_id = case_id
        self.severity = Severity.CRITICAL

    @property
    def _unique_key(self) -> str:
        return str(self.status_code)


class MalformedJson(Failure):
    """Failed to deserialize JSON."""

    __slots__ = (
        "operation",
        "validation_message",
        "document",
        "position",
        "lineno",
        "colno",
        "message",
        "title",
        "case_id",
        "severity",
    )

    def __init__(
        self,
        *,
        operation: str,
        validation_message: str,
        document: str,
        position: int,
        lineno: int,
        colno: int,
        message: str,
        title: str = "JSON deserialization error",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.validation_message = validation_message
        self.document = document
        self.position = position
        self.lineno = lineno
        self.colno = colno
        self.message = message
        self.title = title
        self.case_id = case_id
        self.severity = Severity.MEDIUM

    @property
    def _unique_key(self) -> Any:
        return self.title

    @classmethod
    def from_exception(cls, *, operation: str, exc: JSONDecodeError) -> MalformedJson:
        message = f"Response must be valid JSON with 'Content-Type: application/json' header:\n\n  {exc}"
        return cls(
            operation=operation,
            message=message,
            validation_message=exc.msg,
            document=exc.doc,
            position=exc.pos,
            lineno=exc.lineno,
            colno=exc.colno,
        )


class FailureGroup(BaseExceptionGroup):
    """Multiple distinct check failures."""

    exceptions: Sequence[Failure]

    def __init__(self, exceptions: Sequence[Failure], message: str = "", /) -> None:
        super().__init__(message, exceptions)

    def __new__(cls, failures: Sequence[Failure], message: str | None = None) -> FailureGroup:
        if message is None:
            message = failure_report_title(failures)
        return super().__new__(cls, message, list(failures))


class MessageBlock(str, Enum):
    CASE_ID = "case_id"
    FAILURE = "failure"
    STATUS = "status"
    CURL = "curl"


BlockFormatter = Callable[[MessageBlock, str], str]


def failure_report_title(failures: Sequence[Failure]) -> str:
    message = f"Schemathesis found {len(failures)} distinct failure"
    if len(failures) > 1:
        message += "s"
    return message


def format_failures(
    *,
    case_id: str | None,
    response: Response | None,
    failures: Sequence[Failure],
    curl: str,
    formatter: BlockFormatter | None = None,
    config: OutputConfig,
) -> str:
    """Format failure information with custom styling."""
    formatter = formatter or (lambda _, x: x)

    if case_id is not None:
        output = formatter(MessageBlock.CASE_ID, f"{case_id}\n")
    else:
        output = ""

    # Failures
    for idx, failure in enumerate(failures):
        output += formatter(MessageBlock.FAILURE, f"\n- {failure.title}")
        if failure.message:
            output += "\n\n"
            output += textwrap.indent(failure.message, "    ")
        if idx != len(failures):
            output += "\n"

    # Response status
    if isinstance(response, Response):
        reason = http.client.responses.get(response.status_code, "Unknown")
        output += formatter(MessageBlock.STATUS, f"\n[{response.status_code}] {reason}:\n")
        # Response payload
        if response.content is None or not response.content:
            output += "\n    <EMPTY>"
        else:
            try:
                payload = prepare_response_payload(response.text, config=config)
                output += textwrap.indent(f"\n`{payload}`", prefix="    ")
            except UnicodeDecodeError:
                output += "\n    <BINARY>"
    else:
        output += "\n    <NO RESPONSE>"

    # cURL
    _curl = "\n".join(f"    {line}" for line in curl.splitlines())
    output += "\n" + formatter(MessageBlock.CURL, f"\nReproduce with: \n\n{_curl}")

    return output
