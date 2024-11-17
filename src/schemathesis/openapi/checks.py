from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING, Any

from schemathesis.core.failures import Failure

if TYPE_CHECKING:
    from jsonschema import ValidationError

    from schemathesis.internal.output import OutputConfig


class UndefinedStatusCode(Failure):
    """Response has a status code that is not defined in the schema."""

    def __init__(
        self,
        *,
        operation: str,
        status_code: int,
        defined_status_codes: list[str],
        allowed_status_codes: list[int],
        message: str,
        title: str = "Undocumented HTTP status code",
        code: str = "undefined_status_code",
    ) -> None:
        self.operation = operation
        self.status_code = status_code
        self.defined_status_codes = defined_status_codes
        self.allowed_status_codes = allowed_status_codes
        self.message = message
        self.title = title
        self.code = code

    @property
    def _unique_key(self) -> str:
        return str(self.status_code)


class MissingHeaders(Failure):
    """Some required headers are missing."""

    def __init__(
        self,
        *,
        operation: str,
        missing_headers: list[str],
        message: str,
        title: str = "Missing required headers",
        code: str = "missing_headers",
    ) -> None:
        self.operation = operation
        self.missing_headers = missing_headers
        self.message = message
        self.title = title
        self.code = code


class JsonSchemaError(Failure):
    """Additional information about JSON Schema validation errors."""

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
        code: str = "json_schema",
    ) -> None:
        self.operation = operation
        self.validation_message = validation_message
        self.schema_path = schema_path
        self.schema = schema
        self.instance_path = instance_path
        self.instance = instance
        self.message = message
        self.title = title
        self.code = code

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
        output_config: OutputConfig | None = None,
    ) -> JsonSchemaError:
        from schemathesis.internal.output import OutputConfig, truncate_json

        output_config = OutputConfig.from_parent(output_config, max_lines=20)
        schema = textwrap.indent(truncate_json(exc.schema, config=output_config), prefix="    ")
        value = textwrap.indent(truncate_json(exc.instance, config=output_config), prefix="    ")
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

    def __init__(
        self,
        *,
        operation: str,
        media_types: list[str],
        message: str,
        title: str = "Missing Content-Type header",
        code: str = "missing_content_type",
    ) -> None:
        self.operation = operation
        self.media_types = media_types
        self.message = message
        self.title = title
        self.code = code

    @property
    def _unique_key(self) -> str:
        return ""


class MalformedMediaType(Failure):
    """Media type name is malformed."""

    def __init__(
        self,
        *,
        operation: str,
        actual: str,
        defined: str,
        message: str,
        title: str = "Malformed media type",
        code: str = "malformed_media_type",
    ) -> None:
        self.operation = operation
        self.actual = actual
        self.defined = defined
        self.message = message
        self.title = title
        self.code = code


class UndefinedContentType(Failure):
    """Response has Content-Type that is not documented in the schema."""

    def __init__(
        self,
        *,
        operation: str,
        content_type: str,
        defined_content_types: list[str],
        message: str,
        title: str = "Undocumented Content-Type",
        code: str = "undefined_content_type",
    ) -> None:
        self.operation = operation
        self.content_type = content_type
        self.defined_content_types = defined_content_types
        self.message = message
        self.title = title
        self.code = code

    @property
    def _unique_key(self) -> str:
        return self.content_type


class UseAfterFree(Failure):
    """Resource was used after a successful DELETE operation on it."""

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        free: str,
        usage: str,
        title: str = "Use after free",
        code: str = "use_after_free",
    ) -> None:
        self.operation = operation
        self.message = message
        self.free = free
        self.usage = usage
        self.title = title
        self.code = code

    @property
    def _unique_key(self) -> str:
        return ""


class EnsureResourceAvailability(Failure):
    """Resource is not available immediately after creation."""

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        created_with: str,
        not_available_with: str,
        title: str = "Resource is not available after creation",
        code: str = "ensure_resource_availability",
    ) -> None:
        self.operation = operation
        self.message = message
        self.created_with = created_with
        self.not_available_with = not_available_with
        self.title = title
        self.code = code

    @property
    def _unique_key(self) -> str:
        return ""


class IgnoredAuth(Failure):
    """The API operation does not check the specified authentication."""

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        title: str = "Authentication declared but not enforced for this operation",
        code: str = "ignored_auth",
    ) -> None:
        self.operation = operation
        self.message = message
        self.title = title
        self.code = code

    @property
    def _unique_key(self) -> str:
        return ""


class AcceptedNegativeData(Failure):
    """Response with negative data was accepted."""

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        status_code: int,
        allowed_statuses: list[str],
        title: str = "Accepted negative data",
        code: str = "accepted_negative_data",
    ) -> None:
        self.operation = operation
        self.message = message
        self.status_code = status_code
        self.allowed_statuses = allowed_statuses
        self.title = title
        self.code = code

    @property
    def _unique_key(self) -> str:
        return str(self.status_code)


class RejectedPositiveData(Failure):
    """Response with positive data was rejected."""

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        status_code: int,
        allowed_statuses: list[str],
        title: str = "Rejected positive data",
        code: str = "rejected_positive_data",
    ) -> None:
        self.operation = operation
        self.message = message
        self.status_code = status_code
        self.allowed_statuses = allowed_statuses
        self.title = title
        self.code = code

    @property
    def _unique_key(self) -> str:
        return str(self.status_code)