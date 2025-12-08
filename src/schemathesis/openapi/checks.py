from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING, Any

from schemathesis.config import OutputConfig
from schemathesis.core.failures import Failure, Severity
from schemathesis.core.jsonschema.bundler import unbundle
from schemathesis.core.output import truncate_json

if TYPE_CHECKING:
    from jsonschema import ValidationError


class UndefinedStatusCode(Failure):
    """Response has a status code that is not defined in the schema."""

    __slots__ = (
        "operation",
        "status_code",
        "defined_status_codes",
        "allowed_status_codes",
        "message",
        "title",
        "case_id",
        "severity",
    )

    def __init__(
        self,
        *,
        operation: str,
        status_code: int,
        defined_status_codes: list[str],
        allowed_status_codes: list[int],
        message: str,
        title: str = "Undocumented HTTP status code",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.status_code = status_code
        self.defined_status_codes = defined_status_codes
        self.allowed_status_codes = allowed_status_codes
        self.message = message
        self.title = title
        self.case_id = case_id
        self.severity = Severity.MEDIUM

    @property
    def _unique_key(self) -> str:
        return str(self.status_code)


class MissingHeaders(Failure):
    """Some required headers are missing."""

    __slots__ = ("operation", "missing_headers", "message", "title", "case_id", "severity")

    def __init__(
        self,
        *,
        operation: str,
        missing_headers: list[str],
        message: str,
        title: str = "Missing required headers",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.missing_headers = missing_headers
        self.message = message
        self.title = title
        self.case_id = case_id
        self.severity = Severity.MEDIUM


class JsonSchemaError(Failure):
    """Additional information about JSON Schema validation errors."""

    __slots__ = (
        "operation",
        "validation_message",
        "schema_path",
        "schema",
        "instance_path",
        "instance",
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
        schema_path: list[str | int],
        schema: dict[str, Any] | bool,
        instance_path: list[str | int],
        instance: None | bool | float | str | list | dict[str, Any],
        message: str,
        title: str = "Response violates schema",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.validation_message = validation_message
        self.schema_path = schema_path
        self.schema = schema
        self.instance_path = instance_path
        self.instance = instance
        self.message = message
        self.title = title
        self.case_id = case_id
        self.severity = Severity.HIGH

    @property
    def _unique_key(self) -> str:
        return "/".join(map(str, self.schema_path))

    @classmethod
    def from_exception(
        cls,
        *,
        title: str = "Response violates schema",
        operation: str,
        exc: ValidationError,
        config: OutputConfig | None = None,
        name_to_uri: dict[str, str] | None = None,
    ) -> JsonSchemaError:
        schema_path = list(exc.absolute_schema_path)

        # Reorder schema to prioritize the failing keyword in the output
        schema_to_display = exc.schema
        if isinstance(schema_to_display, dict) and schema_path:
            failing_keyword = schema_path[-1]
            if isinstance(failing_keyword, str) and failing_keyword in schema_to_display:
                # Create a new dict with the failing keyword first
                schema_to_display = {
                    failing_keyword: schema_to_display[failing_keyword],
                    **{k: v for k, v in schema_to_display.items() if k != failing_keyword},
                }
        # Restore original $ref paths for display if mapping is available
        if name_to_uri:
            schema_to_display = unbundle(schema_to_display, name_to_uri)
        schema = textwrap.indent(
            truncate_json(schema_to_display, config=config or OutputConfig(), max_lines=20), prefix="    "
        )
        value = textwrap.indent(
            truncate_json(exc.instance, config=config or OutputConfig(), max_lines=20), prefix="    "
        )
        schema_path = list(exc.absolute_schema_path)
        if len(schema_path) > 1:
            # Exclude the last segment, which is already in the schema
            schema_title = "Schema at "
            for segment in schema_path[:-1]:
                schema_title += f"/{segment}"
        else:
            schema_title = "Schema"
        message = f"{exc.message}\n\n{schema_title}:\n\n{schema}\n\nValue:\n\n{value}"
        return cls(
            operation=operation,
            title=title,
            message=message,
            validation_message=exc.message,
            schema_path=schema_path,
            schema=exc.schema,
            instance_path=list(exc.absolute_path),
            instance=exc.instance,
        )


class MissingContentType(Failure):
    """Content type header is missing."""

    __slots__ = ("operation", "media_types", "message", "title", "case_id", "severity")

    def __init__(
        self,
        *,
        operation: str,
        media_types: list[str],
        message: str,
        title: str = "Missing Content-Type header",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.media_types = media_types
        self.message = message
        self.title = title
        self.case_id = case_id
        self.severity = Severity.MEDIUM

    @property
    def _unique_key(self) -> str:
        return ""


class MalformedMediaType(Failure):
    """Media type name is malformed."""

    __slots__ = ("operation", "actual", "defined", "message", "title", "case_id", "severity")

    def __init__(
        self,
        *,
        operation: str,
        actual: str,
        defined: str,
        message: str,
        title: str = "Malformed media type",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.actual = actual
        self.defined = defined
        self.message = message
        self.title = title
        self.case_id = case_id
        self.severity = Severity.MEDIUM


class UndefinedContentType(Failure):
    """Response has Content-Type that is not documented in the schema."""

    __slots__ = (
        "operation",
        "content_type",
        "defined_content_types",
        "message",
        "title",
        "case_id",
        "severity",
    )

    def __init__(
        self,
        *,
        operation: str,
        content_type: str,
        defined_content_types: list[str],
        message: str,
        title: str = "Undocumented Content-Type",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.content_type = content_type
        self.defined_content_types = defined_content_types
        self.message = message
        self.title = title
        self.case_id = case_id
        self.severity = Severity.MEDIUM

    @property
    def _unique_key(self) -> str:
        return self.content_type


class UseAfterFree(Failure):
    """Resource was used after a successful DELETE operation on it."""

    __slots__ = ("operation", "message", "free", "usage", "title", "case_id", "severity")

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        free: str,
        usage: str,
        title: str = "Use after free",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.message = message
        self.free = free
        self.usage = usage
        self.title = title
        self.case_id = case_id
        self.severity = Severity.CRITICAL

    @property
    def _unique_key(self) -> str:
        return ""


class EnsureResourceAvailability(Failure):
    """Resource is not available immediately after creation."""

    __slots__ = ("operation", "message", "created_with", "not_available_with", "title", "case_id", "severity")

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        created_with: str,
        not_available_with: str,
        title: str = "Resource is not available after creation",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.message = message
        self.created_with = created_with
        self.not_available_with = not_available_with
        self.title = title
        self.case_id = case_id
        self.severity = Severity.MEDIUM

    @property
    def _unique_key(self) -> str:
        return ""


class IgnoredAuth(Failure):
    """The API operation does not check the specified authentication."""

    __slots__ = ("operation", "message", "title", "case_id", "severity")

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        title: str = "API accepts requests without authentication",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.message = message
        self.title = title
        self.case_id = case_id
        self.severity = Severity.CRITICAL

    @property
    def _unique_key(self) -> str:
        return ""


class AcceptedNegativeData(Failure):
    """Response with negative data was accepted."""

    __slots__ = ("operation", "message", "status_code", "expected_statuses", "title", "case_id", "severity")

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        status_code: int,
        expected_statuses: list[str],
        title: str = "API accepted schema-violating request",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.message = message
        self.status_code = status_code
        self.expected_statuses = expected_statuses
        self.title = title
        self.case_id = case_id
        self.severity = Severity.MEDIUM

    @property
    def _unique_key(self) -> str:
        return str(self.status_code)


class RejectedPositiveData(Failure):
    """Response with positive data was rejected."""

    __slots__ = ("operation", "message", "status_code", "allowed_statuses", "title", "case_id", "severity")

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        status_code: int,
        allowed_statuses: list[str],
        title: str = "API rejected schema-compliant request",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.message = message
        self.status_code = status_code
        self.allowed_statuses = allowed_statuses
        self.title = title
        self.case_id = case_id
        self.severity = Severity.MEDIUM

    @property
    def _unique_key(self) -> str:
        return str(self.status_code)


class MissingHeaderNotRejected(Failure):
    """API did not reject request without required header."""

    __slots__ = (
        "operation",
        "header_name",
        "status_code",
        "expected_statuses",
        "message",
        "title",
        "case_id",
        "severity",
    )

    def __init__(
        self,
        *,
        operation: str,
        header_name: str,
        status_code: int,
        expected_statuses: list[int],
        message: str,
        title: str = "Missing header not rejected",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.header_name = header_name
        self.status_code = status_code
        self.expected_statuses = expected_statuses
        self.message = message
        self.title = title
        self.case_id = case_id
        self.severity = Severity.MEDIUM

    @property
    def _unique_key(self) -> str:
        return self.header_name


class UnsupportedMethodResponse(Failure):
    """API response for unsupported HTTP method is incorrect."""

    __slots__ = (
        "operation",
        "method",
        "status_code",
        "allow_header_present",
        "failure_reason",
        "message",
        "title",
        "case_id",
        "severity",
    )

    def __init__(
        self,
        *,
        operation: str,
        method: str,
        status_code: int,
        allow_header_present: bool | None = None,
        failure_reason: str,  # "wrong_status" or "missing_allow_header"
        message: str,
        title: str = "Unsupported methods",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.method = method
        self.status_code = status_code
        self.allow_header_present = allow_header_present
        self.failure_reason = failure_reason
        self.message = message
        self.title = title
        self.case_id = case_id
        self.severity = Severity.MEDIUM

    @property
    def _unique_key(self) -> str:
        return self.failure_reason
