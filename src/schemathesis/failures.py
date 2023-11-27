from __future__ import annotations
import textwrap
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from jsonschema import ValidationError


class FailureContext:
    """Additional data specific to certain failure kind."""

    # Short description of what happened
    title: str
    # A longer one
    message: str
    type: str

    def unique_by_key(self, check_message: str | None) -> tuple[str, ...]:
        """A key to distinguish different failure contexts."""
        return (check_message or self.message,)


@dataclass(repr=False)
class ValidationErrorContext(FailureContext):
    """Additional information about JSON Schema validation errors."""

    validation_message: str
    schema_path: list[str | int]
    schema: dict[str, Any] | bool
    instance_path: list[str | int]
    instance: None | bool | float | str | list | dict[str, Any]
    message: str
    title: str = "Response violates schema"
    type: str = "json_schema"

    def unique_by_key(self, check_message: str | None) -> tuple[str, ...]:
        # Deduplicate by JSON Schema path. All errors that happened on this sub-schema will be deduplicated
        return ("/".join(map(str, self.schema_path)),)

    @classmethod
    def from_exception(cls, exc: ValidationError) -> ValidationErrorContext:
        from .exceptions import truncated_json

        schema = textwrap.indent(truncated_json(exc.schema, max_lines=20), prefix="    ")
        value = textwrap.indent(truncated_json(exc.instance, max_lines=20), prefix="    ")
        message = f"{exc.message}\n\nSchema:\n\n{schema}\n\nValue:\n\n{value}"
        return cls(
            message=message,
            validation_message=exc.message,
            schema_path=list(exc.absolute_schema_path),
            schema=exc.schema,
            instance_path=list(exc.absolute_path),
            instance=exc.instance,
        )


@dataclass(repr=False)
class JSONDecodeErrorContext(FailureContext):
    """Failed to decode JSON."""

    validation_message: str
    document: str
    position: int
    lineno: int
    colno: int
    message: str
    title: str = "JSON deserialization error"
    type: str = "json_decode"

    def unique_by_key(self, check_message: str | None) -> tuple[str, ...]:
        # Treat different JSON decoding failures as the same issue
        # Payloads often contain dynamic data and distinguishing it by the error location still would not be sufficient
        # as it may be different on different dynamic payloads
        return (self.title,)

    @classmethod
    def from_exception(cls, exc: JSONDecodeError) -> JSONDecodeErrorContext:
        return cls(
            message=str(exc),
            validation_message=exc.msg,
            document=exc.doc,
            position=exc.pos,
            lineno=exc.lineno,
            colno=exc.colno,
        )


@dataclass(repr=False)
class ServerError(FailureContext):
    status_code: int
    title: str = "Server error"
    message: str = ""
    type: str = "server_error"


@dataclass(repr=False)
class MissingContentType(FailureContext):
    """Content type header is missing."""

    media_types: list[str]
    message: str
    title: str = "Missing Content-Type header"
    type: str = "missing_content_type"


@dataclass(repr=False)
class UndefinedContentType(FailureContext):
    """Response has Content-Type that is not documented in the schema."""

    content_type: str
    defined_content_types: list[str]
    message: str
    title: str = "Undocumented Content-Type"
    type: str = "undefined_content_type"


@dataclass(repr=False)
class UndefinedStatusCode(FailureContext):
    """Response has a status code that is not defined in the schema."""

    # Response's status code
    status_code: int
    # Status codes as defined in schema
    defined_status_codes: list[str]
    # Defined status code with expanded wildcards
    allowed_status_codes: list[int]
    message: str
    title: str = "Undocumented HTTP status code"
    type: str = "undefined_status_code"


@dataclass(repr=False)
class MissingHeaders(FailureContext):
    """Some required headers are missing."""

    missing_headers: list[str]
    message: str
    title: str = "Missing required headers"
    type: str = "missing_headers"


@dataclass(repr=False)
class MalformedMediaType(FailureContext):
    """Media type name is malformed.

    Example: `application-json` instead of `application/json`
    """

    actual: str
    defined: str
    message: str
    title: str = "Malformed media type"
    type: str = "malformed_media_type"


@dataclass(repr=False)
class ResponseTimeExceeded(FailureContext):
    """Response took longer than expected."""

    elapsed: float
    deadline: int
    message: str
    title: str = "Response time limit exceeded"
    type: str = "response_time_exceeded"

    def unique_by_key(self, check_message: str | None) -> tuple[str, ...]:
        return (self.title,)


@dataclass(repr=False)
class RequestTimeout(FailureContext):
    """Request took longer than timeout."""

    timeout: int
    message: str
    title: str = "Response timeout"
    type: str = "request_timeout"
