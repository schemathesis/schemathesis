from __future__ import annotations

import http.client
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from json import JSONDecodeError
from typing import Callable

from schemathesis.core.compat import BaseExceptionGroup
from schemathesis.core.output import OutputConfig, prepare_response_payload
from schemathesis.core.transport import Response


@dataclass
class Failure(AssertionError):
    """API check failure."""

    __slots__ = ("operation", "title", "message", "code")

    def __init__(self, *, operation: str, title: str, message: str, code: str) -> None:
        self.operation = operation
        self.title = title
        self.message = message
        self.code = code

    def __str__(self) -> str:
        if not self.message:
            return self.title
        return f"{self.title}\n\n{self.message}"

    # Comparison & hashing is done purely on classes to simplify keeping the minimized failure during shrinking
    def __hash__(self) -> int:
        return hash(self.__class__)

    def __eq__(self, other: object, /) -> bool:
        if not isinstance(other, Failure):
            return NotImplemented
        return type(self) is type(other) and self.operation == other.operation and self._unique_key == other._unique_key

    @classmethod
    def from_assertion(cls, *, name: str, operation: str, exc: AssertionError) -> Failure:
        return Failure(
            operation=operation,
            title=f"Custom check failed: `{name}`",
            message=str(exc),
            code="custom",
        )

    @property
    def _unique_key(self) -> str:
        return self.message


@dataclass
class MaxResponseTimeConfig:
    limit: float = 10.0


class ResponseTimeExceeded(Failure):
    """Response took longer than expected."""

    def __init__(
        self,
        *,
        operation: str,
        elapsed: float,
        deadline: int,
        message: str,
        title: str = "Response time limit exceeded",
        code: str = "response_time_exceeded",
    ) -> None:
        self.operation = operation
        self.elapsed = elapsed
        self.deadline = deadline
        self.title = title
        self.message = message
        self.code = code

    @property
    def _unique_key(self) -> str:
        return self.title


class ServerError(Failure):
    """Server responded with an error."""

    def __init__(
        self,
        *,
        operation: str,
        status_code: int,
        title: str = "Server error",
        message: str = "",
        code: str = "server_error",
    ) -> None:
        self.operation = operation
        self.status_code = status_code
        self.title = title
        self.message = message
        self.code = code

    @property
    def _unique_key(self) -> str:
        return str(self.status_code)


class MalformedJson(Failure):
    """Failed to deserialize JSON."""

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
        code: str = "malformed_json",
    ) -> None:
        self.operation = operation
        self.validation_message = validation_message
        self.document = document
        self.position = position
        self.lineno = lineno
        self.colno = colno
        self.message = message
        self.title = title
        self.code = code

    @property
    def _unique_key(self) -> str:
        return self.title

    @classmethod
    def from_exception(cls, *, operation: str, exc: JSONDecodeError) -> MalformedJson:
        return cls(
            operation=operation,
            message=str(exc),
            validation_message=exc.msg,
            document=exc.doc,
            position=exc.pos,
            lineno=exc.lineno,
            colno=exc.colno,
        )


class FailureGroup(BaseExceptionGroup):
    """Multiple distinct check failures."""

    exceptions: Sequence[Failure]

    def __new__(cls, failures: Sequence[Failure], message: str | None = None) -> FailureGroup:
        if message is None:
            message = failure_report_title(failures)
        return super().__new__(cls, message, list(failures))


class MessageBlock(Enum):
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
    response: Response,
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

    # cURL
    output += "\n" + formatter(MessageBlock.CURL, f"\nReproduce with: \n\n    {curl}")

    return output