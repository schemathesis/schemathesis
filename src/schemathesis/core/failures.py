from __future__ import annotations

import http.client
import itertools
import textwrap
import traceback
from collections.abc import Callable, Sequence
from enum import StrEnum
from json import JSONDecodeError
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from schemathesis.core.output import (
    BINARY_PAYLOAD,
    EMPTY_PAYLOAD,
    escape_surrogates,
    format_response_payload,
)
from schemathesis.core.transport import Response

if TYPE_CHECKING:
    from schemathesis.config import OutputConfig

# Display label for failures produced by class-based checks' `after_run` (not tied to a case).
RUN_CHECKS_LABEL = "Run checks"

# Python 3.12 renamed several HTTP phrases per RFC 9110. Use the new names
# consistently across all Python versions so snapshots don't vary by version.
_RFC9110_PHRASES: dict[int, str] = {
    413: "Content Too Large",
    414: "URI Too Long",
    416: "Range Not Satisfiable",
    422: "Unprocessable Content",
}


def reason_phrase(status_code: int) -> str:
    """HTTP reason phrase, preferring RFC 9110 wording over the stdlib default."""
    return _RFC9110_PHRASES.get(status_code) or http.client.responses.get(status_code, "Unknown")


class Severity(StrEnum):
    # For server errors, security issues like ignored auth
    CRITICAL = "critical"
    # For schema violations
    HIGH = "high"
    # For content type issues, header problems
    MEDIUM = "medium"
    # For performance issues, minor inconsistencies
    LOW = "low"

    def __lt__(self, other: Severity) -> bool:  # type: ignore[override]
        return _SEVERITY_ORDER[self] < _SEVERITY_ORDER[other]


_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}


class Failure(AssertionError):
    """API check failure."""

    # Stable identity in reports; a code is never renumbered or reused.
    # The hundreds digit says what went wrong, never the protocol: 1 response, 2 request handling, 3 stateful,
    # 4 auth, 9 custom. A protocol-specific failure gets the next free code in its group.
    code: ClassVar[str] = "ST901"
    # Checks that report this failure.
    checks: ClassVar[tuple[str, ...]] = ()
    kind_title: ClassVar[str] = "Custom check failed"

    __slots__ = ("operation", "title", "message", "case_id", "severity")
    # Whether rerunning as the identity that sent the request reproduces the failure.
    reproducible_by_identity: ClassVar[bool] = True

    def __init__(
        self,
        *,
        operation: str | None,
        title: str | None = None,
        message: str,
        case_id: str | None = None,
        severity: Severity = Severity.MEDIUM,
    ) -> None:
        self.operation = operation
        self.title = title if title is not None else self.kind_title
        self.message = message
        self.case_id = case_id
        self.severity = severity

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(operation={self.operation!r}, title={self.title!r}, "
            f"message={self.message!r}, case_id={self.case_id!r}, severity={self.severity!r})"
        )

    def __str__(self) -> str:
        if not self.message:
            return self.title
        return f"{self.title}\n\n{self.message}"

    def asdict(self) -> dict[str, Any]:
        """Wire representation shared by the NDJSON report and cross-worker transport."""
        return {
            "type": self.__class__.__name__,
            "code": self.code,
            "operation": self.operation,
            "title": self.title,
            "message": self.message,
            "case_id": self.case_id,
            "severity": self.severity.value,
        }

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
    def _unique_key(self) -> str:
        # Coarse but stable: one finding per operation per failure class. A message-derived key
        # would change every time wording improves, which a stored key cannot survive.
        return ""

    def related_case_ids(self) -> tuple[str, ...]:
        """Other case IDs whose request/response history is needed to reproduce this failure.

        Default: none. Subclasses override when reproduction depends on a sibling case
        (e.g. `UseAfterFree` references the prior DELETE).
        """
        return ()

    def identity_roles(self) -> dict[str, str]:
        """Roles of the identities this failure involves, by identity name, to label their requests."""
        return {}


def _portable_filename(filename: str) -> str:
    # Origins are part of a stored failure's identity, so an absolute checkout root would tie it to one machine.
    try:
        return Path(filename).resolve().relative_to(Path.cwd().resolve()).as_posix()
    except (ValueError, OSError):
        return filename


def get_origin(exception: BaseException, seen: tuple[BaseException, ...] = ()) -> tuple:
    filename, lineno = None, None
    if tb := exception.__traceback__:
        filename, lineno, *_ = traceback.extract_tb(tb)[-1]
        filename = _portable_filename(filename)
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
    code = "ST901"
    checks = ()
    kind_title = "Custom check failed"

    __slots__ = ("exception", "origin")

    def __init__(
        self,
        *,
        operation: str | None,
        title: str,
        message: str,
        exception: Exception,
        case_id: str | None = None,
        severity: Severity = Severity.MEDIUM,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=severity)
        self.exception = exception
        self.origin = get_origin(exception)

    @property
    def _unique_key(self) -> str:
        # Include `title` (the check) so distinct checks raising at the same line don't collapse.
        return f"{self.title}|{self.origin}"


class ResponseTimeExceeded(Failure):
    """Response took longer than expected."""

    code = "ST112"
    checks = ("max_response_time",)
    kind_title = "Response time limit exceeded"

    __slots__ = ("elapsed", "deadline")

    def __init__(
        self,
        *,
        operation: str,
        elapsed: float,
        deadline: float,
        message: str,
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.LOW)
        self.elapsed = elapsed
        self.deadline = deadline

    @property
    def _unique_key(self) -> str:
        return self.title


def is_reproducible_failure(failure: Failure) -> bool:
    """Re-sending the request can reproduce this failure; timing-dependent ones cannot."""
    return not isinstance(failure, ResponseTimeExceeded)


class ServerError(Failure):
    """Server responded with an error."""

    code = "ST101"
    checks = ("not_a_server_error",)
    kind_title = "Server error"

    __slots__ = ("status_code",)

    def __init__(
        self,
        *,
        operation: str,
        status_code: int,
        title: str | None = None,
        message: str = "",
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.CRITICAL)
        self.status_code = status_code

    @property
    def _unique_key(self) -> str:
        return str(self.status_code)


class ContentTypeServerError(ServerError):
    """Server failed on a request whose `Content-Type` is malformed or not accepted by the operation."""

    code = "ST102"
    checks = ("not_a_server_error",)
    kind_title = "Server error on undeclared request Content-Type"

    __slots__ = ()

    def __init__(self, *, operation: str, status_code: int, message: str, case_id: str | None = None) -> None:
        super().__init__(
            operation=operation,
            status_code=status_code,
            message=message,
            case_id=case_id,
        )


class MalformedJson(Failure):
    """Failed to deserialize JSON."""

    code = "ST109"
    checks = ("response_schema_conformance",)
    kind_title = "JSON deserialization error"

    __slots__ = ("validation_message", "document", "position", "lineno", "colno")

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
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.MEDIUM)
        self.validation_message = validation_message
        self.document = document
        self.position = position
        self.lineno = lineno
        self.colno = colno

    @property
    def _unique_key(self) -> str:
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


class AcceptedNegativeData(Failure):
    """Response with negative data was accepted."""

    code = "ST201"
    checks = ("negative_data_rejection",)
    kind_title = "Schema-violating request accepted"

    __slots__ = ("status_code", "expected_statuses")

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        status_code: int,
        expected_statuses: list[str],
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.MEDIUM)
        self.status_code = status_code
        self.expected_statuses = expected_statuses

    @property
    def _unique_key(self) -> str:
        return str(self.status_code)


class FailureGroup(BaseExceptionGroup[Failure]):
    """Multiple distinct check failures."""

    exceptions: tuple[Failure, ...]

    def __init__(self, exceptions: Sequence[Failure], message: str = "", /) -> None:
        super().__init__(message, exceptions)

    def __new__(cls, failures: Sequence[Failure], message: str | None = None) -> FailureGroup:
        if message is None:
            message = failure_report_title(failures)
        return super().__new__(cls, message, list(failures))


class _ReportedFailureGroup(FailureGroup, AssertionError):
    """FailureGroup as reported to pytest so reporters classify it as a failed test."""


_ReportedFailureGroup.__name__ = _ReportedFailureGroup.__qualname__ = "FailureGroup"


def as_reported_failure(exc: FailureGroup) -> FailureGroup:
    reported = _ReportedFailureGroup(list(exc.exceptions), exc.message)
    reported.__notes__ = list(getattr(exc, "__notes__", []))
    return reported.with_traceback(exc.__traceback__)


class MessageBlock(StrEnum):
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


def group_failures(failures: Sequence[Failure]) -> list[tuple[str, list[Failure]]]:
    """Collapse runs of same-title same-class failures (e.g. several `JsonSchemaError`s on one response)."""
    return [
        (title, list(group))
        for (_, title), group in itertools.groupby(failures, key=lambda failure: (type(failure), failure.title))
    ]


def reproducible_by_identity(failures: Sequence[Failure]) -> bool:
    """Whether replaying as the identity that sent the request reproduces every failure."""
    return all(failure.reproducible_by_identity for failure in failures)


def case_header(index: int, case_id: str) -> str:
    """The heading of a numbered failure block."""
    return f"{index}. Test Case ID: {case_id}"


def format_failures(
    *,
    case_id: str | None,
    response: Response | None,
    failures: Sequence[Failure],
    curl: str | None,
    auth_identity: str | None = None,
    formatter: BlockFormatter | None = None,
    config: OutputConfig,
) -> str:
    """Format failure information with custom styling."""
    formatter = formatter or (lambda _, x: x)

    if case_id is not None:
        output = formatter(MessageBlock.CASE_ID, f"{case_id}\n")
    else:
        output = ""

    for title, group in group_failures(failures):
        suffix = f" ({len(group)} violations)" if len(group) > 1 else ""
        output += formatter(MessageBlock.FAILURE, f"\n- {group[0].code} {title}{suffix}")
        for failure in group:
            if failure.message:
                output += "\n\n"
                output += textwrap.indent(failure.message, "    ")
        output += "\n"

    # Response status
    if isinstance(response, Response):
        status_line = response.status_line or f"[{response.status_code}] {reason_phrase(response.status_code)}"
        output += formatter(MessageBlock.STATUS, f"\n{status_line}:\n")
        payload = format_response_payload(response, config=config)
        if payload in (EMPTY_PAYLOAD, BINARY_PAYLOAD):
            output += f"\n    {payload}"
        else:
            output += textwrap.indent(f"\n`{payload}`", prefix="    ")
    if auth_identity is not None and reproducible_by_identity(failures):
        output += "\n" + formatter(
            MessageBlock.STATUS, f"\nIdentity: {auth_identity} (reproduce with --auth-wfc-user {auth_identity})"
        )
    # `response`/`curl` are None for failures not tied to a case (e.g. after_run): nothing to show.
    if curl is not None:
        _curl = "\n".join(f"    {line}" if line else line for line in curl.splitlines())
        output += "\n" + formatter(MessageBlock.CURL, f"\nReproduce with:\n\n{_curl}")

    return escape_surrogates(output.rstrip("\n"))
